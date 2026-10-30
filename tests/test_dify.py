from io import BytesIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest

from kite import dify, quality, safety
from kite.__main__ import run, write_json


class DifyTest(unittest.TestCase):
    def test_external_stream_runs_through_kite(self):
        class App(BaseHTTPRequestHandler):
            def do_POST(self):
                self.server.seen = {"path": self.path, "authorization": self.headers.get("Authorization"),
                                    "body": json.loads(self.rfile.read(int(self.headers["Content-Length"])))}
                events = [
                    {"event": "message", "answer": "市内交通上限为 150 元。"},
                    {"event": "message_end", "metadata": {"retriever_resources": [
                        {"document_name": "travel-2026.txt", "document_id": "dify-123", "segment_id": "seg-1",
                         "content": "市内交通单次报销上限为 150 元。", "score": 0.9}]}}]
                body = "".join(f"data: {json.dumps(item, ensure_ascii=False)}\n\n" for item in events).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), App)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        import os
        os.environ["KITE_DIFY_TEST_KEY"] = "test-secret"
        try:
            with TemporaryDirectory() as folder:
                root = Path(folder)
                agent, suite, out = root / "agent.json", root / "suite.jsonl", root / "run"
                write_json(agent, {"type": "dify", "profiles": {"baseline": {
                    "url": f"http://127.0.0.1:{server.server_port}/v1", "api_key_env": "KITE_DIFY_TEST_KEY",
                    "role_input": "kite_role"}}})
                row = {"id": "travel-employee", "query": "现在市内交通单次报销上限是多少？", "role": "employee",
                       "knowledge_profile": "baseline", "category": "answer", "as_of": "2026-10-30",
                       "expected": "answer", "allowed_doc_ids": ["travel-2026"], "forbidden_doc_ids": [],
                       "required_facts": ["150 元"]}
                suite.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
                run(suite, agent, out)
                case = json.loads((out / "cases/0001.json").read_text(encoding="utf-8"))
                self.assertEqual(case["status"], "ok")
                self.assertEqual(case["trace"]["references"][0]["document_id"], "travel-2026")
                self.assertEqual(server.seen["path"], "/v1/chat-messages")
                self.assertEqual(server.seen["authorization"], "Bearer test-secret")
                self.assertEqual(server.seen["body"]["inputs"], {"kite_role": "employee"})
                self.assertNotIn("test-secret", (out / "manifest.json").read_text(encoding="utf-8"))
                checks = quality.deterministic(case)
                self.assertEqual(checks["reference_recall"], 1)
                self.assertEqual(safety.exposure(row, case["trace"])[0]["status"], "none_observed")
        finally:
            del os.environ["KITE_DIFY_TEST_KEY"]
            server.shutdown()
            server.server_close()

    def test_missing_retrieval_metadata_stays_unknown(self):
        payload = b'data: {"event":"message","answer":"hello"}\n\ndata: {"event":"message_end","metadata":{}}\n\n'
        answer, metadata, events = dify.decode(BytesIO(payload), "streaming")
        self.assertEqual(answer, "hello")
        self.assertEqual(len(events), 2)
        trace = {"references": [], "events": [{"stage": "references", "visibility": "unobservable"}]}
        self.assertEqual(safety.exposure({"role": "guest", "forbidden_doc_ids": []}, trace)[0]["status"], "unknown")
        checks = quality.deterministic({"scenario": {"allowed_doc_ids": ["travel-2026"], "as_of": "2026-10-30", "required_facts": []},
                                        "trace": {**trace, "answer": "hello"}})
        self.assertIsNone(checks["reference_recall"])

    def test_agent_stream_closing_message_does_not_duplicate_answer(self):
        payload = (b'data: {"event":"agent_message","answer":"hello"}\n\n'
                   b'data: {"event":"message","answer":"hello"}\n\n'
                   b'data: {"event":"message_end","metadata":{}}\n\n')
        self.assertEqual(dify.decode(BytesIO(payload), "streaming")[0], "hello")


if __name__ == "__main__":
    unittest.main()
