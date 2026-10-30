"""Fail-closed runtime policy for a trusted caller around LightRAG queries."""

import json
from pathlib import Path
from uuid import uuid4

from kite import lightrag, safety

REASONS = {
    "unknown_identity_or_document": "Identity or document metadata could not be verified",
    "document_role_filter": "Documents restricted to the authenticated role",
    "no_authorized_documents": "No document is available to this role",
    "tool_not_allowed": "Tool is not permitted for this role",
    "tool_arguments_not_allowed": "Tool arguments need approval",
    "tool_allowed": "Tool call matches the approved arguments",
    "unknown_identity_or_profile": "Identity or knowledge profile could not be verified",
    "index_authorized": "Every indexed document permits this role",
    "index_not_authorized": "Index contains an unknown or unauthorized document",
    "index_check_failed": "Index inventory could not be verified",
    "returned_reference_not_authorized": "Answer references a document outside the verified index",
    "answer_disclosure_check": "Answer checked against protected facts",
    "output_check_failed": "Answer check could not be completed",
}


def decision(action, rule, evidence=None):
    return {"action": action, "rule_id": rule, "reason": REASONS[rule], "evidence": evidence or []}


def filter_documents(role, document_ids):
    """Return the IDs a retrieval hook may search; unknown IDs stop the search."""
    docs, _ = lightrag.fixtures()
    if not role or any(doc_id not in docs for doc_id in document_ids):
        return decision("block", "unknown_identity_or_document"), []
    allowed = [doc_id for doc_id in document_ids if role in docs[doc_id]["roles"]]
    if not allowed:
        return decision("block", "no_authorized_documents"), []
    return decision("allow", "document_role_filter", allowed), allowed


def check_tool(role, name, arguments, rules):
    """A tool adapter must call this before execution; rules come from trusted config."""
    allowed_calls = rules.get(role, {}).get(name)
    if allowed_calls is None:
        return decision("block", "tool_not_allowed", [name])
    if arguments not in allowed_calls:
        return decision("review", "tool_arguments_not_allowed", [name])
    return decision("allow", "tool_allowed", [name])


def indexed_documents(base_url):
    """Read the actual LightRAG index, not a claimed role in the question."""
    ids = []
    page = 1
    while True:
        result = lightrag.api("POST", "/documents/paginated", {"page": page, "page_size": 200}, base_url)
        for item in result["documents"]:
            if item["status"].lower() != "processed" or not item.get("file_path"):
                raise ValueError("index contains an unprocessed or unnamed document")
            ids.append(Path(item["file_path"]).stem)
        if not result["pagination"]["has_next"]:
            break
        page += 1
    if len(ids) != len(set(ids)):
        raise ValueError("index contains duplicate document names")
    return ids


def audit(path, role, profile, stage, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": lightrag.now(), "role": role, "profile": profile, "stage": stage, **result}
    with path.open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")


def guarded_query(query, role, profile, base_url, audit_path):
    """The host must supply role from authentication, never from query text."""
    docs, profiles = lightrag.fixtures()
    try:
        if profile not in profiles or not role:
            pre = decision("block", "unknown_identity_or_profile")
        else:
            indexed = indexed_documents(base_url)
            expected = set(profiles[profile])
            invalid = [doc_id for doc_id in indexed if doc_id not in expected or doc_id not in docs or role not in docs[doc_id]["roles"]]
            pre = decision("allow", "index_authorized", indexed) if indexed and not invalid else decision("block", "index_not_authorized", invalid or ["empty_index"])
    except Exception as error:
        pre = decision("block", "index_check_failed", [type(error).__name__])
    audit(audit_path, role, profile, "before_retrieval", pre)
    if pre["action"] != "allow":
        return {"decision": pre, "answer": "当前知识库无法安全提供该资料。"}

    trace = None
    try:
        trace = lightrag.query_trace(query, role, profile, base_url)
        unknown = [ref.get("file_path") for ref in trace["references"] if ref.get("document_id") not in pre["evidence"]]
        if unknown:
            post = decision("block", "returned_reference_not_authorized", unknown)
        else:
            from openai import OpenAI
            from kite.quality import credentials
            key, url, model = credentials()
            client = OpenAI(api_key=key, base_url=url, timeout=60, max_retries=1)
            protected = {doc_id: doc for doc_id, doc in docs.items() if role not in doc["roles"]}
            finding = safety.disclosure(client, model, {"query": query}, trace, protected, set())
            status = finding["status"]
            action = "block" if status in {"judge_supported", "answer_only"} else "allow" if status in {"none_observed", "not_assessed"} else "review"
            post = decision(action, "answer_disclosure_check", [status, finding.get("source_doc_ids", []), finding.get("answer_quote")])
    except Exception as error:
        post = decision("review", "output_check_failed", [type(error).__name__])
    audit_result = post
    if trace is not None:
        trace_path = audit_path.parent / "policy-traces" / f"{uuid4().hex}.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        audit_result = {**post, "trace_path": str(trace_path)}
    audit(audit_path, role, profile, "before_return", audit_result)
    return {"decision": post, "answer": trace["answer"] if post["action"] == "allow" else "该请求需要人工复核。" if post["action"] == "review" else "该回答包含受限资料，已阻止返回。"}
