"""Opt-in static-site tests; run with ``python -m unittest tests.site_test_build``."""

from __future__ import annotations

import copy
import gzip
import hashlib
import html
import io
import json
import re
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree

import eval_briefing
import site_metrics
from agent_runner.failures import FailureRecord
from agent_runner.integrity import add_phase, blank_integrity
from agent_runner.output import render_briefing, render_validation_status
from build_site import (
    BriefingEntry,
    _count_label,
    _decorate_briefing,
    _humanize_corpus_health,
    _render_markdown,
    _render_report,
)
from build_site import build_site as _build_site
from build_site import main as build_site_main
from tests.test_briefing_output import fixture_contract


def build_site(*args, **kwargs):
    """Call build_site defaulting the empty-history opt-in on.

    Most site tests build from briefings/bootstrap with no prior history; the
    real guard (build_site refuses that unless allow_empty_history is set) is
    exercised directly by test_build_site_refuses_empty_history_without_optin
    and by the CLI's mutually-exclusive group. Tests that pass prior_history
    exercise the ordinary path unchanged. The metrics-ledger opt-in defaults on
    the same way; MetricsLedgerTests exercise its guard and prior-ledger merge.
    """
    if "allow_empty_history" not in kwargs and "prior_history" not in kwargs:
        kwargs["allow_empty_history"] = True
    if "allow_empty_metrics" not in kwargs and "prior_metrics" not in kwargs:
        kwargs["allow_empty_metrics"] = True
    return _build_site(*args, **kwargs)


class BuildSiteTests(unittest.TestCase):
    def test_failed_chain_explanation_survives_history_and_appears_on_public_pages(self) -> None:
        from datetime import date

        from prepare_publication import prepare_publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chain = root / "run"
            chain.mkdir()
            (chain / "fallback-log.json").write_text(json.dumps({
                "schema_version": 2, "status": "failed", "model_chain": ["google/gemini-3.7-flash"],
                "attempts": [{"model": "google/gemini-3.7-flash", "status": "failed",
                              "failure": FailureRecord("invalid_request", status_code=400).payload(),
                              "failure_reason": 'ProviderError: openrouter HTTP 400: <script>private</script>'}],
            }))
            prepare_publication(chain, root / "missing.json", root / "briefings", date(2026, 9, 6))
            build_site(root / "briefings", root / "site")
            for relative in ("index.html", "reports/2026-09-06.html"):
                page = (root / "site" / relative).read_text()
                self.assertIn("Every model in the fallback chain failed", page)
                self.assertIn("Gemini 3.7 Flash", page)
                self.assertIn("rejected the request", page)
                self.assertNotIn("<script>private</script>", page)
            (root / "empty").mkdir()
            build_site(root / "empty", root / "rebuilt", prior_history=root / "site/history.json")
            self.assertIn("rejected the request", (root / "rebuilt/index.html").read_text())

    def test_history_failure_metadata_rejects_unknown_text_and_public_prose(self) -> None:
        from build_site import _entry_from_payload

        base = {"date": "2026-09-06", "disposition": "blocked", "findings_count": 0,
                "findings": [], "degraded_sources": [], "repair_actions": [],
                "generation_failures": [{"model": "tencent/hy3", "reason": "generation_failed"}]}
        for changes in (
            {"disposition": "ready"},
            {"generation_failures": [{"model": "tencent/hy3", "reason": "private"}]},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                _entry_from_payload({**base, **changes}, source="test")

    def test_cli_refuses_to_build_without_prior_history_unless_explicitly_allowed(self) -> None:
        # A prior-history download failure and "there is genuinely no history
        # yet" both leave --prior-history unset. Silently treating them the
        # same would let a CI network blip quietly erase the published
        # archive; --allow-empty-history is the explicit opt-in for the
        # legitimate case.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            output = root / "site"
            with (
                patch.object(sys, "argv", ["build_site.py", str(briefings), str(output)]),
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit),
            ):
                build_site_main()
            self.assertIn("--allow-empty-history", stderr.getvalue())
            self.assertFalse((output / "index.html").exists())

            # The history opt-in does not also restart the metrics ledger.
            with (
                patch.object(sys, "argv", ["build_site.py", str(briefings), str(output),
                                           "--allow-empty-history"]),
                redirect_stderr(io.StringIO()) as stderr,
                self.assertRaises(SystemExit),
            ):
                build_site_main()
            self.assertIn("allow_empty_metrics", stderr.getvalue())
            self.assertFalse((output / "index.html").exists())

            with patch.object(
                sys,
                "argv",
                ["build_site.py", str(briefings), str(output), "--allow-empty-history",
                 "--allow-empty-metrics"],
            ):
                self.assertEqual(build_site_main(), 0)
            self.assertTrue((output / "index.html").exists())

    def test_pages_explain_the_project_and_include_social_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            build_site(briefings, root / "site")

            page = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn('<meta name="description"', page)
            self.assertIn('<meta property="og:title"', page)
            self.assertIn('<meta property="og:description"', page)
            self.assertIn('<meta name="twitter:card" content="summary">', page)
            self.assertIn('class="site-header"', page)
            self.assertIn("https://github.com/elanthus/news-briefing", page)
            self.assertIn('class="site-footer"', page)
            self.assertIn("semantic review assesses frozen excerpts", page)

    def test_build_copies_theme_aware_favicons(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            output = root / "site"

            build_site(briefings, output)

            page = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn(
                '<link rel="icon" href="favicon-light.png" '
                'type="image/png" sizes="512x512">',
                page,
            )
            self.assertIn('media="(prefers-color-scheme: light)"', page)
            self.assertIn('media="(prefers-color-scheme: dark)"', page)
            repository_root = Path(__file__).resolve().parents[1]
            for filename in ("favicon-light.png", "favicon-dark.png"):
                self.assertEqual(
                    (output / filename).read_bytes(),
                    (repository_root / "docs" / "images" / filename).read_bytes(),
                )

    def test_build_site_refuses_empty_history_without_optin(self) -> None:
        # The never-silently-truncate-the-archive invariant lives in build_site
        # itself, so a programmatic caller (not just the CLI) must opt in before
        # building with no prior history.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            with self.assertRaises(ValueError):
                _build_site(briefings, root / "site")
            self.assertFalse((root / "site" / "index.html").exists())
            _build_site(briefings, root / "site", allow_empty_history=True, allow_empty_metrics=True)
            self.assertTrue((root / "site" / "index.html").exists())

    def test_review_required_report_summarizes_findings_without_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            output = root / "site"
            briefings.mkdir()
            (briefings / "2026-08-19.md").write_text("prior ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-19", disposition="ready")
            (briefings / "2026-08-20.md").write_text(
                '## US Politics\n\n'
                '**Important detail** — Story summary [destination omitted; use citation refs] '
                'with [source](https://example.com/story).'
                '\n🔗 https://example.com/citation'
                '\n\n- First item\n\nBare link: https://example.com/bare'
                '\n\n[unsafe](javascript:alert(1))'
                '\n\n<script>alert("preview")</script>'
                '\n',
                encoding="utf-8",
            )
            findings = [
                self._finding(
                    "unsupported_figure",
                    "US Politics: 'Important detail' states '<60>', which the cited excerpt does not support.",
                    context={
                        "section": "US Politics",
                        "headline": "Important detail",
                        "model_authored": (
                            '{"headline":"Important detail",'
                            '"summary":"<script>alert(1)</script> https://model.example/claim"}'
                        ),
                    },
                ),
                self._finding(
                    "claim_exceeds_evidence",
                    "US Politics: 'Important detail' has too much unsupported detail.",
                    context={
                        "section": "US Politics",
                        "headline": "Important detail",
                        "model_authored": "same story",
                    },
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
            self.assertIn('<strong aria-current="date"><time datetime="2026-08-20">', index)
            self.assertIn('href="2026-08-19.html"', index)
            self.assertFalse((output / "2026-08-20.html").exists())
            self.assertIn("did not pass automated checks", index)
            self.assertIn("reports/2026-08-20.html", index)
            self.assertNotIn('<section class="review-story">', index)
            self.assertNotIn("Important detail", index)
            self.assertNotIn("UNPUBLISHED BRIEFING CANDIDATE", index)
            report = (output / "reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('href="../favicon-light.png"', report)
            self.assertIn('href="../favicon-dark.png"', report)
            self.assertIn("Recorded deterministic findings", report)
            self.assertIn("unsupported figure", report)
            self.assertIn("claim exceeds evidence", report)
            self.assertIn("Important detail", report)
            self.assertNotIn('<article class="briefing-content">', report)
            self.assertNotIn("https://model.example/claim", report)
            self.assertNotIn("<script>", report)
            self.assertNotIn('href="javascript:', report)
            history = json.loads((output / "history.json").read_text())
            preview = history['entries'][0]
            self.assertIn('Important detail', preview['markdown'])
            self.assertEqual(preview['findings'], findings)

            prior = (output / "2026-08-19.html").read_text(encoding="utf-8")
            self.assertIn('href="index.html"><time datetime="2026-08-20">', prior)
            self.assertIn('<span class="weekday">Thu</span><span class="monthday">Aug 20</span>', prior)
            self.assertIn("prior ready briefing", prior)

    def test_ordinary_warning_does_not_duplicate_unredacted_story(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## US Politics\n\n**Fuel story** — Sales start September 1.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "unsupported_figure",
                        "US Politics: 'Fuel story' states '1', which is unsupported.",
                        context={
                            "section": "US Politics",
                            "headline": "Fuel story",
                            "model_authored": (
                                '{"headline":"Fuel story",'
                                '"summary":"Sales start September 1."}'
                            ),
                        },
                    )
                ],
            )

            output = root / "site"
            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("did not pass automated checks", index)
            self.assertNotIn('<section class="review-story">', index)
            report = (output / "reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("unsupported figure", report)

    def test_adjacent_flagged_stories_are_counted_without_reprinting_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## US Politics\n\n"
                "**First story** — First summary.\n"
                "🔗 https://example.com/first\n"
                "**Second story** — Second summary.\n"
                "🔗 https://example.com/second\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "unsupported_figure",
                        "US Politics: 'First story' states '1', which is unsupported.",
                    ),
                    self._finding(
                        "unsupported_figure",
                        "US Politics: 'Second story' states '2', which is unsupported.",
                    ),
                ],
            )

            output = root / "site"
            build_site(briefings, output)

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn('<section class="review-story">', index)
            self.assertIn("did not pass automated checks", index)
            report = (output / "reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('<td>unsupported figure</td><td>2</td>', report)
            self.assertNotIn('First summary', report)
            self.assertNotIn('Second summary', report)

    def test_grouped_and_excluded_story_findings_have_summary_counts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## AI/Tech\n\n"
                "**AI News (4 slots)**\n\n"
                "<!-- story: topics.AI News[0] -->\n"
                "**AI story** — Included summary.\n"
                "🔗 https://example.com/ai\n\n"
                "---\n\n"
                "### Excluded Topics (candidate)\n\n"
                "**US News**\n\n"
                "<!-- story: excluded_topics.US News[0] -->\n"
                "- **Excluded story** — Lower impact.\n"
                "🔗 https://example.com/excluded\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "category_ineligible_ref",
                        "topics.AI News[0] uses an ineligible citation",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "included entry",
                            "path": "topics.AI News[0]",
                        },
                    ),
                    self._finding(
                        "duplicate_item",
                        "excluded_topics.US News[0] repeats an item",
                        context={
                            "section": "Excluded Topics: US News",
                            "headline": "Excluded story",
                            "model_authored": "excluded entry",
                            "path": "excluded_topics.US News[0]",
                        },
                    ),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn('<section class="review-story">', index)
            self.assertNotIn('<section class="review-panel">', index)
            self.assertIn("did not pass automated checks", index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('category ineligible ref', report)
            self.assertIn('duplicate item', report)
            self.assertNotIn('Included summary', report)
            self.assertNotIn('Lower impact', report)

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
            self.assertIn("Not published", index)
            self.assertIn("No briefing prose is available", index)
            self.assertNotIn("REJECTED CONTENT", index)
            self.assertFalse((output / "2026-08-20.html").exists())
            self.assertNotIn("REJECTED CONTENT", (output / "history.json").read_text(encoding="utf-8"))

    def test_rejected_sidecar_with_advisory_findings_is_refused(self) -> None:
        # advisory_findings only makes sense on a published entry (ready or
        # review_required); a rejected run has no public record to attach
        # them to, so a non-empty advisory list there is a producer bug.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("REJECTED CONTENT", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="rejected",
                findings_count=1,
                advisory_findings=[
                    self._finding(
                        "slots_underfilled",
                        "World Events: 2 topics, expected 5",
                        domain="quality",
                    ),
                ],
            )

            with self.assertRaisesRegex(ValueError, "advisory findings require a published disposition"):
                build_site(briefings, root / "site")

    def test_current_fixture_shape_and_retired_sidecar_rejection(self) -> None:
        from build_site import SIDECAR_FIELDS

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            markdown = "# Daily Briefing\n\nCurrent fixture.\n"
            (briefings / "2026-08-20.md").write_text(markdown)
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")
            sidecar = briefings / "2026-08-20.json"
            payload = json.loads(sidecar.read_text())
            self.assertEqual(set(payload), SIDECAR_FIELDS)
            self.assertEqual(payload["integrity"]["decision"], "original_retained")
            self.assertEqual(payload["integrity"]["artifacts"][-1], {
                "id": "published", "sha256": hashlib.sha256(markdown.encode()).hexdigest(),
            })
            build_site(briefings, root / "current")
            current = json.loads((root / "current/history.json").read_text())["entries"][0]
            self.assertEqual(current["integrity"], payload["integrity"])
            for field in ("integrity", "semantic_audit"):
                payload.pop(field)
            sidecar.write_text(json.dumps(payload))
            with self.assertRaises(ValueError):
                build_site(briefings, root / "legacy")

    def test_page_bearing_entry_requires_matching_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            markdown = briefings / "2026-08-20.md"
            markdown.write_text("# Test briefing\n")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[self._finding("unsupported_figure", "Verify 60.")],
            )

            markdown.unlink()
            with self.assertRaisesRegex(ValueError, "requires matching Markdown"):
                build_site(briefings, root / "site")

    def test_keeps_date_gaps_when_fewer_than_eight_entries_exist(self) -> None:
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
            self.assertIn("2026-08-13", index)
            self.assertTrue((output / "2026-08-14.html").is_file())
            self.assertTrue((output / "2026-08-13.html").is_file())
            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["date"] for entry in history["entries"]],
                ["2026-08-20", "2026-08-14", "2026-08-13"],
            )

    def test_discards_oldest_entry_only_when_an_eighth_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            for day in range(13, 20):
                date_string = f"2026-08-{day}"
                (briefings / f"{date_string}.md").write_text(
                    f"briefing {date_string}", encoding="utf-8"
                )
                self._write_sidecar(
                    briefings, date=date_string, disposition="ready"
                )

            output = root / "site"
            build_site(briefings, output)
            seven_day_history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(seven_day_history["entries"]), 7)
            self.assertEqual(seven_day_history["entries"][-1]["date"], "2026-08-13")
            self.assertTrue((output / "2026-08-13.html").is_file())

            (briefings / "2026-08-20.md").write_text(
                "briefing 2026-08-20", encoding="utf-8"
            )
            self._write_sidecar(
                briefings, date="2026-08-20", disposition="ready"
            )
            build_site(briefings, output)

            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history["entries"]), 7)
            self.assertEqual(history["entries"][0]["date"], "2026-08-20")
            self.assertEqual(history["entries"][-1]["date"], "2026-08-14")
            self.assertFalse((output / "2026-08-13.html").exists())

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

    def test_replace_existing_uses_lower_rank_backfill_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            initial.mkdir()
            (initial / "2026-08-20.md").write_text("old briefing", encoding="utf-8")
            self._write_sidecar(initial, date="2026-08-20", disposition="ready")
            initial_site = root / "initial-site"
            build_site(initial, initial_site)

            backfill = root / "backfill"
            backfill.mkdir()
            (backfill / "2026-08-20.md").write_text(
                "replacement preview", encoding="utf-8"
            )
            self._write_sidecar(
                backfill,
                date="2026-08-20",
                disposition="review_required",
                findings=[self._finding("unsupported_figure", "Verify replacement figure.")],
            )
            output = root / "site"
            build_site(
                backfill,
                output,
                prior_history=initial_site / "history.json",
                replace_existing=True,
            )

            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("did not pass automated checks", index)
            self.assertNotIn("old briefing", index)
            self.assertNotIn("replacement preview", index)

    def test_replace_existing_preserves_prior_page_when_backfill_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            initial.mkdir()
            (initial / "2026-08-20.md").write_text("live briefing", encoding="utf-8")
            self._write_sidecar(initial, date="2026-08-20", disposition="ready")
            initial_site = root / "initial-site"
            build_site(initial, initial_site)

            failed = root / "failed"
            failed.mkdir()
            self._write_sidecar(failed, date="2026-08-20", disposition="blocked")
            output = root / "site"
            build_site(
                failed,
                output,
                prior_history=initial_site / "history.json",
                replace_existing=True,
            )

            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["entries"][0]["disposition"], "ready")
            self.assertEqual(history["entries"][0]["markdown"], "live briefing")

    def test_legacy_findings_without_anchors_have_summary_counts(self) -> None:
        # Legacy context remains readable without reproducing the preview.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## AI/Tech\n\n"
                "**AI News (4 slots)**\n\n"
                "**AI story** — Included summary.\n"
                "🔗 https://example.com/ai\n\n"
                "---\n\n"
                "### Excluded Topics (candidate)\n\n"
                "**US News**\n\n"
                "- **Excluded story** — Lower impact.\n"
                "🔗 https://example.com/excluded\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "category_ineligible_ref",
                        "topics.AI News[0] uses an ineligible citation",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "included entry",
                        },
                    ),
                    self._finding(
                        "duplicate_item",
                        "excluded_topics.US News[0] repeats an item",
                        context={
                            "section": "Excluded Topics: US News",
                            "headline": "Excluded story",
                            "model_authored": "excluded entry",
                        },
                    ),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn('<section class="review-story">', index)
            self.assertIn("did not pass automated checks", index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('category ineligible ref', report)
            self.assertIn('duplicate item', report)
            self.assertIn('AI story', report)
            self.assertIn('Excluded story', report)

    def test_repair_actions_survive_history_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            first.mkdir()
            (first / "2026-08-19.md").write_text("repaired briefing", encoding="utf-8")
            actions = [
                {"action": "drop_entry", "path": "topics.US News[3]", "reason": "duplicate"},
            ]
            self._write_sidecar(
                first, date="2026-08-19", disposition="ready", repair_actions=actions
            )
            first_site = root / "site-1"
            build_site(first, first_site)
            history = json.loads((first_site / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["schema_version"], 10)
            self.assertEqual(history["entries"][0]["repair_actions"], actions)

            second = root / "second"
            second.mkdir()
            (second / "2026-08-20.md").write_text("next briefing", encoding="utf-8")
            self._write_sidecar(second, date="2026-08-20", disposition="ready")
            second_site = root / "site-2"
            build_site(second, second_site, prior_history=first_site / "history.json")
            rebuilt = json.loads((second_site / "history.json").read_text(encoding="utf-8"))
            by_date = {entry["date"]: entry for entry in rebuilt["entries"]}
            self.assertEqual(by_date["2026-08-19"]["repair_actions"], actions)
            self.assertEqual(by_date["2026-08-20"]["repair_actions"], [])
            # The restored entry's chip and report keep the repair provenance.
            restored_page = (second_site / "2026-08-19.html").read_text(encoding="utf-8")
            self.assertIn("1 automatic correction", restored_page)
            restored_report = (second_site / "reports/2026-08-19.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Automated repair actions (1)", restored_report)
            self.assertIn("topics.US News[3]", restored_report)

    def test_status_chip_contract_checks_passed_for_clean_ready_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("clean briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("status-chip", index)
            self.assertIn('<p class="status-chip status-ok"><a href="reports/2026-08-20.html">'
                          "✓ Passed publication checks · All sources healthy</a></p>", index)
            self.assertIn("reports/2026-08-20.html", index)
            self.assertNotIn("Checker verdict:", index)
            self.assertNotIn("Corpus health:", index)

    def test_status_chip_shows_advisory_note_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("clean briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                advisory_findings=[
                    self._finding(
                        "slots_underfilled",
                        "World Events: 2 topics, expected 5; 3 unused eligible corpus item(s) remain",
                        domain="quality",
                    ),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("⚠ Passed publication checks · 1 advisory note", index)

    def test_status_chip_repair_count_for_repaired_ready_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("repaired briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                repair_actions=[
                    {"action": "drop_entry", "path": "topics.AI[1]", "reason": "dup"},
                    {"action": "drop_ref", "path": "topics.AI[0]", "reason": "ineligible"},
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("⚠ Passed publication checks · 2 automatic corrections", index)

    def test_ready_page_places_linked_status_and_dividers_below_daily_title(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "# Daily Briefing — August 20, 2026\n\n"
                "Corpus window: start → end\n\n"
                "## AI/Tech\n\n**AI story** — Summary.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                repair_actions=[
                    {"action": "drop_entry", "path": "topics.AI[1]", "reason": "dup"},
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn("Briefing for 2026-08-20", index)
            title = index.index("<h1>Daily Briefing — August 20, 2026</h1>")
            first_rule = index.index("<hr>", title)
            status = index.index("1 automatic correction", first_rule)
            corpus = index.index("Corpus window: start → end", status)
            second_rule = index.index("<hr>", corpus)
            news = index.index("<h2>AI/Tech</h2>", second_rule)
            self.assertLess(title, first_rule)
            self.assertLess(first_rule, status)
            self.assertLess(status, corpus)
            self.assertLess(corpus, second_rule)
            self.assertLess(second_rule, news)
            self.assertIn(".status-chip a { text-decoration: underline; }", index)

    def test_ready_page_without_news_closes_metadata_with_second_divider(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "# Daily Briefing — August 20, 2026\n\n"
                "Corpus window: start → end\n",
                encoding="utf-8",
            )
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            title = index.index("<h1>Daily Briefing — August 20, 2026</h1>")
            first_rule = index.index("<hr>", title)
            status = index.index("Passed publication checks", first_rule)
            corpus = index.index("Corpus window: start → end", status)
            second_rule = index.index("<hr>", corpus)
            self.assertLess(title, first_rule)
            self.assertLess(first_rule, status)
            self.assertLess(status, corpus)
            self.assertLess(corpus, second_rule)

    def test_run_outcome_without_corpus_health_keeps_its_labels_and_order(self) -> None:
        corpus, config, projected, output = fixture_contract()
        corpus["errors"] = []
        corpus["sources"] = []
        findings = [
            {"level": eval_briefing.WARN, "check": "unsupported_figure",
             "message": "states '152', which is not supported"},
            {"level": eval_briefing.WARN, "check": "figure_supported_elsewhere",
             "message": "figure appears in another story"},
        ]
        markdown = (
            render_briefing(output, corpus, config, projected.citations).rstrip()
            + "\n"
            + render_validation_status(findings, corpus)
        )
        self.assertNotIn("### Corpus health", markdown)
        self.assertIn("unsupported_figure", markdown)

        rendered = _render_markdown(markdown)

        excluded = rendered.index("<h3>Excluded Topics (accountability log)</h3>")
        outcome = rendered.index("<h3>Run outcome</h3>")
        self.assertLess(excluded, outcome)
        run_outcome = rendered[outcome:]
        for label in ("Disposition: ", "Errors", "Warnings", "Source issues",
                      "Undated source drops"):
            with self.subTest(label=label):
                self.assertIn(f"<strong>{label}", run_outcome)
                self.assertNotIn(f"<strong>{label}", rendered[excluded:outcome])
        self.assertNotIn("unsupported_figure", rendered)
        self.assertNotIn("figure_supported_elsewhere", rendered)
        warnings = run_outcome.index("<strong>Warnings</strong>")
        self.assertIn("None</p>", run_outcome[warnings:run_outcome.index("Source issues")])

    def test_status_chip_shows_degraded_sources_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("degraded briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                degraded_sources=["reddit:cursor"],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            # Without the verified per-source record, the label admits the
            # reason is unknown rather than guessing failed or empty.
            self.assertIn('<p class="status-chip status-warn"><a href="reports/2026-08-20.html">'
                          "⚠ Passed publication checks · Some sources had problems</a></p>", index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("Sources: <a href=\"#sources\">Some sources had problems</a>", report)
            self.assertIn("<strong>r/cursor</strong> (subreddit) reason not recorded", report)

    def test_status_chip_review_required(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("preview content", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding("unsupported_figure", "fig check"),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("🔍 Withheld for review · All sources healthy", index)

    def test_review_required_renders_stub_without_briefing_prose(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## US News\n\n**Secret story** — Sensitive preview content.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding("unsupported_figure", "fig check"),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn("Secret story", index)
            self.assertNotIn("Sensitive preview", index)
            self.assertIn("did not pass automated checks", index)
            self.assertIn("reports/2026-08-20.html", index)
            title = index.index("<h1>Daily briefing — 2026-08-20</h1>")
            first_rule = index.index("<hr>", title)
            status = index.index("Withheld for review", first_rule)
            second_rule = index.index("<hr>", status)
            stub = index.index("did not pass automated checks", second_rule)
            self.assertLess(title, first_rule)
            self.assertLess(first_rule, status)
            self.assertLess(status, second_rule)
            self.assertLess(second_rule, stub)

    def test_briefing_pages_have_no_inline_review_panels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-19.md").write_text("prior ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-19", disposition="ready")
            (briefings / "2026-08-20.md").write_text(
                "## AI News\n\n"
                "<!-- story: topics.AI News[0] -->\n"
                "**AI story** — Summary.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "unsupported_figure",
                        "topics.AI News[0].summary states '42'",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "entry json",
                            "path": "topics.AI News[0]",
                        },
                    ),
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn('<section class="review-panel">', index)
            self.assertNotIn('<section class="review-story">', index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("unsupported figure", report)

    def test_report_page_contains_findings_and_repair_actions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## AI News\n\n"
                "<!-- story: topics.AI News[0] -->\n"
                "**AI story** — Summary.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "unsupported_figure",
                        "topics.AI News[0].summary states '42'",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "entry json",
                            "path": "topics.AI News[0]",
                        },
                    ),
                ],
                repair_actions=[
                    {"action": "drop_entry", "path": "topics.AI News[1]", "reason": "duplicate"},
                ],
                degraded_sources=["reddit:cursor"],
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("unsupported figure", report)
            self.assertIn("Recorded deterministic findings", report)
            self.assertIn("drop_entry", report)
            self.assertIn("topics.AI News[1]", report)
            self.assertIn("duplicate", report)
            self.assertIn("r/cursor", report)

    def test_report_page_keeps_provenance_line_in_operator_records(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("ready briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                provenance=self._provenance(),
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn(
                "Generated by openrouter / deepseek/deepseek-v4-flash-0731, fallback position 2 of 3, "
                "1 selection correction, 0 prose corrections, 0 total repair actions.",
                report,
            )
            decision_pos = report.index('<p class="decision">')
            details_pos = report.index("<details><summary>Provenance and costs</summary>")
            provenance_pos = report.index("Generated by openrouter / ")
            self.assertLess(decision_pos, details_pos)
            self.assertLess(details_pos, provenance_pos)
            # The reader-facing page closes with the model, time, and charges
            # only; chain position, correction counts, and the prompt hash stay
            # in the report's operator records.
            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn('<p class="muted generation-summary">Generated by DeepSeek V4 Flash via OpenRouter. '
                          "Generation time was not recorded. Model charges were not recorded.</p>", index)
            self.assertNotIn("fallback position", index)
            self.assertNotIn("selection correction", index)
            self.assertNotIn("Prompt SHA-256", index)
            self.assertIn("Generated by DeepSeek V4 Flash via OpenRouter. Generation time was not recorded.", report)

    def test_report_page_without_provenance_omits_the_line(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertNotIn("provenance", report)

    def test_provenance_line_omits_chain_position_for_a_single_manual_attempt(self) -> None:
        # A manual run_briefing.py run has no fallback chain: attempt_count is 1
        # and the "attempt X of Y" clause would be noise rather than signal.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("ready briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                provenance=self._provenance(
                    provider="claude-code-cli",
                    model="claude-opus-4",
                    attempt_index=1,
                    attempt_count=1,
                ),
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("Generated by claude-code-cli / claude-opus-4, 1 selection correction", report)
            self.assertNotIn("attempt 1 of 1", report)

    def test_provenance_survives_history_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first"
            first.mkdir()
            (first / "2026-08-19.md").write_text("generated briefing", encoding="utf-8")
            provenance = self._provenance()
            self._write_sidecar(
                first, date="2026-08-19", disposition="ready", provenance=provenance
            )
            first_site = root / "site-1"
            build_site(first, first_site)
            history = json.loads((first_site / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["schema_version"], 10)
            self.assertEqual(history["entries"][0]["provenance"], provenance)

            second = root / "second"
            second.mkdir()
            (second / "2026-08-20.md").write_text("next briefing", encoding="utf-8")
            self._write_sidecar(second, date="2026-08-20", disposition="ready")
            second_site = root / "site-2"
            build_site(second, second_site, prior_history=first_site / "history.json")
            rebuilt = json.loads((second_site / "history.json").read_text(encoding="utf-8"))
            by_date = {entry["date"]: entry for entry in rebuilt["entries"]}
            self.assertEqual(by_date["2026-08-19"]["provenance"], provenance)
            self.assertIsNone(by_date["2026-08-20"]["provenance"])
            restored_report = (second_site / "reports/2026-08-19.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Generated by openrouter / deepseek/deepseek-v4-flash-0731", restored_report)

    def test_historical_history_versions_are_rejected(self) -> None:
        from build_site import _load_history

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            for version in (True, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11):
                with self.subTest(version=version):
                    path.write_text(json.dumps({"schema_version": version, "entries": []}))
                    with self.assertRaisesRegex(ValueError, "schema_version 10"):
                        _load_history(path)

    def test_history_rejects_provenance_without_a_public_artifact(self) -> None:
        from build_site import _entry_from_payload

        base = {
            "date": "2026-08-20", "disposition": "blocked", "findings_count": 0,
            "findings": [], "degraded_sources": [], "repair_actions": [],
            "generation_failures": [], "advisory_findings": [],
            "provenance": self._provenance(),
        }
        with self.assertRaises(ValueError):
            _entry_from_payload(base, source="test")

    def test_history_rejects_malformed_provenance(self) -> None:
        from build_site import _entry_from_payload

        base = {
            "date": "2026-08-20", "disposition": "ready", "findings_count": 0,
            "findings": [], "degraded_sources": [], "repair_actions": [],
            "generation_failures": [], "advisory_findings": [],
        }
        malformed = self._provenance()
        malformed["attempt_index"] = 5  # exceeds attempt_count
        with self.assertRaises(ValueError):
            _entry_from_payload({**base, "provenance": malformed}, source="test")

    def test_report_page_links_between_briefing_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("reports/2026-08-20.html", index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("index.html", report)

    def test_clean_entry_report_page_states_all_checks_passed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("clean briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            # A day with no semantic review says so in the decision sentence.
            self.assertIn('<p class="decision"><strong>Published as originally generated.</strong> '
                          "No automated story review was recorded for this day.</p>", report)
            self.assertIn("Deterministic checker: 0 findings; all contract checks passed.", report)
            self.assertIn("Nothing was recorded as changed after generation.", report)

    def test_ready_report_with_advisory_findings_states_gate_passed_with_notes(self) -> None:
        # A passed gate still names and counts its nonblocking notes.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## World Events\n\n**A story** — Summary.\n", encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                advisory_findings=[
                    self._finding(
                        "slots_underfilled",
                        "World Events: 2 topics, expected 5; 3 unused eligible corpus item(s) remain",
                        domain="quality",
                    ),
                ],
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertNotIn("all contract checks passed", report)
            self.assertIn("Deterministic checker: 0 findings, 1 advisory note.", report)
            self.assertIn("No automated story review was recorded for this day.", report)
            notes = report.index("<details><summary>Checker notes</summary>")
            self.assertLess(notes, report.index('<td>slots underfilled</td><td>1</td>'))
            self.assertNotIn('Summary.', report)
            self.assertNotIn("Action:", report)

    def test_advisory_context_does_not_reprint_briefing_and_report_owns_the_log(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## AI News\n\n"
                "<!-- story: topics.AI News[0] -->\n"
                "**AI story** — Summary.\n\n"
                "### Excluded Topics (accountability log)\n\n"
                "**Other story** — Accountability entry retained.\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                advisory_findings=[
                    self._finding(
                        "low_claim_evidence_overlap",
                        "AI News: 'AI story' shares only 1 of 6 claim terms with its cited evidence",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "entry json",
                            "path": "topics.AI News[0]",
                        },
                        domain="quality",
                    ),
                ],
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('low claim evidence overlap', report)
            self.assertNotIn('<article class="briefing-content">', report)
            self.assertNotIn('Summary.', report)
            self.assertEqual(report.count('Accountability entry retained.'), 1)
            log = report.index('<section class="excluded-topics">')
            self.assertLess(log, report.index('Accountability entry retained.'))
            index = (root / 'site/index.html').read_text()
            self.assertIn('Summary.', index)
            self.assertNotIn('Accountability entry retained.', index)

    def test_blocked_report_does_not_claim_checks_passed(self) -> None:
        # A blocked run means the checker never accepted a candidate; its
        # zero findings_count must not read as an all-clear.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            self._write_sidecar(briefings, date="2026-08-20", disposition="blocked")

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('<p class="decision"><strong>Not published.</strong> The run stopped before '
                          "a briefing passed the publication checks.</p>", report)
            self.assertNotIn("all contract checks passed", report)
            self.assertIn("a zero count does not mean a candidate passed", report)
            self.assertNotIn("What changed in the published briefing", report)

    def test_rejected_report_notes_unpublished_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            self._write_sidecar(
                briefings, date="2026-08-20", disposition="rejected", findings_count=1
            )

            build_site(briefings, root / "site")

            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('<p class="decision"><strong>Not published.</strong> '
                          "The checker rejected the briefing.</p>", report)
            self.assertNotIn("all contract checks passed", report)
            self.assertIn("no findings are recorded for unpublished runs", report)
    @staticmethod
    def _corpus_health_markdown(payload: str) -> str:
        return (
            "## AI News\n\n"
            "**AI story** — Summary.\n\n"
            "---\n\n"
            "### Corpus health\n"
            "Coverage was degraded by the source failures or empty responses listed below.\n"
            "\n"
            "```json\n"
            f"{payload}\n"
            "```\n"
        )

    def test_corpus_health_json_renders_as_grouped_prose(self) -> None:
        payload = json.dumps(
            {
                "failed_sources": [
                    {"source_type": "rss", "source_id": "NPR Politics", "status": "empty"},
                    {"source_type": "rss", "source_id": "The Hill", "status": "empty"},
                    {"source_type": "hacker_news", "source_id": "llm agent", "status": "empty"},
                    {"source_type": "reddit", "source_id": "LocalLLaMA", "status": "error"},
                ]
            },
            separators=(",", ":"),
        )
        rendered = _render_markdown(self._corpus_health_markdown(payload))
        self.assertNotIn("failed_sources", rendered)
        self.assertNotIn("<pre", rendered)
        self.assertNotIn("<code", rendered)
        self.assertIn(
            "2 RSS feeds and 1 Hacker News search returned no items in this day's window.",
            rendered,
        )
        self.assertIn("1 subreddit fetch failed.", rendered)
        self.assertIn("fetch failed", rendered)
        self.assertIn("NPR Politics", rendered)
        self.assertIn("The Hill", rendered)
        # Hacker News source ids are search queries; they render quoted.
        self.assertIn("&quot;llm agent&quot;", rendered)
        self.assertIn("Subreddits — fetch failed:", rendered)

    def test_undated_source_health_renders_as_grouped_prose(self) -> None:
        payload = json.dumps(
            {
                "failed_sources": [],
                "undated_sources": [
                    {"source_type": "rss", "source_id": "NPR Politics", "count": 2},
                ],
            },
            separators=(",", ":"),
        )
        markdown = self._corpus_health_markdown(payload).replace(
            "source failures or empty responses",
            "source failures, empty responses, or undated drops",
        )
        rendered = _render_markdown(markdown)
        self.assertNotIn("undated_sources", rendered)
        self.assertIn("1 source dropped 2 items without parseable dates.", rendered)
        self.assertIn("NPR Politics (2)", rendered)

    def test_malformed_corpus_health_json_is_left_verbatim(self) -> None:
        rendered = _render_markdown(
            self._corpus_health_markdown('{"failed_sources":[{"source_type":')
        )
        self.assertIn("<code", rendered)
        self.assertIn("failed_sources", rendered)

    def test_wrong_shape_corpus_health_json_is_left_verbatim(self) -> None:
        for payload in (
            '{"failed_sources":{}}',
            '{"failed_sources":[]}',
            '{"failed_sources":[],"undated_sources":null}',
            ('{"failed_sources":[],"undated_sources":['
             '{"source_type":"rss","source_id":"NPR","count":1},'
             '{"source_type":"rss","source_id":"NPR","count":2}]}'),
            '{"failed_sources":["rss"]}',
            '{"failed_sources":[{"source_type":"rss","source_id":"NPR","status":42}]}',
            '["not","an","object"]',
        ):
            rendered = _render_markdown(self._corpus_health_markdown(payload))
            self.assertIn("<code", rendered, payload)
            self.assertNotIn("this day's window", rendered, payload)

    def test_empty_source_type_is_left_verbatim(self) -> None:
        # A degenerate-but-well-typed payload must not crash the site build.
        payload = '{"failed_sources":[{"source_type":"","source_id":"x","status":"empty"}]}'
        rendered = _render_markdown(self._corpus_health_markdown(payload))
        self.assertIn("<code", rendered)
        self.assertIn("failed_sources", rendered)

    def test_control_characters_in_values_are_left_verbatim(self) -> None:
        # JSON \n escapes fit in a single-line fence; promoting them to active
        # markdown would inject story anchors, headings, or fence desyncs.
        payload = json.dumps(
            {
                "failed_sources": [
                    {
                        "source_type": "rss",
                        "source_id": (
                            "X\n<!-- story: topics.AI News[0] -->\n### Injected heading"
                        ),
                        "status": "empty",
                    }
                ]
            },
            separators=(",", ":"),
        )
        rendered = _render_markdown(self._corpus_health_markdown(payload))
        self.assertIn("<code", rendered)
        self.assertIn("failed_sources", rendered)
        self.assertNotIn("<h3>Injected heading</h3>", rendered)
        self.assertNotIn("<!-- story:", rendered)

    def test_corpus_health_heading_inside_code_fence_is_not_transformed(self) -> None:
        markdown = (
            "## AI News\n\n"
            "````markdown\n"
            "### Corpus health\n"
            "Coverage was degraded by the source failures or empty responses listed below.\n"
            "\n"
            "```json\n"
            '{"failed_sources":[{"source_type":"rss","source_id":"NPR","status":"empty"}]}\n'
            "```\n"
            "````\n"
        )
        rendered = _render_markdown(markdown)
        self.assertIn("failed_sources", rendered)
        self.assertNotIn("⚠", rendered)

    def test_humanizer_round_trips_the_real_emitter(self) -> None:
        # Pins heading, explanation sentence, blank line, fence, and JSON shape
        # against agent_runner.output.render_briefing: if the emitter's wording
        # or layout drifts, this fails instead of the site silently regressing
        # to raw JSON.
        corpus, config, projected, output = fixture_contract()
        self.assertTrue(corpus["errors"])
        markdown = render_briefing(output, corpus, config, projected.citations)
        self.assertIn("failed_sources", markdown)
        humanized = _humanize_corpus_health(markdown)
        self.assertNotIn("failed_sources", humanized)
        self.assertNotIn("```json", humanized)
        self.assertIn("⚠", humanized)

    def test_corpus_health_unknown_type_and_status_render_verbatim(self) -> None:
        payload = json.dumps(
            {
                "failed_sources": [
                    {"source_type": "mastodon", "source_id": "fosstodon", "status": "empty"},
                    {"source_type": "rss", "source_id": "NPR Politics", "status": "timeout"},
                ]
            },
            separators=(",", ":"),
        )
        rendered = _render_markdown(self._corpus_health_markdown(payload))
        self.assertNotIn("failed_sources", rendered)
        self.assertIn("1 mastodon returned no items in this day's window.", rendered)
        self.assertIn("1 RSS feed timeout.", rendered)
        self.assertIn("fosstodon", rendered)
        self.assertIn("NPR Politics", rendered)

    def test_json_fence_without_corpus_health_heading_is_untouched(self) -> None:
        markdown = (
            "## AI News\n\n"
            "**AI story** — Summary.\n\n"
            "```json\n"
            '{"failed_sources":[{"source_type":"rss","source_id":"NPR","status":"empty"}]}\n'
            "```\n"
        )
        rendered = _render_markdown(markdown)
        self.assertIn("<code", rendered)
        self.assertIn("failed_sources", rendered)
    def test_sidecar_v4_with_repair_actions_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text(
                "## AI News\n\n"
                "<!-- story: topics.AI News[0] -->\n"
                "**AI story** — Included summary.\n"
                "🔗 https://example.com/ai\n",
                encoding="utf-8",
            )
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="review_required",
                findings=[
                    self._finding(
                        "unsupported_figure",
                        "topics.AI News[0].summary states '42'",
                        context={
                            "section": "AI News",
                            "headline": "AI story",
                            "model_authored": "entry json",
                            "path": "topics.AI News[0]",
                        },
                    ),
                ],
                repair_actions=[
                    {"action": "drop_entry", "path": "topics.AI News[1]", "reason": "duplicate"},
                ],
            )

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertNotIn('<section class="review-story">', index)
            self.assertIn("did not pass automated checks", index)
            report = (root / "site/reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn("unsupported figure", report)
            self.assertIn("drop_entry", report)

    def test_current_history_round_trips_advisory_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("clean briefing", encoding="utf-8")
            self._write_sidecar(
                briefings,
                date="2026-08-20",
                disposition="ready",
                advisory_findings=[
                    self._finding(
                        "exclusion_log_missing",
                        "exclusion log has no entries for 'World Events'",
                        domain="quality",
                    ),
                ],
            )

            build_site(briefings, root / "site")

            history = json.loads((root / "site/history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["schema_version"], 10)
            entry = history["entries"][0]
            self.assertEqual(len(entry["advisory_findings"]), 1)
            self.assertEqual(entry["advisory_findings"][0]["check"], "exclusion_log_missing")


    def test_publishes_text_free_manifests_from_validated_corpora(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")
            corpora = root / "corpora"
            corpora.mkdir()
            valid_corpus, _config, _projected, _output = fixture_contract()
            fresh_corpus = json.dumps(
                {**valid_corpus, "report_date": "2026-08-20"}
            )
            (corpora / "2026-08-20.json").write_text(fresh_corpus, encoding="utf-8")
            (corpora / "2026-08-14.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-14"}),
                encoding="utf-8",
            )
            (corpora / "not-a-date.json").write_text("{}", encoding="utf-8")
            (corpora / "2026-08-19.json").write_text("{invalid json", encoding="utf-8")
            (corpora / "2026-08-18.json").write_text("{}", encoding="utf-8")
            (corpora / "2026-08-21.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-21"}),
                encoding="utf-8",
            )

            output = root / "site"
            build_site(briefings, output, corpora_dir=corpora)

            published_path = output / "manifests/2026-08-20.json"
            published = json.loads(published_path.read_text(encoding="utf-8"))
            self.assertEqual(published["report_date"], "2026-08-20")
            self.assertEqual(
                published["corpus_sha256"],
                hashlib.sha256(fresh_corpus.encode("utf-8")).hexdigest(),
            )
            first_item = valid_corpus["categories"][next(iter(valid_corpus["categories"]))][0]
            rendered_manifest = published_path.read_text(encoding="utf-8")
            self.assertNotIn(first_item["title"], rendered_manifest)
            self.assertNotIn(first_item.get("summary", "not present"), rendered_manifest)
            self.assertTrue((output / "manifests/2026-08-14.json").is_file())
            self.assertFalse((output / "manifests/2026-08-19.json").exists())
            self.assertFalse((output / "manifests/2026-08-18.json").exists())
            self.assertFalse((output / "manifests/2026-08-21.json").exists())
            self.assertFalse((output / "manifests/not-a-date.json").exists())
            self.assertFalse((output / "corpora").exists())
            report = (output / "reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertIn('../manifests/2026-08-20.json', report)
            self.assertIn("source-owned titles and excerpts are not included", report)

    def test_prunes_manifests_older_than_fourteen_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")
            corpora = root / "corpora"
            corpora.mkdir()
            valid_corpus, _config, _projected, _output = fixture_contract()
            # 2026-08-07 is exactly newest - 13 days: the oldest date kept.
            for day in ("2026-08-07", "2026-08-06", "2026-08-05"):
                (corpora / f"{day}.json").write_text(
                    json.dumps({**valid_corpus, "report_date": day}),
                    encoding="utf-8",
                )

            output = root / "site"
            build_site(briefings, output, corpora_dir=corpora)

            self.assertTrue((output / "manifests/2026-08-07.json").is_file())
            self.assertFalse((output / "manifests/2026-08-06.json").exists())
            self.assertFalse((output / "manifests/2026-08-05.json").exists())
            self.assertFalse((output / "corpora").exists())

    def test_unusable_historical_corpus_does_not_abort_site_build(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")
            first = root / "corpora"
            first.mkdir()
            valid_corpus, _config, _projected, _output = fixture_contract()
            (first / "2026-08-19.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-19"}),
                encoding="utf-8",
            )
            unusable = json.loads(json.dumps(valid_corpus))
            unusable["categories"][next(iter(unusable["categories"]))][0][
                "title"
            ] = "Projection failure"
            (first / "2026-08-20.json").write_text(
                json.dumps({**unusable, "report_date": "2026-08-20"}),
                encoding="utf-8",
            )

            def build_manifest(payload, _raw):
                first_item = next(iter(payload["categories"].values()))[0]
                if first_item["title"] == "Projection failure":
                    raise ValueError("projection cannot use historical corpus")
                return {"report_date": payload["report_date"]}

            output = root / "site"
            with patch(
                "build_site.build_audit_manifest", side_effect=build_manifest
            ) as manifest_builder:
                build_site(briefings, output, corpora_dir=first)

            self.assertTrue((output / "index.html").is_file())
            self.assertTrue((output / "reports/2026-08-20.html").is_file())
            self.assertTrue((output / "manifests/2026-08-19.json").is_file())
            self.assertFalse((output / "manifests/2026-08-20.json").exists())
            self.assertEqual(manifest_builder.call_count, 2)

    def test_no_corpora_dir_builds_site_without_manifests_or_raw_corpora(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            output = root / "site"
            (output / "corpora").mkdir(parents=True)
            (output / "corpora/stale.json").write_text("private", encoding="utf-8")
            build_site(briefings, output)

            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((output / "corpus-storage.json").exists())
            self.assertFalse((output / "corpora").exists())
            self.assertFalse((output / "manifests").exists())
            report = (output / "reports/2026-08-20.html").read_text(encoding="utf-8")
            self.assertNotIn("Download audit manifest", report)

    def test_corpora_without_history_entries_publish_nothing(self) -> None:
        # No entries means no date anchor for pruning: fail safe, publish nothing.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            corpora = root / "corpora"
            corpora.mkdir()
            valid_corpus, _config, _projected, _output = fixture_contract()
            (corpora / "2026-08-20.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-20"}),
                encoding="utf-8",
            )

            output = root / "site"
            build_site(briefings, output, corpora_dir=corpora)

            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((output / "corpora").exists())
            self.assertFalse((output / "manifests").exists())

    @staticmethod
    def _finding(
        check: str,
        message: str,
        *,
        context: dict[str, str] | None = None,
        domain: str = "evidence",
    ) -> dict[str, object]:
        finding: dict[str, object] = {
            "level": "WARN",
            "check": check,
            "domain": domain,
            "message": message,
        }
        finding["context"] = context
        return finding

    @staticmethod
    def _provenance(
        *,
        provider: str = "openrouter",
        model: str = "deepseek/deepseek-v4-flash-0731",
        attempt_index: int = 2,
        attempt_count: int = 3,
        selection_corrections: int = 1,
        prose_corrections: int = 0,
        repair_action_count: int = 0,
        prompt_sha256: str = "a" * 64,
    ) -> dict[str, object]:
        return {
            "provider": provider,
            "model": model,
            "attempt_index": attempt_index,
            "attempt_count": attempt_count,
            "selection_corrections": selection_corrections,
            "prose_corrections": prose_corrections,
            "repair_action_count": repair_action_count,
            "prompt_sha256": prompt_sha256,
        }

    @staticmethod
    def _write_sidecar(
        directory: Path,
        *,
        date: str,
        disposition: str,
        findings: list[dict[str, object]] | None = None,
        findings_count: int | None = None,
        degraded_sources: list[str] | None = None,
        repair_actions: list[dict[str, str]] | None = None,
        advisory_findings: list[dict[str, object]] | None = None,
        provenance: dict[str, object] | None = None,
    ) -> None:
        details = findings or []
        count = len(details) if findings_count is None else findings_count
        payload: dict[str, object] = {
            "date": date,
            "disposition": disposition,
            "findings_count": count,
            "findings": details,
            "degraded_sources": degraded_sources or [],
        }
        payload.update(
            repair_actions=repair_actions or [], generation_failures=[],
            advisory_findings=advisory_findings or [], provenance=provenance,
        )
        integrity = blank_integrity(
            "original_retained" if disposition in {"ready", "review_required"} else "unpublished",
            ["audit_unavailable"] if disposition in {"ready", "review_required"} else ["verification_failed"],
        )
        markdown_path = directory / f"{date}.md"
        if disposition in {"ready", "review_required"} and markdown_path.is_file():
            digest = hashlib.sha256(markdown_path.read_bytes()).hexdigest()
            integrity["artifacts"] = [
                {"id": "original", "sha256": digest}, {"id": "published", "sha256": digest},
            ]
        integrity["generation"] = provenance
        add_phase(integrity, "publication_verification",
                  "complete" if integrity["artifacts"] else "failed")
        payload.update(semantic_audit=None, integrity=integrity)
        (directory / f"{date}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )




def _decorated(markdown: str) -> str:
    entry = BriefingEntry(
        day=date(2026, 8, 20), disposition="ready", findings_count=0, findings=(),
        degraded_sources=(), markdown=markdown,
    )
    return _decorate_briefing(_render_markdown(markdown), entry)


class BriefingDecorationTests(unittest.TestCase):
    BRIEFING = (
        "# Daily Briefing — August 20, 2026\n\n"
        "Corpus window: 2026-08-19T19:22:09+00:00 → 2026-08-20T19:22:09+00:00\n\n"
        "## AI/Tech\n\n"
        "**AI News (2 slots)**\n\n"
        "**Release ships** *(consolidated)* [verbatim] — Summary <b> text.\n"
        "🔗 https://github.com/org/repo/releases/tag/v1.0\n"
        "🔗 https://github.com/org/repo/releases/tag/v1.1\n"
        "🔗 https://www.example.com/news/story.html\n"
        "🔗 HN: https://news.ycombinator.com/item?id=1\n\n"
        "## US News\n\n"
        "**Plain story** — Second summary.\n"
        "🔗 https://example.org/a\n\n"
        "---\n\n"
        "### Excluded Topics (accountability log)\n\n"
        "- *Skipped* — Reason. 🔗 https://example.net/skipped\n\n"
        "### Run outcome\n\n"
        "**Disposition: READY**\n"
    )

    def test_citation_links_become_host_chips_with_unchanged_destinations(self) -> None:
        page = _decorated(self.BRIEFING)
        for href, label in (
            ("https://github.com/org/repo/releases/tag/v1.0", "github.com · v1.0"),
            ("https://github.com/org/repo/releases/tag/v1.1", "github.com · v1.1"),
            ("https://www.example.com/news/story.html", "example.com"),
            ("https://news.ycombinator.com/item?id=1", "HN discussion"),
        ):
            self.assertIn(f'href="{href}" title="{href}">{label}</a>', page)
        self.assertIn('class="source-chip source-hn"', page)
        self.assertNotIn("🔗", page)
        self.assertNotIn(">https://", page)

    def test_stories_sections_and_run_notes_are_structured(self) -> None:
        page = _decorated(self.BRIEFING)
        self.assertIn(
            '<p class="story"><strong class="story-title">Release ships</strong> '
            '<span class="story-tag">consolidated</span> <span class="story-tag">verbatim</span>'
            '<span class="story-dash"> — </span>Summary &lt;b&gt; text.',
            page,
        )
        self.assertIn('<p class="group-label"><strong>AI News</strong></p>', page)
        self.assertIn('<section class="topic" id="topic-ai-tech"><h2>AI/Tech</h2>', page)
        self.assertIn('<a href="#topic-us-news">US News<span class="count">1</span></a>', page)
        self.assertIn('<a href="#run-notes">Behind this briefing</a>', page)
        notes = page.index('<section class="run-notes"')
        self.assertLess(page.index("Plain story"), notes)
        self.assertLess(notes, page.index("<strong>Disposition: READY</strong>"))
        self.assertLess(notes, page.index('href="reports/2026-08-20.html#excluded-topics"'))
        self.assertTrue(page.startswith('<p class="dateline">Thursday edition · 2 stories</p>'))

    def test_exclusion_log_moves_from_briefing_to_integrity_report(self) -> None:
        page = _decorated(self.BRIEFING)
        self.assertNotIn("Excluded Topics", page)
        self.assertNotIn("example.net/skipped", page)
        self.assertNotIn("<hr", page[page.index('<section class="run-notes"'):])

        entry = BriefingEntry(
            day=date(2026, 8, 20), disposition="ready", findings_count=0, findings=(),
            degraded_sources=(), markdown=self.BRIEFING,
        )
        report = _render_report(entry, [entry])
        log = report[report.index('<section class="excluded-topics">'):]
        self.assertTrue(log.startswith(
            '<section class="excluded-topics">'
            '<h2 id="excluded-topics">Excluded Topics (accountability log)</h2>'
        ))
        self.assertIn(
            'href="https://example.net/skipped" title="https://example.net/skipped">example.net</a>',
            log[: log.index("</section>")],
        )
        self.assertNotIn("Disposition: READY", log[: log.index("</section>")])

        withheld = BriefingEntry(
            day=date(2026, 8, 20), disposition="review_required", findings_count=0, findings=(),
            degraded_sources=(), markdown=self.BRIEFING,
        )
        self.assertNotIn('<section class="excluded-topics">', _render_report(withheld, [withheld]))

    def test_corpus_window_is_humanized_only_when_it_parses(self) -> None:
        page = _decorated(self.BRIEFING)
        self.assertIn(
            '<time datetime="2026-08-19T19:22:09+00:00">Aug 19, 7:22 PM</time> to '
            '<time datetime="2026-08-20T19:22:09+00:00">Aug 20, 7:22 PM</time> UTC',
            page,
        )
        self.assertIn("Corpus window: start → end", _decorated("Corpus window: start → end\n"))

    def test_unrecognized_shapes_render_unchanged(self) -> None:
        markdown = (
            "## AI/Tech\n\n"
            "**Nested *title*** — Summary with [model link](https://model.example/x).\n"
        )
        rendered = _render_markdown(markdown)
        page = _decorated(markdown)
        self.assertIn(rendered.split("<h2>AI/Tech</h2>")[1], page)
        self.assertNotIn("source-chip", page)
        self.assertNotIn("run-notes", page)

    def test_model_markdown_links_and_images_render_inert(self) -> None:
        payloads = (
            r"[colon](https\://attacker.example/colon)",
            r"[slash](https:\/\/attacker.example/slash)",
            "[plain](https://attacker.example/plain)",
            "![image](https://attacker.example/image.png)",
            "[reference][r]\n\n[r]: https://attacker.example/reference",
        )
        for payload in payloads:
            with self.subTest(payload=payload):
                rendered = _render_markdown(f"## AI/Tech\n\n{payload}\n")
                self.assertNotIn("<a ", rendered)
                self.assertNotIn("<img", rendered)
        cited = _render_markdown("## AI/Tech\n\n🔗 https://example.com/story\n")
        self.assertIn('<a href="https://example.com/story">', cited)

    def test_count_label_pluralizes_y_nouns(self) -> None:
        self.assertEqual(_count_label(0, "story"), "0 stories")
        self.assertEqual(_count_label(1, "story"), "1 story")
        self.assertEqual(_count_label(2, "semantic flag"), "2 semantic flags")
        self.assertEqual(_count_label(2, "day"), "2 days")

class SemanticAuditSiteTests(unittest.TestCase):
    def test_public_repair_audit_is_escaped_and_survives_rebuild(self):
        from datetime import date

        from agent_runner.semantic_repairs import daily_semantic_review
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import Judge, PatchProvider, make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'jev-review',
                                          judge=Judge(headline), repair_provider=PatchProvider())
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'history', date(2026, 9, 30))
            payload = record.payload()
            # The renderer must treat public model prose as text even in restored history.
            changed_topic = next(t for t in payload['semantic_audit']['topics'] if t['changed'])
            changed_topic['original']['prose'] = '<script>alert(1)</script>'
            (root / 'history/2026-09-30.json').write_text(json.dumps(payload))
            build_site(root / 'history', root / 'site')
            page = (root / 'site/reports/2026-09-30.html').read_text()
            self.assertIn('Original prose', page)
            self.assertIn('Changed prose — applied', page)
            self.assertNotIn('Semantic faithfulness was not assessed', page)
            self.assertIn('0.90 / 0.90', page)
            self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', page)
            self.assertNotIn('<script>alert(1)</script>', page)
            self.assertEqual(page.count('<h4>Original prose</h4>'), sum(len(c['positions']) for c in audit['checks']
                                  if c['confirmation_label'] == 'confirmed' and c['check'] != 'duplicate'
                                  and c['positions'][0]['bucket'] == 'sections'))
            history = json.loads((root / 'site/history.json').read_text())
            self.assertEqual(history['schema_version'], 10)
            self.assertEqual(history['entries'][0]['semantic_audit'], payload['semantic_audit'])
            self.assertNotIn('"evidence":', json.dumps(history['entries'][0]['semantic_audit']))
            self.assertNotIn('"prompt":', json.dumps(history['entries'][0]['semantic_audit']))
            (root / 'empty').mkdir()
            build_site(root / 'empty', root / 'rebuilt', prior_history=root / 'site/history.json')
            self.assertEqual(page, (root / 'rebuilt/reports/2026-09-30.html').read_text())

    def test_summary_only_without_corrections_and_out_of_scope_checks(self):
        import build_site as build_site_module
        from agent_runner.semantic_repairs import daily_semantic_review
        from publication_schema import parse_semantic_audit
        from tests.test_semantic_repairs import Judge, make_run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', judge=Judge('never flagged'))
            for index, row in enumerate(audit['checks']):
                row['probability'] = (index % 40) / 100
            page = build_site_module._render_semantic_audit(audit)
            self.assertNotIn('Prose differences', page)
            for topic in audit['topics']:
                self.assertNotIn(topic['original']['prose'], page)
            self.assertNotIn('<h4>Original prose</h4>', page)
            self.assertIn('Total checks', page)
            self.assertNotIn('Highest scores', page)
            self.assertIn('<th scope="row">Citation irrelevance probability</th><td>0.60</td>', page)
            audit['checks'] = [c for c in audit['checks'] if c['check'] != 'irrelevant_citation']
            audit['planned_checks'] = len(audit['checks'])
            audit['citation_threshold'] = 0.8
            parse_semantic_audit(audit)
            page = build_site_module._render_semantic_audit(audit)
            self.assertIn('<th scope="row">Citation irrelevance probability</th><td>0.80</td>'
                          '<td colspan="5">Not in scope', page)
            self.assertNotIn('Prose differences', page)
            audit['status'] = 'partial'
            partial = build_site_module._render_semantic_audit(audit)
            self.assertIn('No results returned — scope unresolved', partial)

    def test_multiple_correction_findings_show_story_prose_once(self):
        import build_site as build_site_module
        from agent_runner.semantic_repairs import daily_semantic_review
        from tests.test_semantic_repairs import Judge, PatchProvider, make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review',
                                          judge=Judge(headline), repair_provider=PatchProvider())
            topic = next(t for t in audit['topics'] if t['changed'])
            extra = next(c for c in audit['checks'] if c['check'] == 'strengthened_claim'
                         and c['positions'] == [topic['position']])
            extra.update(probability=0.9, confirmation_probability=0.9, confirmation_label='confirmed')
            page = build_site_module._render_semantic_audit(audit)
            self.assertEqual(page.count('<h4>Original prose</h4>'), 1)
            self.assertEqual(page.count(topic['original']['prose']), 1)
            self.assertIn('Unsupported claims: 0.90 / 0.90', page)
            self.assertIn('Overstated claims: 0.90 / 0.90', page)

    def test_citation_only_removal_is_struck_through_without_repeating_prose(self):
        import build_site as build_site_module
        from agent_runner.jev_review import load_topics
        from agent_runner.semantic_repairs import daily_semantic_review
        from tests.test_semantic_repairs import CitationJudge, PatchProvider, make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            original = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            audit = daily_semantic_review(run, root / 'review',
                                          judge=CitationJudge(headline, original['evidence'][0]['title']),
                                          repair_provider=PatchProvider())
            citation = next(c for c in audit['checks'] if c['after_basis'] == 'citation_removed')
            topic = next(t for t in audit['topics'] if t['changed'])
            for status, outcome in (('applied', 'Removed'), ('rejected', 'Proposed removal — rejected')):
                topic['repair_status'] = status
                page = build_site_module._render_semantic_audit(audit)
                self.assertIn('<del><a href="' + citation['citation_urls'][0], page)
                self.assertIn('</a></del>', page)
                self.assertIn(outcome, page)
                self.assertIn('0.70 / 0.70', page)
                self.assertIn('Original prose', page)
                self.assertIn(topic['original']['prose'], page)
                self.assertIn(topic['changed']['prose'], page)
                topic['changed'] = dict(topic['original'])
                unchanged = build_site_module._render_semantic_audit(audit)
                self.assertNotIn('Original prose', unchanged)
                topic['changed'] = {'headline': 'Repaired headline', 'prose': 'A supported complete sentence.'}

    def test_schema_seven_history_is_rejected_without_rebuilding_or_losing_source(self):
        from datetime import date

        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _headline = make_run(root)
            prepare_publication(run, root / 'input.json', root / 'history', date(2026, 9, 30))
            build_site(root / 'history', root / 'site')
            old = json.loads((root / 'site/history.json').read_text())
            old['schema_version'] = 7
            for entry in old['entries']:
                del entry['semantic_audit']
                del entry['integrity']
            (root / 'legacy.json').write_text(json.dumps(old))
            (root / 'empty').mkdir()
            with self.assertRaises(ValueError):
                build_site(root / 'empty', root / 'rebuilt', prior_history=root / 'legacy.json')
            self.assertEqual(json.loads((root / 'legacy.json').read_text()), old)



class IntegrityReportTests(unittest.TestCase):
    def test_bound_quiet_health_is_consistent_in_summary_and_details(self):
        import hashlib
        from datetime import date

        import build_site as site
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _ = make_run(root)
            corpus_path = run / 'corpus.json'
            corpus = json.loads(corpus_path.read_text())
            source = {**corpus['sources'][0], 'source_id': 'ordinary quiet feed', 'status': 'quiet',
                      'parsed_entries': 3, 'dated_entries': 3, 'retained_entries': 0, 'retained_bytes': 0,
                      'estimated_tokens': 0, 'error_type': 'NoWindowEntries', 'message': 'private feed detail'}
            corpus['sources'] = [row for row in corpus['sources'] if row['status'] == 'ok'] + [source]
            corpus['errors'] = []
            corpus_path.write_text(json.dumps(corpus))
            manifest_path = run / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['artifacts']['corpus.json'] = hashlib.sha256(corpus_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest))
            record = prepare_publication(run, root / 'missing.json', root / 'public', date(2026, 9, 30))
            entry = site._entry_from_payload(record.payload(), source='fixture')
            page = site._render_report(entry, [entry])
            self.assertEqual(record.degraded_sources, ())
            self.assertIn('Sources: <a href="#sources">All sources healthy</a>', page)
            self.assertIn('<p class="coverage-state coverage-healthy">All sources healthy.</p>', page)
            self.assertIn('Quiet sources returned valid dated entries', page)
            self.assertIn('ordinary quiet feed', page)
            self.assertNotIn('private feed detail', page)
            corpus_path.write_text('{}')
            record = prepare_publication(run, root / 'missing.json', root / 'public', date(2026, 9, 30))
            entry = site._entry_from_payload(record.payload(), source='fixture')
            page = site._render_report(entry, [entry])
            self.assertIn('Source health was not recorded', page)
            self.assertNotIn('All sources healthy', page)


    def test_failed_and_skipped_targets_keep_subjects_and_all_causes(self):
        from datetime import date

        import build_site as site
        from agent_runner.semantic_repairs import daily_semantic_review
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import CitationJudge, PatchProvider, make_run

        for skipped in (False, True):
            with self.subTest(skipped=skipped), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, headline = make_run(root, grouping=not skipped)
                from agent_runner.jev_review import load_topics
                topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
                judge = CitationJudge(headline, topic["evidence"][0]["title"])
                daily_semantic_review(run, root / 'jev-review',
                                      judge=judge,
                                      repair_provider=PatchProvider(incomplete=True))
                (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
                record = prepare_publication(root, root / 'input.json', root / 'public', date(2026, 9, 30))
                entry = site._entry_from_payload(record.payload(), source='fixture')
                page = site._render_report(entry, [entry])
                self.assertIn(headline, page)
                self.assertIn('Original briefing published', page)
                self.assertIn('skipped' if skipped else 'failed', page)
                self.assertNotIn('Original prose', page)
                self.assertIn('Flagged source item 1', page)
                self.assertIn('Article ·', page)
                self.assertIn('0.70 / 0.70', page)
                self.assertIn('No removal established', page)
                self.assertLess(page.index('Action ledger'), page.index('Initial review statistics'))
                self.assertIn('remove every source item' if skipped else 'failed validation', page)

    def test_no_targets_states_no_followup_and_nullable_sidecar_does_not_inherit_history(self):
        from datetime import date

        import build_site as site
        from agent_runner.semantic_repairs import daily_semantic_review
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import Judge, make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _ = make_run(root)
            daily_semantic_review(run, root / 'jev-review', judge=Judge('never flagged'))
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'public', date(2026, 9, 30))
            entry = site._entry_from_payload(record.payload(), source='fixture')
            page = site._render_report(entry, [entry])
            self.assertIn('No follow-up review ran.', page)
            self.assertNotIn('Follow-up statistics unavailable', page)
            build_site(root / 'public', root / 'site')
            prior = root / 'site/history.json'
            stored = json.loads(prior.read_text())['entries'][0]
            self.assertIsNotNone(stored['integrity'])
            self.assertIsNotNone(stored['semantic_audit'])
            sidecar = root / 'public/2026-09-30.json'
            nullable = json.loads(sidecar.read_text())
            nullable.update(integrity=None, semantic_audit=None)
            sidecar.write_text(json.dumps(nullable))
            for replace_existing in (False, True):
                with self.subTest(replace_existing=replace_existing):
                    output = root / f'rebuilt-{replace_existing}'
                    build_site(root / 'public', output, prior_history=prior, replace_existing=replace_existing)
                    rebuilt = json.loads((output / 'history.json').read_text())['entries'][0]
                    self.assertIsNone(rebuilt['integrity'])
                    self.assertIsNone(rebuilt['semantic_audit'])
                    self.assertEqual(rebuilt['markdown'], stored['markdown'])

    def test_current_history_roundtrip_preserves_integrity_and_rejects_old_inputs(self):
        from datetime import date

        import build_site as site
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _ = make_run(root)
            record = prepare_publication(run, root / 'input.json', root / 'public', date(2026, 9, 30))
            build_site(root / 'public', root / 'site')
            current = json.loads((root / 'site/history.json').read_text())
            self.assertEqual(current['entries'][0]['integrity'], record.payload()['integrity'])
            (root / 'empty').mkdir()
            build_site(root / 'empty', root / 'rebuilt', prior_history=root / 'site/history.json')
            self.assertEqual(current, json.loads((root / 'rebuilt/history.json').read_text()))
            sidecar = root / 'public/2026-09-30.json'
            legacy_sidecar = json.loads(sidecar.read_text())
            del legacy_sidecar['integrity']
            sidecar.write_text(json.dumps(legacy_sidecar))
            with self.assertRaises(ValueError):
                build_site(root / 'public', root / 'merged', prior_history=root / 'site/history.json')
            current['schema_version'] = 8
            del current['entries'][0]['integrity']
            (root / 'legacy.json').write_text(json.dumps(current))
            with self.assertRaises(ValueError):
                site._load_history(root / 'legacy.json')

    def test_word_diff_escapes_both_sides_and_marks_changed_words(self):
        import build_site as site
        old, new = site._word_diff('A <script>bad</script> sentence.', 'A supported sentence.')
        self.assertIn('<del>&lt;script&gt;bad&lt;/script&gt; ', old)
        self.assertIn('<ins>supported ', new)
        self.assertNotIn('<script>', old + new)
        self.assertTrue(old.startswith('A '))
        self.assertTrue(new.endswith('sentence.'))

    def test_nonpublic_integrity_keeps_status_only_rendering(self):
        import build_site as site
        from agent_runner.integrity import blank_integrity

        for disposition in ('blocked', 'rejected', 'no_result'):
            with self.subTest(disposition=disposition):
                payload = {
                    'date': '2026-09-30', 'disposition': disposition, 'findings_count': 0,
                    'degraded_sources': [], 'findings': [], 'advisory_findings': [],
                    'generation_failures': [], 'provenance': None, 'semantic_audit': None,
                    'integrity': blank_integrity('unpublished', ['no_result']),
                    'repair_actions': [{'action': 'drop_entry', 'path': 'private-subject',
                                        'reason': 'private-reason'}],
                }
                entry = site._entry_from_payload(payload, source='fixture')
                page = site._render_report(entry, [entry])
                self.assertIn('No briefing published', page)
                self.assertNotIn('private-subject', page)
                self.assertNotIn('private-reason', page)
                payload['integrity']['generation'] = {
                    'provider': 'private', 'model': 'private', 'attempt_index': 1, 'attempt_count': 1,
                    'selection_corrections': 0, 'prose_corrections': 0, 'repair_action_count': 0,
                    'prompt_sha256': 'a' * 64,
                }
                with self.assertRaises(ValueError):
                    site._entry_from_payload(payload, source='fixture')

    def test_retained_original_flags_are_separate_from_candidate_assessment(self):
        from datetime import date

        import build_site as site
        from agent_runner.semantic_repairs import daily_semantic_review
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import Judge, PatchProvider, make_run, repaired

        class NewBlockers(Judge):
            def evaluate(self, state, questions, **kwargs):
                result = super().evaluate(state, questions, **kwargs)
                for index, (key, question) in enumerate(questions.items()):
                    if repaired(state['checks'][index].get('topic', {})):
                        result['probabilities'][key] = (
                            0.9 if ('strengthen certainty' in question['instructions']
                                    or 'contradict or reverse' in question['instructions']) else 0.1)
                return result

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            daily_semantic_review(run, root / 'jev-review', judge=NewBlockers(headline),
                                  repair_provider=PatchProvider())
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'public', date(2026, 9, 30))
            entry = site._entry_from_payload(record.payload(), source='fixture')
            page = site._render_report(entry, [entry])
            self.assertIn('Published briefing: <strong>1 semantic flag</strong> (initial assessment)', page)
            self.assertIn('Unpublished candidate: 2 semantic flags', page)
            self.assertIn('Candidate follow-up', page)
            self.assertIn('Follow-up blocker</strong> — Overstated claims', page)
            self.assertIn('Follow-up blocker</strong> — Reversed meaning', page)
            self.assertEqual(entry.integrity['decision'], 'original_retained')

    def test_partial_publication_renders_decision_split_flags_and_per_story_ledger(self):
        from datetime import date

        import build_site as site
        from prepare_publication import prepare_publication
        from publication_schema import INTEGRITY_DECISION_MESSAGES
        from tests.test_semantic_repairs import split_review

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _run, _audit, first, second = split_review(root)
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'public', date(2026, 10, 13))
            entry = site._entry_from_payload(record.payload(), source='fixture')
            page = site._render_report(entry, [entry])
            self.assertIn(f"<strong>{INTEGRITY_DECISION_MESSAGES['partial_applied']}</strong>", page)
            self.assertIn('1 rejected; 1 applied', page)
            self.assertIn('Published briefing: <strong>0 semantic flags</strong> on repaired and unaffected '
                          'stories (follow-up assessment); <strong>1 semantic flag</strong> on stories that kept '
                          'their original prose (initial assessment).', page)
            self.assertIn('Withheld repairs: 1 semantic flag in follow-up assessment.', page)
            self.assertNotIn('Unpublished candidate', page)
            self.assertIn('Follow-up review statistics', page)
            self.assertIn('Changed prose — applied (published)', page)
            self.assertIn('Changed prose — rejected (proposed)', page)
            for action, story in (('apply repair — applied', first), ('withhold repair — rejected', second)):
                anchor = site._story_anchor(story['position'])
                self.assertIn(f'code: {action}</strong> <a href="#{anchor}">'
                              f'{html.escape(story["headline"])}</a>', page)
            build_site(root / 'public', root / 'site')
            (root / 'empty').mkdir()
            build_site(root / 'empty', root / 'rebuilt', prior_history=root / 'site/history.json')
            self.assertEqual((root / 'site/reports/2026-10-13.html').read_text(),
                             (root / 'rebuilt/reports/2026-10-13.html').read_text())

    def test_integrity_published_digest_rejects_tampering_and_preserves_exact_bytes(self):
        import hashlib
        from datetime import date

        import build_site as site
        from agent_runner.semantic_repairs import daily_semantic_review
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import Judge, PatchProvider, make_run

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            daily_semantic_review(run, root / 'jev-review',
                                  judge=Judge(headline), repair_provider=PatchProvider())
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            prepare_publication(root, root / 'input.json', root / 'public', date(2026, 9, 30))
            sidecar = root / 'public/2026-09-30.json'
            markdown = sidecar.with_suffix('.md')
            valid_bytes = markdown.read_bytes()
            entry = site._entry_from_sidecar(sidecar)
            self.assertEqual(entry.integrity['decision'], 'repaired_applied')
            build_site(root / 'public', root / 'site')
            history = root / 'site/history.json'
            valid_history = json.loads(history.read_text())
            markdown.write_bytes(b'Unrelated publication text.\n')
            with self.assertRaisesRegex(ValueError, 'published artifact does not match Markdown'):
                site._entry_from_sidecar(sidecar)
            with self.assertRaisesRegex(ValueError, 'published artifact does not match Markdown'):
                build_site(root / 'public', root / 'replacement', prior_history=history, replace_existing=True)
            markdown.write_bytes(valid_bytes)
            tampered = json.loads(history.read_text())
            tampered['entries'][0]['markdown'] = 'Unrelated restored history text.'
            history.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError, 'published artifact does not match Markdown'):
                site._load_history(history)
            history.write_text(json.dumps(valid_history))
            (root / 'empty').mkdir()
            build_site(root / 'empty', root / 'rebuilt', prior_history=history)
            self.assertEqual(valid_history, json.loads((root / 'rebuilt/history.json').read_text()))
            build_site(root / 'public', root / 'replacement', prior_history=history, replace_existing=True)
            self.assertEqual(valid_history, json.loads((root / 'replacement/history.json').read_text()))
            # Exact UTF-8 identity must survive CRLF sidecars and JSON history;
            # universal-newline text reads would incorrectly reject this input.
            crlf_bytes = valid_bytes.replace(b'\n', b'\r\n')
            payload = json.loads(sidecar.read_text())
            for artifact in payload['integrity']['artifacts']:
                if artifact['id'] in {'published', 'candidate'}:
                    artifact['sha256'] = hashlib.sha256(crlf_bytes).hexdigest()
            sidecar.write_text(json.dumps(payload))
            markdown.write_bytes(crlf_bytes)
            exact = site._entry_from_sidecar(sidecar)
            self.assertEqual(exact.markdown.encode('utf-8'), crlf_bytes)
            history.write_text(json.dumps(site._history_payload([exact])))
            self.assertEqual(site._load_history(history)[0].markdown.encode('utf-8'), crlf_bytes)


# Single entries copied unchanged from the live site's history.json. 2026-10-13
# retained its original after a withheld repair; 2026-10-12 published a repair.
LIVE_FIXTURES = {
    "2026-10-12": "203611f0908c5673438bcc172a9c4402dd246e7154e2d62da9c771ab3be3758b",
    "2026-10-13": "8afd49495e035359f284aa770dea92943a70b071ccd4532846ad99c3242a146d",
}


def _live_entry(day: str) -> dict[str, object]:
    path = Path(__file__).resolve().parents[1] / f"fixtures/history-{day}.json.gz"
    compressed = path.read_bytes()
    if hashlib.sha256(compressed).hexdigest() != LIVE_FIXTURES[day]:
        raise ValueError(f"live history fixture digest mismatch: {day}")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError(f"live history fixture exceeds bound: {day}")
    payload = json.loads(raw)
    if payload["schema_version"] != 10 or [e["date"] for e in payload["entries"]] != [day]:
        raise ValueError(f"live history fixture shape changed: {day}")
    entry: dict[str, object] = payload["entries"][0]
    return entry


def _build_from_entries(root: Path, entries: list[dict[str, object]], schema_version: int = 10) -> Path:
    (root / "prior.json").write_text(json.dumps({"schema_version": schema_version, "entries": entries}))
    (root / "empty").mkdir()
    build_site(root / "empty", root / "site", prior_history=root / "prior.json")
    return root / "site"


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment))


def _between(page: str, start: str, end: str) -> str:
    begin = page.index(start)
    return page[begin:page.index(end, begin)]


class _LayoutScan(HTMLParser):
    """Record where each table sits: inside <details>, inside a scroll region, and its width."""

    def __init__(self) -> None:
        super().__init__()
        self.details = 0
        self.scroll: list[bool] = []
        self.tables: list[dict[str, object]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "details":
            self.details += 1
        elif tag == "div":
            self.scroll.append("table-scroll" in (dict(attrs).get("class") or ""))
        elif tag == "table":
            self.tables.append({"in_details": self.details > 0, "scrolls": any(self.scroll), "columns": 0})
        elif tag == "th" and self.tables and dict(attrs).get("scope") != "row":
            columns = self.tables[-1]["columns"]
            assert isinstance(columns, int)
            self.tables[-1]["columns"] = columns + 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "details":
            self.details -= 1
        elif tag == "div" and self.scroll:
            self.scroll.pop()


class ReaderFacingReportTests(unittest.TestCase):
    def test_live_retained_original_states_the_decision_in_one_sentence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-13")])
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")

            self.assertEqual(
                _text(_between(report, '<p class="decision">', "</p>")),
                "Published as originally generated. Automated review flagged 2 of 22 stories; "
                "a repair was attempted and withheld because one story was re-flagged.",
            )
            self.assertIn("Deterministic checker: 0 findings; all contract checks passed.", report)
            self.assertNotIn("READY · 0 findings", report)
            changed = _text(_between(report, 'id="what-changed"', 'id="stories-flagged"'))
            self.assertIn("Nothing. Review did not change the briefing", changed)
            flagged = [_text(item) for item in re.findall(
                r"<li>(.*?)</li>", _between(report, 'id="stories-flagged"', "</section>"))]
            self.assertEqual(flagged, [
                "GitHub Copilot adds Gemini 4 Argon and expands enterprise controls and metrics: "
                "one of four sources judged off-topic; removal proposed, not published",
                "Federal agents search Rep. Ilhan Omar's home in immigration probe; pro-Trump ad "
                "spending questioned: unrelated events grouped as one story; repair attempted, "
                "re-flagged, not published",
            ])
            # Section order: decision, changes, flags, exclusion log, sources, operator records.
            order = [report.index(marker) for marker in (
                'class="decision"', 'id="what-changed"', 'id="stories-flagged"',
                'id="excluded-topics"', 'id="sources"', 'id="operator-title"')]
            self.assertEqual(order, sorted(order))

    def test_live_reader_sections_use_words_and_operator_records_keep_numbers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-13")])
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")

            operator = report.index('id="operator-title"')
            self.assertNotIn("0.78 / 0.65", report[:operator])
            self.assertIn("0.78 / 0.65 (confirmed)", report[operator:])
            self.assertNotIn("typesafe/jev-1.13", report[:operator])
            self.assertIn("Judge: typesafe/jev-1.13", report[operator:])
            for title in ("Decision record", "Action ledger", "Review statistics", "Provenance and costs"):
                self.assertIn(f"<details><summary>{title}</summary>", report[operator:])
            scan = _LayoutScan()
            scan.feed(report)
            self.assertTrue(scan.tables)
            self.assertTrue(all(table["in_details"] for table in scan.tables), scan.tables)
            self.assertIn("Initial review statistics", report[operator:])

    def test_live_chip_panel_and_report_share_one_coverage_account(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-13")])
            index = (site / "index.html").read_text(encoding="utf-8")
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")

            label = "Some sources returned nothing usable"
            chip = _text(_between(index, '<p class="status-chip', "</p>"))
            self.assertEqual(chip, f"⚠ Passed publication checks · 2 stories flagged in review · {label}")
            panel = _between(index, '<section class="run-notes"', "</section>")
            self.assertIn(f'<p class="coverage-state coverage-empty">{label}.</p>', panel)
            self.assertIn(f"<li>Coverage: {label}</li>", panel)
            self.assertNotIn("Coverage: <code>degraded</code>", panel)
            self.assertNotIn("Source issues", panel)
            self.assertNotIn("Undated source drops", panel)
            sources = _between(report, 'id="sources"', "<details>")
            self.assertIn(f"Sources: <a href=\"#sources\">{label}</a>", report)
            for name, reason in (
                ("Claude Code Releases</strong> (RSS feed)", "had nothing new in this day's window"),
                ("MCP Servers Releases</strong> (RSS feed)", "had nothing new in this day's window"),
                ("“claude”</strong> (Hacker News search)", "had every item filtered out"),
                ("“cursor”</strong> (Hacker News search)", "had every item filtered out"),
                ("“codex”</strong> (Hacker News search)", "had every item filtered out"),
            ):
                row = f"<li><strong>{name} {html.escape(reason)}</li>"
                self.assertIn(row, panel)
                self.assertIn(row, sources)
            self.assertNotIn("Verified source health", report)
            self.assertNotIn("Corpus health", report)
            # Page rendering only: the stored footer keeps its checked wording.
            stored = json.loads((site / "history.json").read_text())["entries"][0]["markdown"]
            self.assertIn("- Coverage: `degraded`", stored)
            self.assertIn("**Source issues**", stored)

    def test_live_applied_repair_shows_the_published_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-12")])
            report = (site / "reports/2026-10-12.html").read_text(encoding="utf-8")
            index = (site / "index.html").read_text(encoding="utf-8")

            self.assertEqual(
                _text(_between(report, '<p class="decision">', "</p>")),
                "Published with automated repairs to one story. Automated review flagged 1 of 22 "
                "stories; the repair passed a second review.",
            )
            changed = _between(report, 'id="what-changed"', 'id="stories-flagged"')
            self.assertIn("<h3>Federal agents search Rep. Ilhan Omar&#x27;s home in immigration probe</h3>",
                          changed)
            self.assertIn("Source removed: <del><a href=", changed)
            self.assertIn('<p class="diff-label">After (published)</p>', changed)
            self.assertIn("<del>", changed)
            self.assertIn("<ins>", changed)
            self.assertIn("one of six sources judged off-topic; repair published", _text(report))
            # A fetch failure outranks quiet sources in the shared three-state wording.
            self.assertIn("· Some sources failed</a></p>", index)
            self.assertIn("<li><strong>Google DeepMind</strong> (RSS feed) could not be fetched</li>", index)
            self.assertIn("<li><strong>Google DeepMind</strong> (RSS feed) could not be fetched</li>", report)
            self.assertIn("Published after automated repair", index)
            scan = _LayoutScan()
            scan.feed(report)
            self.assertTrue(all(table["in_details"] for table in scan.tables), scan.tables)

    def test_day_without_review_or_verified_health_still_renders(self) -> None:
        entry = _live_entry("2026-10-13")
        entry["semantic_audit"] = None
        entry["integrity"] = None
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [entry])
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")
            index = (site / "index.html").read_text(encoding="utf-8")

            self.assertEqual(_text(_between(report, '<p class="decision">', "</p>")),
                             "Published. No automated story review was recorded for this day.")
            self.assertIn("No automated story review was recorded for this day.</p></section>", report)
            self.assertIn("Historical publication decision and phase records unavailable", report)
            label = "Some sources had problems"
            self.assertIn(f"⚠ Passed publication checks · {label}</a></p>", index)
            panel = _between(index, '<section class="run-notes"', "</section>")
            self.assertIn(f"<li>Coverage: {label}</li>", panel)
            # Without verified reasons, the footer's own source lists stay visible.
            self.assertIn("<strong>Source issues</strong>", panel)
            self.assertIn("<strong>“claude”</strong> (Hacker News search) reason not recorded", report)

    def test_failed_and_withheld_days_state_the_decision(self) -> None:
        from datetime import date

        from prepare_publication import prepare_publication

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            chain = root / "run"
            chain.mkdir()
            (chain / "fallback-log.json").write_text(json.dumps({
                "schema_version": 2, "status": "failed", "model_chain": ["google/gemini-3.7-flash"],
                "attempts": [{"model": "google/gemini-3.7-flash", "status": "failed",
                              "failure": FailureRecord("invalid_request", status_code=400).payload(),
                              "failure_reason": "private"}],
            }))
            prepare_publication(chain, root / "missing.json", root / "briefings", date(2026, 9, 6))
            (root / "briefings/2026-09-07.md").write_text("withheld preview", encoding="utf-8")
            BuildSiteTests._write_sidecar(
                root / "briefings", date="2026-09-07", disposition="review_required",
                findings=[BuildSiteTests._finding("unsupported_figure", "fig check"),
                          BuildSiteTests._finding("claim_exceeds_evidence", "too much detail")],
            )
            build_site(root / "briefings", root / "site")

            failed = (root / "site/reports/2026-09-06.html").read_text(encoding="utf-8")
            self.assertEqual(_text(_between(failed, '<p class="decision">', "</p>")),
                             "Not published. Every model tried failed to produce a briefing that "
                             "passed the checks.")
            self.assertIn("Every model in the fallback chain failed", failed)
            self.assertNotIn('id="stories-flagged"', failed)
            withheld = (root / "site/reports/2026-09-07.html").read_text(encoding="utf-8")
            self.assertEqual(_text(_between(withheld, '<p class="decision">', "</p>")),
                             "Withheld for review. The deterministic checker recorded 2 findings that "
                             "need a person to review them before publication.")
            self.assertIn("Deterministic checker: 2 findings require review.", withheld)
            self.assertIn("<h2>Checker findings</h2>", withheld)
            self.assertNotIn("withheld preview", withheld)

    def test_partial_publication_states_both_outcomes(self) -> None:
        import build_site as site
        from prepare_publication import prepare_publication
        from tests.test_semantic_repairs import split_review

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _run, _audit, first, second = split_review(root)
            (root / "fallback-log.json").write_text(json.dumps({"status": "ready", "selected_run_dir": "run"}))
            record = prepare_publication(root, root / "input.json", root / "public", date(2026, 10, 13))
            entry = site._entry_from_payload(record.payload(), source="fixture")
            self.assertEqual(entry.integrity["decision"], "partial_applied")
            page = site._render_report(entry, [entry])

            self.assertEqual(
                _text(_between(page, '<p class="decision">', "</p>")),
                "Published with automated repairs to one story. Automated review flagged 2 of 22 "
                "stories; one repair passed a second review, and one was withheld because one story "
                "was re-flagged.",
            )
            flagged = _text(_between(page, 'id="stories-flagged"', "</section>"))
            self.assertIn(f"{first['headline']}: one of two sources judged off-topic; repair published", flagged)
            self.assertIn(f"{second['headline']}: a claim its sources do not support; repair attempted, "
                          "re-flagged, not published", flagged)
            # The withheld story kept its original, still-flagged prose.
            self.assertIn("· 1 story flagged in review ·", site._status_chip(entry))

    def test_undated_drops_from_a_working_source_are_not_called_empty(self) -> None:
        from build_site import _source_coverage

        def row(source_id: str, status: str, reason: str | None, parsed: int, dated: int,
                category: str = "ai_tech") -> dict[str, object]:
            return {"source_type": "rss", "source_id": source_id, "category": category, "status": status,
                    "reason": reason, "parsed_entries": parsed, "dated_entries": dated,
                    "retained_entries": dated}

        def coverage(rows: list[dict[str, object]]) -> tuple[str, str]:
            integrity = {"corpus_health": [row("Healthy feed", "ok", None, 10, 10), *rows]}
            entry = BriefingEntry(day=date(2026, 10, 13), disposition="ready", findings_count=0, findings=(),
                                  degraded_sources=(), markdown="", integrity=integrity)
            result = _source_coverage(entry)
            return result.state, result.label

        undated = row("Mostly dated feed", "ok", "undated_entries", 12, 10)
        quiet = [row(f"Quiet feed {n}", "quiet", "no_window_entries", 5, 5, category="dev") for n in range(4)]
        failed = row("Broken feed", "error", "fetch_error", 0, 0)
        # A source that returned usable dated items is not "returned nothing usable".
        self.assertEqual(coverage([undated]), ("partial", "Some sources dropped undated items"))
        self.assertEqual(coverage([undated, *quiet]), ("empty", "Some sources returned nothing usable"))
        self.assertEqual(coverage([*quiet, undated, failed]), ("failed", "Some sources failed"))
        self.assertEqual(coverage([]), ("healthy", "All sources healthy"))

    def test_every_reason_code_has_reader_wording(self) -> None:
        from build_site import READER_REASON_PHRASES
        from publication_schema import INTEGRITY_REASON_MESSAGES

        self.assertEqual(set(READER_REASON_PHRASES), set(INTEGRITY_REASON_MESSAGES))

    def test_pages_fit_phone_width_in_both_themes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-13")])
            pages = [(site / name).read_text(encoding="utf-8")
                     for name in ("index.html", "reports/2026-10-13.html")]
            for page in pages:
                self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', page)
                self.assertIn("@media (max-width: 40rem)", page)
                self.assertIn("@media (prefers-color-scheme: dark)", page)
                self.assertIn("overflow-wrap: anywhere", page)
                self.assertNotIn("<script", page)
                self.assertNotIn('rel="stylesheet"', page)
            self.assertIn(".table-scroll { overflow-x: auto; }", pages[1])
            # Wide tables scroll inside their own region; only narrow ones may sit in the flow.
            scan = _LayoutScan()
            scan.feed(pages[1])
            for table in scan.tables:
                columns = table["columns"]
                assert isinstance(columns, int)
                self.assertTrue(table["scrolls"] or columns <= 3, table)
            # The chip wraps as a rounded box rather than a pill when it runs to two lines.
            self.assertIn("p.status-chip { border-radius: .9rem;", pages[0])


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _ledger_strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for key, item in value.items() for s in [key, *_ledger_strings(item)]]
    if isinstance(value, list):
        return [s for item in value for s in _ledger_strings(item)]
    return []


def _legacy_ready_entry(day: str) -> dict[str, object]:
    """A ready entry from before provenance, Jev review, and integrity records existed."""
    entry = _live_entry("2026-10-13")
    return {**entry, "date": day, "provenance": None, "semantic_audit": None, "integrity": None,
            "advisory_findings": []}


class MetricsLedgerTests(unittest.TestCase):
    def _build(self, root: Path, name: str, briefings: Path, **kwargs: object) -> Path:
        output = root / name
        build_site(briefings, output, **kwargs)
        return output

    def test_ledger_rebuild_is_idempotent_and_outlives_history_truncation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entries = [_legacy_ready_entry(f"2026-09-{day:02d}") for day in range(1, 10)]
            (root / "prior.json").write_text(json.dumps({"schema_version": 10, "entries": entries}))
            (root / "empty").mkdir()
            first = self._build(root, "first", root / "empty", prior_history=root / "prior.json",
                                allow_empty_metrics=True)
            history = json.loads((first / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(len(history["entries"]), 7)
            ledger = json.loads((first / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual([row["date"] for row in ledger["rows"]],
                             [f"2026-09-{day:02d}" for day in range(1, 10)])

            second = self._build(root, "second", root / "empty", prior_history=first / "history.json",
                                 prior_metrics=first / "metrics.json")
            third = self._build(root, "third", root / "empty", prior_history=second / "history.json",
                                prior_metrics=second / "metrics.json")
            self.assertEqual((first / "metrics.json").read_bytes(), (second / "metrics.json").read_bytes())
            self.assertEqual((second / "metrics.json").read_bytes(), (third / "metrics.json").read_bytes())
            self.assertEqual((second / "metrics.html").read_bytes(), (third / "metrics.html").read_bytes())

    def test_lower_rank_day_never_clobbers_a_ready_row(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            initial.mkdir()
            (initial / "2026-08-20.md").write_text("validated briefing", encoding="utf-8")
            BuildSiteTests._write_sidecar(initial, date="2026-08-20", disposition="ready")
            seeded = self._build(root, "seeded", initial)

            # The date has left history (no prior history passed); only the ledger remembers it.
            failed = root / "failed"
            failed.mkdir()
            BuildSiteTests._write_sidecar(failed, date="2026-08-20", disposition="blocked")
            kept = self._build(root, "kept", failed, prior_metrics=seeded / "metrics.json",
                               allow_empty_history=True, replace_existing=True)
            rows = json.loads((kept / "metrics.json").read_text(encoding="utf-8"))["rows"]
            self.assertEqual([(row["date"], row["disposition"]) for row in rows], [("2026-08-20", "ready")])

            # An equal-rank rerun replaces the row; an explicit republish of a page replaces it too.
            review = root / "review"
            review.mkdir()
            (review / "2026-08-20.md").write_text("review preview", encoding="utf-8")
            BuildSiteTests._write_sidecar(
                review, date="2026-08-20", disposition="review_required",
                findings=[BuildSiteTests._finding("unsupported_figure", "Verify figure.")],
            )
            scheduled = self._build(root, "scheduled", review, prior_metrics=seeded / "metrics.json",
                                    allow_empty_history=True)
            self.assertEqual(
                json.loads((scheduled / "metrics.json").read_text(encoding="utf-8"))["rows"][0]["disposition"],
                "ready",
            )
            manual = self._build(root, "manual", review, prior_metrics=seeded / "metrics.json",
                                 allow_empty_history=True, replace_existing=True)
            self.assertEqual(
                json.loads((manual / "metrics.json").read_text(encoding="utf-8"))["rows"][0]["disposition"],
                "review_required",
            )

            # An equal-rank scheduled rerun replaces the row without --replace-existing.
            rerun = root / "rerun"
            rerun.mkdir()
            (rerun / "2026-08-20.md").write_text("second review preview", encoding="utf-8")
            BuildSiteTests._write_sidecar(
                rerun, date="2026-08-20", disposition="review_required",
                findings=[BuildSiteTests._finding("unsupported_figure", "Verify figure."),
                          BuildSiteTests._finding("unsupported_quotation", "Verify quotation.")],
            )
            equal = self._build(root, "equal", rerun, prior_metrics=manual / "metrics.json",
                                allow_empty_history=True)
            row = json.loads((equal / "metrics.json").read_text(encoding="utf-8"))["rows"][0]
            self.assertEqual((row["disposition"], row["findings_count"]), ("review_required", 2))

    def test_build_refuses_to_drop_the_ledger_without_optin(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "empty").mkdir()
            (root / "prior.json").write_text(json.dumps({"schema_version": 10, "entries": []}))
            with self.assertRaisesRegex(ValueError, "allow_empty_metrics"):
                _build_site(root / "empty", root / "site", prior_history=root / "prior.json")
            # Recovering history does not also license restarting the ledger.
            with self.assertRaisesRegex(ValueError, "allow_empty_metrics"):
                _build_site(root / "empty", root / "site", allow_empty_history=True)
            self.assertFalse((root / "site" / "index.html").exists())

            # A row that is valid except for one extra text field is refused.
            seeded = self._build(root, "seeded", root / "empty", prior_history=root / "prior.json",
                                 allow_empty_metrics=True)
            (root / "valid").mkdir()
            valid_site = _build_from_entries(root / "valid", [_legacy_ready_entry("2026-08-20")])
            valid = json.loads((valid_site / "metrics.json").read_text(encoding="utf-8"))
            site_metrics.parse_ledger(valid)
            valid["rows"][0]["headline"] = "Injected text"
            (root / "bad.json").write_text(json.dumps(valid))
            with self.assertRaisesRegex(ValueError, "exactly"):
                _build_site(root / "empty", root / "site", prior_history=seeded / "history.json",
                            prior_metrics=root / "bad.json")
            self.assertFalse((root / "site" / "index.html").exists())

    def test_unusual_model_identifier_degrades_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            bracketed, spaced = _live_entry("2026-10-13"), _live_entry("2026-10-12")
            for entry, model in ((bracketed, "anthropic/claude-opus-4[1m]"), (spaced, "free text model")):
                provenance = entry["provenance"]
                assert isinstance(provenance, dict)
                entry["provenance"] = {**provenance, "model": model}
            site = _build_from_entries(Path(directory), [bracketed, spaced])
            rows = {row["date"]: row for row in json.loads(
                (site / "metrics.json").read_text(encoding="utf-8"))["rows"]}
            self.assertEqual(rows["2026-10-13"]["model"], "anthropic/claude-opus-4[1m]")
            self.assertIsNone(rows["2026-10-12"]["model"])
            self.assertTrue((site / "index.html").exists())

    def test_ledger_the_next_build_would_refuse_fails_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = _legacy_ready_entry("2026-09-01")
            # History accepts any non-negative count; the ledger caps it at 100000.
            entry["findings_count"] = 100_001
            (root / "prior.json").write_text(json.dumps({"schema_version": 10, "entries": [entry]}))
            (root / "empty").mkdir()
            with self.assertRaisesRegex(ValueError, "metrics count"):
                build_site(root / "empty", root / "site", prior_history=root / "prior.json",
                           allow_empty_metrics=True)
            self.assertFalse((root / "site" / "metrics.json").exists())
            self.assertFalse((root / "site" / "index.html").exists())

    def test_legacy_entry_records_null_and_renders_unknown_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_legacy_ready_entry("2026-09-01")])
            row = json.loads((site / "metrics.json").read_text(encoding="utf-8"))["rows"][0]
            for field in ("provider", "model", "attempt_index", "fallback_used", "review_status",
                          "repair_decision", "generation_cost_usd", "generation_unknown_cost_calls",
                          "review_stage_cost_usd", "phase_costs_usd",
                          "unknown_cost_calls", "flags_raised", "flags_confirmed"):
                self.assertIsNone(row[field], field)
            page = (site / "metrics.html").read_text(encoding="utf-8")
            text = _text(page)
            self.assertIn("unknown", text)
            self.assertIn("Known generation spend across recorded days: unknown; days with unknown "
                          "generation cost: 1.", text)
            self.assertIn("follow-up spend across recorded days: unknown.", text)
            self.assertIn("Days with unknown or partly unknown cost, marked below the axis: 1.", text)
            self.assertIn("2026-09-01: cost unknown", text)
            self.assertNotIn("$0.0000", text)
            self.assertIn("Days with no review record: 1.", text)

    def test_generation_cost_reaches_the_ledger_the_page_and_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = copy.deepcopy(_live_entry("2026-10-13"))
            integrity = entry["integrity"]
            assert isinstance(integrity, dict)
            integrity["costs"].insert(
                0, {"phase": "generation", "reported_cost_usd": 0.1234, "unknown_cost_calls": 1})
            site = _build_from_entries(Path(directory), [entry, _legacy_ready_entry("2026-09-01")])
            rows = {row["date"]: row for row in json.loads(
                (site / "metrics.json").read_text(encoding="utf-8"))["rows"]}
            self.assertEqual(rows["2026-10-13"]["generation_cost_usd"], 0.1234)
            self.assertEqual(rows["2026-10-13"]["generation_unknown_cost_calls"], 1)
            self.assertIsNone(rows["2026-09-01"]["generation_cost_usd"])
            # The review-stage figures are untouched by the new phase.
            self.assertEqual(set(rows["2026-10-13"]["phase_costs_usd"]), set(site_metrics.COST_PHASES))
            page = (site / "metrics.html").read_text(encoding="utf-8")
            text = _text(page)
            self.assertIn("Known generation spend across recorded days: $0.1234; days with unknown "
                          "generation cost: 1.", text)
            self.assertIn("including attempts that did not publish", text)
            self.assertIn('class="chart-bar cost-generation"', page)
            self.assertIn('class="chart-bar cost-review"', page)
            self.assertIn("2026-10-13: generation $0.1234 known, plus calls of unknown cost; review stage $", text)
            self.assertIn("2026-09-01: cost unknown", text)
            report = _text((site / "reports/2026-10-13.html").read_text(encoding="utf-8"))
            self.assertIn("generation (every attempt in the fallback chain): reported $0.1234; "
                          "unknown billing calls: 1.", report)

    def test_generation_model_time_and_cost_close_the_briefing_and_lead_the_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            entry = copy.deepcopy(_live_entry("2026-10-13"))
            integrity = entry["integrity"]
            assert isinstance(integrity, dict)
            integrity["costs"].insert(
                0, {"phase": "generation", "reported_cost_usd": 0.1234, "unknown_cost_calls": 0})
            integrity["generation_timing"] = {"started_at": "2026-10-13T13:30:05+00:00",
                                              "completed_at": "2026-10-13T13:36:17+00:00"}
            unrecorded = _live_entry("2026-10-12")  # the fixture predates timing and generation cost
            site = _build_from_entries(Path(directory), [entry, unrecorded, _legacy_ready_entry("2026-09-01")])
            expected = ("Generated by Tencent HY3 via OpenRouter in 6 min 12 s. Model charges: $0.1888 "
                        "(generation $0.1234, review $0.0654).")
            page = (site / "index.html").read_text(encoding="utf-8")
            footer = f'<p class="muted generation-summary">{expected}</p>'
            self.assertIn(footer, page)
            # The line is the last thing in the briefing, after the run notes.
            self.assertLess(page.index("</section>", page.index('<section class="run-notes"')), page.index(footer))
            self.assertLess(page.index(footer), page.index("</article>"))
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")
            facts = f'<p class="generation-facts">{expected}</p>'
            self.assertIn(facts, report)
            summary = _between(report, '<section class="integrity-summary"', "</section>")
            self.assertIn(facts, summary)
            self.assertIn("Generation timing (every attempt in the fallback chain): 2026-10-13T13:30:05+00:00 — "
                          "2026-10-13T13:36:17+00:00, 6 min 12 s.", _text(report))
            # A day whose record carries neither says so instead of showing zero.
            older = (site / "2026-10-12.html").read_text(encoding="utf-8")
            self.assertIn("Generated by Tencent HY3 via OpenRouter. Generation time was not recorded. "
                          "Model charges: $", _text(older))
            self.assertIn("for review; the generation charge was not recorded.", _text(older))
            self.assertIn("Generation timing: not recorded.",
                          _text((site / "reports/2026-10-12.html").read_text(encoding="utf-8")))
            # An entry without any record shows no line at all.
            legacy = (site / "2026-09-01.html").read_text(encoding="utf-8")
            self.assertNotIn('<p class="muted generation-summary">', legacy)
            self.assertNotIn('<p class="generation-facts">',
                             (site / "reports/2026-09-01.html").read_text(encoding="utf-8"))

    def test_generation_summary_states_uncertain_charges_and_failed_days(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            entry = copy.deepcopy(_live_entry("2026-10-13"))
            integrity = entry["integrity"]
            assert isinstance(integrity, dict)
            integrity["costs"].insert(
                0, {"phase": "generation", "reported_cost_usd": 0.1, "unknown_cost_calls": 2})
            # A repair by a different model is named; the same model is not repeated.
            integrity["repair_generation"] = {**integrity["generation"], "model": "deepseek/deepseek-v4-flash-0731"}
            blocked = {
                "date": "2026-10-11", "disposition": "blocked", "findings_count": 0, "findings": [],
                "degraded_sources": [], "repair_actions": [], "advisory_findings": [], "provenance": None,
                "semantic_audit": None, "markdown": None,
                "generation_failures": [{"model": "tencent/hy3", "reason": "generation_failed"}],
                "integrity": {**blank_integrity("unpublished", ["no_result"]),
                              "costs": [{"phase": "generation", "reported_cost_usd": None,
                                         "unknown_cost_calls": None}],
                              "generation_timing": {"started_at": "2026-10-11T13:30:00+00:00",
                                                    "completed_at": "2026-10-11T14:31:00+00:00"}},
            }
            add_phase(blocked["integrity"], "publication_verification", "failed", ["no_result"])
            site = _build_from_entries(root, [entry, blocked])
            page = _text((site / "index.html").read_text(encoding="utf-8"))
            self.assertIn("Generated by Tencent HY3 via OpenRouter, with repairs by DeepSeek V4 Flash. "
                          "Generation time was not recorded. Model charges: $0.1654 (generation $0.1000, "
                          "review $0.0654), plus 2 calls of unknown cost.", page)
            report = _text((site / "reports/2026-10-11.html").read_text(encoding="utf-8"))
            self.assertIn("No model produced this briefing; the attempts took 1 h 1 min. "
                          "Model charges: $0.0000, with the generation charge not reported.", report)
            self.assertNotIn('<p class="muted generation-summary">',
                             (site / "2026-10-11.html").read_text(encoding="utf-8"))

    def test_ledger_at_any_other_schema_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            seeded = _build_from_entries(root, [_live_entry("2026-10-13")])
            current = json.loads((seeded / "metrics.json").read_text(encoding="utf-8"))
            self.assertEqual(current["schema_version"], site_metrics.LEDGER_SCHEMA_VERSION)
            for version in (1, 3, "2", None):
                with self.subTest(schema_version=version), self.assertRaisesRegex(ValueError, "schema_version"):
                    site_metrics.parse_ledger({**current, "schema_version": version})
            # A refused prior ledger fails the build before anything is written,
            # so an old deployed ledger is replaced by a rebuild, never upgraded.
            (root / "stale.json").write_text(json.dumps({**current, "schema_version": 1}), encoding="utf-8")
            (root / "none").mkdir()
            with self.assertRaisesRegex(ValueError, "schema_version"):
                build_site(root / "none", root / "rebuilt", prior_history=seeded / "history.json",
                           prior_metrics=root / "stale.json")
            self.assertFalse((root / "rebuilt").exists())

    def test_ledger_rows_carry_no_feed_or_model_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            live = [_live_entry("2026-10-13"), _live_entry("2026-10-12")]
            site = _build_from_entries(Path(directory), live)
            raw = (site / "metrics.json").read_text(encoding="utf-8")
            ledger = json.loads(raw)
            enums = {"ready", "complete", "partial", "failed", "original_retained", "repaired_applied",
                     "partial_applied", "unpublished", *site_metrics.ROW_FIELDS, *site_metrics.COST_PHASES,
                     "schema_version", "rows"}
            identifiers = {"openrouter", "tencent/hy3"}
            for value in _ledger_strings(ledger):
                self.assertTrue(value in enums or value in identifiers or _ISO_DATE.fullmatch(value), value)
            self.assertNotIn("http", raw)
            for entry in live:
                markdown = entry["markdown"]
                assert isinstance(markdown, str)
                for line in markdown.splitlines():
                    if len(line.strip()) > 24:
                        self.assertNotIn(line.strip(), raw)

    def test_every_svg_string_is_escaped_and_well_formed(self) -> None:
        hostile = '</text><script>alert("x")</script>&'
        chart = site_metrics._hbar_chart(hostile, [(hostile, 1, "chart-bar")], 1)
        self.assertNotIn("<script", chart)
        self.assertIn("&lt;/text&gt;&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;&amp;", chart)
        ElementTree.fromstring(chart)
        self.assertIsNone(site_metrics._identifier(hostile))
        with tempfile.TemporaryDirectory() as directory:
            entries = [_live_entry("2026-10-13"), _live_entry("2026-10-12"), _legacy_ready_entry("2026-09-01")]
            site = _build_from_entries(Path(directory), entries)
            page = (site / "metrics.html").read_text(encoding="utf-8")
            charts = re.findall(r"<svg\b.*?</svg>", page, flags=re.S)
            self.assertGreaterEqual(len(charts), 4)
            for chart in charts:
                ElementTree.fromstring(chart)
                self.assertNotIn("<script", chart)

    def test_metrics_page_fits_phone_width_and_is_linked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            site = _build_from_entries(Path(directory), [_live_entry("2026-10-13"), _live_entry("2026-10-12")])
            page = (site / "metrics.html").read_text(encoding="utf-8")
            self.assertIn('<meta name="viewport" content="width=device-width, initial-scale=1">', page)
            self.assertIn("@media (max-width: 40rem)", page)
            self.assertIn("@media (prefers-color-scheme: dark)", page)
            self.assertIn(".metrics-chart { display: block; height: auto;", page)
            self.assertIn("max-width: 100%; width: 100%; }", page)
            self.assertNotIn("<script", page)
            self.assertNotIn('rel="stylesheet"', page)
            for chart in re.findall(r"<svg\b[^>]*>", page):
                self.assertIn('class="metrics-chart"', chart)
                self.assertIn(f'viewBox="0 0 {site_metrics.CHART_WIDTH} ', chart)
            scan = _LayoutScan()
            scan.feed(page)
            for table in scan.tables:
                columns = table["columns"]
                assert isinstance(columns, int)
                self.assertTrue(table["scrolls"] or columns <= 3, table)
            text = _text(page)
            self.assertIn("operational summaries of the daily integrity records", text)
            self.assertIn("not an independent assessment, a grounding rate, or a quality measurement", text)
            index = (site / "index.html").read_text(encoding="utf-8")
            self.assertIn('<a href="metrics.html">Operational metrics</a>', index)
            self.assertIn('<a href="metrics.html">operational metrics</a> page', _between(
                index, 'id="run-notes"', "</section>"))
            report = (site / "reports/2026-10-13.html").read_text(encoding="utf-8")
            self.assertIn('<a href="../metrics.html">Operational metrics</a>', report)


if __name__ == "__main__":
    unittest.main()
