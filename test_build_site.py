from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_site import build_site

ROOT = Path(__file__).resolve().parent
REFERENCE_BRIEFING = ROOT / "fixtures" / "briefing-2026-08-09.md"


class BuildSiteTests(unittest.TestCase):
    def test_builds_newest_first_index_and_escapes_ready_briefing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            output = root / "site"
            briefings.mkdir()
            reference = REFERENCE_BRIEFING.read_text(encoding="utf-8")
            (briefings / "2026-08-09.md").write_text(
                reference + '\n<script>alert("feed")</script>\n',
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-09",
                disposition="ready",
                findings_count=0,
                degraded_sources=["rss:Example & Wire"],
            )
            self._write_sidecar(
                briefings,
                date="2026-08-10",
                disposition="review_required",
                findings_count=2,
                degraded_sources=["rss:Unavailable"],
            )

            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertLess(index.index("2026-08-10"), index.index("2026-08-09"))
            self.assertIn('href="2026-08-09.html"', index)
            self.assertNotIn('href="2026-08-10.html"', index)
            self.assertIn("REVIEW REQUIRED · 2 findings", index)
            self.assertIn("Degraded sources: rss:Example &amp; Wire", index)

            page = (output / "2026-08-09.html").read_text(encoding="utf-8")
            self.assertIn("Daily Briefing — August 9, 2026", page)
            self.assertNotIn("<script>", page)
            self.assertIn("&lt;script&gt;alert(&quot;feed&quot;)&lt;/script&gt;", page)
            self.assertFalse((output / "2026-08-10.html").exists())

    def test_never_publishes_non_ready_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-11.md").write_text(
                "QUARANTINED CONTENT",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-11",
                disposition="rejected",
                findings_count=1,
                degraded_sources=[],
            )

            output = root / "site"
            output.mkdir()
            (output / "2026-08-11.html").write_text(
                "STALE QUARANTINED CONTENT",
                encoding="utf-8",
            )
            build_site(briefings, output)

            self.assertFalse((output / "2026-08-11.html").exists())
            self.assertNotIn(
                "QUARANTINED CONTENT",
                (output / "index.html").read_text(encoding="utf-8"),
            )
            self.assertNotIn(
                "QUARANTINED CONTENT",
                (output / "history.json").read_text(encoding="utf-8"),
            )

    def test_ready_entry_requires_matching_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            self._write_sidecar(
                briefings,
                date="2026-08-12",
                disposition="ready",
                findings_count=0,
                degraded_sources=[],
            )

            with self.assertRaisesRegex(ValueError, "matching Markdown"):
                build_site(briefings, root / "site")

    def test_round_trips_prior_live_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            first.mkdir()
            (first / "2026-08-09.md").write_text("safe prior briefing", encoding="utf-8")
            self._write_sidecar(
                first,
                date="2026-08-09",
                disposition="ready",
                findings_count=0,
                degraded_sources=[],
            )
            first_site = root / "first-site"
            build_site(first, first_site)

            current = root / "current"
            current.mkdir()
            self._write_sidecar(
                current,
                date="2026-08-10",
                disposition="blocked",
                findings_count=0,
                degraded_sources=["fetch:all"],
            )
            next_site = root / "next-site"
            build_site(current, next_site, first_site / "history.json")

            index = (next_site / "index.html").read_text(encoding="utf-8")
            self.assertLess(index.index("2026-08-10"), index.index("2026-08-09"))
            self.assertIn("safe prior briefing", (next_site / "2026-08-09.html").read_text())
            history = json.loads((next_site / "history.json").read_text())
            self.assertEqual([entry["date"] for entry in history["entries"]], ["2026-08-10", "2026-08-09"])

    @staticmethod
    def _write_sidecar(
        directory: Path,
        *,
        date: str,
        disposition: str,
        findings_count: int,
        degraded_sources: list[str],
    ) -> None:
        (directory / f"{date}.json").write_text(
            json.dumps(
                {
                    "date": date,
                    "disposition": disposition,
                    "findings_count": findings_count,
                    "degraded_sources": degraded_sources,
                }
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
