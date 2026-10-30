"""Local, token-protected KITE evaluation workbench."""

import argparse
from datetime import datetime, timezone
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
from threading import Lock, Thread
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from kite.__main__ import ROOT
from kite import repeat


RUNS = ROOT / "runs"
SCENARIOS = ROOT / "examples/scenarios/core.jsonl"
AGENTS = ROOT / "examples/agents"
UI = Path(__file__).resolve().parent / "ui"
NAME = re.compile(r"^[A-Za-z0-9_-]+$")
JOBS = {}
LOCK = Lock()


def setting(name):
    if os.getenv(name):
        return os.environ[name]
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1]
    return None


def scenarios():
    return [json.loads(line) for line in SCENARIOS.read_text(encoding="utf-8").splitlines() if line.strip()]


def bundle_path(name):
    if not name or not NAME.fullmatch(name):
        raise ValueError("invalid bundle name")
    path = RUNS / name
    if not (path / "summary.json").is_file():
        raise FileNotFoundError(name)
    return path


def record(kind, operator, **fields):
    RUNS.mkdir(exist_ok=True)
    row = {"at": datetime.now(timezone.utc).isoformat(), "operator": operator, "kind": kind, **fields}
    with LOCK, (RUNS / "workbench-audit.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps(row, ensure_ascii=False) + "\n")


def review_rows(bundle):
    summary = json.loads((bundle_path(bundle) / "summary.json").read_text(encoding="utf-8"))
    decisions = {}
    audit_path = RUNS / "workbench-audit.jsonl"
    if audit_path.exists():
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("kind") == "review" and row.get("bundle") == bundle:
                decisions[row["scenario_id"]] = row
    return [{"scenario_id": scenario_id, "attempts": attempts,
             "decision": decisions.get(scenario_id)}
            for scenario_id, attempts in summary["cases"].items()
            if any(item["status"] != "pass" for item in attempts)]


class Handler(BaseHTTPRequestHandler):
    token = None
    operator = "local-admin"

    def send(self, status, payload, content_type="application/json"):
        body = (json.dumps(payload, ensure_ascii=False) if content_type == "application/json" else payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        header = self.headers.get("Authorization", "")
        return header.startswith("Bearer ") and compare_digest(header[7:], self.token)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size <= 0 or size > 16384:
            raise ValueError("request body must be 1 to 16384 bytes")
        data = json.loads(self.rfile.read(size))
        if not isinstance(data, dict):
            raise ValueError("request body must be an object")
        return data

    def do_GET(self):
        parsed = urlsplit(self.path)
        static = {"/": ("index.html", "text/html"), "/app.css": ("app.css", "text/css"),
                  "/app.js": ("app.js", "application/javascript")}
        if parsed.path in static:
            filename, content_type = static[parsed.path]
            return self.send(200, (UI / filename).read_text(encoding="utf-8"), content_type)
        if not parsed.path.startswith("/api/"):
            return self.send(404, {"error": "not found"})
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        query = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        try:
            if parsed.path == "/api/scenarios":
                result = scenarios()
            elif parsed.path == "/api/me":
                result = {"operator": self.operator}
            elif parsed.path == "/api/agents":
                result = [path.name for path in sorted(AGENTS.glob("*.json")) if not path.name.endswith(".example.json")]
            elif parsed.path == "/api/bundles":
                result = []
                for path in sorted(RUNS.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
                    if path.is_dir() and (path / "summary.json").is_file():
                        summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
                        if "attempts" in summary and "cases" in summary:
                            result.append({"name": path.name, "attempts": summary["attempts"],
                                           "counts": summary["counts"],
                                           "single_pass_rate": summary["single_pass_rate"],
                                           "all_attempts_pass_rate": summary["all_attempts_pass_rate"]})
            elif parsed.path == "/api/bundle":
                result = json.loads((bundle_path(query.get("name")) / "summary.json").read_text(encoding="utf-8"))
            elif parsed.path == "/api/reviews":
                result = review_rows(query.get("bundle"))
            elif parsed.path == "/api/jobs":
                with LOCK:
                    result = dict(JOBS)
            elif parsed.path == "/api/trace":
                bundle = bundle_path(query.get("bundle"))
                number = int(query.get("attempt", "0"))
                scenario_id = query.get("scenario")
                if number < 1 or number > 20 or not scenario_id or not NAME.fullmatch(scenario_id):
                    raise ValueError("invalid trace selection")
                attempt = bundle / f"attempt-{number}"
                result = None
                for path in (attempt / "run/cases").glob("*.json"):
                    case = json.loads(path.read_text(encoding="utf-8"))
                    if (case.get("scenario") or {}).get("id") == scenario_id:
                        result = {"run": case,
                                  "quality": json.loads((attempt / "quality/cases" / path.name).read_text(encoding="utf-8")),
                                  "safety": json.loads((attempt / "safety/cases" / path.name).read_text(encoding="utf-8"))}
                        break
                if result is None:
                    raise FileNotFoundError("trace")
            else:
                return self.send(404, {"error": "not found"})
            self.send(200, result)
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, {"error": str(error)})
        except FileNotFoundError:
            self.send(404, {"error": "not found"})

    def do_POST(self):
        if not self.authorized():
            return self.send(401, {"error": "unauthorized"})
        try:
            data = self.body()
            if self.path == "/api/run":
                agent_name = data.get("agent")
                if agent_name not in {path.name for path in AGENTS.glob("*.json") if not path.name.endswith(".example.json")}:
                    raise ValueError("unknown agent config")
                split = data.get("split")
                if split not in {None, "dev", "holdout"}:
                    raise ValueError("invalid split")
                selected = data.get("scenarios") or None
                if selected is not None and (not isinstance(selected, list) or not set(selected) <= {item["id"] for item in scenarios()}):
                    raise ValueError("invalid scenarios")
                times = data.get("times", 3)
                if times not in {2, 3}:
                    raise ValueError("times must be 2 or 3")
                job_id = uuid4().hex[:12]
                bundle = f"workbench-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{job_id}"
                with LOCK:
                    if any(item["status"] == "running" for item in JOBS.values()):
                        return self.send(409, {"error": "another evaluation is running"})
                    JOBS[job_id] = {"status": "running", "bundle": bundle}
                record("run_started", self.operator, bundle=bundle, agent=agent_name, split=split, scenarios=selected)
                def work():
                    try:
                        repeat.run_repeated(SCENARIOS, AGENTS / agent_name, RUNS / bundle, times,
                                            set(selected) if selected else None, {split} if split else None)
                        status, error = "complete", None
                    except Exception as failure:
                        status, error = "error", f"{type(failure).__name__}: {failure}"
                    with LOCK:
                        JOBS[job_id] = {"status": status, "bundle": bundle, "error": error}
                    record("run_finished", self.operator, bundle=bundle, status=status, error=error)
                Thread(target=work, daemon=True).start()
                return self.send(202, {"job_id": job_id, "bundle": bundle})
            if self.path == "/api/compare":
                old, new = bundle_path(data.get("old")), bundle_path(data.get("new"))
                report = RUNS / f"comparison-{uuid4().hex[:12]}.md"
                repeat.compare(old, new, report)
                record("comparison", self.operator, old=old.name, new=new.name, report=report.name)
                return self.send(200, {"markdown": report.read_text(encoding="utf-8"), "report": report.name})
            if self.path == "/api/review":
                bundle = data.get("bundle")
                bundle_path(bundle)
                scenario_id = data.get("scenario_id")
                if scenario_id not in {item["scenario_id"] for item in review_rows(bundle)}:
                    raise ValueError("scenario is not in the review queue")
                verdict = data.get("verdict")
                note = data.get("note", "")
                if verdict not in {"confirmed", "false_positive", "needs_followup"} or not isinstance(note, str) or len(note) > 1000:
                    raise ValueError("invalid review decision")
                record("review", self.operator, bundle=bundle, scenario_id=scenario_id, verdict=verdict, note=note)
                return self.send(200, {"saved": True})
            self.send(404, {"error": "not found"})
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, {"error": str(error)})
        except FileNotFoundError:
            self.send(404, {"error": "not found"})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    token = setting("KITE_WORKBENCH_TOKEN")
    if not token or len(token) < 24 or token.startswith("<"):
        raise ValueError("set KITE_WORKBENCH_TOKEN to a random value of at least 24 characters")
    Handler.token = token
    Handler.operator = setting("KITE_WORKBENCH_OPERATOR") or "local-admin"
    RUNS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"KITE workbench: http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
