#!/usr/bin/env python3
"""Unit tests for the briefing contract checker.

Most cases run against a small synthetic corpus so the expected findings are
obvious from reading the test. The last class runs the checker against the
committed fixture pair, which is the actual regression guard: if a prompt
change breaks the contract, that test fails.

Run:
    python3 -m unittest -v
"""

import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import eval_briefing
from briefing_config import BriefingConfig, load_config
from eval_briefing import ERROR, WARN, load_corpus

FIXTURE_CONFIG = load_config("fixtures/briefing-config-2026-08-09.json")


def evaluate(corpus, text):
    """Run unit fixtures against their frozen contract, not a user's config."""
    return eval_briefing.evaluate(corpus, text, FIXTURE_CONFIG)


def parse_briefing(text):
    return eval_briefing.parse_briefing(text, FIXTURE_CONFIG)


def _items(prefix, count):
    # Each item carries a plausible summary: claim-grounding checks compare
    # briefing prose against it, so an evidence-free corpus would make even a
    # faithful briefing look unsupported.
    return [{"title": f"{prefix}{n}".upper(),
             "url": f"https://ex.com/{prefix}{n}",
             "summary": f"Reported summary text here for story {prefix}{n}."}
            for n in range(1, count + 1)]


# Large enough to cover both the topic slots and the exclusion log that
# follows them, so a clean briefing really is clean.
CORPUS = {
    "generated_at": "2026-08-08T00:00:00+00:00",
    "errors": [],
    "categories": {
        "us_politics": _items("p", 10),
        "us_news": _items("n", 10),
        "world": _items("w", 10),
        "ai_tech": _items("a", 5),
        "dev_community": _items("t", 8) + _items("d", 8),
    },
}
# t1 is the Hacker News item: it carries a discussion link and engagement.
CORPUS["categories"]["dev_community"][0].update(
    discussion="https://news.ycombinator.com/item?id=1", points=40, comments=12)


def briefing(politics=3, us_news=4, world=5, ai_news=4, tools=3, practices=3,
             exclusions=5, health="", extra_link=None):
    """Build a briefing that satisfies the contract, with dials for breaking it."""

    def topics(prefix, count, base):
        out = []
        for n in range(1, count + 1):
            out.append(f"**{prefix} topic {n}** — summary text here.")
            out.append(f"🔗 https://ex.com/{base}{n}")
            if base == "t" and n == 1:
                out.append("🔗 HN: https://news.ycombinator.com/item?id=1")
                out.append("`↑ 40 pts · 12 comments`")
        return "\n".join(out)

    def log(name, base, count, start):
        rows = [f"**{name}**"]
        for n in range(start, start + count):
            rows.append(f"- *Dropped {n}* — lower impact. 🔗 https://ex.com/{base}{n}")
        return "\n".join(rows)

    parts = [
        "# Daily Briefing — August 9, 2026",
        "## US Politics", topics("Politics", politics, "p"),
        "## US News", topics("US news", us_news, "n"),
        "## World Events", topics("World", world, "w"),
        "## AI/Tech",
        "**AI News (4 slots)**", topics("AI", ai_news, "a"),
        "**AI Dev Tools (3 slots)**", topics("Tools", tools, "t"),
        "**AI Dev Practices (3 slots)**", topics("Practices", practices, "d"),
        "---",
        "### Excluded Topics (accountability log)",
        log("US Politics", "p", exclusions, 4),
        log("US News", "n", exclusions, 5),
        log("World Events", "w", exclusions, 6),
        log("AI Dev Tools", "t", exclusions, 4),
        log("AI Dev Practices", "d", exclusions, 4),
    ]
    if extra_link:
        parts.insert(2, f"🔗 {extra_link}")
    if health:
        parts += ["---", "### Corpus health", health]
    return "\n\n".join(parts)


def checks(findings, level=None):
    return {f.check for f in findings if level is None or f.level == level}


class CleanBriefingTest(unittest.TestCase):
    def test_contract_satisfying_briefing_produces_no_findings(self):
        self.assertEqual(evaluate(CORPUS, briefing()), [])


class GroundingTest(unittest.TestCase):
    """The core invariant: the output may not link anywhere outside the corpus."""

    def test_flags_link_absent_from_corpus(self):
        findings = evaluate(CORPUS, briefing(extra_link="https://ex.com/invented"))
        self.assertIn("ungrounded_link", checks(findings, ERROR))

    def test_flags_ungrounded_link_inside_exclusion_log(self):
        """The log writes links inline; an anchored regex would miss them."""
        text = briefing().replace("https://ex.com/p6", "https://ex.com/invented")
        self.assertIn("ungrounded_link", checks(evaluate(CORPUS, text), ERROR))

    def test_accepts_discussion_urls_as_grounded(self):
        findings = evaluate(CORPUS, briefing())
        self.assertNotIn("ungrounded_link", checks(findings))

    def test_ignores_sentence_punctuation_after_urls(self):
        text = briefing().replace("🔗 https://ex.com/p1", "🔗 https://ex.com/p1.),")
        sections = parse_briefing(text)
        self.assertIn("https://ex.com/p1", sections["US Politics"]["links"])
        self.assertNotIn("ungrounded_link", checks(evaluate(CORPUS, text), ERROR))

    def test_preserves_balanced_parentheses_in_urls(self):
        text = "## US Politics\n\n🔗 https://ex.com/wiki/Example_(topic)."
        sections = parse_briefing(text)
        self.assertEqual(sections["US Politics"]["links"],
                         ["https://ex.com/wiki/Example_(topic)"])

    def test_rejects_every_output_link_bypass_outside_citation_markers(self):
        attacks = {
            "Markdown link": "[click](https://attacker.example/markdown)",
            "HTML link": '<a href="https://attacker.example/html">click</a>',
            "single-quoted HTML link": "<a href='https://attacker.example/single'>click</a>",
            "HTML entity link": '<a href="https&#58;//attacker.example/entity">click</a>',
            "fully entity-encoded scheme": (
                '<a href="https&#58;&#47;&#47;attacker.example/encoded">click</a>'),
            "autolink": "<https://attacker.example/autolink>",
            "bare URL": "visit HTTPS://attacker.example/bare",
            "protocol-relative URL": "[click](//attacker.example/protocol-relative)",
            "bare www URL": "visit www.attacker.example/bare-www",
        }
        for kind, attack in attacks.items():
            with self.subTest(kind=kind):
                # Put the payload before any recognized section: a scan coupled
                # to section parsing would silently skip every one of these.
                findings = evaluate(CORPUS, f"{attack}\n\n{briefing()}")
                self.assertIn("ungrounded_link", checks(findings, ERROR))

    def test_semicolonless_html_entity_name_does_not_mangle_raw_query(self):
        corpus = json.loads(json.dumps(CORPUS))
        corpus["categories"]["us_politics"][0]["url"] = "https://ex.com/p1?id=2&copy=1"
        text = briefing().replace(
            "🔗 https://ex.com/p1", "🔗 https://ex.com/p1?id=2&copy=1")
        self.assertNotIn("ungrounded_link", checks(evaluate(corpus, text), ERROR))

    def test_allows_grounded_urls_in_non_citation_syntax(self):
        variants = (
            "[source](https://ex.com/p1)",
            '<a href="https://ex.com/p1">source</a>',
            "<a href='https://ex.com/p1'>source</a>",
            "<https://ex.com/p1>",
            "Reader note: https://ex.com/p1.",
            "[source](//ex.com/p1)",
        )
        for variant in variants:
            with self.subTest(variant=variant):
                findings = evaluate(CORPUS, f"{variant}\n\n{briefing()}")
                self.assertNotIn("ungrounded_link", checks(findings, ERROR))

    def test_allows_grounded_protocol_relative_and_bare_www_destinations(self):
        corpus = json.loads(json.dumps(CORPUS))
        corpus["categories"]["us_news"].append({
            "title": "Grounded web destination",
            "url": "https://www.example.test/story",
            "summary": "Grounded web destination summary.",
        })
        for variant in ("//www.example.test/story", "www.example.test/story"):
            with self.subTest(variant=variant):
                findings = evaluate(corpus, f"Reader note: {variant}\n\n{briefing()}")
                self.assertNotIn("ungrounded_link", checks(findings, ERROR))

    def test_flags_included_topic_without_a_link(self):
        text = briefing().replace("🔗 https://ex.com/p1", "")
        self.assertIn("topic_without_link", checks(evaluate(CORPUS, text), ERROR))

    def test_flags_excluded_topic_without_a_link(self):
        text = briefing().replace("🔗 https://ex.com/p6", "")
        self.assertIn("excluded_topic_without_link", checks(evaluate(CORPUS, text), ERROR))


class CitationIdentityTest(unittest.TestCase):
    """Two URLs that address the same article are the same citation.

    Exact string comparison conflates three different events: a fabricated
    link, a link the model tidied, and a link that is character-for-character
    equivalent to the corpus one. The first must fail loudly, the third must
    not fail at all, and the second must say which it is so the reader is not
    hunting a hallucination that never happened.
    """

    TRACKED = {
        "generated_at": "2026-08-08T00:00:00+00:00",
        "errors": [],
        "categories": {
            "us_politics": [{
                "title": "TRACKED STORY",
                "url": "https://www.bbc.co.uk/news/articles/abc?at_medium=RSS&at_campaign=rss",
                "summary": "Reported summary text here for the tracked story.",
            }],
            "us_news": _items("n", 1),
            "world": _items("w", 1),
            "ai_tech": _items("a", 1),
            "dev_community": _items("d", 1),
        },
    }

    def _cite(self, url):
        return f"## US Politics\n\n**Topic** — summary text here.\n🔗 {url}"

    def _findings(self, url):
        return evaluate(self.TRACKED, self._cite(url))

    def test_equivalent_url_is_grounded(self):
        """Trailing slash, host case, and utm_ noise do not change the article."""
        for variant in (
            "https://ex.com/n1/",
            "https://EX.com/n1",
            "https://ex.com/n1?utm_source=rss",
        ):
            with self.subTest(variant=variant):
                findings = evaluate(self.TRACKED,
                                    f"## US News\n\n**Topic** — summary text here.\n🔗 {variant}")
                self.assertNotIn("ungrounded_link", checks(findings))
                self.assertNotIn("altered_link", checks(findings))

    def test_dropped_publisher_parameter_is_reported_as_altered_not_invented(self):
        """The failure that reads as a hallucination but is a transcription."""
        findings = self._findings("https://www.bbc.co.uk/news/articles/abc")
        self.assertIn("altered_link", checks(findings, ERROR))
        self.assertNotIn("ungrounded_link", checks(findings))

    def test_altered_link_message_shows_the_corpus_url(self):
        """The fix is mechanical, so the checker prints what to paste back."""
        message = next(f.message for f in self._findings(
            "https://www.bbc.co.uk/news/articles/abc") if f.check == "altered_link")
        self.assertIn("at_medium=RSS", message)

    QUERY_ROUTED = {
        "generated_at": "2026-08-08T00:00:00+00:00",
        "errors": [],
        "categories": {
            "us_politics": _items("p", 1),
            "us_news": _items("n", 1),
            "world": _items("w", 1),
            "ai_tech": _items("a", 1),
            "dev_community": [
                {"title": "HN STORY 123",
                 "url": "https://news.ycombinator.com/item?id=123",
                 "summary": "Reported summary text here for story 123."},
                {"title": "HN STORY 456",
                 "url": "https://news.ycombinator.com/item?id=456",
                 "summary": "Reported summary text here for story 456."},
            ],
        },
    }

    def _cite_tools(self, *urls):
        lines = ["## AI Dev Tools", "", "**Topic** — summary text here."]
        lines += [f"🔗 {url}" for url in urls]
        return "\n".join(lines)

    def test_different_query_id_on_the_same_path_is_not_an_alteration(self):
        """Host and path alone do not identify a query-routed article.

        `item?id=999` is a different Hacker News story from `item?id=123`,
        not a rewrite of it. Calling it one would tell the reader to paste
        back a URL for an article they never cited.
        """
        findings = eval_briefing.evaluate(
            self.QUERY_ROUTED,
            self._cite_tools("https://news.ycombinator.com/item?id=999"),
            FIXTURE_CONFIG)
        self.assertIn("ungrounded_link", checks(findings, ERROR))
        self.assertNotIn("altered_link", checks(findings))

    def test_corpus_urls_sharing_a_path_stay_individually_citable(self):
        """Two stories under one path must not collapse into one entry."""
        for url in ("https://news.ycombinator.com/item?id=123",
                    "https://news.ycombinator.com/item?id=456"):
            with self.subTest(url=url):
                findings = eval_briefing.evaluate(
                    self.QUERY_ROUTED, self._cite_tools(url), FIXTURE_CONFIG)
                self.assertNotIn("ungrounded_link", checks(findings))
                self.assertNotIn("altered_link", checks(findings))

    def test_ambiguous_alteration_is_not_claimed(self):
        """With two candidates under the path, no single rewrite can be named."""
        findings = eval_briefing.evaluate(
            self.QUERY_ROUTED,
            self._cite_tools("https://news.ycombinator.com/item"),
            FIXTURE_CONFIG)
        self.assertIn("ungrounded_link", checks(findings, ERROR))
        self.assertNotIn("altered_link", checks(findings))

    def test_added_parameter_is_not_treated_as_a_tidy_up(self):
        """Only dropped parameters are mechanical; an added one changes the ask."""
        findings = evaluate(
            self.TRACKED,
            self._cite_tools("https://ex.com/n1?id=7"))
        self.assertIn("ungrounded_link", checks(findings, ERROR))
        self.assertNotIn("altered_link", checks(findings))

    def test_invented_path_on_a_known_host_is_still_ungrounded(self):
        """Host similarity must not launder a fabricated article."""
        findings = self._findings("https://www.bbc.co.uk/news/articles/invented")
        self.assertIn("ungrounded_link", checks(findings, ERROR))
        self.assertNotIn("altered_link", checks(findings))

    def test_equivalent_url_still_resolves_its_evidence(self):
        """Claim grounding reads evidence by URL; a variant must not lose it."""
        text = ("## US Politics\n\n**Topic** — this states 87 percent.\n"
                "🔗 https://www.bbc.co.uk/news/articles/abc?utm_source=x"
                "&at_campaign=rss&at_medium=RSS")
        self.assertIn("unsupported_figure", checks(evaluate(self.TRACKED, text), WARN))

    def test_equivalent_url_cited_in_two_sections_is_one_repeat(self):
        """Rewriting a link must not smuggle a story past the placement rule."""
        text = ("## US Politics\n\n**Topic** — summary text here.\n🔗 https://ex.com/n1\n\n"
                "## US News\n\n**Topic** — summary text here.\n🔗 https://ex.com/n1/")
        self.assertIn("repeated_topic", checks(evaluate(self.TRACKED, text), ERROR))


class SlotAllocationTest(unittest.TestCase):
    """Fixed slots exist so no sub-category can crowd out the others."""

    def test_overfilled_section_is_an_error(self):
        findings = evaluate(CORPUS, briefing(world=6))
        self.assertIn("slots_overfilled", checks(findings, ERROR))

    def test_underfilled_section_is_only_a_warning(self):
        """A thin corpus legitimately can't fill every slot."""
        findings = evaluate(CORPUS, briefing(practices=2))
        self.assertIn("slots_underfilled", checks(findings, WARN))
        self.assertEqual(checks(findings, ERROR), set())

    def test_each_section_is_counted_independently(self):
        findings = evaluate(CORPUS, briefing(ai_news=3, tools=3))
        messages = [f.message for f in findings if f.check == "slots_underfilled"]
        self.assertEqual(len(messages), 1)
        self.assertIn("AI News", messages[0])

    def test_us_news_over_four_topics_is_an_error(self):
        findings = evaluate(CORPUS, briefing(us_news=5))
        self.assertIn("slots_overfilled", checks(findings, ERROR))

    def test_us_politics_over_three_topics_is_an_error(self):
        findings = evaluate(CORPUS, briefing(politics=4))
        self.assertIn("slots_overfilled", checks(findings, ERROR))


class StructureTest(unittest.TestCase):
    def test_missing_section_is_an_error(self):
        text = briefing().replace("**AI Dev Practices (3 slots)**", "**Other**")
        self.assertIn("missing_section", checks(evaluate(CORPUS, text), ERROR))

    def test_exclusion_log_shorter_than_required_is_a_warning(self):
        findings = evaluate(CORPUS, briefing(exclusions=3))
        self.assertIn("exclusion_log_short", checks(findings, WARN))

    def test_exclusion_subheaders_do_not_reopen_top_level_sections(self):
        """`**US Politics**` inside the log must not re-enter the real section."""
        sections = parse_briefing(briefing())
        self.assertEqual(len(sections["US Politics"]["topics"]), 3)
        self.assertEqual(len(sections["Excluded Topics"]["excluded"]), 5)


class ConfigurationDrivenContractTest(unittest.TestCase):
    def config_with(self, section_name, **changes):
        sections = tuple(
            section._replace(**changes) if section.name == section_name else section
            for section in FIXTURE_CONFIG.sections
        )
        return BriefingConfig(FIXTURE_CONFIG.schema_version, sections)

    def test_story_target_comes_from_config(self):
        config = self.config_with("US Politics", target_stories=2)
        findings = eval_briefing.evaluate(CORPUS, briefing(), config)
        self.assertIn("slots_overfilled", checks(findings, ERROR))

    def test_exclusion_target_comes_from_config(self):
        config = self.config_with("US Politics", excluded_stories=3)
        findings = eval_briefing.evaluate(CORPUS, briefing(exclusions=3), config)
        politics_warnings = [
            finding for finding in findings
            if finding.check.startswith("exclusion_log") and "US Politics" in finding.message
        ]
        self.assertEqual(politics_warnings, [])

    def test_section_name_comes_from_config(self):
        config = self.config_with("US Politics", name="Public Policy")
        text = briefing().replace("## US Politics", "## Public Policy")
        text = text.replace("**US Politics**", "**Public Policy**")
        findings = eval_briefing.evaluate(CORPUS, text, config)
        self.assertNotIn("missing_section", checks(findings, ERROR))

    def test_exclusion_log_is_required_while_any_section_keeps_one(self):
        """Exempting one section does not exempt the briefing."""
        config = self.config_with("US Politics", excluded_stories=0)
        text = briefing().replace("### Excluded Topics (accountability log)", "### Other")
        self.assertIn("missing_section", checks(
            eval_briefing.evaluate(CORPUS, text, config), ERROR))

    def test_exclusion_log_is_not_required_when_every_section_is_exempt(self):
        """`excluded_stories: 0` exempts a section, so exempting all of them
        leaves nothing for the log to hold — demanding the heading anyway
        contradicts the configuration it is supposed to enforce."""
        config = BriefingConfig(
            FIXTURE_CONFIG.schema_version,
            tuple(section._replace(excluded_stories=0)
                  for section in FIXTURE_CONFIG.sections))
        text = briefing().split("### Excluded Topics")[0]
        self.assertNotIn("missing_section",
                         checks(eval_briefing.evaluate(CORPUS, text, config), ERROR))

    def test_missing_configured_corpus_category_is_an_error(self):
        config = self.config_with("US Politics", corpus_categories=("climate",))
        findings = eval_briefing.evaluate(CORPUS, briefing(), config)
        self.assertIn("config_category_missing", checks(findings, ERROR))

    def test_story_must_come_from_a_category_eligible_for_its_section(self):
        config = self.config_with("US Politics", corpus_categories=("world",))
        findings = eval_briefing.evaluate(CORPUS, briefing(), config)
        self.assertIn("category_ineligible", checks(findings, ERROR))

    def test_exclusion_category_check_ignores_sentence_closing_parenthesis(self):
        text = briefing().replace(
            "🔗 https://ex.com/p4", "🔗 https://ex.com/w10).", 1)
        findings = evaluate(CORPUS, text)
        self.assertIn("category_ineligible", checks(findings, ERROR))


class DoubleListingTest(unittest.TestCase):
    def test_story_in_both_briefing_and_exclusion_log_is_an_error(self):
        text = briefing().replace("🔗 https://ex.com/p3", "🔗 https://ex.com/p4")
        self.assertIn("included_and_excluded", checks(evaluate(CORPUS, text), ERROR))

    def test_same_link_spelling_twice_in_one_topic_is_a_duplicate_citation(self):
        text = briefing().replace(
            "🔗 https://ex.com/w1", "🔗 https://ex.com/w1\n🔗 https://ex.com/w1", 1)
        self.assertIn("duplicate_citation", checks(evaluate(CORPUS, text), ERROR))
        self.assertNotIn("repeated_topic", checks(evaluate(CORPUS, text), ERROR))

    def test_canonical_equivalent_spellings_remain_a_repeated_topic(self):
        text = briefing().replace(
            "🔗 https://ex.com/w1", "🔗 https://ex.com/w1\n🔗 https://EX.com/w1/", 1)
        self.assertIn("repeated_topic", checks(evaluate(CORPUS, text), ERROR))
        self.assertNotIn("duplicate_citation", checks(evaluate(CORPUS, text), ERROR))

    def test_a_story_reported_in_two_sections_is_an_error(self):
        text = briefing().replace("🔗 https://ex.com/n1", "🔗 https://ex.com/p1")
        self.assertIn("repeated_topic", checks(evaluate(CORPUS, text), ERROR))


class EngagementSignalTest(unittest.TestCase):
    def test_hn_item_cited_without_its_discussion_link_warns(self):
        text = briefing().replace("🔗 HN: https://news.ycombinator.com/item?id=1\n", "")
        self.assertIn("missing_discussion_link", checks(evaluate(CORPUS, text), WARN))

    def test_hn_self_post_is_cited_once_without_a_missing_discussion_warning(self):
        corpus = json.loads(json.dumps(CORPUS))
        hn = corpus["categories"]["dev_community"][0]
        hn["url"] = hn["discussion"]
        text = briefing().replace("🔗 https://ex.com/t1\n", "").replace("🔗 HN: ", "🔗 ")
        findings = evaluate(corpus, text)
        self.assertNotIn("missing_discussion_link", checks(findings, WARN))
        self.assertNotIn("duplicate_citation", checks(findings, ERROR))


class CorpusHealthTest(unittest.TestCase):
    """A degraded run must look degraded rather than merely short."""

    def setUp(self):
        self.degraded = dict(CORPUS, errors=["r/ClaudeAI: HTTP Error 429",
                                             "r/ClaudeCode: HTTP Error 429"])

    def test_missing_health_section_on_degraded_run_is_an_error(self):
        findings = evaluate(self.degraded, briefing())
        self.assertIn("corpus_health_missing", checks(findings, ERROR))

    def test_unnamed_failed_source_is_an_error(self):
        text = briefing(health="`r/ClaudeAI` failed.")
        findings = evaluate(self.degraded, text)
        self.assertIn("failed_source_unnamed", checks(findings, ERROR))

    def test_hn_query_name_is_not_truncated_at_its_colon(self):
        degraded = dict(CORPUS, errors=["HN:agentic coding: HTTP Error 503"])
        text = briefing(health="All sources healthy.")
        findings = evaluate(degraded, text)
        self.assertIn("failed_source_unnamed", checks(findings, ERROR))
        self.assertTrue(any("HN:agentic coding" in finding.message
                            for finding in findings))

    def test_named_hn_query_source_satisfies_the_check(self):
        degraded = dict(CORPUS, errors=["HN:agentic coding: HTTP Error 503"])
        text = briefing(health="`HN:agentic coding` failed with HTTP 503.")
        self.assertEqual(checks(evaluate(degraded, text), ERROR), set())

    def test_cosmetic_source_variants_satisfy_the_check(self):
        cases = (
            ("subreddit slash", "r/ClaudeAI: HTTP Error 429", "/r/ClaudeAI failed."),
            ("HN colon space", "HN:agentic coding: HTTP Error 503",
             "HN: agentic coding failed."),
            ("wrapped source", "Ars Technica: timed out", "Ars\n  Technica failed."),
        )
        for label, error, health in cases:
            with self.subTest(label=label):
                degraded = dict(CORPUS, errors=[error])
                self.assertEqual(checks(evaluate(degraded, briefing(health=health)), ERROR), set())

    def test_failed_sources_may_be_named_in_health_heading(self):
        text = briefing(health="Failures are listed above.").replace(
            "### Corpus health",
            "### Corpus health — r/ClaudeAI and r/ClaudeCode failed")
        self.assertEqual(checks(evaluate(self.degraded, text), ERROR), set())

    def test_source_named_outside_health_section_does_not_count(self):
        text = briefing(health="All sources healthy.").replace(
            "summary text here.", "r/ClaudeAI reports summary text here.", 1)
        findings = evaluate(self.degraded, text)
        self.assertIn("failed_source_unnamed", checks(findings, ERROR))

    def test_fully_reported_degradation_is_clean(self):
        text = briefing(health="`r/ClaudeAI` and `r/ClaudeCode` failed with HTTP 429.")
        self.assertEqual(evaluate(self.degraded, text), [])

    def test_healthy_run_needs_no_health_section(self):
        self.assertNotIn("corpus_health_missing", checks(evaluate(CORPUS, briefing())))

    def test_current_schema_requires_exact_machine_readable_source_ids(self):
        error = {
            "source_type": "hacker_news",
            "source_id": "agentic coding",
            "status": "error",
            "error_type": "HTTPError",
            "message": "503 Service Unavailable",
            "duration_ms": 812,
        }
        degraded = dict(CORPUS, schema_version=eval_briefing.corpus_schema.SCHEMA_VERSION,
                        errors=[error])
        exact = ('```json\n{"failed_sources":[{"source_type":"hacker_news",'
                 '"source_id":"agentic coding","status":"error"}]}\n```')
        self.assertEqual(checks(evaluate(degraded, briefing(health=exact)), ERROR), set())

        paraphrased = exact.replace("agentic coding", "HN agentic coding")
        findings = evaluate(degraded, briefing(health=paraphrased))
        self.assertIn("failed_source_unnamed", checks(findings, ERROR))
        self.assertIn("unexpected_failed_source", checks(findings, ERROR))

    def test_current_schema_reports_a_status_mismatch_directly(self):
        error = {
            "source_type": "rss", "source_id": "Feed A", "status": "error",
            "error_type": "HTTPError", "message": "503", "duration_ms": 12,
        }
        degraded = dict(CORPUS, schema_version=eval_briefing.corpus_schema.SCHEMA_VERSION,
                        errors=[error])
        health = ('```json\n{"failed_sources":[{"source_type":"rss",'
                  '"source_id":"Feed A","status":"empty"}]}\n```')
        findings = checks(evaluate(degraded, briefing(health=health)), ERROR)
        self.assertIn("failed_source_status_mismatch", findings)
        self.assertNotIn("failed_source_unnamed", findings)
        self.assertNotIn("unexpected_failed_source", findings)

    def test_current_schema_rejects_prose_only_health(self):
        error = {
            "source_type": "reddit", "source_id": "ClaudeAI", "status": "empty",
            "error_type": "EmptySource", "message": "zero entries", "duration_ms": 12,
        }
        degraded = dict(CORPUS, schema_version=eval_briefing.corpus_schema.SCHEMA_VERSION,
                        errors=[error])
        findings = evaluate(degraded, briefing(health="r/ClaudeAI returned no entries."))
        self.assertIn("corpus_health_not_machine_readable", checks(findings, ERROR))


class ClaimGroundingTest(unittest.TestCase):
    """Citations can be verified exactly; claims can only be sampled.

    These checks do not attempt entailment — that needs a second model. They
    cover the parts that are decidable: figures, quotations, and prose that
    outgrew the evidence behind it. All WARN by design.
    """

    def test_figure_absent_from_the_cited_item_is_flagged(self):
        text = briefing().replace("**Politics topic 1** — summary text here.",
                                  "**Politics topic 1** — summary text here, up 47 percent.")
        self.assertIn("unsupported_figure", checks(evaluate(CORPUS, text), WARN))

    def test_figure_present_in_the_cited_item_is_accepted(self):
        corpus = dict(CORPUS)
        corpus["categories"] = dict(CORPUS["categories"])
        corpus["categories"]["us_politics"] = [
            dict(i, summary="Reported summary text here, up 47 percent.")
            for i in CORPUS["categories"]["us_politics"]]
        text = briefing().replace("**Politics topic 1** — summary text here.",
                                  "**Politics topic 1** — summary text here, up 47 percent.")
        self.assertNotIn("unsupported_figure", checks(evaluate(corpus, text)))

    def test_quotation_not_in_the_cited_item_is_flagged(self):
        """Attributing a real quote to the wrong source still misleads."""
        text = briefing().replace(
            "**Politics topic 1** — summary text here.",
            '**Politics topic 1** — summary text here, calling it "a total disaster".')
        self.assertIn("unsupported_quotation", checks(evaluate(CORPUS, text), WARN))

    def test_prose_that_outgrows_its_evidence_is_flagged(self):
        text = briefing().replace(
            "**Politics topic 1** — summary text here.",
            "**Politics topic 1** — " + "elaboration well beyond the source. " * 5)
        self.assertIn("claim_exceeds_evidence", checks(evaluate(CORPUS, text), WARN))

    def test_claim_findings_never_escalate_to_errors(self):
        """Claim grounding is a heuristic; only citation grounding is a contract."""
        text = briefing().replace(
            "**Politics topic 1** — summary text here.",
            "**Politics topic 1** — " + "unsupported padding 99 percent. " * 6)
        findings = evaluate(CORPUS, text)
        self.assertEqual(checks(findings, ERROR), set())
        self.assertTrue(checks(findings, WARN))

    def test_a_topic_with_no_resolvable_evidence_is_skipped(self):
        """Ungrounded links are already an ERROR; don't double-report them."""
        text = briefing(extra_link="https://ex.com/unknown")
        self.assertNotIn("claim_exceeds_evidence", checks(evaluate(CORPUS, text), WARN))

    def test_hacker_news_evidence_is_not_double_counted(self):
        """url and discussion resolve to one item; counting it twice would
        forgive twice as much unsupported prose."""
        from eval_briefing import corpus_evidence
        evidence = corpus_evidence(CORPUS)
        hn = CORPUS["categories"]["dev_community"][0]
        self.assertEqual(evidence[hn["url"]], evidence[hn["discussion"]])


class CommittedFixtureTest(unittest.TestCase):
    """The real regression guard: the shipped reference pair must stay consistent."""

    def test_reference_briefing_satisfies_its_corpus(self):
        corpus = load_corpus("fixtures/corpus-2026-08-09.json")
        with open("fixtures/briefing-2026-08-09.md", encoding="utf-8") as f:
            findings = evaluate(corpus, f.read())
        self.assertEqual(findings, [], f"reference briefing regressed: {findings}")

    def test_readme_full_result_matches_reference_fixture(self):
        """The portfolio showcase must contain the complete frozen result."""
        readme = Path("README.md").read_text(encoding="utf-8")
        marker = "<summary><b>Click to expand full briefing</b></summary>"
        quoted = readme.split(marker, 1)[1].split("</details>", 1)[0]
        sample = "\n".join(
            line[1:].lstrip() for line in quoted.splitlines()
            if line.startswith(">")
        ).strip()

        reference = Path("fixtures/briefing-2026-08-09.md").read_text(encoding="utf-8")
        comment_start = reference.index("<!--")
        comment_end = reference.index("-->", comment_start) + len("-->")
        expected = (
            reference[:comment_start].rstrip()
            + "\n\n"
            + reference[comment_end:].lstrip()
        ).strip()
        self.assertEqual(sample, expected, "README does not contain the full reference result")

        corpus = load_corpus("fixtures/corpus-2026-08-09.json")
        errors = [
            finding for finding in evaluate(corpus, sample)
            if finding.level == ERROR
        ]
        self.assertEqual(errors, [], f"README sample regressed: {errors}")


class PromptSafetyContractTest(unittest.TestCase):
    """Keep the untrusted-data and thin-evidence rules from regressing silently."""

    def test_prompt_preserves_security_and_grounding_boundary(self):
        with open("briefing-prompt.md", encoding="utf-8") as prompt_file:
            prompt = prompt_file.read().lower()
        for required in ("untrusted data", "never as instructions", "do not browse",
                         "never fill missing context", "summary is empty",
                         "briefing-config.json", "overlapping corpus categories"):
            with self.subTest(required=required):
                self.assertIn(required, prompt)

        for stale_wording in ("two local files", "us news and us politics",
                              "ai news and ai dev tools"):
            with self.subTest(stale_wording=stale_wording):
                self.assertNotIn(stale_wording, prompt)


class PromptInjectionContainmentTest(unittest.TestCase):
    """What the checker does when the summarizer obeys the corpus.

    The prompt tells the model to treat corpus text as data, but a prompt is
    not an enforcement mechanism, and a public feed is exactly where an
    instruction would be planted. These tests characterize the property that
    actually holds: an injection can only alter the briefing in ways that
    leave the corpus behind, and citation grounding is what notices.

    This documents existing behavior rather than driving new behavior, which
    is why no production change accompanies it.
    """

    CORPUS = load_corpus("fixtures/injection-corpus.json")
    CONFIG = load_config("fixtures/injection-config.json")

    def _evaluate(self, text):
        return eval_briefing.evaluate(self.CORPUS, text, self.CONFIG)

    def test_injection_fixture_is_a_valid_corpus(self):
        """The payload is item content, not malformed JSON — the point is that
        a perfectly well-formed corpus can carry one."""
        import corpus_schema
        self.assertEqual(corpus_schema.validate_corpus(self.CORPUS), [])
        self.assertIn("ignore all previous instructions",
                      self.CORPUS["categories"]["dev_community"][0]["summary"])

    def test_obeying_the_injection_fails_citation_grounding(self):
        """The attacker's URL is not in the corpus, so it cannot be cited."""
        text = Path("fixtures/injection-briefing.md").read_text(encoding="utf-8")
        findings = self._evaluate(text)
        self.assertIn("ungrounded_link", checks(findings, ERROR))
        self.assertTrue(any("security-advisory.example.com" in f.message
                            for f in findings))

    def test_reporting_the_injection_attempt_is_not_blocked(self):
        """Containment must not stop the briefing covering the post itself.

        The injected item is legitimate corpus content. A checker that refused
        to let it be cited would be filtering the news, not grounding it.
        """
        text = ("# Daily Briefing — August 9, 2026\n\n## AI Dev Tools\n\n"
                "**Show HN: a tiny MCP server for local notes** — a Show HN post "
                "describes a small MCP server for local notes.\n"
                "🔗 https://news.ycombinator.com/item?id=90000001")
        self.assertNotIn("ungrounded_link", checks(self._evaluate(text)))
        self.assertNotIn("altered_link", checks(self._evaluate(text)))


class TextEncodingContractTest(unittest.TestCase):
    """Every file the pipeline reads or writes is UTF-8, on every platform.

    Briefings and corpora carry `🔗`, em dashes and accented names. An `open`
    without `encoding` uses the platform's locale encoding, so the same file
    that round-trips here raises UnicodeDecodeError on a cp1252 Windows box —
    a failure the whole test suite is blind to when it only runs on UTF-8
    systems. The modules checked are the ones pyproject type-checks.
    """

    PIPELINE_MODULES = ("briefing_config.py", "fetch_news.py",
                        "eval_briefing.py", "corpus_schema.py")

    def test_pipeline_never_relies_on_the_locale_encoding(self):
        for module in self.PIPELINE_MODULES:
            tree = ast.parse(Path(module).read_text(encoding="utf-8"), filename=module)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name not in {"open", "read_text", "write_text"}:
                    continue
                with self.subTest(module=module, call=name, line=node.lineno):
                    self.assertIn(
                        "encoding", {kw.arg for kw in node.keywords},
                        f"{module}:{node.lineno}: {name}() without an explicit encoding")


class CommandLineFailureTest(unittest.TestCase):
    """A bad invocation reports what is wrong; it does not dump a traceback.

    Every other failure path in the project explains itself. These two reached
    the user as a stack trace, which buried the one line worth reading.
    """

    def _run(self, corpus_path, briefing_path="fixtures/briefing-2026-08-09.md"):
        argv = ["eval_briefing.py", "--corpus", corpus_path,
                "--briefing", briefing_path,
                "--config", "fixtures/briefing-config-2026-08-09.json"]
        with (patch.object(eval_briefing.sys, "argv", argv),
              redirect_stdout(io.StringIO()),
              redirect_stderr(io.StringIO()) as stderr,
              self.assertRaises(SystemExit) as exit_context):
            eval_briefing.main()
        return exit_context.exception.code, stderr.getvalue()

    def test_unreadable_corpus_path_is_reported(self):
        code, stderr = self._run("does-not-exist.json")
        self.assertEqual(code, 2)
        self.assertIn("cannot load corpus", stderr)
        self.assertIn("does-not-exist.json", stderr)

    def test_non_object_corpus_is_reported(self):
        for value in (None, [], "not an object", 42):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                corpus = Path(directory) / "corpus.json"
                corpus.write_text(json.dumps(value), encoding="utf-8")
                code, stderr = self._run(str(corpus))
            self.assertEqual(code, 2)
            self.assertIn("corpus is not a JSON object", stderr)
            self.assertNotIn("Traceback", stderr)

    def test_unreadable_briefing_path_is_reported(self):
        code, stderr = self._run("fixtures/corpus-2026-08-09.json",
                                 briefing_path="no-such-briefing.md")
        self.assertEqual(code, 2)
        self.assertIn("cannot read briefing", stderr)
        self.assertIn("no-such-briefing.md", stderr)

    def test_corpus_newer_than_the_checker_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            newer = Path(directory) / "corpus.json"
            newer.write_text(json.dumps({"schema_version": 999, "categories": {}}),
                             encoding="utf-8")
            code, stderr = self._run(str(newer))
        self.assertEqual(code, 2)
        self.assertIn("newer than", stderr)
        self.assertIn("upgrade eval_briefing.py", stderr)

    def test_malformed_current_version_corpus_is_refused_before_evaluation(self):
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "corpus.json"
            malformed.write_text(json.dumps({
                "schema_version": eval_briefing.corpus_schema.SCHEMA_VERSION,
                "generated_at": "2026-08-08",
                "categories": {"news": [{"url": 7}]},
            }), encoding="utf-8")
            code, stderr = self._run(str(malformed))
        self.assertEqual(code, 2)
        self.assertIn("violates schema", stderr)
        self.assertIn("missing top-level field", stderr)
        self.assertNotIn("Traceback", stderr)


if __name__ == "__main__":
    unittest.main()
