"""Import KITE fixtures and capture observable LightRAG REST results."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = os.getenv("KITE_LIGHTRAG_URL", "http://127.0.0.1:9621").rstrip("/")
QUERY_OPTIONS = {
    "mode": "mix",
    "stream": False,
    "include_references": True,
    "include_chunk_content": True,
    "enable_rerank": True,
}


def now():
    return datetime.now(timezone.utc).isoformat()


def api(method, path, body=None, base_url=None):
    headers = {"Content-Type": "application/json"}
    if key := os.getenv("KITE_LIGHTRAG_API_KEY"):
        headers["X-API-Key"] = key
    payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request((base_url or BASE_URL).rstrip("/") + path, data=payload, headers=headers, method=method)
    try:
        with urlopen(request, timeout=120) as response:
            return json.load(response)
    except HTTPError as error:
        detail = error.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"LightRAG {method} {path}: HTTP {error.code}: {detail}") from error


def fixtures():
    lines = (ROOT / "examples/knowledge/documents.jsonl").read_text(encoding="utf-8").splitlines()
    docs = {item["id"]: item for item in map(json.loads, filter(None, lines))}
    profiles = json.loads((ROOT / "examples/knowledge/profiles.json").read_text(encoding="utf-8"))
    return docs, profiles


def ingest(profile, base_url=None, role=None):
    docs, profiles = fixtures()
    selected = [doc_id for doc_id in profiles[profile] if role is None or role in docs[doc_id]["roles"]]
    if role is not None:
        existing = api("POST", "/documents/paginated", {"page": 1, "page_size": 10}, base_url)
        if existing["pagination"]["total_count"]:
            raise ValueError("role-scoped ingestion requires an empty index")
        if not selected:
            raise ValueError(f"no documents in {profile} for role {role}")
    for doc_id in selected:
        doc = docs[doc_id]
        content = f"Title: {doc['title']}\nVersion: {doc['version']}\nValid from: {doc['valid_from']}\nValid to: {doc['valid_to'] or 'current'}\n\n{doc['content']}"
        result = api("POST", "/documents/text", {"text": content, "file_source": f"{doc_id}.txt"}, base_url)
        track_id = result["track_id"]
        print(f"Queued {doc_id}: {track_id}", flush=True)
        deadline = time.monotonic() + 600
        while time.monotonic() < deadline:
            status = api("GET", f"/documents/track_status/{quote(track_id)}", base_url=base_url)
            records = status["documents"]
            if records and all(item["status"].upper() == "PROCESSED" for item in records):
                print(f"Processed {doc_id}", flush=True)
                break
            if any(item["status"].upper() == "FAILED" for item in records):
                raise RuntimeError(f"LightRAG failed to index {doc_id}: {records[0].get('error_msg')}")
            time.sleep(5)
        else:
            raise TimeoutError(f"LightRAG indexing timed out for {doc_id}: {track_id}")


def trace(query, role, profile, raw, sent_at, request):
    docs, _ = fixtures()
    observed_at = now()
    references = []
    for item in raw.get("references") or []:
        file_path = item.get("file_path", "")
        candidate = Path(file_path).stem
        doc = docs.get(candidate)
        references.append({
            "reference_id": item.get("reference_id"),
            "file_path": file_path,
            "document_id": candidate if doc else None,
            "source": doc.get("source") if doc else None,
            "roles": doc["roles"] if doc else None,
            "version": doc["version"] if doc else None,
            "valid_from": doc["valid_from"] if doc else None,
            "valid_to": doc["valid_to"] if doc else None,
            "content": item.get("content"),
        })
    return {
        "agent": "LightRAG",
        "query": query,
        "role": role,
        "knowledge_profile": profile,
        "answer": raw["response"],
        "llm_generated": raw.get("llm_generated"),
        "references": references,
        "events": [
            {"stage": "request", "at": sent_at, "parameters": request},
            {"stage": "references", "at": observed_at, "items": references},
            {"stage": "answer", "at": observed_at, "text": raw["response"]},
            {"stage": "internal_tool_calls", "visibility": "unobservable"},
        ],
        "server_response_time": raw.get("response_time"),
        "raw_response": raw,
    }


def query_trace(query, role, profile, base_url=None):
    sent_at = now()
    request = {"query": query, **QUERY_OPTIONS}
    raw = api("POST", "/query", request, base_url)
    return trace(query, role, profile, raw, sent_at, request)


def ask(query, role, profile, output, base_url=None):
    result = query_trace(query, role, profile, base_url)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    importer = commands.add_parser("ingest")
    importer.add_argument("profile", choices=fixtures()[1])
    importer.add_argument("--url", default=BASE_URL)
    importer.add_argument("--role", help="Only ingest documents visible to this role into an empty index")
    asker = commands.add_parser("query")
    asker.add_argument("query")
    asker.add_argument("--role", required=True)
    asker.add_argument("--profile", required=True, choices=fixtures()[1])
    asker.add_argument("--out", type=Path, default=ROOT / "runs/trace.json")
    asker.add_argument("--url", default=BASE_URL)
    args = parser.parse_args()
    if args.command == "ingest":
        ingest(args.profile, args.url, args.role)
    else:
        ask(args.query, args.role, args.profile, args.out, args.url)


if __name__ == "__main__":
    main()
