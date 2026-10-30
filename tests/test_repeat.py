from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kite.__main__ import write_json
from kite import repeat


class RepeatTest(unittest.TestCase):
    def test_three_attempt_rate_and_scorer_change(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for number, flags in enumerate(([], ["extra_references"], []), 1):
                attempt = root / "old" / f"attempt-{number}"
                for part in ("run", "quality", "safety"):
                    (attempt / part / "cases").mkdir(parents=True)
                write_json(attempt / "run/manifest.json", {
                    "suite": {"sha256": "suite"}, "knowledge": {}, "runner_sha256": "runner",
                    "agent": {}, "servers": {}})
                write_json(attempt / "run/cases/0001.json", {
                    "status": "ok", "scenario": {"id": "case", "category": "answer", "expected": "answer"},
                    "trace": {"server_response_time": 2}})
                write_json(attempt / "quality/cases/0001.json", {
                    "status": "scored", "scenario_id": "case", "flags": flags})
                write_json(attempt / "safety/cases/0001.json", {
                    "status": "scored", "scenario_id": "case", "judge_errors": [],
                    "returned_reference_access": {"status": "none_observed"},
                    "answer_disclosure": {"status": "not_assessed"}})
                write_json(attempt / "quality/summary.json", {"grader_version": "quality-v1", "judge_model": "judge"})
                write_json(attempt / "safety/summary.json", {"grader_version": "safety-v1"})
            old = repeat.summarize(root / "old")
            self.assertAlmostEqual(old["single_pass_rate"], 2 / 3)
            self.assertEqual(old["all_attempts_pass_rate"], 0)
            self.assertEqual(old["observed_server_seconds"], 6)
            write_json(root / "old/summary.json", old)
            new = {**old, "versions": {**old["versions"], "quality_grader": "quality-v2"}}
            (root / "new").mkdir()
            write_json(root / "new/summary.json", new)
            repeat.compare(root / "old", root / "new", root / "report.md")
            self.assertIn("Scenario or scorer changed; review", (root / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
