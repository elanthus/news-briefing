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
import urllib.parse
import xml.etree.ElementTree as ET
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import corpus_schema
import fetch_news
from fetch_news import (
    DEFAULT_SOURCES_PATH,
    DEFAULT_WINDOW_HOURS,
    MAX_RESPONSE_BYTES,
    REDDIT_MAX_LIMIT,
    _reddit_md_text,
    apply_global_context_budget,
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
    publication_in_window,
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


class PublicationWindowTest(unittest.TestCase):
    def test_window_includes_start_and_excludes_end(self):
        cutoff = utc(2026, 8, 8, 12)
        window_end = utc(2026, 8, 9, 12)

        self.assertTrue(publication_in_window(cutoff, cutoff, window_end))
        self.assertFalse(publication_in_window(window_end, cutoff, window_end))
        self.assertFalse(
            publication_in_window(utc(2026, 8, 8, 11, 59, 59), cutoff, window_end)
        )
        self.assertFalse(
            publication_in_window(utc(2026, 8, 9, 12, 0, 1), cutoff, window_end)
        )


class StripHtmlTest(unittest.TestCase):
    def test_removes_tags_and_unescapes_entities(self):
        self.assertEqual(strip_html("<p>A &amp; B</p>"), "A & B")

    def test_trims_surrounding_whitespace(self):
        self.assertEqual(strip_html("  <b>x</b> "), "x")

    def test_handles_missing_text(self):
        """findtext() returns None for absent elements; that must not raise."""
        self.assertEqual(strip_html(None), "")


class FeedSummaryFallbackTest(unittest.TestCase):
    def test_item_published_during_fetch_is_outside_fixed_snapshot(self):
        feed = (b'<rss><channel>'
                b'<item><title>At boundary</title><link>https://ex.com/at</link>'
                b'<pubDate>Sat, 08 Aug 2026 12:00:00 GMT</pubDate></item>'
                b'<item><title>After boundary</title><link>https://ex.com/after</link>'
                b'<pubDate>Sat, 08 Aug 2026 12:00:01 GMT</pubDate></item>'
                b'</channel></rss>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed",
                utc(2026, 8, 7, 12), utc(2026, 8, 8, 12),
            )

        self.assertEqual(result.items, [])

    def test_rss_uses_content_encoded_when_description_is_empty(self):
        feed = (b'<rss xmlns:content="http://purl.org/rss/1.0/modules/content/">'
                b'<channel><item><title>Story</title><link>https://ex.com/story</link>'
                b'<pubDate>Sat, 08 Aug 2026 12:00:00 GMT</pubDate>'
                b'<description> </description>'
                b'<content:encoded><![CDATA[<p>Full <b>technical</b> summary</p>]]>'
                b'</content:encoded></item></channel></rss>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["summary"], "Full technical summary")

    def test_atom_uses_content_when_summary_is_empty(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title><link href="https://ex.com/story"/>'
                b'<published>2026-08-08T12:00:00Z</published><summary />'
                b'<content type="html">&lt;p&gt;Detailed Atom content&lt;/p&gt;</content>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["summary"], "Detailed Atom content")

    def test_atom_prefers_alternate_link_over_self_link(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title>'
                b'<link rel="self" href="https://ex.com/feed-entry"/>'
                b'<link rel="alternate" href="https://ex.com/article"/>'
                b'<published>2026-08-08T12:00:00Z</published>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["url"], "https://ex.com/article")

    def test_atom_treats_omitted_rel_as_alternate(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title>'
                b'<link rel="self" href="https://ex.com/feed-entry"/>'
                b'<link href="https://ex.com/article"/>'
                b'<published>2026-08-08T12:00:00Z</published>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["url"], "https://ex.com/article")

    def test_atom_falls_back_when_no_alternate_link_exists(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title>'
                b'<link rel="self" href="https://ex.com/feed-entry"/>'
                b'<published>2026-08-08T12:00:00Z</published>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["url"], "https://ex.com/feed-entry")

    def test_atom_rejects_blank_links_and_strips_the_selected_href(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                b'<title>Story</title>'
                b'<link rel="alternate" href="   "/>'
                b'<link rel="alternate" href=" https://ex.com/article "/>'
                b'<published>2026-08-08T12:00:00Z</published>'
                b'</entry></feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(result.items[0]["url"], "https://ex.com/article")


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

    def test_preserves_article_identifying_and_unknown_query_parameters(self):
        url = "https://example.com/story?output=1&id=2&edition=west&utm_source=rss"
        self.assertEqual(
            canonicalize_url(url),
            "https://example.com/story?edition=west&id=2&output=1",
        )


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
        """AI relevance terms must not override the commerce exclusion."""
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

    Every item cited in the committed briefing is a positive relevance fixture,
    including infrastructure and autonomous-vehicle stories that do not use
    the literal term "AI".
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

    def test_truncates_bounded_text_and_reports_telemetry(self):
        item = {
            **self.item(1),
            "title": "é" * 400,
            "summary": "x" * 500,
        }
        kept, stats = prepare_category([item])
        self.assertLessEqual(len(kept[0]["title"].encode("utf-8")), fetch_news.TITLE_BYTES)
        self.assertEqual(len(kept[0]["summary"]), fetch_news.SUMMARY_CHARS)
        self.assertEqual(stats["title_truncated"], 1)
        self.assertEqual(stats["summary_truncated"], 1)

    def test_summary_budget_preserves_complete_feed_date_near_old_boundary(self):
        summary = (
            "The Trump administration is ending summertime smog restrictions on gasoline "
            "early this year in a bid to offer relief for high prices at the pump.  The "
            "Environmental Protection Agency (EPA) issued a waiver on Thursday allowing "
            "for sales of gasoline with a higher evaporation potential starting on Sept. 1, "
            "rather than the middle of the month."
        )
        kept, stats = prepare_category([{**self.item(1), "summary": summary}])

        self.assertEqual(kept[0]["summary"], summary)
        self.assertIn("starting on Sept. 1", kept[0]["summary"])
        self.assertEqual(stats["summary_truncated"], 0)

    def test_per_field_token_ceilings_are_derived_telemetry(self):
        limits = (
            (corpus_schema.ITEM_TITLE_MAX_BYTES, corpus_schema.ITEM_TITLE_MAX_TOKENS),
            (corpus_schema.ITEM_URL_MAX_BYTES, corpus_schema.ITEM_URL_MAX_TOKENS),
            (corpus_schema.ITEM_SUMMARY_MAX_BYTES, corpus_schema.ITEM_SUMMARY_MAX_TOKENS),
            (corpus_schema.ITEM_SOURCE_MAX_BYTES, corpus_schema.ITEM_SOURCE_MAX_TOKENS),
            (corpus_schema.ITEM_QUERY_MAX_BYTES, corpus_schema.ITEM_QUERY_MAX_TOKENS),
        )
        for byte_limit, reported_tokens in limits:
            with self.subTest(byte_limit=byte_limit):
                self.assertEqual(
                    corpus_schema.estimated_tokens_for_bytes(byte_limit),
                    reported_tokens)

    def test_drops_overlong_urls_instead_of_changing_their_identity(self):
        item = {**self.item(1), "url": "https://example.com/" + "x" * 3000}
        kept, stats = prepare_category([item])
        self.assertEqual(kept, [])
        self.assertEqual(stats["field_budget_dropped"], 1)

    def test_enforces_per_source_byte_and_token_budgets(self):
        items = [
            {**self.item(n), "summary": "x" * 200}
            for n in range(1, 4)
        ]
        one_size, one_tokens = fetch_news.item_context_usage(items[0])
        kept, stats = prepare_category(
            items, source_byte_budget=one_size + 10,
            source_token_budget=one_tokens + 10)
        self.assertEqual(len(kept), 1)
        self.assertEqual(stats["source_budget_dropped"], 2)

    def test_enforces_one_global_budget_across_categories(self):
        first, first_stats = prepare_category([self.item(1, "A")])
        second, second_stats = prepare_category([self.item(2, "B")])
        size, tokens = fetch_news.item_context_usage(first[0])
        categories = {"first": first, "second": second}
        processing = {"first": first_stats, "second": second_stats}
        used = apply_global_context_budget(
            categories, processing, byte_budget=size + 1, token_budget=tokens + 1)
        self.assertEqual(used, (size, tokens))
        self.assertEqual(len(categories["first"]), 1)
        self.assertEqual(categories["second"], [])
        self.assertEqual(processing["second"]["global_budget_dropped"], 1)

    def test_budget_totals_serialize_each_item_once_per_budget_pass(self):
        items = [self.item(n) for n in range(1, 4)]
        real_usage = fetch_news.item_context_usage
        with patch.object(fetch_news, "item_context_usage", wraps=real_usage) as usage:
            kept, stats = prepare_category(items)
        self.assertEqual(usage.call_count, len(items))

        categories = {"news": kept}
        processing = {"news": stats}
        with patch.object(fetch_news, "item_context_usage", wraps=real_usage) as usage:
            apply_global_context_budget(categories, processing)
        self.assertEqual(usage.call_count, len(kept))


class HackerNewsTest(unittest.TestCase):
    def test_excludes_hits_after_the_fixed_snapshot(self):
        payload = {"hits": [
            {
                "objectID": "20", "title": "At boundary", "url": None,
                "story_text": "", "created_at_i": int(utc(2026, 8, 9).timestamp()),
                "points": fetch_news.HN_MIN_POINTS, "num_comments": 1,
            },
            {
                "objectID": "21", "title": "After boundary", "url": None,
                "story_text": "", "created_at_i": int(utc(2026, 8, 9, 0, 0, 1).timestamp()),
                "points": fetch_news.HN_MIN_POINTS, "num_comments": 1,
            },
        ]}
        with patch.object(
            fetch_news, "http_get", return_value=json.dumps(payload).encode()
        ) as get:
            result = fetch_hn("agent", utc(2026, 8, 8), utc(2026, 8, 9))

        self.assertEqual(result.items, [])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
        self.assertEqual(
            query["numericFilters"],
            [
                f"created_at_i>={int(utc(2026, 8, 8).timestamp())},"
                f"created_at_i<{int(utc(2026, 8, 9).timestamp())}"
            ],
        )

    def test_minimum_point_threshold_is_inclusive(self):
        payload = {"hits": [{
            "objectID": "20", "title": "At the threshold", "url": None,
            "story_text": "", "created_at_i": 1786204800,
            "points": fetch_news.HN_MIN_POINTS, "num_comments": 1,
        }]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8), utc(2026, 8, 9))
        self.assertEqual(len(result.items), 1)

    def test_carries_story_text_as_grounding_context(self):
        payload = {"hits": [{
            "objectID": "42", "title": "Ask HN", "url": None,
            "story_text": "<p>Measured details</p>", "created_at_i": 1786204800,
            "points": 21, "num_comments": 4,
        }]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8), utc(2026, 8, 9))
        self.assertEqual(result.items[0]["summary"], "Measured details")

    def test_counts_hits_with_no_usable_timestamp(self):
        """A hit without created_at_i is counted as undated without raising."""
        payload = {"hits": [
            {"objectID": "1", "title": "No date", "points": 99},
            {"objectID": "2", "title": "Dated", "url": "https://ex.com/a",
             "created_at_i": 1786204800, "points": 99, "num_comments": 1},
        ]}
        with patch.object(fetch_news, "http_get", return_value=json.dumps(payload).encode()):
            result = fetch_hn("agent", utc(2026, 8, 8), utc(2026, 8, 9))
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
            ("UTF-32", "utf-32"),
            ("ISO-8859-1", "iso-8859-1"),
        )
        for declaration, codec in cases:
            with self.subTest(encoding=declaration), self.assertRaises(ValueError):
                parse_feed_xml(document.format(encoding=declaration).encode(codec))

    def test_rejects_utf32_entity_expansion_payload(self):
        payload = self.BILLION_LAUGHS.decode("utf-8").replace(
            '<?xml version="1.0"?>',
            '<?xml version="1.0" encoding="UTF-32"?>',
        ).encode("utf-32")
        with self.assertRaises(ValueError):
            parse_feed_xml(payload)

    def test_parses_bom_marked_utf32_in_both_byte_orders(self):
        template = (
            '<?xml version="1.0" encoding="{encoding}"?>'
            '<rss><channel><item><title>ok</title></item></channel></rss>'
        )
        cases = (
            ("UTF-32", "utf-32"),
            ("UTF-32LE", "utf-32-le"),
            ("UTF-32BE", "utf-32-be"),
        )
        for declaration, codec in cases:
            text = template.format(encoding=declaration)
            if codec == "utf-32":
                payload = text.encode(codec)
            elif codec.endswith("le"):
                payload = b"\xff\xfe\x00\x00" + text.encode(codec)
            else:
                payload = b"\x00\x00\xfe\xff" + text.encode(codec)
            with self.subTest(codec=codec):
                self.assertEqual(parse_feed_xml(payload).find(".//title").text, "ok")

    def test_rejects_utf32_with_contradictory_declaration_or_invalid_bytes(self):
        contradictory = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<rss><channel /></rss>'
        ).encode("utf-32")
        with self.assertRaisesRegex(ValueError, "contradicts"):
            parse_feed_xml(contradictory)
        with self.assertRaises(UnicodeDecodeError):
            parse_feed_xml(b"\xff\xfe\x00\x00\x3c\x00\x00")

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
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 8, 1), utc(2026, 8, 9)
            )
        self.assertEqual(len(result.items), 1)
        self.assertEqual(result.undated, 2)

    def test_stale_items_are_not_counted_as_undated(self):
        """Too old and unparseable are different failures."""
        with patch.object(fetch_news, "http_get", return_value=self.FEED):
            result = fetch_news.fetch_rss(
                "Test", "https://ex.com/feed", utc(2026, 9, 1), utc(2026, 9, 2)
            )
        self.assertEqual(result.items, [])
        self.assertEqual(result.undated, 2)

    def test_undated_count_reaches_the_processing_stats(self):
        _, stats = prepare_category([], undated_dropped=4)
        self.assertEqual(stats["undated_dropped"], 4)

    def test_every_fetched_item_is_accounted_for(self):
        """Kept plus post-date-processing drops equals fetched; undated is separate."""
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
            + stats["source_cap_dropped"] + stats["category_cap_dropped"]
            + stats["field_budget_dropped"] + stats["source_budget_dropped"]
            + stats["global_budget_dropped"],
            stats["fetched"])


class PositiveIntTest(unittest.TestCase):
    def test_accepts_positive_values(self):
        self.assertEqual(positive_int("24"), 24)

    def test_rejects_zero_negative_and_non_numeric_values(self):
        for value in ("0", "-1", "nope"):
            with (self.subTest(value=value),
                  self.assertRaises(fetch_news.argparse.ArgumentTypeError)):
                positive_int(value)


class UtcTimestampTest(unittest.TestCase):
    def test_normalizes_an_explicit_offset(self):
        self.assertEqual(
            fetch_news.utc_timestamp("2026-08-20T00:00:00-04:00"),
            utc(2026, 8, 20, 4),
        )

    def test_rejects_naive_and_malformed_values(self):
        for value in ("2026-08-20T00:00:00", "not-a-timestamp"):
            with self.subTest(value=value), self.assertRaises(
                fetch_news.argparse.ArgumentTypeError
            ):
                fetch_news.utc_timestamp(value)


class RedditTopBucketTest(unittest.TestCase):
    """`--hours` must reach Reddit too; pick the smallest bucket that covers it."""

    def test_default_window_requests_reddit_day_bucket(self):
        self.assertEqual(DEFAULT_WINDOW_HOURS, 24)
        self.assertEqual(reddit_top_bucket(DEFAULT_WINDOW_HOURS), "day")
        self.assertEqual(reddit_limit(DEFAULT_WINDOW_HOURS), 25)

    def test_default_fetch_url_uses_day_bucket(self):
        empty_feed = b'<feed xmlns="http://www.w3.org/2005/Atom"></feed>'
        with patch.object(fetch_news, "http_get", return_value=empty_feed) as get:
            self.assertEqual(fetch_news.fetch_reddit_rss(
                "ClaudeAI", utc(2026, 8, 8), utc(2026, 8, 9), DEFAULT_WINDOW_HOURS
            ).items,
                             [])
        url = get.call_args.args[0]
        self.assertIn("t=day", url)
        self.assertIn("limit=25", url)

    def test_excludes_entries_after_the_fixed_snapshot(self):
        feed = (b'<feed xmlns="http://www.w3.org/2005/Atom">'
                b'<entry><title>At boundary</title><link href="https://ex.com/at"/>'
                b'<updated>2026-08-09T00:00:00Z</updated></entry>'
                b'<entry><title>After boundary</title><link href="https://ex.com/after"/>'
                b'<updated>2026-08-09T00:00:01Z</updated></entry>'
                b'</feed>')
        with patch.object(fetch_news, "http_get", return_value=feed):
            result = fetch_news.fetch_reddit_rss(
                "ClaudeAI", utc(2026, 8, 8), utc(2026, 8, 9), DEFAULT_WINDOW_HOURS
            )

        self.assertEqual(result.items, [])

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
        """A bucket narrower than the window would silently truncate coverage."""
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


class RedditFallbackTest(unittest.TestCase):
    CUTOFF = utc(2026, 8, 8)
    WINDOW_END = utc(2026, 8, 9)

    def result(self, title="Post"):
        return fetch_news.FetchResult([{
            "title": title,
            "url": "https://www.reddit.com/r/ClaudeCode/comments/abc123/",
            "published": "2026-08-08T12:00:00+00:00",
            "source": "r/ClaudeCode",
        }], 0, 1, 1)

    def test_rss_result_skips_both_fallbacks(self):
        with (patch.object(fetch_news, "fetch_reddit_rss", return_value=self.result("RSS")),
              patch.object(fetch_news, "fetch_reddit_arctic_shift") as arctic,
              patch.object(fetch_news, "fetch_reddit_scrapecreators") as authenticated):
            result = fetch_reddit(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
            )
        self.assertEqual(result.items[0]["title"], "RSS")
        arctic.assert_not_called()
        authenticated.assert_not_called()

    def test_arctic_shift_recovers_an_rss_error_without_spending_a_credit(self):
        with (patch.object(fetch_news, "fetch_reddit_rss", side_effect=urllib.error.HTTPError(
                  "https://reddit.test", 429, "Too Many Requests", {}, None)),
              patch.object(fetch_news, "fetch_reddit_arctic_shift",
                           return_value=self.result("Arctic")) as arctic,
              patch.object(fetch_news, "fetch_reddit_scrapecreators") as authenticated):
            result = fetch_reddit(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
            )
        self.assertEqual(result.items[0]["title"], "Arctic")
        arctic.assert_called_once()
        authenticated.assert_not_called()

    def test_authenticated_fallback_runs_only_after_both_free_paths_are_empty(self):
        empty = fetch_news.FetchResult([], 0, 0, 0)
        with (patch.object(fetch_news, "fetch_reddit_rss", return_value=empty),
              patch.object(fetch_news, "fetch_reddit_arctic_shift", return_value=empty),
              patch.object(fetch_news, "fetch_reddit_scrapecreators",
                           return_value=self.result("Authenticated")) as authenticated):
            result = fetch_reddit(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
            )
        self.assertEqual(result.items[0]["title"], "Authenticated")
        authenticated.assert_called_once_with(
            "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
        )

    def test_missing_key_returns_the_last_free_empty_result(self):
        rss_empty = fetch_news.FetchResult([], 2, 3, 1)
        arctic_empty = fetch_news.FetchResult([], 0, 0, 0)
        with (patch.object(fetch_news, "fetch_reddit_rss", return_value=rss_empty),
              patch.object(fetch_news, "fetch_reddit_arctic_shift", return_value=arctic_empty),
              patch.object(fetch_news, "fetch_reddit_scrapecreators") as authenticated):
            result = fetch_reddit(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24
            )
        self.assertEqual(result, arctic_empty)
        authenticated.assert_not_called()

    def test_authenticated_failure_preserves_a_valid_free_empty_result(self):
        rss_empty = fetch_news.FetchResult([], 2, 3, 1)
        arctic_empty = fetch_news.FetchResult([], 0, 0, 0)
        with (patch.object(fetch_news, "fetch_reddit_rss", return_value=rss_empty),
              patch.object(fetch_news, "fetch_reddit_arctic_shift", return_value=arctic_empty),
              patch.object(fetch_news, "fetch_reddit_scrapecreators",
                           side_effect=TimeoutError("authenticated provider down"))):
            result = fetch_reddit(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
            )
        self.assertEqual(result, arctic_empty)

    def test_all_free_transport_failures_remain_an_error_without_a_key(self):
        with (patch.object(fetch_news, "fetch_reddit_rss", side_effect=OSError("rss down")),
              patch.object(fetch_news, "fetch_reddit_arctic_shift",
                           side_effect=TimeoutError("archive down"))):
            with self.assertRaisesRegex(RuntimeError, "RSS.*Arctic Shift"):
                fetch_reddit("ClaudeCode", self.CUTOFF, self.WINDOW_END, 24)

    def test_arctic_shift_uses_exact_window_and_canonical_reddit_destinations(self):
        payload = json.dumps({"data": [{
            "id": "ABC123",
            "title": "Fresh &amp; useful",
            "selftext": "body",
            "created_utc": self.CUTOFF.timestamp() + 3600,
            "url": "https://attacker.example/not-used",
        }, {
            "id": "old123",
            "title": "Outside fixed window",
            "created_utc": self.CUTOFF.timestamp() - 1,
        }]}).encode()
        fractional_end = self.WINDOW_END + timedelta(microseconds=1)
        with patch.object(fetch_news, "http_get", return_value=payload) as get:
            result = fetch_news.fetch_reddit_arctic_shift(
                "ClaudeCode", self.CUTOFF, fractional_end
            )
        self.assertEqual([item["title"] for item in result.items], ["Fresh & useful"])
        self.assertEqual(
            result.items[0]["url"],
            "https://www.reddit.com/r/ClaudeCode/comments/abc123/",
        )
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
        self.assertEqual(query["after"], [str(int(self.CUTOFF.timestamp()))])
        self.assertEqual(query["before"], [str(int(self.WINDOW_END.timestamp()) + 1)])
        self.assertEqual(query["limit"], [str(fetch_news.REDDIT_FALLBACK_LIMIT)])

    def test_scrapecreators_result_is_filtered_to_the_fixed_window(self):
        payload = json.dumps({"success": True, "posts": [{
            "post_id": "t3_fresh1",
            "title": "Fresh",
            "selftext": "[removed]",
            "created_at_iso": "2026-08-08T12:00:00Z",
        }, {
            "post_id": "t3_future1",
            "title": "Future",
            "created_at_iso": "2026-08-09T00:00:01Z",
        }]}).encode()
        with patch.object(fetch_news, "scrapecreators_get", return_value=payload) as get:
            result = fetch_news.fetch_reddit_scrapecreators(
                "ClaudeCode", self.CUTOFF, self.WINDOW_END, 24, "secret"
            )
        self.assertEqual([item["title"] for item in result.items], ["Fresh"])
        self.assertNotIn("summary", result.items[0])
        self.assertEqual(get.call_args.args[1], "secret")
        self.assertNotIn("secret", get.call_args.args[0])
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(get.call_args.args[0]).query)
        self.assertEqual(query["sort"], ["new"])
        self.assertEqual(query["timeframe"], ["day"])


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
        """A newer unscored item sorts ahead of an older high-engagement item."""
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
    def test_fixed_window_end_is_normalized_and_recorded(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["news"],
                "rss_feeds": {},
                "hn_category": "news",
                "hn_queries": ["agent tools"],
                "reddit_category": "news",
                "subreddits": [],
            }), encoding="utf-8")
            item = {
                "title": "Historical agent release",
                "url": "https://example.com/agent",
                "published": "2026-08-19T12:00:00+00:00",
                "source": "Hacker News",
                "query": "agent tools",
            }
            argv = [
                "fetch_news.py",
                "--sources", str(sources),
                "--window-end", "2026-08-20T00:00:00-04:00",
                "-o", str(output),
            ]
            with (
                patch.object(fetch_news.sys, "argv", argv),
                patch.object(
                    fetch_news,
                    "fetch_hn",
                    return_value=fetch_news.FetchResult([item], 0),
                ) as fetch,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                result = fetch_news.main()

            self.assertEqual(result, 0)
            corpus = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(corpus["generated_at"], "2026-08-20T04:00:00+00:00")
            self.assertEqual(corpus["cutoff"], "2026-08-19T04:00:00+00:00")
            fetch.assert_called_once_with(
                "agent tools",
                utc(2026, 8, 19, 4),
                utc(2026, 8, 20, 4),
            )

    def test_explicit_calendar_window_records_report_date_and_dst_duration(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "corpus.json"
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["news"],
                "rss_feeds": {},
                "hn_category": "news",
                "hn_queries": ["agent tools"],
                "reddit_category": "news",
                "subreddits": [],
            }), encoding="utf-8")
            item = {
                "title": "Agent tools DST-day release",
                "url": "https://example.com/dst",
                "published": "2026-11-01T12:00:00+00:00",
                "source": "Hacker News",
                "query": "agent tools",
            }
            argv = [
                "fetch_news.py",
                "--sources", str(sources),
                "--window-start", "2026-11-01T00:00:00-04:00",
                "--window-end", "2026-11-02T00:00:00-05:00",
                "--report-date", "2026-11-01",
                "-o", str(output),
            ]
            with (
                patch.object(fetch_news.sys, "argv", argv),
                patch.object(
                    fetch_news,
                    "fetch_hn",
                    return_value=fetch_news.FetchResult([item], 0),
                ) as fetch,
                redirect_stdout(io.StringIO()),
                redirect_stderr(io.StringIO()),
            ):
                result = fetch_news.main()

            self.assertEqual(result, 0)
            corpus = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(corpus["report_date"], "2026-11-01")
            self.assertEqual(corpus["window_hours"], 25)
            self.assertEqual(corpus["cutoff"], "2026-11-01T04:00:00+00:00")
            self.assertEqual(corpus["generated_at"], "2026-11-02T05:00:00+00:00")
            fetch.assert_called_once_with(
                "agent tools",
                utc(2026, 11, 1, 4),
                utc(2026, 11, 2, 5),
            )

    def test_historical_source_without_window_entries_is_degraded(self):
        outcome = fetch_news.TimedFetchResult(
            fetch_news.FetchResult([], 0, parsed_entries=25, dated_entries=25),
            None,
            None,
            12,
            True,
        )
        status = fetch_news.source_status("rss", "Example", "news", outcome)
        self.assertEqual(status["status"], "empty")
        self.assertEqual(status["error_type"], "NoWindowEntries")

    def test_markdown_digest_omits_mutable_hn_points(self):
        with tempfile.TemporaryDirectory() as directory:
            sources = Path(directory) / "sources.json"
            sources.write_text(json.dumps({
                "categories": ["news"],
                "rss_feeds": {},
                "hn_category": "news",
                "hn_queries": ["agent tools"],
                "reddit_category": "news",
                "subreddits": [],
            }), encoding="utf-8")
            hn_result = fetch_news.FetchResult([{
                "title": "AI coding agent",
                "url": "https://example.com/hn",
                "discussion": "https://news.ycombinator.com/item?id=1",
                "published": datetime.now(timezone.utc).isoformat(),
                "source": "Hacker News",
                "query": "agent tools",
                "points": 32,
                "comments": 25,
            }], 0)
            argv = ["fetch_news.py", "--sources", str(sources), "--markdown"]
            with (patch.object(fetch_news.sys, "argv", argv),
                  patch.object(fetch_news, "fetch_hn", return_value=hn_result),
                  redirect_stdout(io.StringIO()) as stdout,
                  redirect_stderr(io.StringIO())):
                result = fetch_news.main()
            self.assertEqual(result, 0)
            self.assertIn("AI coding agent", stdout.getvalue())
            self.assertNotIn("32 pts", stdout.getvalue())

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
                  redirect_stdout(io.StringIO()) as stdout,
                  redirect_stderr(io.StringIO())):
                result = fetch_news.main()
            self.assertEqual(result, 1)
            # A failed fetch must not leave a corpus on disk (it would validate
            # and be published/reused as if the fetch succeeded); the value
            # still goes to stdout for inspection.
            self.assertFalse(output.exists())
            corpus = json.loads(stdout.getvalue())
            self.assertEqual(corpus["errors"][0]["source_type"], "rss")
            self.assertEqual(corpus["errors"][0]["source_id"], "Broken Feed")
            self.assertEqual(corpus["errors"][0]["error_type"], "ValueError")
            self.assertEqual(corpus["sources"][0]["status"], "error")
            self.assertEqual(corpus["sources"][0]["message"], "ValueError")

    def test_empty_corpus_is_not_written_and_returns_failure(self):
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
                  redirect_stdout(io.StringIO()) as stdout,
                  redirect_stderr(io.StringIO()) as stderr):
                result = fetch_news.main()
            self.assertEqual(result, 1)
            self.assertIn("no usable items", stderr.getvalue())
            # The zero-item corpus is schema-valid, so it must not be left on
            # disk where publication and backfill would treat it as a good run.
            self.assertFalse(output.exists())
            corpus = json.loads(stdout.getvalue())
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
            self.assertTrue(all(status["retained_bytes"] >= 0
                                and status["estimated_tokens"] >= 0
                                for status in corpus["sources"]))
            self.assertEqual(
                corpus["context_budget"]["used_bytes"],
                sum(stats["context_bytes"] for stats in corpus["processing"].values()))
            self.assertLessEqual(
                corpus["context_budget"]["used_bytes"],
                corpus["context_budget"]["global_max_bytes"])

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
                  redirect_stdout(io.StringIO()) as stdout,
                  redirect_stderr(io.StringIO())):
                self.assertEqual(fetch_news.main(), 1)
            self.assertFalse(output.exists())
            corpus = json.loads(stdout.getvalue())
            health = corpus["sources"][0]
            self.assertTrue(health["http_success"])
            self.assertEqual(health["parsed_entries"], 0)
            self.assertEqual(health["status"], "empty")
            self.assertEqual(health["error_type"], "EmptySource")
            self.assertEqual(corpus["errors"][0]["source_id"], "Changed Feed")


class PublicIpTest(unittest.TestCase):
    """NAT64/6to4 IPv6 forms can embed a private IPv4 address; the address
    that reaches the socket must be checked, not just the IPv6 wrapper."""

    def test_rejects_addresses_embedded_in_translation_prefixes(self):
        for address in (
            "64:ff9b::7f00:1",         # well-known NAT64 (RFC 6052) embedding 127.0.0.1
            "64:ff9b::a9fe:a9fe",      # well-known NAT64 embedding 169.254.169.254
            "64:ff9b:1::7f00:1",       # local-use NAT64 (RFC 8215) embedding 127.0.0.1
            "64:ff9b:1::a9fe:a9fe",    # local-use NAT64 embedding 169.254.169.254
            "2002:7f00:0001::",        # 6to4 (RFC 3056) embedding 127.0.0.1
            "2002:a9fe:a9fe::",        # 6to4 embedding 169.254.169.254
            "::7f00:1",                # IPv4-compatible (RFC 4291) embedding 127.0.0.1
            "::a9fe:a9fe",             # IPv4-compatible embedding 169.254.169.254
            "::ffff:0:7f00:1",         # IPv4-translated (RFC 2765) embedding 127.0.0.1
            "::ffff:0:a9fe:a9fe",      # IPv4-translated embedding 169.254.169.254
        ):
            with self.subTest(address=address):
                self.assertFalse(fetch_news._public_ip(address))

    def test_accepts_a_public_address_embedded_in_the_well_known_nat64_prefix(self):
        self.assertTrue(fetch_news._public_ip("64:ff9b::5db8:d822"))  # 93.184.216.34


class HttpGetTest(unittest.TestCase):
    PUBLIC = (fetch_news.ResolvedAddress(2, ("93.184.216.34", 443)),)

    def get(self, results, url="https://example.com/feed"):
        with (patch.object(fetch_news, "_resolve_public_addresses",
                           return_value=self.PUBLIC),
              patch.object(fetch_news, "_request_once", side_effect=results)):
            return fetch_news.http_get(url)

    def test_rejects_oversized_response(self):
        result = fetch_news.HttpResult(200, "OK", {}, b"x" * (MAX_RESPONSE_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "response exceeded"):
            self.get([result])
        self.assertEqual(MAX_RESPONSE_BYTES, 5 * 1024 * 1024)

    def test_requests_identify_the_project_and_carry_a_contact_url(self):
        """An operator seeing this traffic must be able to look up who is sending it.

        Every clone polls the same public feeds from a different address, so the
        User-Agent is the only thing tying that traffic back to a project. A bare
        description gives a feed owner nothing to search for and no way to reach
        anyone before resorting to a block.
        """
        captured = []

        def fake_request(*args):
            captured.append(args)
            return fetch_news.HttpResult(200, "OK", {}, b"ok")

        with (patch.object(fetch_news, "_resolve_public_addresses",
                           return_value=self.PUBLIC),
              patch.object(fetch_news, "_request_once", side_effect=fake_request)):
            self.assertEqual(fetch_news.http_get("https://example.com/feed"), b"ok")

        agent = captured[0][-2]
        self.assertIn("news-briefing/", agent)
        self.assertRegex(agent, r"https://github\.com/\S+")

    def test_rejects_non_http_credentials_and_private_literal_destinations(self):
        for url in (
            "file:///etc/passwd",
            "https://user:secret@example.com/feed",
            "http://127.0.0.1/feed",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/feed",
            "http://[64:ff9b::7f00:1]/feed",
            "http://[64:ff9b::a9fe:a9fe]/feed",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                fetch_news.http_get(url)

    def test_dns_answers_must_all_be_public(self):
        answers = [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.7", 443)),
        ]
        with patch.object(fetch_news.socket, "getaddrinfo", return_value=answers):
            with self.assertRaisesRegex(ValueError, "non-public address 10.0.0.7"):
                fetch_news._resolve_public_addresses("example.com", 443)

    def test_request_uses_the_address_from_the_single_dns_resolution(self):
        captured = []

        def fake_request(*args):
            captured.append(args)
            return fetch_news.HttpResult(200, "OK", {}, b"ok")

        with (patch.object(fetch_news, "_resolve_public_addresses",
                           return_value=self.PUBLIC) as resolve,
              patch.object(fetch_news, "_request_once", side_effect=fake_request)):
            self.assertEqual(fetch_news.http_get("https://example.com/feed"), b"ok")
        resolve.assert_called_once_with("example.com", 443)
        self.assertEqual(captured[0][4], self.PUBLIC[0])

    def test_redirect_destination_is_revalidated_and_repinned(self):
        redirect = fetch_news.HttpResult(
            302, "Found", {"Location": "https://cdn.example.net/feed"}, b"")
        ok = fetch_news.HttpResult(200, "OK", {}, b"ok")
        first = self.PUBLIC
        second = (fetch_news.ResolvedAddress(2, ("93.184.216.35", 443)),)
        with (patch.object(fetch_news, "_resolve_public_addresses",
                           side_effect=[first, second]) as resolve,
              patch.object(fetch_news, "_request_once", side_effect=[redirect, ok])):
            self.assertEqual(fetch_news.http_get("https://example.com/feed"), b"ok")
        self.assertEqual(resolve.call_args_list[1].args, ("cdn.example.net", 443))

    def test_redirect_to_private_destination_is_rejected_before_request(self):
        redirect = fetch_news.HttpResult(
            302, "Found", {"Location": "http://127.0.0.1/admin"}, b"")
        with (patch.object(fetch_news, "_resolve_public_addresses",
                           return_value=self.PUBLIC),
              patch.object(fetch_news, "_request_once", return_value=redirect) as request):
            with self.assertRaisesRegex(ValueError, "non-public"):
                fetch_news.http_get("https://example.com/feed")
        self.assertEqual(request.call_count, 1)

    def test_scrapecreators_key_is_origin_locked_and_redirects_are_refused(self):
        redirect = fetch_news.HttpResult(
            302, "Found", {"Location": "https://attacker.example/collect"}, b"")
        captured = []

        def fake_request(*args):
            captured.append(args)
            return redirect

        with (patch.object(fetch_news, "_resolve_public_addresses",
                           return_value=self.PUBLIC),
              patch.object(fetch_news, "_request_once", side_effect=fake_request)):
            with self.assertRaises(urllib.error.HTTPError):
                fetch_news.scrapecreators_get(
                    "https://api.scrapecreators.com/v1/reddit/subreddit?subreddit=test",
                    "secret",
                )
        self.assertEqual(captured[0][-1]["x-api-key"], "secret")
        self.assertEqual(len(captured), 1)
        with self.assertRaisesRegex(ValueError, "only be sent"):
            fetch_news.scrapecreators_get("https://attacker.example/collect", "secret")

    def test_scrapecreators_key_must_be_single_line(self):
        for key in ("", " ", "secret\nforwarded"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                fetch_news.scrapecreators_get(
                    "https://api.scrapecreators.com/v1/reddit/subreddit", key
                )


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

    def test_rejects_unsafe_feed_urls_before_fetching(self):
        for url in (
            "file:///etc/passwd",
            "http://localhost@127.0.0.1/feed",
            "http://169.254.169.254/latest/meta-data",
        ):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as directory:
                path = self.write_sources(directory, {
                    "categories": ["news"],
                    "rss_feeds": {"news": [["News", url]]},
                    "hn_category": "news", "hn_queries": [],
                    "reddit_category": "news", "subreddits": [],
                })
                with self.assertRaisesRegex(ValueError, "unsafe URL"):
                    load_sources(path)

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

    def test_rejects_duplicate_source_ids_before_fetching(self):
        cases = (
            ("rss", {"rss_feeds": {"news": [["Same", "https://ex.com/a"],
                                                 ["Same", "https://ex.com/b"]]}}),
            ("hacker news", {"hn_queries": ["agent", "agent"]}),
            ("reddit", {"subreddits": ["python", "python"]}),
        )
        for label, override in cases:
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                value = {
                    "categories": ["news"],
                    "rss_feeds": {"news": [["News", "https://example.com/feed.xml"]]},
                    "hn_category": "news",
                    "hn_queries": ["agent"],
                    "reddit_category": "news",
                    "subreddits": [],
                }
                value.update(override)
                path = self.write_sources(directory, value)
                with self.assertRaisesRegex(ValueError, "duplicate source ID"):
                    load_sources(path)

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
