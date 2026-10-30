"""Capture Dify App API replies in KITE's trace format."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from kite.lightrag import fixtures


def now():
    return datetime.now(timezone.utc).isoformat()


def api_key(name):
    if name in os.environ:
        return os.environ[name]
    path = Path(__file__).resolve().parents[1] / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
    raise ValueError(f"set {name} in the environment or local .env")


def references(resources):
    docs, _ = fixtures()
    result = []
    for item in resources:
        name = item.get("document_name") or ""
        candidate = Path(name).stem
        doc = docs.get(candidate)
        result.append({
            "reference_id": item.get("segment_id"),
            "file_path": name,
            "document_id": candidate if doc else None,
            "external_document_id": item.get("document_id"),
            "source": doc.get("source") if doc else None,
            "roles": doc["roles"] if doc else None,
            "version": doc["version"] if doc else None,
            "valid_from": doc["valid_from"] if doc else None,
            "valid_to": doc["valid_to"] if doc else None,
            "content": [item["content"]] if isinstance(item.get("content"), str) else [],
            "score": item.get("score"),
        })
    return result


def decode(response, mode):
    if mode == "blocking":
        raw = json.load(response)
        if not isinstance(raw.get("answer"), str):
            raise ValueError("Dify response has no answer")
        return raw["answer"], (raw.get("metadata") or {}), [raw]
    answer = []
    metadata = {}
    events = []
    ended = False
    agent_answer = False
    for line in response:
        line = line.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:].strip())
        events.append(event)
        kind = event.get("event")
        if kind == "agent_message":
            agent_answer = True
            answer.append(event.get("answer") or "")
        elif kind == "message" and agent_answer:
            answer = [event.get("answer") or ""]
        elif kind == "message":
            answer.append(event.get("answer") or "")
        elif kind == "message_replace":
            answer = [event.get("answer") or ""]
        elif kind == "message_end":
            metadata = event.get("metadata") or {}
            ended = True
        elif kind == "workflow_finished" and (event.get("data") or {}).get("status") == "failed":
            raise RuntimeError("Dify workflow failed")
        elif kind == "error":
            raise RuntimeError(f"Dify stream error: {event.get('code')}: {event.get('message')}")
    if not ended:
        raise RuntimeError("Dify stream ended without message_end")
    return "".join(answer), metadata, events


def query_trace(query, role, profile, settings):
    mode = settings.get("response_mode", "streaming")
    user = f"kite-{role}-{uuid4().hex}"
    body = {"inputs": {settings["role_input"]: role} if settings.get("role_input") else {},
            "query": query, "response_mode": mode, "user": user}
    sent_at = now()
    request = Request(settings["url"].rstrip("/") + "/chat-messages",
                      data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key(settings['api_key_env'])}"},
                      method="POST")
    try:
        with urlopen(request, timeout=120) as response:
            answer, metadata, raw_events = decode(response, mode)
    except HTTPError as error:
        detail = error.read(500).decode("utf-8", "replace")
        raise RuntimeError(f"Dify HTTP {error.code}: {detail}") from error
    observed_at = now()
    refs = references(metadata.get("retriever_resources") or [])
    return {
        "agent": "Dify", "query": query, "role": role, "knowledge_profile": profile,
        "answer": answer, "references": refs,
        "events": [
            {"stage": "request", "at": sent_at, "parameters": {"query": query, "response_mode": mode, "role": role}},
            {"stage": "references", "at": observed_at, "items": refs,
             "visibility": "observed" if "retriever_resources" in metadata else "unobservable"},
            {"stage": "answer", "at": observed_at, "text": answer},
            {"stage": "internal_tool_calls", "visibility": "unobservable"},
        ],
        "server_response_time": (metadata.get("usage") or {}).get("latency"),
        "usage": metadata.get("usage"), "raw_response": raw_events,
    }
