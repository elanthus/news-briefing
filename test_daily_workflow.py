from __future__ import annotations

import unittest
from pathlib import Path

WORKFLOW = Path(".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")


class DailyWorkflowTests(unittest.TestCase):
    def test_supports_daily_schedule_and_manual_dispatch_only(self) -> None:
        triggers = WORKFLOW.split("permissions:", 1)[0]
        self.assertIn('cron: "30 13 * * *"', triggers)
        self.assertIn("workflow_dispatch:", triggers)
        for automatic_trigger in ("push:", "pull_request:"):
            self.assertNotIn(automatic_trigger, triggers)

    def test_scheduled_run_handles_one_completed_eastern_day(self) -> None:
        self.assertIn(
            'latest_completed_date=$(TZ=America/New_York date --date="yesterday" +%F)',
            WORKFLOW,
        )
        self.assertIn('if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]; then', WORKFLOW)
        self.assertEqual(
            WORKFLOW.count('if [[ "$GITHUB_EVENT_NAME" == "workflow_dispatch" ]]; then'),
            2,
        )
        self.assertIn("day_offsets=(0)", WORKFLOW)

    def test_manual_run_backfills_seven_completed_eastern_days(self) -> None:
        self.assertIn("day_offsets=(6 5 4 3 2 1 0)", WORKFLOW)
        self.assertIn('for days_ago in "${day_offsets[@]}"; do', WORKFLOW)
        self.assertIn('--date="$latest_completed_date $days_ago days ago" +%F', WORKFLOW)
        self.assertIn("TZ=America/New_York", WORKFLOW)
        self.assertIn('--date="$report_date 00:00:00"', WORKFLOW)
        self.assertIn('--date="$report_date 1 day" +%F', WORKFLOW)
        self.assertIn('--date="$next_date 00:00:00"', WORKFLOW)
        self.assertIn('--window-start "$window_start"', WORKFLOW)
        self.assertIn('--window-end "$window_end"', WORKFLOW)
        self.assertIn('--report-date "$report_date"', WORKFLOW)
        self.assertNotIn("--hours 24", WORKFLOW)

    def test_preserves_dated_corpora_and_replaces_reports(self) -> None:
        self.assertIn("ref: main", WORKFLOW)
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
