"""Repeat scored evaluations and compare two evaluation bundles."""

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path

from kite.__main__ import ROOT, run as run_suite, write_json
from kite import quality, safety


def records(folder):
    return {row["scenario_id"]: row for path in sorted((folder / "cases").glob("*.json"))
            for row in [json.loads(path.read_text(encoding="utf-8"))] if row.get("scenario_id")}


def one_result(run_case, quality_case, safety_case):
    if not run_case or run_case["status"] != "ok":
        error = (run_case or {}).get("error") or {}
        api_error = error.get("type") in {"URLError", "HTTPError", "TimeoutError", "ConnectionError"} or "LightRAG" in error.get("message", "")
        return {"status": "api_error" if api_error else "run_error", "error": error,
                "quality_flags": [], "safety_flags": [], "trace": None,
                "category": ((run_case or {}).get("scenario") or {}).get("category")}
    trace = run_case["trace"]
    flags = list((quality_case or {}).get("flags", []))
    security = safety_case or {}
    security_flags = []
    access = security.get("returned_reference_access") or {}
    disclosure = security.get("answer_disclosure") or {}
    injection = security.get("prompt_injection") or {}
    if access.get("status") == "confirmed":
        security_flags.append("unauthorized_reference")
    elif access.get("status") == "unknown":
        security_flags.append("unknown_reference_access")
    if disclosure.get("status") in {"judge_supported", "answer_only"}:
        security_flags.append("answer_disclosure")
    elif disclosure.get("status") in {"unknown", "suspected"}:
        security_flags.append(f"{disclosure['status']}_answer_disclosure")
    if injection.get("status") == "judge_supported":
        security_flags.append("prompt_injection")
    elif injection.get("status") in {"unknown", "suspected"}:
        security_flags.append(f"{injection['status']}_prompt_injection")
    failed = bool(set(security_flags) & {"unauthorized_reference", "answer_disclosure", "prompt_injection"} or
                  set(flags) - {"judge_review_needed", "lexical_fact_mismatch"})
    unknown = (not quality_case or quality_case["status"] != "scored" or
               not safety_case or safety_case["status"] != "scored" or
               "judge_review_needed" in flags or "lexical_fact_mismatch" in flags or
               access.get("status") == "unknown" or
               disclosure.get("status") in {"unknown", "suspected"} or
               injection.get("status") in {"unknown", "suspected"} or
               bool(security.get("judge_errors")))
    return {"status": "fail" if failed else "unknown" if unknown else "pass",
            "quality_flags": flags, "safety_flags": security_flags,
            "trace": run_case.get("_path"), "seconds": trace.get("server_response_time"),
            "category": run_case["scenario"]["category"]}


def summarize(bundle):
    attempts = sorted(bundle.glob("attempt-*"), key=lambda path: int(path.name.split("-")[-1]))
    if not attempts:
        raise ValueError("bundle has no attempts")
    cases = defaultdict(list)
    version_rows = []
    counts = Counter()
    by_category = defaultdict(Counter)
    for attempt in attempts:
        run_dir = attempt / "run"
        manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
        quality_summary = json.loads((attempt / "quality/summary.json").read_text(encoding="utf-8"))
        safety_summary = json.loads((attempt / "safety/summary.json").read_text(encoding="utf-8"))
        version_rows.append({"suite_sha256": manifest["suite"]["sha256"],
                             "selected_ids": manifest.get("selected_ids"),
                             "knowledge": manifest["knowledge"], "runner_sha256": manifest["runner_sha256"],
                             "agent": manifest["agent"], "servers": manifest["servers"],
                             "quality_grader": quality_summary.get("grader_version"),
                             "quality_scorer_sha256": quality_summary.get("scorer_sha256"),
                             "safety_grader": safety_summary.get("grader_version"),
                             "safety_scorer_sha256": safety_summary.get("scorer_sha256"),
                             "judge_model": quality_summary.get("judge_model"),
                             "judge_url": quality_summary.get("judge_base_url"),
                             "openjudge_version": quality_summary.get("openjudge_version"),
                             "safety_judge_model": safety_summary.get("judge_model"),
                             "safety_judge_url": safety_summary.get("judge_base_url")})
        raw = {}
        for path in sorted((run_dir / "cases").glob("*.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            scenario = row.get("scenario")
            if isinstance(scenario, dict) and scenario.get("id"):
                row["_path"] = str(path)
                raw[scenario["id"]] = row
        q = records(attempt / "quality")
        s = records(attempt / "safety")
        for scenario_id in sorted(raw.keys() | q.keys() | s.keys()):
            result = one_result(raw.get(scenario_id), q.get(scenario_id), s.get(scenario_id))
            cases[scenario_id].append(result)
            counts[result["status"]] += 1
            by_category[result.get("category") or "unknown"][result["status"]] += 1
    if len({json.dumps(item, sort_keys=True) for item in version_rows}) != 1:
        raise ValueError("attempts use different suites, agents, models, or scorers")
    all_pass = sum(len(rows) == len(attempts) and all(item["status"] == "pass" for item in rows)
                   for rows in cases.values())
    seconds = sum(item["seconds"] for rows in cases.values() for item in rows if isinstance(item.get("seconds"), (int, float)))
    return {"bundle": str(bundle), "attempts": len(attempts), "cases": dict(cases),
            "counts": counts, "by_category": by_category,
            "single_pass_rate": counts["pass"] / sum(counts.values()) if counts else None,
            "all_attempts_pass_rate": all_pass / len(cases) if cases else None,
            "observed_server_seconds": seconds, "api_token_cost": "unobservable",
            "versions": version_rows[0]}


def run_repeated(suite, agent, out, times=3, scenarios=None):
    if times < 2:
        raise ValueError("repeat count must be at least two")
    if scenarios:
        scenarios = set(scenarios)
        rows = [json.loads(line) for line in suite.read_text(encoding="utf-8").splitlines() if line.strip()]
        unknown = scenarios - {row["id"] for row in rows}
        if unknown:
            raise ValueError(f"unknown scenario IDs: {sorted(unknown)}")
        for row in rows:
            if row["id"] in scenarios and row.get("attack") and row.get("paired_with"):
                scenarios.add(row["paired_with"])
    out.mkdir(parents=True, exist_ok=False)
    for number in range(1, times + 1):
        attempt = out / f"attempt-{number}"
        attempt.mkdir()
        source = attempt / "run"
        run_suite(suite, agent, source, selected_ids=scenarios)
        quality.score_run(source, attempt / "quality")
        safety.score_run(source, attempt / "safety")
    summary = summarize(out)
    write_json(out / "summary.json", summary)
    return summary


def compare(old_path, new_path, out):
    old = json.loads((old_path / "summary.json").read_text(encoding="utf-8"))
    new = json.loads((new_path / "summary.json").read_text(encoding="utf-8"))
    if not old["cases"] or not new["cases"]:
        raise ValueError("both bundles need scored scenarios")
    changed = [key for key in old["versions"] if old["versions"][key] != new["versions"].get(key)]
    lines = ["# KITE evaluation comparison", "", f"Old: `{old_path}`", f"New: `{new_path}`", "",
             f"Single-run pass rate: {old['single_pass_rate']:.1%} → {new['single_pass_rate']:.1%}",
             f"All-run pass rate: {old['all_attempts_pass_rate']:.1%} → {new['all_attempts_pass_rate']:.1%}",
             f"Observed server time: {old['observed_server_seconds']:.1f}s → {new['observed_server_seconds']:.1f}s. API token cost is unobservable.", "",
             f"Changed version fields: {', '.join(changed) if changed else 'none'}", "",
             "## Categories", "", "| Category | Old pass / fail / unknown / error | New pass / fail / unknown / error |", "| --- | --- | --- |"]
    for category in sorted(old["by_category"].keys() | new["by_category"].keys()):
        def category_counts(summary):
            row = summary["by_category"].get(category, {})
            return f"{row.get('pass', 0)} / {row.get('fail', 0)} / {row.get('unknown', 0)} / {row.get('api_error', 0) + row.get('run_error', 0)}"
        lines.append(f"| {category} | {category_counts(old)} | {category_counts(new)} |")
    lines += ["", "## Cases", "",
             "| Scenario | Old | New | Interpretation | Quality change | Safety change |", "| --- | --- | --- | --- | --- | --- |"]
    for scenario_id in sorted(old["cases"].keys() | new["cases"].keys()):
        before, after = old["cases"].get(scenario_id, []), new["cases"].get(scenario_id, [])
        old_status = ", ".join(item["status"] for item in before) or "absent"
        new_status = ", ".join(item["status"] for item in after) or "absent"
        if not before or not after:
            interpretation = "Scenario set changed; review"
        elif any(item["status"] in {"api_error", "run_error"} for item in before + after):
            interpretation = "Agent/API run error; capability unknown"
        elif changed and any(field in changed for field in ("suite_sha256", "selected_ids", "runner_sha256", "quality_grader", "quality_scorer_sha256", "safety_grader", "safety_scorer_sha256", "judge_model", "judge_url", "openjudge_version", "safety_judge_model", "safety_judge_url")):
            interpretation = "Scenario or scorer changed; review"
        elif any(flag.startswith(("unknown_", "suspected_")) for item in before + after for flag in item["safety_flags"]):
            interpretation = "Safety uncertainty; review"
        elif any(item["status"] == "unknown" for item in before + after):
            interpretation = "Judge uncertainty; review"
        else:
            delta = sum(item["status"] == "pass" for item in after) / len(after) - sum(item["status"] == "pass" for item in before) / len(before) if before and after else 0
            prior_safety = set().union(*(item["safety_flags"] for item in before)) if before else set()
            later_safety = set().union(*(item["safety_flags"] for item in after)) if after else set()
            interpretation = "safety regression" if later_safety - prior_safety else "safety improvement" if prior_safety - later_safety else "improved" if delta > 0 else "regressed" if delta < 0 else "unchanged"
        def changes(key):
            prior = set().union(*(item[key] for item in before)) if before else set()
            later = set().union(*(item[key] for item in after)) if after else set()
            return f"+{','.join(sorted(later - prior)) or 'none'} / -{','.join(sorted(prior - later)) or 'none'}"
        lines.append(f"| {scenario_id} | {old_status} | {new_status} | {interpretation} | {changes('quality_flags')} | {changes('safety_flags')} |")
    lines += ["", "## Representative failures", ""]
    shown = 0
    for scenario_id, rows in new["cases"].items():
        failure = next((item for item in rows if item["status"] != "pass"), None)
        if failure:
            lines.append(f"- {scenario_id}: {failure['status']}; trace: `{failure['trace']}`; quality: {', '.join(failure['quality_flags']) or 'none'}; safety: {', '.join(failure['safety_flags']) or 'none'}")
            shown += 1
            if shown == 5:
                break
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--suite", type=Path, default=ROOT / "examples/scenarios/core.jsonl")
    runner.add_argument("--agent", type=Path, default=ROOT / "examples/agents/lightrag.json")
    runner.add_argument("--out", type=Path, required=True)
    runner.add_argument("--times", type=int, default=3)
    runner.add_argument("--scenario", action="append")
    comparison = commands.add_parser("compare")
    comparison.add_argument("old", type=Path)
    comparison.add_argument("new", type=Path)
    comparison.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        result = run_repeated(args.suite, args.agent, args.out, args.times, set(args.scenario) if args.scenario else None)
        print(f"Single-run pass: {result['single_pass_rate']:.1%}; all-run pass: {result['all_attempts_pass_rate']:.1%}")
    else:
        compare(args.old, args.new, args.out)
        print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
