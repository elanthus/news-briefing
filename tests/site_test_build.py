"""Opt-in static-site tests; run with ``python -m unittest tests.site_test_build``."""

from __future__ import annotations

import argparse
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from agent_runner.output import render_briefing
from build_site import (
    STYLE,
    ReviewFinding,
    _humanize_corpus_health,
    _parse_canonical_date,
    _render_markdown,
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
    exercise the ordinary path unchanged.
    """
    if "allow_empty_history" not in kwargs and "prior_history" not in kwargs:
        kwargs["allow_empty_history"] = True
    return _build_site(*args, **kwargs)


class BuildSiteTests(unittest.TestCase):
    def test_exclude_date_parser_requires_canonical_calendar_date(self) -> None:
        self.assertEqual(_parse_canonical_date("2026-08-15").isoformat(), "2026-08-15")
        for value in ("20260815", "2026-W33-6"):
            with self.subTest(value=value), self.assertRaisesRegex(
                argparse.ArgumentTypeError, "canonical YYYY-MM-DD"
            ):
                _parse_canonical_date(value)

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

            with patch.object(
                sys,
                "argv",
                ["build_site.py", str(briefings), str(output), "--allow-empty-history"],
            ):
                self.assertEqual(build_site_main(), 0)
            self.assertTrue((output / "index.html").exists())

    def test_review_panel_pre_uses_the_dark_mode_safe_translucent_gray_fill(self) -> None:
        """A pure-white translucent fill (#fff8) stays light in dark mode, while
        `color-scheme: light dark` flips foreground text to white — leaving
        redacted-destination disclosures pale-on-pale. The neutral gray fill
        (#8881) tracks the page background in both themes instead. Asserting the
        two load-bearing facts, not the full rule text, keeps the test from
        breaking on unrelated formatting changes to the stylesheet."""
        self.assertIn("background: #8881", STYLE)
        self.assertNotIn("#fff8", STYLE)

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
            self.assertIn("semantic faithfulness is not automatically assessed", page)

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
            _build_site(briefings, root / "site", allow_empty_history=True)
            self.assertTrue((root / "site" / "index.html").exists())

    def test_renders_latest_review_preview_on_index_with_detailed_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            output = root / "site"
            briefings.mkdir()
            (briefings / "2026-08-19.md").write_text("prior ready briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-19", disposition="ready")
            (briefings / "2026-08-20.md").write_text(
                '# UNPUBLISHED BRIEFING CANDIDATE\n\n'
                'This candidate requires review and was not written to the configured output path.\n'
                'Unknown citations are omitted and model-authored web destinations are redacted.\n\n'
                '## US Politics\n\n'
                '**Important detail** — Story summary [destination omitted; use citation refs] '
                'with [source](https://example.com/story).'
                '\n🔗 https://example.com/citation'
                '\n\n- First item\n\nBare link: https://example.com/bare'
                '\n\n[unsafe](javascript:alert(1))'
                '\n\n<script>alert("preview")</script>'
                '\n\n### Run outcome\n**Warnings**\n- duplicate public warning',
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
            self.assertIn('<strong aria-current="date">2026-08-20</strong>', index)
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
            self.assertIn("Review required · 2 findings", report)
            self.assertIn("WARN · evidence · unsupported figure:", report)
            self.assertIn("states &#x27;&lt;60&gt;&#x27;", report)
            self.assertIn("Verify the figure against the cited source", report)
            # The annotated preview renders on the report with findings attached
            # inline to their stories and the redaction disclosure intact.
            self.assertIn('<section class="review-story">', report)
            self.assertIn("Important detail", report)
            self.assertNotIn("UNPUBLISHED BRIEFING CANDIDATE", report)
            self.assertNotIn("duplicate public warning", report)
            self.assertIn("<details>", report)
            self.assertNotIn("<details open", report)
            self.assertIn("Click to see redacted information", report)
            self.assertNotIn("INLINE_REVIEW_", report)
            self.assertIn("https://model.example/claim", report)
            self.assertNotIn('href="https://model.example/claim"', report)
            self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", report)
            self.assertNotIn("<script>", report)
            self.assertNotIn('href="javascript:', report)
            self.assertIn("&lt;script&gt;alert(&quot;preview&quot;)&lt;/script&gt;", report)

            prior = (output / "2026-08-19.html").read_text(encoding="utf-8")
            self.assertIn('href="index.html">2026-08-20</a>', prior)
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

    def test_adjacent_flagged_stories_have_balanced_review_boxes(self) -> None:
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
            self.assertIn("First story&#x27; states &#x27;1&#x27;", report)
            self.assertIn("Second story&#x27; states &#x27;2&#x27;", report)

    def test_grouped_and_excluded_story_findings_render_inline(self) -> None:
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
            self.assertIn("ineligible citation", report)
            self.assertIn("repeats an item", report)

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

    def test_excluded_dates_are_removed_from_prior_history_and_generated_pages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initial = root / "initial"
            initial.mkdir()
            for day in ("2026-08-15", "2026-08-16", "2026-08-17"):
                (initial / f"{day}.md").write_text(
                    f"briefing {day}", encoding="utf-8"
                )
                self._write_sidecar(initial, date=day, disposition="ready")

            initial_site = root / "initial-site"
            build_site(initial, initial_site)
            self.assertTrue((initial_site / "2026-08-15.html").is_file())
            self.assertTrue((initial_site / "2026-08-16.html").is_file())

            current = root / "current"
            current.mkdir()
            output = initial_site
            build_site(
                current,
                output,
                prior_history=output / "history.json",
                exclude_dates={"2026-08-15", "2026-08-16"},
            )

            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [entry["date"] for entry in history["entries"]],
                ["2026-08-17"],
            )
            index = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("2026-08-15", index)
            self.assertNotIn("2026-08-16", index)
            self.assertFalse((output / "2026-08-15.html").exists())
            self.assertFalse((output / "2026-08-16.html").exists())
            self.assertFalse((output / "reports/2026-08-15.html").exists())
            self.assertFalse((output / "reports/2026-08-16.html").exists())

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

            self.assertIn("current briefing", (output / "index.html").read_text(encoding="utf-8"))
            self.assertIn("legacy live briefing", (output / "2026-08-19.html").read_text(encoding="utf-8"))
            page_18 = (output / "2026-08-18.html").read_text(encoding="utf-8")
            self.assertIn("did not pass automated checks", page_18)
            self.assertNotIn("dogfood preview", page_18)
            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            self.assertEqual(history["schema_version"], 4)
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

    def test_legacy_findings_without_anchors_render_inline(self) -> None:
        # Production preview.md predating story anchors, with v3 findings that
        # carry only section/headline context: the legacy subheading tracking
        # must still attach grouped and excluded findings inline.
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
            self.assertEqual(report.count('<section class="review-story">'), 2)
            self.assertNotIn('<section class="review-panel">', report)
            self.assertLess(report.index("AI story"), report.index("ineligible citation"))
            self.assertLess(report.index("Excluded story"), report.index("repeats an item"))

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
            self.assertEqual(history["schema_version"], 4)
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
            self.assertIn("Published after automated repair (1 action)", restored_page)
            restored_report = (second_site / "reports/2026-08-19.html").read_text(
                encoding="utf-8"
            )
            self.assertIn("Automated repair actions (1)", restored_report)
            self.assertIn("topics.US News[3]", restored_report)

    def test_anchor_based_matching_ignores_section_heading_format(self) -> None:
        markdown = (
            "## Renamed Section Container\n\n"
            "<!-- story: topics.AI News[0] -->\n"
            "**AI story** — Summary.\n"
            "🔗 https://example.com/ai\n\n"
            "<!-- story: topics.US News[0] -->\n"
            "**US story** — Summary.\n"
            "🔗 https://example.com/us\n"
        )
        findings = (
            ReviewFinding(
                level="WARN", check="unsupported_figure", domain="evidence",
                message="topics.AI News[0].summary states '42'",
                section="AI News", headline="AI story",
                path="topics.AI News[0]",
            ),
        )
        rendered, matched = _render_markdown(markdown, findings)
        self.assertEqual(matched, frozenset({0}))
        self.assertIn("review-story", rendered)
        self.assertIn("AI story", rendered)

    def test_anchor_matching_distinguishes_identical_headlines(self) -> None:
        markdown = (
            "## Section A\n\n"
            "<!-- story: topics.Section A[0] -->\n"
            "**Same headline** — Summary A.\n\n"
            "## Section B\n\n"
            "<!-- story: topics.Section B[0] -->\n"
            "**Same headline** — Summary B.\n"
        )
        findings = (
            ReviewFinding(
                level="WARN", check="unsupported_figure", domain="evidence",
                message="topics.Section B[0].summary states '99'",
                section="Section B", headline="Same headline",
                path="topics.Section B[0]",
            ),
        )
        rendered, matched = _render_markdown(markdown, findings)
        self.assertEqual(matched, frozenset({0}))
        self.assertIn("review-story", rendered)
        second_headline_pos = rendered.index("Summary B")
        review_pos = rendered.index("review-story")
        self.assertLess(review_pos, second_headline_pos)

    def test_finding_with_path_but_no_matching_anchor_is_unmatched(self) -> None:
        markdown = (
            "## US News\n\n"
            "**Some story** — Summary.\n"
        )
        findings = (
            ReviewFinding(
                level="WARN", check="unsupported_figure", domain="evidence",
                message="topics.AI News[0].summary states '42'",
                path="topics.AI News[0]",
            ),
        )
        rendered, matched = _render_markdown(markdown, findings)
        self.assertEqual(matched, frozenset())
        self.assertNotIn("review-story", rendered)

    def test_legacy_section_headline_matching_still_works(self) -> None:
        markdown = (
            "## US News\n\n"
            "<!-- story: topics.US News[0] -->\n"
            "**Legacy story** — Summary.\n"
        )
        findings = (
            ReviewFinding(
                level="WARN", check="unsupported_figure", domain="evidence",
                message="US News: 'Legacy story' states '42'",
                section="US News", headline="Legacy story",
            ),
        )
        rendered, matched = _render_markdown(markdown, findings)
        self.assertEqual(matched, frozenset({0}))
        self.assertIn("review-story", rendered)

    def test_status_chip_verified_for_clean_ready_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("clean briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            build_site(briefings, root / "site")

            index = (root / "site/index.html").read_text(encoding="utf-8")
            self.assertIn("status-chip", index)
            self.assertIn("✓", index)
            self.assertIn("Verified", index)
            self.assertIn("reports/2026-08-20.html", index)
            self.assertNotIn("Checker verdict:", index)
            self.assertNotIn("Corpus health:", index)

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
            self.assertIn("⚠", index)
            self.assertIn("automated repair", index)
            self.assertIn("2 actions", index)

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
            status = index.index("Published after automated repair", first_rule)
            corpus = index.index("Corpus window: start → end", status)
            second_rule = index.index("<hr>", corpus)
            news = index.index("<h2>AI/Tech</h2>", second_rule)
            self.assertLess(title, first_rule)
            self.assertLess(first_rule, status)
            self.assertLess(status, corpus)
            self.assertLess(corpus, second_rule)
            self.assertLess(second_rule, news)
            self.assertIn(".status-chip a { text-decoration: underline; }", index)

    def test_public_pages_reorder_current_and_archived_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            for day in ("2026-08-19", "2026-08-20"):
                (briefings / f"{day}.md").write_text(
                    f"# Daily Briefing — {day}\n\n"
                    "Corpus window: start → end\n\n"
                    "## US Politics\n\nPolitics story.\n\n"
                    "## US News\n\nUS story.\n\n"
                    "## AI/Tech\n\nAI story.\n\n"
                    "## World Events\n\nWorld story.\n\n"
                    "---\n\n### Excluded Topics (accountability log)\n\n"
                    "**US Politics**\nPolitics exclusion.\n\n"
                    "**US News**\nUS exclusion.\n\n"
                    "**AI Dev Tools**\nTool exclusion.\n\n"
                    "**AI Dev Practices**\nPractice exclusion.\n",
                    encoding="utf-8",
                )
                self._write_sidecar(briefings, date=day, disposition="ready")

            build_site(briefings, root / "site")

            def assert_order(rendered: str) -> None:
                self.assertLess(
                    rendered.index("<h2>AI/Tech</h2>"),
                    rendered.index("<h2>US News</h2>"),
                )
                self.assertLess(
                    rendered.index("<h2>World Events</h2>"),
                    rendered.index("<h2>US Politics</h2>"),
                )
                excluded = rendered.index("<h3>Excluded Topics (accountability log)</h3>")
                tools = rendered.index("<strong>AI Dev Tools</strong>", excluded)
                practices = rendered.index("<strong>AI Dev Practices</strong>", excluded)
                us_news = rendered.index("<strong>US News</strong>", excluded)
                politics = rendered.index("<strong>US Politics</strong>", excluded)
                self.assertLess(tools, practices)
                self.assertLess(practices, us_news)
                self.assertLess(us_news, politics)

            index = (root / "site/index.html").read_text(encoding="utf-8")
            archive = (root / "site/2026-08-19.html").read_text(encoding="utf-8")
            assert_order(index)
            assert_order(archive)

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
            status = index.index("Verified", first_rule)
            corpus = index.index("Corpus window: start → end", status)
            second_rule = index.index("<hr>", corpus)
            self.assertLess(title, first_rule)
            self.assertLess(first_rule, status)
            self.assertLess(status, corpus)
            self.assertLess(corpus, second_rule)

    def test_section_reordering_ignores_headings_inside_fenced_code(self) -> None:
        markdown = (
            "```text\n"
            "## AI/Tech\n"
            "### Excluded Topics (fake)\n"
            "**AI Dev Tools**\n"
            "```\n\n"
            "## US Politics\n\nPolitics.\n\n"
            "## AI/Tech\n\nAI.\n"
        )

        rendered, _matched = _render_markdown(markdown)

        self.assertLess(rendered.index("<h2>AI/Tech</h2>"), rendered.index("<h2>US Politics</h2>"))
        self.assertIn("## AI/Tech", rendered)
        self.assertIn("### Excluded Topics (fake)", rendered)

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
            self.assertIn("sources degraded", index)

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
            self.assertIn("🔍", index)
            self.assertIn("Review required", index)

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
            status = index.index("Review required", first_rule)
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
            self.assertIn("Action:", report)
            self.assertIn("drop_entry", report)
            self.assertIn("topics.AI News[1]", report)
            self.assertIn("duplicate", report)
            self.assertIn("reddit:cursor", report)

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
            self.assertIn("All deterministic contract checks passed", report)
            self.assertIn("Semantic faithfulness was not assessed", report)
            self.assertTrue((root / "site/reports/2026-08-20.html").is_file())

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
            self.assertIn("BLOCKED · 0 findings", report)
            self.assertNotIn("All deterministic contract checks passed", report)
            self.assertIn("does not mean the checker accepted", report)

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
            self.assertIn("REJECTED · 1 finding", report)
            self.assertNotIn("All deterministic contract checks passed", report)
            self.assertIn("details are published only for review-required runs", report)
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
        rendered, _ = _render_markdown(self._corpus_health_markdown(payload))
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
        rendered, _ = _render_markdown(markdown)
        self.assertNotIn("undated_sources", rendered)
        self.assertIn("1 source dropped 2 items without parseable dates.", rendered)
        self.assertIn("NPR Politics (2)", rendered)

    def test_malformed_corpus_health_json_is_left_verbatim(self) -> None:
        rendered, _ = _render_markdown(
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
            rendered, _ = _render_markdown(self._corpus_health_markdown(payload))
            self.assertIn("<code", rendered, payload)
            self.assertNotIn("this day's window", rendered, payload)

    def test_empty_source_type_is_left_verbatim(self) -> None:
        # A degenerate-but-well-typed payload must not crash the site build.
        payload = '{"failed_sources":[{"source_type":"","source_id":"x","status":"empty"}]}'
        rendered, _ = _render_markdown(self._corpus_health_markdown(payload))
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
        rendered, _ = _render_markdown(self._corpus_health_markdown(payload))
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
        rendered, _ = _render_markdown(markdown)
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
        rendered, _ = _render_markdown(self._corpus_health_markdown(payload))
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
        rendered, _ = _render_markdown(markdown)
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
            self.assertIn("states &#x27;42&#x27;", report)
            self.assertIn("drop_entry", report)

    def test_publishes_valid_dated_corpora_with_first_dir_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")
            fresh = root / "fresh"
            prior = root / "prior"
            fresh.mkdir()
            prior.mkdir()
            valid_corpus, _config, _projected, _output = fixture_contract()
            fresh_corpus = json.dumps(
                {**valid_corpus, "report_date": "2026-08-20"}
            )
            (fresh / "2026-08-20.json").write_text(fresh_corpus, encoding="utf-8")
            (prior / "2026-08-20.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-19"}),
                encoding="utf-8",
            )
            (prior / "2026-08-14.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-14"}),
                encoding="utf-8",
            )
            (prior / "not-a-date.json").write_text("{}", encoding="utf-8")
            (prior / "2026-08-19.json").write_text("{invalid json", encoding="utf-8")
            (prior / "2026-08-18.json").write_text("{}", encoding="utf-8")
            (prior / "2026-08-21.json").write_text(
                json.dumps({**valid_corpus, "report_date": "2026-08-21"}),
                encoding="utf-8",
            )

            output = root / "site"
            build_site(briefings, output, corpora_dirs=[fresh, prior])

            published = (output / "corpora/2026-08-20.json").read_text(encoding="utf-8")
            self.assertEqual(published, fresh_corpus)
            self.assertTrue((output / "corpora/2026-08-14.json").is_file())
            self.assertFalse((output / "corpora/2026-08-19.json").exists())
            self.assertFalse((output / "corpora/2026-08-18.json").exists())
            self.assertFalse((output / "corpora/2026-08-21.json").exists())
            self.assertFalse((output / "corpora/not-a-date.json").exists())

    def test_prunes_corpora_older_than_fourteen_days(self) -> None:
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
            build_site(briefings, output, corpora_dirs=[corpora])

            self.assertTrue((output / "corpora/2026-08-07.json").is_file())
            self.assertFalse((output / "corpora/2026-08-06.json").exists())
            self.assertFalse((output / "corpora/2026-08-05.json").exists())

    def test_no_corpora_dir_builds_site_without_corpora(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            briefings = root / "briefings"
            briefings.mkdir()
            (briefings / "2026-08-20.md").write_text("briefing", encoding="utf-8")
            self._write_sidecar(briefings, date="2026-08-20", disposition="ready")

            output = root / "site"
            build_site(briefings, output)

            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((output / "corpora").exists())

    def test_corpora_without_history_entries_publishes_nothing(self) -> None:
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
            build_site(briefings, output, corpora_dirs=[corpora])

            self.assertTrue((output / "index.html").is_file())
            self.assertFalse((output / "corpora").exists())

    @staticmethod
    def _finding(
        check: str,
        message: str,
        *,
        context: dict[str, str] | None = None,
    ) -> dict[str, object]:
        finding: dict[str, object] = {
            "level": "WARN",
            "check": check,
            "domain": "evidence",
            "message": message,
        }
        if context is not None:
            finding["context"] = context
        return finding

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
        if repair_actions is not None:
            payload["repair_actions"] = repair_actions
        (directory / f"{date}.json").write_text(
            json.dumps(payload),
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
