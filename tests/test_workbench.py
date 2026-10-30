from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kite.workbench import Handler
from kite.__main__ import write_json


class WorkbenchTest(unittest.TestCase):
    def test_data_requires_token_and_invalid_bundle_is_rejected(self):
        previous = Handler.token
        Handler.token = "test-token-with-at-least-24-chars"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            with self.assertRaises(HTTPError) as denied:
                urlopen(base + "/api/scenarios")
            self.assertEqual(denied.exception.code, 401)
            headers = {"Authorization": "Bearer test-token-with-at-least-24-chars"}
            with urlopen(Request(base + "/api/scenarios", headers=headers)) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b"travel-employee", response.read())
            with self.assertRaises(HTTPError) as invalid:
                urlopen(Request(base + "/api/bundle?name=..", headers=headers))
            self.assertEqual(invalid.exception.code, 400)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
            Handler.token = previous

    def test_review_records_operator_and_verdict(self):
        with TemporaryDirectory() as folder, patch("kite.workbench.RUNS", Path(folder)):
            bundle = Path(folder) / "run-one"
            bundle.mkdir()
            write_json(bundle / "summary.json", {"cases": {"case": [{"status": "fail"}]}})
            old_token, old_operator = Handler.token, Handler.operator
            Handler.token, Handler.operator = "test-token-with-at-least-24-chars", "reviewer"
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_port}"
            headers = {"Authorization": "Bearer test-token-with-at-least-24-chars", "Content-Type": "application/json"}
            try:
                payload = json.dumps({"bundle": "run-one", "scenario_id": "case",
                                      "verdict": "confirmed", "note": "Checked trace"}).encode()
                with urlopen(Request(base + "/api/review", data=payload, headers=headers, method="POST")) as response:
                    self.assertEqual(response.status, 200)
                with urlopen(Request(base + "/api/reviews?bundle=run-one", headers=headers)) as response:
                    item = json.load(response)[0]
                self.assertEqual(item["decision"]["operator"], "reviewer")
                self.assertEqual(item["decision"]["verdict"], "confirmed")
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                Handler.token, Handler.operator = old_token, old_operator


if __name__ == "__main__":
    unittest.main()
