from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_site import build_site


class BuildSiteTests(unittest.TestCase):
    def test_renders_latest_review_preview_on_index_with_detailed_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            output = root / "site"
            briefings.mkdir()
            (briefings / "2026-08-19.md").write_text("prior ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-19", disposition="ready")
            (briefings / "2026-08-20.md").write_text(
                'LATEST PREVIEW\n<script>alert("preview")</script>', encoding="utf-8"
            )
            findings = [
                self._finding(
                    "unsupported_figure",
                    "The item states <60>, which the cited excerpt does not support.",
                ),
                self._finding(
                    "claim_exceeds_evidence",
                    "The summary is longer than its evidence.",
                ),
            ]
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=findings,
                degraded_sources=["reddit:cursor"],
            )

            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn('<strong aria-current="date">2026-08-20</strong>', index)
            self.assertIn('href="2026-08-19.html"', index)
            self.assertIn("LATEST PREVIEW", index)
            self.assertFalse((output / "2026-08-20.html").exists())
            self.assertIn("Review required before relying on this briefing", index)
            self.assertIn("WARN · evidence · unsupported figure", index)
            self.assertIn("The item states &lt;60&gt;", index)
            self.assertIn("Verify the figure against the cited source", index)
            self.assertLess(index.index("review-panel"), index.index("LATEST PREVIEW"))
            self.assertNotIn("<script>", index)
            self.assertIn("&lt;script&gt;alert(&quot;preview&quot;)&lt;/script&gt;", index)

            prior = (output / "2026-08-19.html").read_text(encoding="utf-8")
            self.assertIn('href="index.html">2026-08-20</a>', prior)
            self.assertIn("prior ready briefing", prior)

    def test_status_only_run_does_not_expose_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            output = root / "site"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("REJECTED CONTENT", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="rejected",
                findings_count=1,
            )
            output.mkdir()
            (output / "2026-08-20.html").write_text("STALE CONTENT", encoding="utf-8")

            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("REJECTED · 1 finding", index)
            self.assertIn("No briefing prose is available", index)
            self.assertNotIn("REJECTED CONTENT", index)
            self.assertFalse((output / "2026-08-20.html").exists())
            self.assertNotIn("REJECTED CONTENT", (output / "history.json").read_text())

    def test_page_bearing_entry_requires_matching_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[self._finding("unsupported_figure", "Verify 60.")],
            )

            with self.assertRaisesRegex(ValueError, "requires matching Markdown"):
                build_site(briefings, root / "site")

    def test_discards_entries_outside_latest_seven_utc_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            for day in ("2026-08-13", "2026-08-14", "2026-08-20"):
                (briefings / f"{day}.md").write_text(f"briefing {day}", encoding="utf-8")
                self._write_sidecar(briefings, date=day, disposition="ready")

            output = root / "site"
            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("2026-08-14", index)
            self.assertNotIn("2026-08-13", index)
            self.assertTrue((output / "2026-08-14.html").is_file())
            self.assertFalse((output / "2026-08-13.html").exists())
            history = json.loads((output / "history.json").read_text())
            self.assertEqual(
                [entry["date"] for entry in history["entries"]],
                ["2026-08-20", "2026-08-14"],
            )

    def test_merges_bootstrap_and_upgrades_legacy_live_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bootstrap = root / "bootstrap"
            current = root / "current"
            bootstrap.mkdir()
            current.mkdir()
            (bootstrap / "2026-08-18.md").write_text("dogfood preview", encoding="utf-8")
            self._write_sidecar(
                bootstrap,
                date="2026-08-18",
                disposition="review_required",
                findings=[self._finding("unsupported_figure", "Verify the dogfood figure.")],
            )
            legacy = root / "history.json"
            legacy.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "entries": [
                            {
                                "date": "2026-08-19",
                                "disposition": "ready",
                                "findings_count": 0,
                                "degraded_sources": [],
                                "markdown": "legacy live briefing",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (current / "2026-08-20.md").write_text("current briefing", encoding="utf-8")
            self._write_sidecar(current, date="2026-08-20", disposition="ready")

            output = root / "site"
            build_site(current, output, prior_history=legacy, bootstrap_dir=bootstrap)

            self.assertIn("current briefing", (output / "index.html").read_text())
            self.assertIn("legacy live briefing", (output / "2026-08-19.html").read_text())
            self.assertIn("dogfood preview", (output / "2026-08-18.html").read_text())
            history = json.loads((output / "history.json").read_text())
            self.assertEqual(history["schema_version"], 2)
            self.assertEqual(len(history["entries"]), 3)

    def test_lower_rank_same_day_retry_preserves_prior_public_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            initial.mkdir()
            (initial / "2026-08-20.md").write_text("validated briefing", encoding="utf-8")
            self._write_sidecar(initial, date="2026-08-20", disposition="ready")
            initial_site = root / "initial-site"
            build_site(initial, initial_site)

            retry = root / "retry"
            retry.mkdir()
            (retry / "2026-08-20.md").write_text("review preview", encoding="utf-8")
            self._write_sidecar(
                retry,
                date="2026-08-20",
                disposition="review_required",
                findings=[self._finding("unsupported_figure", "Verify retry figure.")],
            )
            retry_site = root / "retry-site"
            build_site(retry, retry_site, prior_history=initial_site / "history.json")

            index = (retry_site / "index.html").read_text(encoding="utf-8")
            self.assertIn("validated briefing", index)
            self.assertNotIn("review preview", index)

    @staticmethod
    def _finding(check: str, message: str) -> dict[str, str]:
        return {"level": "WARN", "check": check, "domain": "evidence", "message": message}

    @staticmethod
    def _write_sidecar(
        directory: Path,
        *,
        date: str,
        disposition: str,
        findings: list[dict[str, str]] | None = None,
        findings_count: int | None = None,
        degraded_sources: list[str] | None = None,
    ) -> None:
        details = findings or []
        count = len(details) if findings_count is None else findings_count
        (directory / f"{date}.json").write_text(
            json.dumps(
                {
                    "date": date,
                    "disposition": disposition,
                    "findings_count": count,
                    "findings": details,
                    "degraded_sources": degraded_sources or [],
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
