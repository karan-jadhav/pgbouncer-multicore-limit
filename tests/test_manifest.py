from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments" / "runner"))
sys.path.insert(0, str(ROOT / "analysis"))

from manifest import hash_tree  # noqa: E402
from validation import validate_loadgen  # noqa: E402
from validate_runs import validate_run  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_loadgen_validation_accepts_complete_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "loadgen.json"
            path.write_text(
                json.dumps(
                    {
                        "mode": "api",
                        "counters": {
                            "scheduled": 100,
                            "completed": 100,
                            "failed": 0,
                            "timed_out": 0,
                            "skipped": 0,
                        },
                    }
                )
            )
            self.assertTrue(validate_loadgen(path).accepted)

    def test_loadgen_validation_rejects_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "loadgen.json"
            path.write_text(
                json.dumps(
                    {
                        "mode": "api",
                        "counters": {
                            "scheduled": 10,
                            "completed": 9,
                            "failed": 1,
                            "timed_out": 0,
                            "skipped": 0,
                        },
                    }
                )
            )
            self.assertFalse(validate_loadgen(path).accepted)

    def test_timeouts_are_workload_results_unless_a_rule_rejects_them(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "loadgen.json"
            path.write_text(
                json.dumps(
                    {
                        "mode": "api",
                        "counters": {
                            "scheduled": 100,
                            "completed": 90,
                            "failed": 0,
                            "timed_out": 10,
                            "skipped": 0,
                        },
                    }
                )
            )
            self.assertTrue(validate_loadgen(path).accepted)
            self.assertFalse(validate_loadgen(path, {"maximum_timeouts": 0}).accepted)

    def test_manifest_hash_validation_detects_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run-1"
            raw = run_dir / "raw"
            raw.mkdir(parents=True)
            (raw / "sample.txt").write_text("original")
            manifest = {
                "run_id": "run-1",
                "status": "accepted",
                "validation_status": "accepted",
                "rejection_reason": None,
                "raw_file_sha256": hash_tree(raw),
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(validate_run(run_dir), [])
            (raw / "sample.txt").write_text("changed")
            self.assertIn("raw file hash differs: sample.txt", validate_run(run_dir))


if __name__ == "__main__":
    unittest.main()
