from tempfile import TemporaryDirectory
from pathlib import Path
import unittest
from unittest.mock import patch

from kite import policy


class PolicyTest(unittest.TestCase):
    def test_block_mixed_index_before_query_and_allow_scoped_index(self):
        with TemporaryDirectory() as folder, patch("kite.lightrag.api") as api, patch("kite.lightrag.query_trace") as query:
            audit = Path(folder) / "audit.jsonl"
            api.return_value = {"documents": [{"status": "processed", "file_path": "quote-acme-2026.txt"}],
                                "pagination": {"has_next": False}}
            blocked = policy.guarded_query("报价是多少？", "employee", "baseline", "http://localhost", audit)
            self.assertEqual(blocked["decision"]["action"], "block")
            query.assert_not_called()

            api.return_value["documents"][0]["file_path"] = "public-async-feedback-2026.txt"
            query.return_value = {"answer": "公开流程", "references": [{"document_id": "public-async-feedback-2026"}]}
            with patch("kite.quality.credentials", return_value=("test", "http://localhost", "test")), \
                 patch("kite.safety.disclosure", return_value={"status": "none_observed"}):
                allowed = policy.guarded_query("流程？", "employee", "public", "http://localhost", audit)
            self.assertEqual(allowed["decision"]["action"], "allow")
            self.assertEqual(allowed["answer"], "公开流程")
            with patch("kite.quality.credentials", return_value=("test", "http://localhost", "test")), \
                 patch("kite.safety.disclosure", side_effect=RuntimeError("judge down")):
                held = policy.guarded_query("流程？", "employee", "public", "http://localhost", audit)
            self.assertEqual(held["decision"]["action"], "review")
            self.assertNotIn("公开流程", held["answer"])
            self.assertEqual(len(audit.read_text(encoding="utf-8").splitlines()), 5)
            self.assertEqual(len(list((audit.parent / "policy-traces").glob("*.json"))), 2)

    def test_tool_arguments_require_exact_allowlist_match(self):
        rules = {"employee": {"lookup": [{"target": "staff"}]}}
        self.assertEqual(policy.check_tool("employee", "lookup", {"target": "staff"}, rules)["action"], "allow")
        self.assertEqual(policy.check_tool("employee", "lookup", {"target": "quotes"}, rules)["action"], "review")
        self.assertEqual(policy.check_tool("guest", "lookup", {"target": "staff"}, rules)["action"], "block")


if __name__ == "__main__":
    unittest.main()
