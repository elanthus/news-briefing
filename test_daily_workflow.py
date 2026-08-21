from __future__ import annotations

import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")


class DailyWorkflowTests(unittest.TestCase):
    def test_can_only_be_dispatched_manually(self) -> None:
        triggers = WORKFLOW.split("permissions:", 1)[0]
        self.assertIn("workflow_dispatch:", triggers)
        for automatic_trigger in ("schedule:", "push:", "pull_request:"):
            self.assertNotIn(automatic_trigger, triggers)

    def test_backfills_seven_eastern_24_hour_windows(self) -> None:
        self.assertIn("anchor_date=$(TZ=America/New_York date +%F)", WORKFLOW)
        self.assertIn("for days_ago in 6 5 4 3 2 1 0; do", WORKFLOW)
        self.assertIn("TZ=America/New_York", WORKFLOW)
        self.assertIn("--hours 24", WORKFLOW)
        self.assertIn('--window-end "$window_end"', WORKFLOW)

    def test_preserves_dated_corpora_and_replaces_reports(self) -> None:
        self.assertIn('corpus="corpora/$report_date.json"', WORKFLOW)
        self.assertIn('run_dir="runs/$report_date"', WORKFLOW)
        self.assertIn('report="reports/$report_date.md"', WORKFLOW)
        self.assertIn("--force", WORKFLOW)
        self.assertIn("--replace-existing", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
