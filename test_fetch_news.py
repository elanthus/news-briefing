#!/usr/bin/env python3
"""Offline unit and failure-mode tests for fetch_news.py.

Everything under test here is deterministic and offline. Network boundaries
are patched only where the behavior at that boundary is itself the contract.

Run:
    python3 -m unittest -v
"""

import io
import json
import re
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import fetch_news
from fetch_news import (
    DEFAULT_SOURCES_PATH,
    DEFAULT_WINDOW_HOURS,
    MAX_RESPONSE_BYTES,
    REDDIT_MAX_LIMIT,
    _reddit_md_text,
    canonicalize_url,
    dedupe,
    fetch_hn,
    fetch_reddit,
    is_relevant_item,
    load_sources,
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

    def test_malformed_rfc_dates_cannot_escape_parser_exceptions(self):
        for error in (OverflowError, IndexError):
            with (self.subTest(error=error.__name__),
                  patch.object(fetch_news, "parsedate_to_datetime", side_effect=error)):
                self.assertIsNone(parse_feed_date("malformed"))

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


class FeedSummaryFallbackTest(unittest.TestCase):
    def test_rss_uses_content_encoded_when_description_is_empty(self):
        feed = (b'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
                b'<channel><item><title>Story</title><link>https://ex.com/story</link>'
                b'<pubDate>Sat, 08 Aug 2026 12:00:00 GMT</pubDate>'
                b'<description> </description>'
                b'<content:encoded><![CDATA[<p>Full <b>technical</b> summary</p>]]>'
                b'</content:encoded></item></channel></rss>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss("Test", "https://ex.com/feed", utc(2026, 8, 1))
        self.assertEqual(result.items[0]["summary"], "Full technical summary")

    def test_atom_uses_content_when_summary_is_empty(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title><link href="https://ex.com/story"/>'
                b'<published>2026-08-08T12:00:00Z</published><summary />'
                b'<content type="html">&lt;p&gt;Detailed Atom content&lt;/p&gt;</content>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss("Test", "https://ex.com/feed", utc(2026, 8, 1))
        self.assertEqual(result.items[0]["summary"], "Detailed Atom content")


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

    def test_keeps_ai_stories_that_never_say_ai(self):
        """Infrastructure and autonomy are AI industry news without the word."""
        for title in (
            "An Amazon data center could have the worst polluting power plant",
            "The first self-driving vehicle on Mars has proven a smashing success",
            "Nvidia's next GPU pushes inference costs down",
            "Chipmakers race to expand semiconductor capacity",
        ):
            with self.subTest(title=title):
                self.assertTrue(is_relevant_item(
                    {"source": "The Verge", "title": title, "summary": ""}))

    def test_commerce_content_is_dropped_even_when_it_mentions_ai(self):
        """The broadened vocabulary must not readmit deals pages."""
        for title in (
            "Best GPU Deals (2026): Nvidia, AMD, and More",
            "The AI-powered HP OmniBook Is $550 Off Its Retail Price Today",
            "Surfshark Promo Codes: 87% Off AI Tools | August 2026",
            "Neural Earbuds Review (2026): Fun but Limited",
        ):
            with self.subTest(title=title):
                self.assertFalse(is_relevant_item(
                    {"source": "Wired", "title": title, "summary": ""}))

    def test_industry_moves_are_not_mistaken_for_commerce(self):
        """"Deal" is the standard word for an acquisition or contract."""
        for title in (
            "OpenAI signs multibillion-dollar cloud deal with Oracle",
            "Anthropic strikes chip deal with Google",
        ):
            with self.subTest(title=title):
                self.assertTrue(is_relevant_item(
                    {"source": "The Verge", "title": title, "summary": ""}))


class ReferenceBriefingCoverageTest(unittest.TestCase):
    """The filter must not delete stories the reference briefing led with.

    Ground truth without hand-labelling: every item cited in the committed
    briefing was judged worth reporting, so the filter has to let all of them
    reach the corpus. This is the check that caught the filter removing the
    data-center and self-driving stories that were AI News topics #1 and #4.
    """

    def test_no_cited_item_would_be_filtered_out(self):
        corpus = json.loads(Path("fixtures/corpus-2026-08-09.json").read_text(encoding="utf-8"))
        briefing = Path("fixtures/briefing-2026-08-09.md").read_text(encoding="utf-8")
        cited = set(re.findall(r"🔗\s*(?:HN:\s*)?(\S+)", briefing))
        dropped = [item["title"]
                   for items in corpus["categories"].values() for item in items
                   if item.get("url") in cited and not is_relevant_item(item)]
        self.assertEqual(dropped, [], f"filter removes cited stories: {dropped}")


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
    def test_minimum_point_threshold_is_inclusive(self):
        payload = {"hits": [{
            "objectID": "20", "title": "At the threshold", "url": None,
            "story_text": "", "created_at_i": 1786204800,
            "points": fetch_news.HN_MIN_POINTS, "num_comments": 1,
        }]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8))
        self.assertEqual(len(result.items), 1)

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

    def test_rejects_encoded_doctype_declarations(self):
        document = ('<?xml version="1.0" encoding="{encoding}"?>'
                    '<!DOCTYPE r [<!ENTITY x "expanded">]>'
                    '<rss><item><title>&x;</title></item></rss>')
        cases = (
            ("UTF-16", "utf-16"),
            ("UTF-16", "utf-16-le"),
            ("UTF-16", "utf-16-be"),
            ("ISO-8859-1", "iso-8859-1"),
        )
        for declaration, codec in cases:
            with self.subTest(encoding=declaration), self.assertRaises(ValueError):
                parse_feed_xml(document.format(encoding=declaration).encode(codec))

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

    def test_orders_by_instant_not_iso_string_spelling(self):
        items = [
            {"title": "earlier", "published": "2026-08-08T10:00:00+02:00"},
            {"title": "later", "published": "2026-08-08T09:30:00+00:00"},
        ]
        self.assertEqual([i["title"] for i in sort_items(items)],
                         ["later", "earlier"])

    def test_invalid_timestamps_sort_last_for_schema_validation(self):
        items = [
            {"title": "malformed", "published": "yesterday"},
            {"title": "valid", "published": "2026-08-08T09:30:00+00:00"},
            {"title": "naive", "published": "2026-08-08T10:00:00"},
        ]
        self.assertEqual(sort_items(items)[0], items[1])

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
    def test_empty_exception_message_is_recorded_as_a_source_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["news"],
                "rss_feeds": {"news": [["Broken Feed", "https://example.com/feed"]]},
                "hn_category": "news",
                "hn_queries": [],
                "reddit_category": "news",
                "subreddits": [],
            }), encoding="utf-8")
            argv = ["fetch_news.py", "--sources", str(sources), "-o", str(output)]
            with (patch.object(fetch_news.sys, "argv", argv),
                  patch.object(fetch_news, "fetch_rss", side_effect=ValueError()),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                result = fetch_news.main()
            self.assertEqual(result, 1)
            corpus = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(corpus["errors"][0]["source_type"], "rss")
            self.assertEqual(corpus["errors"][0]["source_id"], "Broken Feed")
            self.assertEqual(corpus["errors"][0]["error_type"], "ValueError")
            self.assertEqual(corpus["sources"][0]["status"], "error")
            self.assertEqual(corpus["sources"][0]["message"], "ValueError")

    def test_empty_corpus_is_written_but_returns_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["empty"],
                "rss_feeds": {
                    "empty": [["Empty Feed", "https://example.com/feed"]],
                },
                "hn_category": "empty",
                "hn_queries": [],
                "reddit_category": "empty",
                "subreddits": [],
            }), encoding="utf-8")
            argv = ["fetch_news.py", "--sources", str(sources), "-o", str(output)]
            with (patch.object(fetch_news.sys, "argv", argv),
                  patch.object(fetch_news, "fetch_rss",
                               return_value=fetch_news.FetchResult([], 0)),
                  redirect_stdout(io.StringIO()),
                  redirect_stderr(io.StringIO()) as stderr):
                result = fetch_news.main()
            self.assertEqual(result, 1)
            self.assertIn("no usable items", stderr.getvalue())
            corpus = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(sum(map(len, corpus["categories"].values())), 0)

    def test_community_sources_use_their_configured_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["from_hn", "from_reddit"],
                "rss_feeds": {},
                "hn_category": "from_hn",
                "hn_queries": ["agent tools"],
                "reddit_category": "from_reddit",
                "subreddits": ["LocalLLaMA"],
            }), encoding="utf-8")
            published = datetime.now(timezone.utc).isoformat()
            hn_result = fetch_news.FetchResult([{
                "title": "AI coding agent",
                "url": "https://example.com/hn",
                "published": published,
                "source": "Hacker News",
                "query": "agent tools",
            }], 0)
            reddit_result = fetch_news.FetchResult([{
                "title": "Local model release",
                "url": "https://example.com/reddit",
                "published": published,
                "source": "r/LocalLLaMA",
            }], 0)
            argv = ["fetch_news.py", "--sources", str(sources), "-o", str(output)]
            with (patch.object(fetch_news.sys, "argv", argv),
                  patch.object(fetch_news, "fetch_hn", return_value=hn_result),
                  patch.object(fetch_news, "fetch_reddit", return_value=reddit_result),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                result = fetch_news.main()
            self.assertEqual(result, 0)
            corpus = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual([item["source"] for item in corpus["categories"]["from_hn"]],
                             ["Hacker News"])
            self.assertEqual([item["source"] for item in corpus["categories"]["from_reddit"]],
                             ["r/LocalLLaMA"])
            self.assertGreaterEqual(corpus["fetch_duration_ms"], 0)
            self.assertEqual(
                [(status["source_type"], status["source_id"], status["status"],
                  status["retained_entries"])
                 for status in corpus["sources"]],
                [("hacker_news", "agent tools", "ok", 1),
                 ("reddit", "LocalLLaMA", "ok", 1)],
            )
            self.assertTrue(all(status["duration_ms"] >= 0
                                for status in corpus["sources"]))

    def test_successful_but_unrecognized_feed_is_a_structured_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["empty"],
                "rss_feeds": {"empty": [["Changed Feed", "https://example.com/feed"]]},
                "hn_category": "empty",
                "hn_queries": [],
                "reddit_category": "empty",
                "subreddits": [],
            }), encoding="utf-8")
            argv = ["fetch_news.py", "--sources", str(sources), "-o", str(output)]
            with (patch.object(fetch_news.sys, "argv", argv),
                  patch.object(fetch_news, "http_get", return_value=b"<html><body>ok</body></html>"),
                  redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO())):
                self.assertEqual(fetch_news.main(), 1)
            corpus = json.loads(output.read_text(encoding="utf-8"))
            health = corpus["sources"][0]
            self.assertTrue(health["http_success"])
            self.assertEqual(health["parsed_entries"], 0)
            self.assertEqual(health["status"], "empty")
            self.assertEqual(health["error_type"], "EmptySource")
            self.assertEqual(corpus["errors"][0]["source_id"], "Changed Feed")


class HttpGetTest(unittest.TestCase):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit):
            return b"x" * limit

    class ShortResponse(Response):
        def read(self, _limit):
            return b"ok"

    def test_rejects_oversized_response(self):
        with patch.object(fetch_news.urllib.request, "urlopen", return_value=self.Response()):
            with self.assertRaisesRegex(ValueError, "response exceeded"):
                fetch_news.http_get("https://example.com/feed")
        self.assertEqual(MAX_RESPONSE_BYTES, 5 * 1024 * 1024)

    def test_requests_identify_the_project_and_carry_a_contact_url(self):
        """An operator seeing this traffic must be able to look up who is sending it.

        Every clone polls the same public feeds from a different address, so the
        User-Agent is the only thing tying that traffic back to a project. A bare
        description gives a feed owner nothing to search for and no way to reach
        anyone before resorting to a block.
        """
        captured = []

        def fake_urlopen(request, **_kwargs):
            captured.append(request)
            return self.ShortResponse()

        with patch.object(fetch_news.urllib.request, "urlopen", fake_urlopen):
            self.assertEqual(fetch_news.http_get("https://example.com/feed"), b"ok")

        agent = captured[0].get_header("User-agent")
        self.assertIn("news-briefing/", agent)
        self.assertRegex(agent, r"https://github\.com/\S+")


class FeedConfigurationTest(unittest.TestCase):
    """The fetcher's categories must match the contract, or the corpus it
    writes fails validation before it is ever read."""

    @classmethod
    def setUpClass(cls):
        cls.sources = load_sources(DEFAULT_SOURCES_PATH)

    def test_every_declared_category_has_a_source(self):
        sourced = {
            category for category, feeds in self.sources.rss_feeds.items() if feeds
        }
        if self.sources.hn_queries:
            sourced.add(self.sources.hn_category)
        if self.sources.subreddits:
            sourced.add(self.sources.reddit_category)
        self.assertEqual(sourced, set(self.sources.categories))

    def test_no_feed_url_is_fetched_under_two_categories(self):
        """The same feed under two categories duplicates every item it
        carries, every run. Outlet overlap is a different thing and is
        deliberate — NPR files under politics, national and world — and the
        one-placement rule in the briefing is what resolves that.
        """
        urls = [url for feeds in self.sources.rss_feeds.values() for _, url in feeds]
        self.assertEqual([u for u, n in Counter(urls).items() if n > 1], [])


class SourcesConfigurationTest(unittest.TestCase):
    def write_sources(self, directory, value):
        path = Path(directory) / "sources.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_default_configuration_loads(self):
        sources = load_sources(DEFAULT_SOURCES_PATH)
        self.assertGreater(len(sources.categories), 0)

    def test_source_identifiers_are_single_line_but_may_contain_colons(self):
        cases = (
            ("rss newline", "rss", "News\nAI", "single-line"),
            ("HN newline", "hn", "agent\ncoding", "single-line"),
        )
        for label, route, bad_value, message in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                value = {
                    "categories": ["news"],
                    "rss_feeds": {"news": [["News", "https://example.com/feed.xml"]]},
                    "hn_category": "news",
                    "hn_queries": ["agent"],
                    "reddit_category": "news",
                    "subreddits": [],
                }
                if route == "rss":
                    value["rss_feeds"]["news"][0][0] = bad_value
                else:
                    value["hn_queries"][0] = bad_value
                path = self.write_sources(directory, value)
                with self.assertRaisesRegex(ValueError, message):
                    load_sources(path)

        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news"],
                "rss_feeds": {"news": [["News: AI", "https://example.com/feed.xml"]]},
                "hn_category": "news",
                "hn_queries": ["agent: coding"],
                "reddit_category": "news",
                "subreddits": [],
            })
            loaded = load_sources(path)
            self.assertEqual(loaded.rss_feeds["news"][0][0], "News: AI")
            self.assertEqual(loaded.hn_queries, ["agent: coding"])

    def test_rejects_missing_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {"rss_feeds": {}, "hn_queries": []})
            with self.assertRaisesRegex(ValueError, "missing field.*subreddits"):
                load_sources(path)

    def test_configuration_defines_category_order_and_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                # Deliberately differs from rss_feeds key order: the explicit
                # list, not source-map insertion order, controls corpus order.
                "categories": ["policy", "climate"],
                "rss_feeds": {
                    "climate": [["Climate News", "https://example.com/climate.xml"]],
                    "policy": [["Policy News", "https://example.com/policy.xml"]],
                },
                "hn_category": "climate",
                "hn_queries": ["climate tech"],
                "reddit_category": "policy",
                "subreddits": ["climate"],
            })
            sources = load_sources(path)
            self.assertEqual(sources.categories, ("policy", "climate"))
            self.assertEqual(sources.hn_category, "climate")
            self.assertEqual(sources.reddit_category, "policy")

    def test_rejects_undeclared_rss_category(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news"],
                "rss_feeds": {"typo": []},
                "hn_category": "news",
                "hn_queries": [],
                "reddit_category": "news",
                "subreddits": [],
            })
            with self.assertRaisesRegex(ValueError, "undeclared categories: typo"):
                load_sources(path)

    def test_rejects_undeclared_hacker_news_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news"],
                "rss_feeds": {"news": []},
                "hn_category": "dev_community",
                "hn_queries": [],
                "reddit_category": "news",
                "subreddits": [],
            })
            with self.assertRaisesRegex(ValueError, "hn_category references undeclared"):
                load_sources(path)

    def test_rejects_duplicate_categories(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news", "news"],
                "rss_feeds": {"news": []},
                "hn_category": "news",
                "hn_queries": [],
                "reddit_category": "news",
                "subreddits": [],
            })
            with self.assertRaisesRegex(ValueError, "categories contains a duplicate"):
                load_sources(path)

    def test_rejects_category_without_a_source_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news", "ghost"],
                "rss_feeds": {
                    "news": [["News", "https://example.com/news.xml"]],
                    "ghost": [],
                },
                "hn_category": "news",
                "hn_queries": [],
                "reddit_category": "news",
                "subreddits": [],
            })
            with self.assertRaisesRegex(ValueError, "without a source destination: ghost"):
                load_sources(path)

    def test_invalid_query_shape_is_reported_before_routing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_sources(directory, {
                "categories": ["news"],
                "rss_feeds": {},
                "hn_category": "news",
                "hn_queries": "not-a-list",
                "reddit_category": "news",
                "subreddits": [],
            })
            with self.assertRaisesRegex(ValueError, "hn_queries must be a list"):
                load_sources(path)


if __name__ == "__main__":
    unittest.main()
