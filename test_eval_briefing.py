#!/usr/bin/env python3
"""Unit tests for the briefing contract checker.

Most cases run against a small synthetic corpus so the expected findings are
obvious from reading the test. The last class runs the checker against the
committed fixture pair, which is the actual regression guard: if a prompt
change breaks the contract, that test fails.

Run:
    python3 -m unittest -v
"""

import unittest

from eval_briefing import ERROR, WARN, evaluate, load_corpus, parse_briefing


def _items(prefix, count):
    return [{"title": f"{prefix}{n}".upper(), "url": f"https://ex.com/{prefix}{n}"}
            for n in range(1, count + 1)]


# Large enough to cover both the topic slots and the exclusion log that
# follows them, so a clean briefing really is clean.
CORPUS = {
    "generated_at": "2026-08-08T00:00:00+00:00",
    "errors": [],
    "categories": {
        "us_politics": _items("p", 10),
        "world": _items("w", 10),
        "ai_tech": _items("a", 5),
        "dev_community": _items("t", 8) + _items("d", 8),
    },
}
# t1 is the Hacker News item: it carries a discussion link and engagement.
CORPUS["categories"]["dev_community"][0].update(
    discussion="https://news.ycombinator.com/item?id=1", points=40, comments=12)


def briefing(politics=5, world=5, ai_news=4, tools=3, practices=3,
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
        "# Daily Briefing — August 8, 2026",
        "## US Politics", topics("Politics", politics, "p"),
        "## World Events", topics("World", world, "w"),
        "## AI/Tech",
        "**AI News (4 slots)**", topics("AI", ai_news, "a"),
        "**AI Dev Tools (3 slots)**", topics("Tools", tools, "t"),
        "**AI Dev Practices (3 slots)**", topics("Practices", practices, "d"),
        "---",
        "### Excluded Topics (accountability log)",
        log("US Politics", "p", exclusions, 6),
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
    """The core invariant: the briefing may not cite anything not in the corpus."""

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
        self.assertEqual(len(sections["US Politics"]["topics"]), 5)
        self.assertEqual(len(sections["Excluded Topics"]["excluded"]), 4)


class DoubleListingTest(unittest.TestCase):
    def test_story_in_both_briefing_and_exclusion_log_is_an_error(self):
        text = briefing().replace("🔗 https://ex.com/p5", "🔗 https://ex.com/p6")
        self.assertIn("included_and_excluded", checks(evaluate(CORPUS, text), ERROR))

    def test_same_link_cited_twice_in_body_is_a_warning(self):
        text = briefing().replace("🔗 https://ex.com/w2", "🔗 https://ex.com/w1")
        self.assertIn("repeated_link", checks(evaluate(CORPUS, text), WARN))


class EngagementSignalTest(unittest.TestCase):
    def test_hn_item_cited_without_its_discussion_link_warns(self):
        text = briefing().replace("🔗 HN: https://news.ycombinator.com/item?id=1\n", "")
        self.assertIn("missing_discussion_link", checks(evaluate(CORPUS, text), WARN))


class CorpusHealthTest(unittest.TestCase):
    """A degraded run must look degraded rather than merely short."""

    def setUp(self):
        self.degraded = dict(CORPUS, errors=["r/ClaudeAI: HTTP Error 429",
                                             "r/ClaudeCode: HTTP Error 429"])

    def test_missing_health_section_on_degraded_run_is_an_error(self):
        findings = evaluate(self.degraded, briefing())
        self.assertIn("corpus_health_missing", checks(findings, ERROR))

    def test_unnamed_failed_source_is_a_warning(self):
        text = briefing(health="`r/ClaudeAI` failed.")
        findings = evaluate(self.degraded, text)
        self.assertIn("failed_source_unnamed", checks(findings, WARN))
        self.assertEqual(checks(findings, ERROR), set())

    def test_fully_reported_degradation_is_clean(self):
        text = briefing(health="`r/ClaudeAI` and `r/ClaudeCode` failed with HTTP 429.")
        self.assertEqual(evaluate(self.degraded, text), [])

    def test_healthy_run_needs_no_health_section(self):
        self.assertNotIn("corpus_health_missing", checks(evaluate(CORPUS, briefing())))


class CommittedFixtureTest(unittest.TestCase):
    """The real regression guard: the shipped reference pair must stay consistent."""

    def test_reference_briefing_satisfies_its_corpus(self):
        corpus = load_corpus("fixtures/corpus-2026-08-08.json")
        with open("fixtures/briefing-2026-08-08.md") as f:
            findings = evaluate(corpus, f.read())
        self.assertEqual(findings, [], f"reference briefing regressed: {findings}")


if __name__ == "__main__":
    unittest.main()
