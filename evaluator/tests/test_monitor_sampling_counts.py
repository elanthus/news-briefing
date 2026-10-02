"""Retained artifact scans must not change the requested week's skip denominator."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evaluator.adapters import Adapter, Generation
from evaluator.production_grounding import ProductionRun, run_weekly_monitor


class UnusedJudge(Adapter):
    provider = "offline"

    def generate(self, prompt: str) -> Generation:
        raise AssertionError("sampling-count regressions must not call a provider")


def verified_topic(run: ProductionRun) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    day = run.report_date or run.run_dir.name
    return [{
        "artifact_dir": run.run_id, "topic_index": 1, "stratum": "News", "private": {},
        "public": {"section": "News", "title": "Supported topic", "prose": "Supported prose.",
                   "evidence": [{"title": "Supported topic", "summary": "Supported prose."}]},
    }], {"report_date": day, "deployment_completed_at": f"{day}T12:00:00+00:00",
         "workflow_run_id": 123, "run_attempt": 1, "artifact_hashes": {}}


class MonitorSamplingCountTests(unittest.TestCase):
    def test_complete_week_has_no_skips_despite_other_retained_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            monday = date(2026, 8, 31)
            runs = [ProductionRun(f"week-{offset}", root / (monday + timedelta(days=offset)).isoformat())
                    for offset in range(7)]
            runs.extend([ProductionRun("previous-week", root / "2026-08-30"),
                         ProductionRun("following-week", root / "2026-09-07")])
            artifacts = [{"run_id": "briefing-diagnostics-1-1", "reason": "missing_receipt"},
                         {"run_id": "briefing-diagnostics-2", "reason": "legacy_diagnostics"}]
            reviewed = {"primary": {"grounding_errors": {"successes": 0, "trials": 7}},
                        "observed_cost_usd": 0, "audit": {"agreement_with_primary": None}}
            with patch("evaluator.production_grounding.verified_run_topics", side_effect=verified_topic), patch(
                "evaluator.production_grounding.run_grounding_machine_review", return_value=reviewed
            ):
                result = run_weekly_monitor(
                    runs, week_label="2026-W36", packet_dir=root / "packets",
                    review_output_dir=root / "review", log_path=root / "log.md",
                    primary_judge=UnusedJudge("primary"), audit_judge=UnusedJudge("audit"),
                    artifact_exclusions=artifacts,
                )
            self.assertEqual(result["runs_reviewed"], 7)
            self.assertEqual(result["runs_skipped"], [])
            self.assertIn("| 2026-W36 | 7 | 0 |", (root / "log.md").read_text())
            self.assertEqual(len(result["out_of_window_exclusions"]), 2)
            self.assertEqual(result["artifact_exclusions"], artifacts)
            sampling = json.loads((root / "packets/sampling.json").read_text())
            self.assertEqual(sampling["exclusions"], result["exclusions"])
            self.assertEqual(len(sampling["exclusions"]), 4)
            self.assertEqual(sampling["runs_skipped"], [])
            self.assertIn("not distinct report dates", sampling["skipped_count_basis"])

    def test_only_in_week_invalid_candidates_and_missing_dates_count_as_skips(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runs = [ProductionRun("invalid-week", root / "2026-09-01"),
                    ProductionRun("invalid-outside", root / "2026-09-08"),
                    ProductionRun("unknown-date", root / "undated")]
            artifacts = [{"run_id": "briefing-diagnostics-3-1", "reason": "missing_receipt"}]
            with patch("evaluator.production_grounding.verified_run_topics", side_effect=ValueError("invalid proof")):
                result = run_weekly_monitor(
                    runs, week_label="2026-W36", packet_dir=root / "packets",
                    review_output_dir=root / "review", log_path=root / "log.md",
                    primary_judge=UnusedJudge("primary"), audit_judge=UnusedJudge("audit"),
                    artifact_exclusions=artifacts,
                )
            missing = {(date(2026, 8, 31) + timedelta(days=offset)).isoformat() for offset in range(7)}
            self.assertEqual(set(result["runs_skipped"]), missing | {"invalid-week"})
            self.assertIn("| 2026-W36 | 0 | 8 |", (root / "log.md").read_text())
            self.assertEqual(result["out_of_window_exclusions"], [
                {"run_id": "invalid-outside", "reason": "invalid proof"}])
            self.assertEqual(result["undated_exclusions"], [
                {"run_id": "unknown-date", "reason": "invalid proof"}])
            self.assertEqual(result["artifact_exclusions"], artifacts)
            sampling = json.loads((root / "packets/sampling.json").read_text())
            self.assertEqual(len(sampling["exclusions"]), 11)
            self.assertEqual(sampling["weekly_exclusions"], result["weekly_exclusions"])


if __name__ == "__main__":
    unittest.main()
