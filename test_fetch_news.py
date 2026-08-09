#!/usr/bin/env python3
"""Unit tests for the pure (non-network) helpers in fetch_news.py.

Everything under test here is deterministic and offline: date parsing, HTML
stripping, deduplication, and Reddit window selection. The fetchers themselves
are not covered — they are thin wrappers around live HTTP.

Run:
    python3 -m unittest -v
"""

import unittest
from datetime import datetime, timezone

from fetch_news import (
    REDDIT_MAX_LIMIT,
    _reddit_md_text,
    dedupe,
    parse_feed_date,
    reddit_limit,
    reddit_top_bucket,
    sort_items,
    strip_html,
)


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class ParseFeedDateTest(unittest.TestCase):
    """Feeds disagree on date format; every accepted form must normalize to UTC."""

    def test_parses_supported_formats_to_utc(self):
        cases = [
            # RFC 822 / RFC 2822 — what RSS 2.0 pubDate uses
            ("Sat, 08 Aug 2026 14:30:00 GMT", utc(2026, 8, 8, 14, 30)),
            ("Sat, 08 Aug 2026 14:30:00 -0400", utc(2026, 8, 8, 18, 30)),
            # ISO 8601 — what Atom published/updated uses
            ("2026-08-08T14:30:00Z", utc(2026, 8, 8, 14, 30)),
            ("2026-08-08T14:30:00+00:00", utc(2026, 8, 8, 14, 30)),
            ("2026-08-08T10:30:00-04:00", utc(2026, 8, 8, 14, 30)),
            # surrounding whitespace is common and must not break parsing
            ("  2026-08-08T14:30:00Z  ", utc(2026, 8, 8, 14, 30)),
        ]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(parse_feed_date(text), expected)

    def test_naive_timestamps_are_assumed_utc(self):
        """A feed that omits the offset must not silently shift by local time."""
        for text in ("Sat, 08 Aug 2026 14:30:00", "2026-08-08T14:30:00"):
            with self.subTest(text=text):
                parsed = parse_feed_date(text)
                self.assertEqual(parsed, utc(2026, 8, 8, 14, 30))
                self.assertIsNotNone(parsed.tzinfo)

    def test_returns_none_for_unusable_input(self):
        for text in (None, "", "   ", "not a date"):
            with self.subTest(text=text):
                self.assertIsNone(parse_feed_date(text))

    def test_result_is_always_timezone_aware(self):
        """Cutoff comparison is `published < cutoff`; a naive value would raise."""
        parsed = parse_feed_date("2026-08-08T14:30:00")
        self.assertLess(parsed, datetime.now(timezone.utc))


class StripHtmlTest(unittest.TestCase):
    def test_removes_tags_and_unescapes_entities(self):
        self.assertEqual(strip_html("<p>A &amp; B</p>"), "A & B")

    def test_trims_surrounding_whitespace(self):
        self.assertEqual(strip_html("  <b>x</b> "), "x")

    def test_handles_missing_text(self):
        """findtext() returns None for absent elements; that must not raise."""
        self.assertEqual(strip_html(None), "")


class RedditMdTextTest(unittest.TestCase):
    def test_extracts_post_body(self):
        content = '<table><tr><td><div class="md">hello <b>world</b></div></td></tr></table>'
        self.assertEqual(_reddit_md_text(content), "hello world")

    def test_returns_empty_when_no_body_present(self):
        self.assertEqual(_reddit_md_text("<div>link post, no selftext</div>"), "")


class DedupeTest(unittest.TestCase):
    """The same story arrives from several feeds; collapse it without losing items."""

    def test_drops_repeated_urls(self):
        items = [
            {"url": "https://example.com/a", "title": "First"},
            {"url": "https://example.com/a", "title": "Same story, other feed"},
        ]
        self.assertEqual(len(dedupe(items)), 1)

    def test_drops_near_duplicate_titles(self):
        """Wire copy differs only in punctuation and case across syndicators."""
        items = [
            {"url": "https://npr.org/x", "title": "Senate Confirms New Attorney General"},
            {"url": "https://thehill.com/y", "title": "senate confirms new attorney general!"},
        ]
        self.assertEqual(len(dedupe(items)), 1)

    def test_keeps_first_occurrence(self):
        items = [
            {"url": "https://example.com/a", "title": "Original"},
            {"url": "https://example.com/a", "title": "Duplicate"},
        ]
        self.assertEqual(dedupe(items)[0]["title"], "Original")

    def test_keeps_distinct_items(self):
        items = [
            {"url": "https://example.com/a", "title": "Story A"},
            {"url": "https://example.com/b", "title": "Story B"},
        ]
        self.assertEqual(len(dedupe(items)), 2)

    def test_empty_url_is_not_treated_as_a_duplicate_key(self):
        """A missing url means link extraction failed, not that items match."""
        items = [
            {"url": "", "title": "Story A"},
            {"url": "", "title": "Completely unrelated story B"},
        ]
        self.assertEqual(len(dedupe(items)), 2)

    def test_empty_title_is_not_treated_as_a_duplicate_key(self):
        items = [
            {"url": "https://example.com/a", "title": ""},
            {"url": "https://example.com/b", "title": ""},
        ]
        self.assertEqual(len(dedupe(items)), 2)


class RedditTopBucketTest(unittest.TestCase):
    """`--hours` must reach Reddit too; pick the smallest bucket that covers it."""

    def test_selects_smallest_covering_bucket(self):
        cases = [
            (1, "hour"),
            (2, "day"),
            (24, "day"),
            (25, "week"),
            (48, "week"),      # the default window — must not stay on t=day
            (168, "week"),
            (169, "month"),
            (720, "month"),
            (721, "year"),
            (8760, "year"),
            (9000, "all"),
        ]
        for hours, expected in cases:
            with self.subTest(hours=hours):
                self.assertEqual(reddit_top_bucket(hours), expected)

    def test_bucket_never_undercovers_the_window(self):
        """Regression guard: a bucket narrower than the window silently
        truncates coverage without reporting an error."""
        spans = {"hour": 1, "day": 24, "week": 168, "month": 720, "year": 8760}
        for hours in (1, 6, 23, 24, 25, 47, 48, 72, 167, 168, 400, 800):
            with self.subTest(hours=hours):
                bucket = reddit_top_bucket(hours)
                self.assertGreaterEqual(spans.get(bucket, float("inf")), hours)


class RedditLimitTest(unittest.TestCase):
    """A coarse bucket must not quietly shrink in-window coverage."""

    def test_no_inflation_when_bucket_matches_window(self):
        self.assertEqual(reddit_limit(24), 25)
        self.assertEqual(reddit_limit(1), 25)

    def test_inflates_when_bucket_overshoots_window(self):
        """48h is served by t=week, so ask for ~3.5x to keep coverage steady."""
        self.assertEqual(reddit_limit(48), 88)
        self.assertGreater(reddit_limit(48), reddit_limit(168))

    def test_never_exceeds_reddit_ceiling(self):
        for hours in (2, 3, 25, 169, 721):
            with self.subTest(hours=hours):
                self.assertLessEqual(reddit_limit(hours), REDDIT_MAX_LIMIT)

    def test_handles_nonsense_windows_without_raising(self):
        for hours in (0, -5, 100000):
            with self.subTest(hours=hours):
                self.assertLessEqual(reddit_limit(hours), REDDIT_MAX_LIMIT)
                self.assertGreater(reddit_limit(hours), 0)


class SortItemsTest(unittest.TestCase):
    """Ordering must mean the same thing for every source in the corpus."""

    def test_orders_newest_first(self):
        items = [
            {"title": "older", "published": "2026-08-07T10:00:00+00:00"},
            {"title": "newest", "published": "2026-08-08T12:00:00+00:00"},
            {"title": "middle", "published": "2026-08-08T09:00:00+00:00"},
        ]
        self.assertEqual([i["title"] for i in sort_items(items)],
                         ["newest", "middle", "older"])

    def test_engagement_does_not_outrank_recency(self):
        """Regression: points used to dominate, burying every Reddit post
        below every Hacker News post no matter how stale."""
        items = [
            {"title": "reddit today", "published": "2026-08-08T12:00:00+00:00"},
            {"title": "hn last week", "published": "2026-08-01T12:00:00+00:00",
             "points": 900, "comments": 400},
        ]
        self.assertEqual([i["title"] for i in sort_items(items)],
                         ["reddit today", "hn last week"])

    def test_items_without_engagement_are_not_demoted(self):
        items = [
            {"title": "no points", "published": "2026-08-08T12:00:00+00:00"},
            {"title": "many points", "published": "2026-08-08T11:00:00+00:00",
             "points": 500},
        ]
        self.assertEqual(sort_items(items)[0]["title"], "no points")

    def test_does_not_mutate_the_input(self):
        items = [{"title": "a", "published": "2026-08-07T10:00:00+00:00"},
                 {"title": "b", "published": "2026-08-08T10:00:00+00:00"}]
        sort_items(items)
        self.assertEqual([i["title"] for i in items], ["a", "b"])

    def test_handles_an_empty_category(self):
        self.assertEqual(sort_items([]), [])


if __name__ == "__main__":
    unittest.main()
