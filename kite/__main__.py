"""Run KITE evaluation scenarios against configured agents."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlsplit

from kite import lightrag


ROOT = Path(__file__).resolve().parents[1]
MODEL_FIELDS = (
    "llm_binding", "llm_model", "llm_binding_host",
    "embedding_binding", "embedding_model", "embedding_binding_host",
    "rerank_binding", "rerank_model", "rerank_binding_host", "enable_rerank",
)


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def agent_config(path):
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("type") != "lightrag" or not isinstance(config.get("profiles"), dict):
        raise ValueError("agent config must define LightRAG profile URLs")
    for profile, url in config["profiles"].items():
        if not isinstance(profile, str) or not isinstance(url, str):
            raise ValueError("LightRAG profile names and URLs must be strings")
        parts = urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError(f"invalid LightRAG URL for profile {profile}")
    return config


def server_info(url):
    try:
        health = lightrag.api("GET", "/health", base_url=url)
        config = health.get("configuration") or {}
        return {
            "core_version": health.get("core_version"),
            "api_version": health.get("api_version"),
            "models": {key: config.get(key) for key in MODEL_FIELDS},
        }
    except Exception as error:
        return {"error": f"{type(error).__name__}: {error}"}


def run(suite, agent, out, selected_profiles=None, selected_ids=None):
    config = agent_config(agent)
    if selected_profiles and not selected_profiles <= config["profiles"].keys():
        raise ValueError("selected profile is missing from agent config")
    suite_bytes = suite.read_bytes()
    out.mkdir(parents=True, exist_ok=False)
    case_dir = out / "cases"
    case_dir.mkdir()
    manifest = {
        "started_at": lightrag.now(),
        "finished_at": None,
        "runner_sha256": sha256(Path(__file__).read_bytes() + (ROOT / "kite/lightrag.py").read_bytes()).hexdigest(),
        "suite": {"path": str(suite), "sha256": sha256(suite_bytes).hexdigest()},
        "knowledge": {
            name: digest(ROOT / "examples/knowledge" / name)
            for name in ("documents.jsonl", "profiles.json")
        },
        "agent": config,
        "servers": {profile: server_info(url) for profile, url in config["profiles"].items()
                    if selected_profiles is None or profile in selected_profiles},
        "query_options": lightrag.QUERY_OPTIONS,
        "scorer_version": None,
        "selected_profiles": sorted(selected_profiles) if selected_profiles else None,
        "selected_ids": sorted(selected_ids) if selected_ids else None,
        "counts": {"ok": 0, "error": 0},
    }
    write_json(out / "manifest.json", manifest)
    for line_number, line in enumerate(suite_bytes.decode("utf-8").splitlines(), 1):
        if not line.strip():
            continue
        started_at = lightrag.now()
        scenario = None
        try:
            scenario = json.loads(line)
            if selected_ids and scenario["id"] not in selected_ids:
                continue
            profile = scenario["knowledge_profile"]
            if selected_profiles and profile not in selected_profiles:
                continue
            url = config["profiles"][profile]
            trace = lightrag.query_trace(scenario["query"], scenario["role"], profile, url)
            result = {"status": "ok", "scenario": scenario, "trace": trace}
        except Exception as error:
            result = {
                "status": "error", "scenario": scenario, "line_number": line_number,
                "error": {"type": type(error).__name__, "message": str(error)},
            }
        result["started_at"] = started_at
        result["finished_at"] = lightrag.now()
        write_json(case_dir / f"{line_number:04d}.json", result)
        manifest["counts"][result["status"]] += 1
        print(f"{line_number:04d} {scenario.get('id', '?') if isinstance(scenario, dict) else '?'}: {result['status']}", flush=True)
    manifest["finished_at"] = lightrag.now()
    write_json(out / "manifest.json", manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["run"])
    parser.add_argument("--suite", type=Path, default=ROOT / "examples/scenarios/core.jsonl")
    parser.add_argument("--agent", type=Path, default=ROOT / "examples/agents/lightrag.json")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--profile", action="append", help="Run only this knowledge profile; repeat to select multiple")
    parser.add_argument("--scenario", action="append", help="Run only this scenario ID; repeat to select multiple")
    args = parser.parse_args()
    manifest = run(args.suite, args.agent, args.out, set(args.profile) if args.profile else None,
                   set(args.scenario) if args.scenario else None)
    print(f"Completed: {manifest['counts']['ok']} ok, {manifest['counts']['error']} errors")


if __name__ == "__main__":
    main()
