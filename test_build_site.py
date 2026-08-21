from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from build_site import ReviewFinding, _render_markdown, build_site


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
            self.assertIn("Review required · 2 findings", report)
            self.assertIn("WARN · evidence · unsupported figure:", report)
            self.assertIn("states &#x27;&lt;60&gt;&#x27;", report)
            self.assertIn("Verify the figure against the cited source", report)

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
            history = json.loads((output / "history.json").read_text())
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
            seven_day_history = json.loads((output / "history.json").read_text())
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

            history = json.loads((output / "history.json").read_text())
            self.assertEqual(len(history["entries"]), 7)
            self.assertEqual(history["entries"][0]["date"], "2026-08-20")
            self.assertEqual(history["entries"][-1]["date"], "2026-08-14")
            self.assertFalse((output / "2026-08-13.html").exists())

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
            page_18 = (output / "2026-08-18.html").read_text()
            self.assertIn("did not pass automated checks", page_18)
            self.assertNotIn("dogfood preview", page_18)
            history = json.loads((output / "history.json").read_text())
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
            self.assertEqual(index.count('<section class="review-story">'), 2)
            self.assertNotIn('<section class="review-panel">', index)
            self.assertLess(index.index("AI story"), index.index("ineligible citation"))
            self.assertLess(index.index("Excluded story"), index.index("repeats an item"))

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
            history = json.loads((first_site / "history.json").read_text())
            self.assertEqual(history["schema_version"], 4)
            self.assertEqual(history["entries"][0]["repair_actions"], actions)

            second = root / "second"
            second.mkdir()
            (second / "2026-08-20.md").write_text("next briefing", encoding="utf-8")
            self._write_sidecar(second, date="2026-08-20", disposition="ready")
            second_site = root / "site-2"
            build_site(second, second_site, prior_history=first_site / "history.json")
            rebuilt = json.loads((second_site / "history.json").read_text())
            by_date = {entry["date"]: entry for entry in rebuilt["entries"]}
            self.assertEqual(by_date["2026-08-19"]["repair_actions"], actions)
            self.assertEqual(by_date["2026-08-20"]["repair_actions"], [])

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
            self.assertIn("All checks passed", report)
            self.assertTrue((root / "site/reports/2026-08-20.html").is_file())

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
