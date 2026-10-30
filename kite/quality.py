"""Score saved KITE runs without calling the tested agent again."""

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import date
from hashlib import sha256
from importlib.metadata import version
import json
import os
from pathlib import Path
import unicodedata


ROOT = Path(__file__).resolve().parents[1]
GRADER_VERSION = "quality-v1"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalized(text):
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def deterministic(case):
    scenario, trace = case["scenario"], case["trace"]
    expected = set(scenario["allowed_doc_ids"])
    references = trace["references"]
    visibility = next((event.get("visibility") for event in trace.get("events", []) if event.get("stage") == "references"), None)
    observed = {item["document_id"] for item in references if item["document_id"]}
    missing = sorted(expected - observed)
    extra = sorted(observed - expected)
    unknown = [item["file_path"] for item in references if not item["document_id"]]
    as_of = date.fromisoformat(scenario["as_of"])
    stale = sorted({item["document_id"] for item in references
                    if item["document_id"] and
                    (date.fromisoformat(item["valid_from"]) > as_of or
                     item["valid_to"] and date.fromisoformat(item["valid_to"]) < as_of)})
    answer = normalized(trace["answer"])
    missing_facts = [fact for fact in scenario["required_facts"] if normalized(fact) not in answer]
    return {
        "reference_recall": (len(expected & observed) / len(expected)) if expected and visibility != "unobservable" else None,
        "reference_precision": (len(expected & observed) / len(observed)) if observed and expected and visibility != "unobservable" else None,
        "missing_expected_documents": missing,
        "reference_visibility": visibility or "observed",
        "extra_referenced_documents": extra,
        "unknown_references": unknown,
        "out_of_date_references": stale,
        "missing_required_facts": missing_facts,
    }


def credentials():
    values = {}
    path = ROOT / ".env"
    if path.exists():
        values = dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines()
                      if "=" in line and not line.lstrip().startswith("#"))
    key = os.getenv("KITE_JUDGE_API_KEY") or os.getenv("LLM_BINDING_API_KEY") or values.get("LLM_BINDING_API_KEY")
    if not key:
        raise ValueError("set KITE_JUDGE_API_KEY or LLM_BINDING_API_KEY before online scoring")
    return key, os.getenv("KITE_JUDGE_URL") or values.get("LLM_BINDING_HOST", "https://api.deepseek.com"), os.getenv("KITE_JUDGE_MODEL") or values.get("LLM_MODEL", "deepseek-flash")


def judge_behavior(client, model_name, scenario, answer):
    required_facts = scenario["required_facts"]
    response = client.chat.completions.create(
        model=model_name,
        messages=[
            {"role": "system", "content": "Classify the actual assistant reply. Return one JSON object with observed_behavior (answer, refuse, clarify), reason, evidence_quote, facts_present (boolean array), and fact_evidence_quotes (string or null array). Clarify asks the user for missing detail before answering. Refuse declines or says the requested fact is unavailable. Answer supplies a substantive answer. Align both arrays exactly with required_facts. Count translated or paraphrased facts as present only when explicitly affirmed in the reply; a negated fact is absent. Evidence quotes must be exact substrings of the reply. Example JSON: {\"observed_behavior\":\"answer\",\"reason\":\"States the limit\",\"evidence_quote\":\"150 yuan\",\"facts_present\":[true],\"fact_evidence_quotes\":[\"150 yuan\"]}."},
            {"role": "user", "content": json.dumps({"query": scenario["query"], "response": answer, "required_facts": required_facts}, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        extra_body={"thinking": {"type": "disabled"}},
        temperature=0,
        max_tokens=600,
    )
    result = json.loads(response.choices[0].message.content)
    if result.get("observed_behavior") not in {"answer", "refuse", "clarify"}:
        raise ValueError(f"invalid judge behavior: {result.get('observed_behavior')}")
    facts_present = result.get("facts_present")
    fact_quotes = result.get("fact_evidence_quotes")
    if not isinstance(facts_present, list) or len(facts_present) != len(required_facts) or any(type(value) is not bool for value in facts_present):
        raise ValueError("judge returned invalid facts_present array")
    if not isinstance(fact_quotes, list) or len(fact_quotes) != len(required_facts):
        raise ValueError("judge returned invalid fact_evidence_quotes array")
    quote = result.get("evidence_quote")
    if quote and quote not in answer:
        quote = None
    fact_checks = [{"fact": fact, "present": present, "evidence_quote": evidence if isinstance(evidence, str) and evidence in answer else None}
                   for fact, present, evidence in zip(required_facts, facts_present, fact_quotes)]
    return {"observed": result["observed_behavior"], "reason": result.get("reason"), "evidence_quote": quote, "fact_checks": fact_checks}


def judge_grounding(grader, runner, scenario, trace):
    chunks = [content for ref in trace["references"] for content in (ref.get("content") or [])]
    if not chunks:
        return {"score": None, "reason": "No retrieved chunks were observable", "context_document_ids": []}
    result = runner.run(grader.aevaluate(query=scenario["query"], response=trace["answer"], context="\n\n".join(chunks)))
    if not hasattr(result, "score"):
        raise RuntimeError(f"OpenJudge returned {type(result).__name__}: {result}")
    return {
        "score": result.score,
        "reason": result.reason,
        "context_document_ids": sorted({ref["document_id"] for ref in trace["references"] if ref["document_id"]}),
    }


def score_run(source, out, offline=False, labels_path=None):
    source_manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    labels = {}
    if labels_path:
        labels = {item["id"]: item for line in labels_path.read_text(encoding="utf-8").splitlines()
                  if line.strip() for item in [json.loads(line)]}
    client = grader = None
    judge_runner = None
    model_name = None
    judge_url = None
    if not offline:
        from openai import OpenAI
        from openjudge.graders.common.hallucination import HallucinationGrader
        from openjudge.models import OpenAIChatModel

        class JsonObjectChatModel(OpenAIChatModel):
            async def achat(self, messages, **kwargs):
                if kwargs.get("structured_model"):
                    kwargs["structured_model"] = {"type": "json_object"}
                return await super().achat(messages, **kwargs)

        key, url, model_name = credentials()
        judge_url = url
        client = OpenAI(api_key=key, base_url=url, timeout=60, max_retries=1)
        grader = HallucinationGrader(model=JsonObjectChatModel(model=model_name, api_key=key, base_url=url, timeout=60, max_retries=1, extra_body={"thinking": {"type": "disabled"}}))
        judge_runner = asyncio.Runner()
    out.mkdir(parents=True, exist_ok=False)
    (out / "cases").mkdir()
    summary = {
        "source_run": str(source),
        "source_suite_sha256": source_manifest["suite"]["sha256"],
        "grader_version": GRADER_VERSION,
        "scorer_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "openjudge_version": version("py-openjudge") if not offline else None,
        "judge_model": model_name,
        "judge_base_url": judge_url,
        "judge_settings": {"thinking": "disabled", "temperature": 0} if not offline else None,
        "counts": Counter(),
        "by_category": defaultdict(Counter),
        "judge_audit": {"behavior_labeled": 0, "behavior_disagreements": [], "grounding_labeled": 0, "grounding_false_positives": [], "grounding_false_negatives": []},
    }
    review_rows = []
    for path in sorted((source / "cases").glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if case["status"] != "ok":
            scenario = case.get("scenario")
            result = {"status": "not_scored", "scenario_id": scenario.get("id") if isinstance(scenario, dict) else None, "source_case": path.name}
        else:
            scenario, trace = case["scenario"], case["trace"]
            checks = deterministic(case)
            semantic = {"behavior": None, "grounding": None, "errors": []}
            if client:
                try:
                    semantic["behavior"] = judge_behavior(client, model_name, scenario, trace["answer"])
                except Exception as error:
                    semantic["errors"].append(f"behavior: {type(error).__name__}: {error}")
                if scenario["expected"] == "answer":
                    try:
                        semantic["grounding"] = judge_grounding(grader, judge_runner, scenario, trace)
                    except Exception as error:
                        semantic["errors"].append(f"grounding: {type(error).__name__}: {error}")
            observed = (semantic["behavior"] or {}).get("observed")
            fact_checks = (semantic["behavior"] or {}).get("fact_checks")
            facts_ok = all(item["present"] for item in fact_checks) if fact_checks is not None else not checks["missing_required_facts"]
            grounding = (semantic["grounding"] or {}).get("score")
            flags = []
            if checks["missing_expected_documents"] and checks["reference_visibility"] == "observed":
                flags.append("expected_document_missing")
            if checks["extra_referenced_documents"]:
                flags.append("extra_references")
            if checks["out_of_date_references"]:
                flags.append("out_of_date_reference")
            if not facts_ok:
                flags.append("required_fact_missing")
            elif checks["missing_required_facts"]:
                flags.append("lexical_fact_mismatch")
            if observed and observed != scenario["expected"]:
                flags.append("wrong_behavior")
            if grounding is not None and grounding < 4:
                flags.append("weak_grounding")
            if semantic["errors"] or observed is None or (scenario["expected"] == "answer" and grounding is None) or checks["reference_visibility"] == "unobservable":
                flags.append("judge_review_needed")
            result = {
                "status": "scored", "scenario_id": scenario["id"], "source_case": path.name,
                "category": scenario["category"], "expected_behavior": scenario["expected"],
                "deterministic": checks, "semantic": semantic, "flags": flags,
                "needs_review": bool(flags),
            }
            category = summary["by_category"][scenario["category"]]
            category["scored"] += 1
            category["behavior_correct"] += observed == scenario["expected"]
            category["review"] += bool(flags)
            if scenario["expected"] == "refuse":
                summary["counts"]["refusal_cases"] += 1
                summary["counts"]["correct_refusals"] += observed == "refuse"
            if scenario["expected"] == "answer":
                if checks["reference_visibility"] == "unobservable":
                    summary["counts"]["answer_cases_unobservable"] += 1
                else:
                    summary["counts"]["answer_cases"] += 1
                    summary["counts"]["grounded_answers"] += bool(grounding is not None and grounding >= 4 and not checks["missing_expected_documents"] and facts_ok and observed == "answer")
            label = labels.get(scenario["id"], {})
            audit = summary["judge_audit"]
            if observed and label.get("human_observed_behavior") in {"answer", "refuse", "clarify"}:
                audit["behavior_labeled"] += 1
                if observed != label["human_observed_behavior"]:
                    audit["behavior_disagreements"].append(scenario["id"])
            if grounding is not None and type(label.get("human_grounded")) is bool:
                audit["grounding_labeled"] += 1
                if grounding >= 4 and not label["human_grounded"]:
                    audit["grounding_false_positives"].append(scenario["id"])
                if grounding < 4 and label["human_grounded"]:
                    audit["grounding_false_negatives"].append(scenario["id"])
            review_rows.append({"id": scenario["id"], "needs_review": bool(flags), "flags": flags,
                                "judge_observed_behavior": observed, "judge_grounding_score": grounding,
                                "human_observed_behavior": label.get("human_observed_behavior"),
                                "human_grounded": label.get("human_grounded")})
        summary["counts"][result["status"]] += 1
        write_json(out / "cases" / path.name, result)
        print(f"{result['scenario_id']}: {result['status']}", flush=True)
    counts = summary["counts"]
    summary["rates"] = {
        "correct_refusal": counts["correct_refusals"] / counts["refusal_cases"] if counts["refusal_cases"] else None,
        "grounded_answer": counts["grounded_answers"] / counts["answer_cases"] if counts["answer_cases"] else None,
    }
    write_json(out / "summary.json", summary)
    (out / "review.jsonl").write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in review_rows), encoding="utf-8")
    if grader:
        judge_runner.run(grader.model.client.close())
        judge_runner.close()
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--offline", action="store_true", help="Only run deterministic checks")
    parser.add_argument("--labels", type=Path, help="Previously reviewed JSONL file with human_observed_behavior and human_grounded")
    args = parser.parse_args()
    result = score_run(args.run, args.out, args.offline, args.labels)
    print(f"Scored {result['counts']['scored']} cases; review {sum(x['review'] for x in result['by_category'].values())}")


if __name__ == "__main__":
    main()
