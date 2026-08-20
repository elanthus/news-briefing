from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from bootstrap_history import DogfoodRun, bootstrap_history

ROOT = Path(__file__).resolve().parent


class BootstrapHistoryTests(unittest.TestCase):
    def test_seeds_verifiable_dogfood_runs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            history = Path(directory)

            bootstrap_history(ROOT, history)

            self.assertEqual(
                (history / "2026-08-17.md").read_bytes(),
                (ROOT / "docs/runs/2026-08-17/final.md").read_bytes(),
            )
            self.assertEqual(
                (history / "2026-08-18.md").read_bytes(),
                (ROOT / "docs/runs/2026-08-18/replay-deepseek-v4-flash/preview.md").read_bytes(),
            )
            legacy = json.loads((history / "2026-08-17.json").read_text())
            modern = json.loads((history / "2026-08-18.json").read_text())
            self.assertEqual(legacy["disposition"], "review_required")
            self.assertEqual(legacy["findings_count"], 2)
            self.assertEqual(legacy["findings"][0]["domain"], "editorial")
            self.assertEqual(modern["disposition"], "review_required")
            self.assertEqual(modern["findings_count"], 2)
            self.assertEqual(modern["findings"][0]["domain"], "evidence")

    def test_future_rejected_seed_stays_status_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "dogfood"
            run.mkdir()
            content = b"rejected dogfood preview"
            (run / "preview.md").write_bytes(content)
            (run / "manifest.json").write_text(
                json.dumps(
                    {
                        "status": "complete",
                        "artifacts": {"preview.md": hashlib.sha256(content).hexdigest()},
                        "final": {
                            "status": "rejected",
                            "run_artifact": "preview.md",
                            "findings": [
                                {
                                    "level": "ERROR",
                                    "check": "ungrounded_link",
                                    "domain": "evidence",
                                    "message": "Rejected link.",
                                }
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )
            (run / "corpus.json").write_text('{"errors": []}', encoding="utf-8")
            configured = (
                DogfoodRun(
                    day=date(2026, 8, 20),
                    directory="dogfood",
                    artifact="preview.md",
                    corpus="corpus.json",
                ),
            )
            history = root / "history"

            with patch("bootstrap_history.DOGFOOD_RUNS", configured):
                bootstrap_history(root, history)

            sidecar = json.loads((history / "2026-08-20.json").read_text())
            self.assertEqual(sidecar["disposition"], "rejected")
            self.assertEqual(sidecar["findings_count"], 1)
            self.assertEqual(sidecar["findings"], [])
            self.assertFalse((history / "2026-08-20.md").exists())


if __name__ == "__main__":
    unittest.main()
