"""Check the first KITE fixture and scenario suite using only the standard library."""

from datetime import date
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "examples"


def rows(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def check():
    documents = rows(ROOT / "knowledge/documents.jsonl")
    scenarios = rows(ROOT / "scenarios/core.jsonl")
    profiles = json.loads((ROOT / "knowledge/profiles.json").read_text(encoding="utf-8"))
    docs = {item["id"]: item for item in documents}
    cases = {item["id"]: item for item in scenarios}
    assert len(docs) == len(documents), "duplicate document ID"
    assert len(cases) == len(scenarios), "duplicate scenario ID"
    assert {"dev", "holdout"} <= {item["split"] for item in scenarios}

    for name, ids in profiles.items():
        assert len(ids) == len(set(ids)), f"duplicate document in profile {name}"
        assert set(ids) <= docs.keys(), f"unknown document in profile {name}"

    for item in documents:
        assert item["roles"], f"document without access roles: {item['id']}"
        start = date.fromisoformat(item["valid_from"])
        end = date.fromisoformat(item["valid_to"]) if item["valid_to"] else None
        assert end is None or start <= end, f"invalid dates: {item['id']}"

    for case in scenarios:
        name = case["id"]
        available = set(profiles[case["knowledge_profile"]])
        allowed = set(case["allowed_doc_ids"])
        forbidden = set(case["forbidden_doc_ids"])
        assert allowed.isdisjoint(forbidden), f"allowed and forbidden overlap: {name}"
        assert allowed | forbidden <= available, f"reference outside knowledge profile: {name}"
        assert case["expected"] in {"answer", "refuse", "clarify"}, f"invalid expected behavior: {name}"
        assert case["category"] in {"answer", "access", "clarify", "no_answer", "injection"}
        assert case["split"] in {"dev", "holdout"}
        as_of = date.fromisoformat(case["as_of"])
        for doc_id in allowed:
            doc = docs[doc_id]
            assert case["role"] in doc["roles"], f"unauthorized allowed document: {name} / {doc_id}"
            assert date.fromisoformat(doc["valid_from"]) <= as_of
            assert doc["valid_to"] is None or as_of <= date.fromisoformat(doc["valid_to"])
        for doc_id in forbidden:
            assert case["role"] not in docs[doc_id]["roles"], f"authorized forbidden document: {name} / {doc_id}"
        if case["expected"] == "answer":
            assert allowed and case["required_facts"], f"answer without evidence: {name}"
        else:
            assert not case["required_facts"], f"unexpected required fact: {name}"
        partner_id = case["paired_with"]
        if partner_id:
            partner = cases[partner_id]
            assert partner["paired_with"] == name and partner["query"] == case["query"]
            assert partner["split"] == case["split"], f"pair crosses split: {name}"
        if "attack" in case:
            assert case["attack"]["source_doc_id"] in available, f"attack source absent: {name}"
            assert case["attack"]["entry"] == "retrieved_document"
            assert case["attack"]["success_condition"]

    references = rows(ROOT / "scenarios/enterprise_rag_bench.jsonl")
    assert all(item["status"] == "reference_only" for item in references)
    print(f"Checked {len(documents)} documents, {len(scenarios)} core scenarios, "
          f"{len(profiles)} knowledge profiles, {len(references)} upstream references.")


if __name__ == "__main__":
    check()
