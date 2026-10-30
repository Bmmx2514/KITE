from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from kite import quality


class QualityTest(unittest.TestCase):
    def test_versions_and_evidence_are_separate(self):
        case = {
            "scenario": {
                "id": "travel", "category": "answer", "expected": "answer", "as_of": "2026-10-30",
                "allowed_doc_ids": ["travel-2026"], "required_facts": ["150 元"],
            },
            "trace": {
                "answer": "市内交通上限为 150元。",
                "references": [
                    {"document_id": "travel-2026", "file_path": "travel-2026.txt", "valid_from": "2026-01-01", "valid_to": None},
                    {"document_id": "travel-2025", "file_path": "travel-2025.txt", "valid_from": "2025-01-01", "valid_to": "2025-12-31"},
                ],
            },
            "status": "ok",
        }
        checks = quality.deterministic(case)
        self.assertEqual(checks["reference_recall"], 1)
        self.assertEqual(checks["reference_precision"], 0.5)
        self.assertEqual(checks["out_of_date_references"], ["travel-2025"])
        self.assertEqual(checks["missing_required_facts"], [])
        with TemporaryDirectory() as folder:
            source, out = Path(folder) / "run", Path(folder) / "score"
            (source / "cases").mkdir(parents=True)
            quality.write_json(source / "manifest.json", {"suite": {"sha256": "test"}})
            quality.write_json(source / "cases/0001.json", case)
            result = quality.score_run(source, out, offline=True)
            self.assertEqual(result["counts"]["scored"], 1)
            self.assertEqual(len((out / "review.jsonl").read_text(encoding="utf-8").splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
