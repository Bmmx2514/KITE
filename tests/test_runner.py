import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from kite import __main__ as runner


class RunnerTest(unittest.TestCase):
    def test_failure_does_not_stop_next_case(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            agent = root / "agent.json"
            suite = root / "suite.jsonl"
            out = root / "run"
            agent.write_text(json.dumps({"type": "lightrag", "profiles": {"baseline": "http://127.0.0.1:9621"}}))
            suite.write_text("\n".join(json.dumps({"id": name, "knowledge_profile": "baseline", "query": name, "role": "employee"}) for name in ("first", "second")))
            with patch.object(runner, "server_info", return_value={"core_version": "test"}), patch.object(
                runner.lightrag, "query_trace", side_effect=[RuntimeError("first failed"), {"answer": "second worked"}]
            ):
                manifest = runner.run(suite, agent, out)
            self.assertEqual(manifest["counts"], {"ok": 1, "error": 1})
            self.assertEqual(json.loads((out / "cases/0001.json").read_text())["status"], "error")
            self.assertEqual(json.loads((out / "cases/0002.json").read_text())["trace"]["answer"], "second worked")


if __name__ == "__main__":
    unittest.main()
