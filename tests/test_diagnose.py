from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kite.__main__ import write_json
from kite import diagnose


class DiagnoseTest(unittest.TestCase):
    def test_holdout_and_safety_gate(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            versions = {"suite_sha256": "suite", "quality_grader": "v1", "agent": "base"}
            def bundle(name, flags, safety, agent="base"):
                path = root / name
                path.mkdir()
                write_json(path / "summary.json", {
                    "attempts": 2, "versions": {**versions, "agent": agent},
                    "observed_server_seconds": 2,
                    "cases": {"case": [{"status": "fail" if flags or safety else "pass",
                                        "quality_flags": flags, "safety_flags": safety,
                                        "trace": "trace.json", "expected_behavior": "answer"}] * 2}})
                return path
            base_dev = bundle("base-dev", [], [])
            base_holdout = bundle("base-holdout", ["extra_references"], [])
            candidate_dev = bundle("candidate-dev", [], [], "candidate")
            candidate_holdout = bundle("candidate-holdout", [], [], "candidate")
            result = diagnose.gate(base_dev, base_holdout, candidate_dev, candidate_holdout, "retrieval_filter")
            self.assertEqual(result["verdict"], "adoptable")
            candidate_holdout = bundle("candidate-unsafe", [], ["unauthorized_reference"], "candidate")
            self.assertEqual(diagnose.gate(base_dev, base_holdout, candidate_dev, candidate_holdout,
                                           "retrieval_filter")["verdict"], "reject")


if __name__ == "__main__":
    unittest.main()
