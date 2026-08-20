from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from bootstrap_history import bootstrap_history

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


if __name__ == "__main__":
    unittest.main()
