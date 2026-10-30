import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
import unittest
from unittest.mock import patch

from kite import lightrag


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        assert self.path == "/query"
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        assert request["include_chunk_content"] and request["enable_rerank"]
        body = json.dumps({
            "response": "上限是 150 元",
            "references": [
                {"reference_id": "1", "file_path": "travel-2026.txt", "content": ["150 元"]},
                {"reference_id": "2", "file_path": "unknown.txt", "content": ["other"]},
            ],
            "llm_generated": True,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


class LightRAGTest(unittest.TestCase):
    def test_query_trace(self):
        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            worker = Thread(target=server.handle_request)
            worker.start()
            with patch.object(lightrag, "BASE_URL", f"http://127.0.0.1:{server.server_port}"):
                raw = lightrag.api("POST", "/query", {
                    "query": "报销上限是多少？", "include_chunk_content": True, "enable_rerank": True
                })
            worker.join(timeout=2)
        result = lightrag.trace("报销上限是多少？", "employee", "baseline", raw, lightrag.now(), {"enable_rerank": True})
        self.assertEqual(result["references"][0]["document_id"], "travel-2026")
        self.assertIsNone(result["references"][1]["document_id"])
        self.assertEqual(result["events"][-1]["visibility"], "unobservable")


if __name__ == "__main__":
    unittest.main()
