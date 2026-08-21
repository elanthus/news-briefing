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

    def test_backfills_seven_adjacent_eastern_calendar_windows(self) -> None:
        self.assertIn("anchor_date=$(TZ=America/New_York date +%F)", WORKFLOW)
        self.assertIn("for days_ago in 6 5 4 3 2 1 0; do", WORKFLOW)
        self.assertIn("TZ=America/New_York", WORKFLOW)
        self.assertIn('--date="$report_date 00:00:00"', WORKFLOW)
        self.assertIn('--date="$report_date 1 day" +%F', WORKFLOW)
        self.assertIn('--date="$next_date 00:00:00"', WORKFLOW)
        self.assertIn('--window-start "$window_start"', WORKFLOW)
        self.assertIn('--window-end "$window_end"', WORKFLOW)
        self.assertIn('--report-date "$report_date"', WORKFLOW)
        self.assertNotIn("--hours 24", WORKFLOW)

    def test_preserves_dated_corpora_and_replaces_reports(self) -> None:
        self.assertIn('corpus="corpora/$report_date.json"', WORKFLOW)
        self.assertIn('run_dir="runs/$report_date"', WORKFLOW)
        self.assertIn('report="reports/$report_date.md"', WORKFLOW)
        self.assertIn("--force", WORKFLOW)
        self.assertIn("--replace-existing", WORKFLOW)

    def test_publication_preparation_failure_does_not_abort_remaining_dates(self) -> None:
        self.assertIn("if ! python prepare_publication.py", WORKFLOW)
        self.assertIn("Publication preparation failed for $report_date", WORKFLOW)


if __name__ == "__main__":
    unittest.main()
