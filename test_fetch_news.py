#!/usr/bin/env python3
"""Offline unit and failure-mode tests for fetch_news.py.

Everything under test here is deterministic and offline. Network boundaries
are patched only where the behavior at that boundary is itself the contract.

Run:
    python3 -m unittest -v
"""

import io
import json
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fetch_news
from fetch_news import (
    DEFAULT_WINDOW_HOURS,
    MAX_RESPONSE_BYTES,
    REDDIT_MAX_LIMIT,
    _reddit_md_text,
    canonicalize_url,
    dedupe,
    fetch_hn,
    fetch_reddit,
    is_relevant_item,
    parse_feed_date,
    parse_feed_xml,
    positive_int,
    prepare_category,
    reddit_limit,
    reddit_top_bucket,
    retry_after_seconds,
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

    def test_tracking_parameters_do_not_defeat_url_deduplication(self):
        items = [
            {"title": "First title", "url": "https://Example.com/story?utm_source=rss&id=7#top"},
            {"title": "Different title", "url": "https://example.com/story?id=7"},
        ]
        self.assertEqual(dedupe(items), [items[0]])


class CanonicalizeUrlTest(unittest.TestCase):
    def test_removes_tracking_and_fragment_but_preserves_meaningful_query(self):
        url = "HTTPS://Example.COM/story/?b=2&utm_medium=rss&a=1&fbclid=x#comments"
        self.assertEqual(canonicalize_url(url), "https://example.com/story?a=1&b=2")

    def test_leaves_non_http_values_unchanged(self):
        self.assertEqual(canonicalize_url("mailto:news@example.com"),
                         "mailto:news@example.com")


class RelevanceTest(unittest.TestCase):
    def test_filters_obvious_noise_from_broad_ai_feed(self):
        item = {"source": "Wired", "title": "Hotels.com coupon codes", "summary": "Save today"}
        self.assertFalse(is_relevant_item(item))

    def test_keeps_ai_story_from_broad_feed(self):
        item = {"source": "Wired", "title": "A new chatbot", "summary": "An AI model launch"}
        self.assertTrue(is_relevant_item(item))

    def test_category_specific_feed_does_not_need_keywords(self):
        item = {"source": "TechCrunch AI", "title": "Data centers and the grid", "summary": ""}
        self.assertTrue(is_relevant_item(item))

    def test_github_changelog_keeps_only_ai_tool_updates(self):
        self.assertTrue(is_relevant_item({
            "source": "GitHub Changelog", "title": "Copilot code review improves", "summary": ""}))
        self.assertFalse(is_relevant_item({
            "source": "GitHub Changelog", "title": "Repository sidebar redesign", "summary": ""}))

    def test_hacker_news_query_match_must_still_be_topically_relevant(self):
        self.assertFalse(is_relevant_item({
            "source": "Hacker News", "title": "Dithered QR Codes", "summary": ""}))
        self.assertTrue(is_relevant_item({
            "source": "Hacker News", "title": "Code was never the hard part", "summary": ""}))


class PrepareCategoryTest(unittest.TestCase):
    @staticmethod
    def item(n, source="A"):
        return {
            "title": f"Story {source} {n}",
            "url": f"https://example.com/{source}/{n}",
            "published": f"2026-08-08T{n:02d}:00:00+00:00",
            "source": source,
        }

    def test_caps_each_source_and_the_whole_category(self):
        items = [self.item(n, "A") for n in range(1, 6)]
        items += [self.item(n, "B") for n in range(1, 6)]
        kept, stats = prepare_category(items, source_cap=2, category_cap=3)
        self.assertEqual(len(kept), 3)
        self.assertLessEqual(sum(i["source"] == "A" for i in kept), 2)
        self.assertLessEqual(sum(i["source"] == "B" for i in kept), 2)
        self.assertEqual(stats["fetched"], 10)
        self.assertEqual(stats["kept"], 3)
        self.assertGreater(stats["source_cap_dropped"] + stats["category_cap_dropped"], 0)

    def test_reports_relevance_and_duplicate_drops(self):
        items = [
            {**self.item(1, "Wired"), "title": "AI model release"},
            {**self.item(2, "Wired"), "title": "Coupon codes"},
            {**self.item(3, "Wired"), "title": "Another AI story",
             "url": self.item(1, "Wired")["url"]},
        ]
        kept, stats = prepare_category(items)
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["relevance_dropped"], 1)
        self.assertEqual(stats["duplicates_dropped"], 1)

    def test_duplicate_keeps_newest_occurrence_not_source_completion_order(self):
        older = self.item(1, "A")
        newer = {**self.item(2, "B"), "url": older["url"]}
        kept, _stats = prepare_category([older, newer])
        self.assertEqual(kept, [newer])


class HackerNewsTest(unittest.TestCase):
    def test_carries_story_text_as_grounding_context(self):
        payload = {"hits": [{
            "objectID": "42", "title": "Ask HN", "url": None,
            "story_text": "<p>Measured details</p>", "created_at_i": 1786204800,
            "points": 21, "num_comments": 4,
        }]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8))
        self.assertEqual(result.items[0]["summary"], "Measured details")

    def test_counts_hits_with_no_usable_timestamp(self):
        """A hit without created_at_i used to raise KeyError mid-loop."""
        payload = {"hits": [
            {"objectID": "1", "title": "No date", "points": 99},
            {"objectID": "2", "title": "Dated", "url": "https://ex.com/a",
             "created_at_i": 1786204800, "points": 99, "num_comments": 1},
        ]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8))
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.undated, 1)


class ParseFeedXmlTest(unittest.TestCase):
    """ElementTree expands entities, so the DOCTYPE is the thing to refuse."""

    BILLION_LAUGHS = (
        b'<?xml version="1.0"?>\n'
        b'<!DOCTYPE lolz [ <!ENTITY lol "lol">\n'
        b' <!ENTITY lol1 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b' <!ENTITY lol2 "&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;&lol1;">\n'
        b' <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">]>\n'
        b'<rss><item><title>&lol3;</title></item></rss>'
    )

    def test_rejects_entity_expansion_payload(self):
        with self.assertRaises(ValueError):
            parse_feed_xml(self.BILLION_LAUGHS)

    def test_rejects_external_entity_declaration(self):
        payload = (b'<?xml version="1.0"?><!DOCTYPE r ['
                   b'<!ENTITY x SYSTEM "file:///etc/passwd">]><r>&x;</r>')
        with self.assertRaises(ValueError):
            parse_feed_xml(payload)

    def test_entity_reference_without_a_doctype_cannot_expand(self):
        """Without a declaration expat rejects the reference outright."""
        with self.assertRaises(ET.ParseError):
            parse_feed_xml(b"<rss><item><title>&lol3;</title></item></rss>")

    def test_parses_an_ordinary_feed(self):
        payload = (b'<?xml version="1.0"?>\n'
                   b'<rss><channel><item><title>Hi &amp; bye</title></item></channel></rss>')
        self.assertEqual(parse_feed_xml(payload).find(".//title").text, "Hi & bye")

    def test_tolerates_byte_order_mark_and_leading_comment(self):
        payload = (b'\xef\xbb\xbf<?xml version="1.0"?><!-- generated -->'
                   b'<rss><item><title>ok</title></item></rss>')
        self.assertEqual(parse_feed_xml(payload).find(".//title").text, "ok")

    def test_doctype_inside_article_text_is_not_a_declaration(self):
        payload = b"<rss><item><title>The &lt;!DOCTYPE html&gt; tag</title></item></rss>"
        self.assertIn("DOCTYPE", parse_feed_xml(payload).find(".//title").text)


class RetryAfterTest(unittest.TestCase):
    """Reddit tells us how long to wait; guessing wastes time or earns a 429."""

    def _error(self, header):
        headers = {} if header is None else {"Retry-After": header}
        error = urllib.error.HTTPError("https://reddit.test", 429, "Too Many Requests",
                                       headers, io.BytesIO(b""))
        self.addCleanup(error.close)
        return error

    def test_uses_the_server_supplied_delay(self):
        self.assertEqual(retry_after_seconds(self._error("7"), 5), 7)

    def test_falls_back_when_the_header_is_absent_or_unparseable(self):
        for header in (None, "", "  ", "Wed, 21 Oct 2026 07:28:00 GMT"):
            with self.subTest(header=header):
                self.assertEqual(retry_after_seconds(self._error(header), 5), 5)

    def test_clamps_an_absurd_delay(self):
        """The header is attacker-influenced; an hour-long sleep would hang."""
        self.assertEqual(retry_after_seconds(self._error("99999"), 5),
                         fetch_news.REDDIT_RETRY_MAX_SLEEP)


class UndatedAccountingTest(unittest.TestCase):
    """A feed that changes date format must not look like a healthy feed."""

    FEED = (b'<rss><channel>'
            b'<item><title>Good</title><link>https://ex.com/a</link>'
            b'<pubDate>Sat, 08 Aug 2026 12:00:00 GMT</pubDate></item>'
            b'<item><title>Broken date</title><link>https://ex.com/b</link>'
            b'<pubDate>yesterday-ish</pubDate></item>'
            b'<item><title>No date at all</title><link>https://ex.com/c</link></item>'
            b'</channel></rss>')

    def test_fetch_rss_counts_unparseable_dates_separately(self):
        with patch.object(fetch_news, "http_get", return_value=self.FEED):
            result = fetch_news.fetch_rss("Test", "https://ex.com/feed", utc(2026, 8, 1))
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.undated, 2)

    def test_stale_items_are_not_counted_as_undated(self):
        """Too old and unparseable are different failures."""
        with patch.object(fetch_news, "http_get", return_value=self.FEED):
            result = fetch_news.fetch_rss("Test", "https://ex.com/feed", utc(2026, 9, 1))
        self.assertEqual(result.items, [])
        self.assertEqual(result.undated, 2)

    def test_undated_count_reaches_the_processing_stats(self):
        _, stats = prepare_category([], undated_dropped=4)
        self.assertEqual(stats["undated_dropped"], 4)

    def test_every_fetched_item_is_accounted_for(self):
        """kept plus every drop reason must equal what was fetched."""
        items = [
            {"title": f"Story {n}", "url": f"https://ex.com/{n}",
             "published": f"2026-08-08T{n:02d}:00:00+00:00", "source": "NPR Politics"}
            for n in range(1, 10)
        ]
        items.append(dict(items[0], title="Story 1"))  # duplicate title
        kept, stats = prepare_category(items, source_cap=5, category_cap=8)
        self.assertEqual(len(kept), stats["kept"])
        self.assertEqual(
            stats["kept"] + stats["relevance_dropped"] + stats["duplicates_dropped"]
            + stats["source_cap_dropped"] + stats["category_cap_dropped"],
            stats["fetched"])


class PositiveIntTest(unittest.TestCase):
    def test_accepts_positive_values(self):
        self.assertEqual(positive_int("24"), 24)

    def test_rejects_zero_negative_and_non_numeric_values(self):
        for value in ("0", "-1", "nope"):
            with (self.subTest(value=value),
                  self.assertRaises(fetch_news.argparse.ArgumentTypeError)):
                positive_int(value)


class RedditTopBucketTest(unittest.TestCase):
    """`--hours` must reach Reddit too; pick the smallest bucket that covers it."""

    def test_default_window_requests_reddit_day_bucket(self):
        self.assertEqual(DEFAULT_WINDOW_HOURS, 24)
        self.assertEqual(reddit_top_bucket(DEFAULT_WINDOW_HOURS), "day")
        self.assertEqual(reddit_limit(DEFAULT_WINDOW_HOURS), 25)

    def test_default_fetch_url_uses_day_bucket(self):
        empty_feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with patch.object(fetch_news, "http_get", return_value=empty_feed) as get:
            self.assertEqual(fetch_reddit("ClaudeAI", utc(2026, 8, 8), DEFAULT_WINDOW_HOURS).items,
                             [])
        url = get.call_args.args[0]
        self.assertIn("t=day", url)
        self.assertIn("limit=25", url)

    def test_selects_smallest_covering_bucket(self):
        cases = [
            (1, "hour"),
            (2, "day"),
            (24, "day"),
            (25, "week"),
            (48, "week"),      # custom windows still choose a covering bucket
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


class MainFailureModeTest(unittest.TestCase):
    def test_empty_corpus_is_written_but_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            argv = ["fetch_news.py", "-o", str(output)]
            with (patch.object(fetch_news, "RSS_FEEDS", {}),
                  patch.object(fetch_news, "HN_QUERIES", []),
                  patch.object(fetch_news, "SUBREDDITS", []),
                  patch.object(fetch_news.sys, "argv", argv),
                  redirect_stdout(io.StringIO()),
                  redirect_stderr(io.StringIO()) as stderr):
                result = fetch_news.main()
            self.assertEqual(result, 1)
            self.assertIn("no usable items", stderr.getvalue())
            corpus = json.loads(output.read_text())
            self.assertEqual(sum(map(len, corpus["categories"].values())), 0)


class HttpGetTest(unittest.TestCase):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            return b"x" * limit

    def test_rejects_oversized_response(self):
        with patch.object(fetch_news.urllib.request, "urlopen", return_value=self.Response()):
            with self.assertRaisesRegex(ValueError, "response exceeded"):
                fetch_news.http_get("https://example.com/feed")
        self.assertEqual(MAX_RESPONSE_BYTES, 5 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
