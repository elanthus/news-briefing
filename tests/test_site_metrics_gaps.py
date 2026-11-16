"""Dates with no ledger row count as not ready on the metrics page."""

from __future__ import annotations

import html
import re
import unittest
from datetime import date
from typing import Any

import site_metrics


def _row(day: str, disposition: str = "ready") -> dict[str, Any]:
    return site_metrics.metrics_row(
        day=date.fromisoformat(day),
        disposition=disposition,
        provenance=None,
        semantic_audit=None,
        integrity=None,
        generation_failures=0,
        findings_count=0,
        advisory_findings=0,
    )


def _text(page: str) -> str:
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page)))


class UnrecordedDatesTests(unittest.TestCase):
    def test_no_gap_in_a_contiguous_ledger(self) -> None:
        rows = [_row("2026-11-09"), _row("2026-11-10")]
        self.assertEqual(site_metrics.unrecorded_dates(rows), [])
        self.assertEqual(site_metrics.unrecorded_dates([]), [])

    def test_lists_every_missing_date_inside_the_span(self) -> None:
        rows = [_row("2026-10-30"), _row("2026-11-02"), _row("2026-11-03")]
        self.assertEqual(
            site_metrics.unrecorded_dates(rows), ["2026-10-31", "2026-11-01"]
        )

    def test_span_ends_at_the_last_recorded_date(self) -> None:
        # Nothing after the newest row is reported; that run may still be pending.
        self.assertEqual(site_metrics.unrecorded_dates([_row("2026-11-10")]), [])


class RollingReadyRateTests(unittest.TestCase):
    def test_a_missing_day_counts_as_not_ready(self) -> None:
        rows = [_row("2026-11-10"), _row("2026-11-12")]
        self.assertEqual(
            site_metrics.rolling_ready_rate(rows),
            [("2026-11-10", 1, 1), ("2026-11-12", 2, 3)],
        )

    def test_window_does_not_reach_before_the_first_recorded_date(self) -> None:
        rows = [_row("2026-11-01"), _row("2026-11-02", "blocked")]
        self.assertEqual(
            site_metrics.rolling_ready_rate(rows, window=28),
            [("2026-11-01", 1, 1), ("2026-11-02", 1, 2)],
        )

    def test_window_drops_days_older_than_its_length(self) -> None:
        rows = [_row("2026-11-01"), _row("2026-11-02"), _row("2026-11-05")]
        self.assertEqual(
            site_metrics.rolling_ready_rate(rows, window=3)[-1], ("2026-11-05", 1, 3)
        )


class MetricsPageGapTests(unittest.TestCase):
    def test_missing_day_lowers_the_headline_rate_and_is_named(self) -> None:
        rows = [_row("2026-11-10"), _row("2026-11-12"), _row("2026-11-13")]
        page = site_metrics.render_metrics_body(rows)
        text = _text(page)
        self.assertIn(
            "3 of 4 report dates were published ready (75.0%), 2026-11-10 to 2026-11-13.",
            text,
        )
        self.assertIn(
            "Dates with no record, counted as not ready: 1 (2026-11-11).", text
        )
        self.assertIn('class="chart-bar disposition-no-record"', page)
        self.assertIn("no record: 1 of 4", text)
        self.assertIn("Latest: 3 of 4 (75.0%).", text)

    def test_contiguous_ledger_shows_no_gap_note(self) -> None:
        page = site_metrics.render_metrics_body([_row("2026-11-10"), _row("2026-11-11")])
        text = _text(page)
        self.assertIn("2 of 2 report dates were published ready (100.0%)", text)
        self.assertNotIn("Dates with no record", text)
        self.assertNotIn("disposition-no-record", page)

    def test_recorded_day_sections_keep_counting_recorded_days(self) -> None:
        page = site_metrics.render_metrics_body([_row("2026-11-10"), _row("2026-11-12")])
        self.assertIn("0 of 2 recorded days had a complete initial Jev review.", _text(page))


if __name__ == "__main__":
    unittest.main()
