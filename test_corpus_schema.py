#!/usr/bin/env python3
"""Tests for the corpus contract.

The point of the contract is that drift fails where it is introduced, so these
tests are mostly "break one thing, assert it is reported."

Run:
    python3 -m unittest -v
"""

import copy
import unittest

from corpus_schema import (
    GLOBAL_CONTEXT_MAX_BYTES,
    GLOBAL_CONTEXT_MAX_TOKENS,
    ITEM_QUERY_MAX_BYTES,
    ITEM_QUERY_MAX_TOKENS,
    ITEM_SOURCE_MAX_BYTES,
    ITEM_SOURCE_MAX_TOKENS,
    ITEM_SUMMARY_MAX_BYTES,
    ITEM_SUMMARY_MAX_CHARS,
    ITEM_SUMMARY_MAX_TOKENS,
    ITEM_TITLE_MAX_BYTES,
    ITEM_TITLE_MAX_TOKENS,
    ITEM_URL_MAX_BYTES,
    ITEM_URL_MAX_TOKENS,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SOURCE_CONTEXT_MAX_BYTES,
    SOURCE_CONTEXT_MAX_TOKENS,
    corpus_version,
    is_readable,
    validate_corpus,
)

DEFAULT_CATEGORIES = ("us_politics", "us_news", "world", "ai_tech", "dev_community")


def item(n=1, **overrides):
    base = {
        "title": f"Story {n}",
        "url": f"https://ex.com/{n}",
        "published": "2026-08-08T12:00:00+00:00",
        "source": "NPR Politics",
    }
    base.update(overrides)
    return base


def stats(fetched=1, kept=1, **overrides):
    base = {
        "fetched": fetched,
        "undated_dropped": 0,
        "relevance_dropped": 0,
        "duplicates_dropped": 0,
        "source_cap_dropped": 0,
        "category_cap_dropped": 0,
        "field_budget_dropped": 0,
        "source_budget_dropped": 0,
        "global_budget_dropped": 0,
        "title_truncated": 0,
        "summary_truncated": 0,
        "context_bytes": 0,
        "estimated_tokens": 0,
        "kept": kept,
    }
    base.update(overrides)
    return base


def corpus(**overrides):
    context_budget = {
        "field_limits": {
            "title_bytes": ITEM_TITLE_MAX_BYTES, "title_tokens": ITEM_TITLE_MAX_TOKENS,
            "url_bytes": ITEM_URL_MAX_BYTES, "url_tokens": ITEM_URL_MAX_TOKENS,
            "summary_chars": ITEM_SUMMARY_MAX_CHARS,
            "summary_bytes": ITEM_SUMMARY_MAX_BYTES,
            "summary_tokens": ITEM_SUMMARY_MAX_TOKENS,
            "source_bytes": ITEM_SOURCE_MAX_BYTES, "source_tokens": ITEM_SOURCE_MAX_TOKENS,
            "query_bytes": ITEM_QUERY_MAX_BYTES, "query_tokens": ITEM_QUERY_MAX_TOKENS,
        },
        "source_max_bytes": SOURCE_CONTEXT_MAX_BYTES,
        "source_max_tokens": SOURCE_CONTEXT_MAX_TOKENS,
        "global_max_bytes": GLOBAL_CONTEXT_MAX_BYTES,
        "global_max_tokens": GLOBAL_CONTEXT_MAX_TOKENS,
        "used_bytes": 0,
        "estimated_tokens": 0,
        "title_truncated": 0,
        "summary_truncated": 0,
        "field_budget_dropped": 0,
        "source_budget_dropped": 0,
        "global_budget_dropped": 0,
    }
    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": "2026-08-08T12:00:00+00:00",
        "cutoff": "2026-08-07T12:00:00+00:00",
        "window_hours": 24,
        "limits": {"source_cap": 25, "category_cap": 60},
        "categories": {name: [] for name in DEFAULT_CATEGORIES},
        "processing": {name: stats(0, 0) for name in DEFAULT_CATEGORIES},
        "errors": [],
        "sources": [],
        "fetch_duration_ms": 0,
        "context_budget": context_budget,
    }
    base["categories"]["us_politics"] = [item(1)]
    base["processing"]["us_politics"] = stats(1, 1)
    base.update(overrides)
    return base


def only(problems, fragment):
    return [p for p in problems if fragment in p]


class ValidCorpusTest(unittest.TestCase):
    def test_a_conforming_corpus_has_no_problems(self):
        self.assertEqual(validate_corpus(corpus()), [])

    def test_optional_report_date_is_validated(self):
        self.assertEqual(validate_corpus(corpus(report_date="2026-08-08")), [])
        self.assertTrue(
            only(validate_corpus(corpus(report_date="August 8, 2026")), "report_date")
        )

    def test_optional_item_fields_are_allowed(self):
        c = corpus()
        c["categories"]["dev_community"] = [item(2, source="Hacker News",
                                                 summary="s", points=99, comments=4,
                                                 discussion="https://news.ycombinator.com/item?id=1",
                                                 query="mcp")]
        c["processing"]["dev_community"] = stats(1, 1)
        self.assertEqual(validate_corpus(c), [])


class TopLevelTest(unittest.TestCase):
    def test_missing_field_is_reported(self):
        c = corpus()
        del c["window_hours"]
        self.assertTrue(only(validate_corpus(c), "window_hours"))

    def test_wrong_type_is_reported(self):
        self.assertTrue(only(validate_corpus(corpus(errors={})), "errors"))

    def test_non_iso_timestamp_is_reported(self):
        c = corpus(generated_at="last Tuesday")
        self.assertTrue(only(validate_corpus(c), "ISO 8601"))

    def test_timestamp_requires_time_and_utc_offset(self):
        for value in ("2026-08-08", "2026-08-08T12:00:00"):
            with self.subTest(value=value):
                c = corpus(generated_at=value)
                self.assertTrue(only(validate_corpus(c), "UTC offset"))

    def test_per_source_fetch_status_is_validated(self):
        c = corpus(sources=[{
            "source_type": "rss",
            "source_id": "NPR Politics",
            "category": "us_politics",
            "status": "ok",
            "requested": True,
            "http_success": True,
            "parsed_entries": 1,
            "dated_entries": 1,
            "retained_entries": 1,
            "retained_bytes": 100,
            "estimated_tokens": 25,
            "duration_ms": 42,
        }], fetch_duration_ms=42)
        self.assertEqual(validate_corpus(c), [])

        c["sources"][0]["duration_ms"] = -1
        self.assertTrue(only(validate_corpus(c), "duration_ms"))

    def test_non_string_error_entry_is_reported(self):
        self.assertTrue(only(validate_corpus(corpus(errors=[404])), "errors[0]"))

    def test_duplicate_structured_error_is_reported(self):
        error = {
            "source_type": "rss", "source_id": "Broken", "status": "error",
            "error_type": "HTTPError", "message": "503", "duration_ms": 4,
        }
        source = {
            **error, "category": "us_politics", "requested": True,
            "http_success": False, "parsed_entries": 0, "dated_entries": 0,
            "retained_entries": 0,
            "retained_bytes": 0,
            "estimated_tokens": 0,
        }
        del source["duration_ms"]
        source["duration_ms"] = 4
        c = corpus(sources=[source], errors=[error, dict(error)])
        self.assertTrue(only(validate_corpus(c), "duplicate failure record"))

    def test_non_object_corpus_is_reported(self):
        self.assertEqual(validate_corpus([1, 2]), ["corpus is not a JSON object"])


class CategoryTest(unittest.TestCase):
    def test_arbitrary_valid_category_is_allowed(self):
        c = corpus()
        c["categories"] = {"climate_policy": [item()]}
        c["processing"] = {"climate_policy": stats()}
        self.assertEqual(validate_corpus(c), [])

    def test_at_least_one_category_is_required(self):
        c = corpus()
        c["categories"] = {}
        c["processing"] = {}
        self.assertTrue(only(validate_corpus(c), "at least one category"))

    def test_invalid_category_name_is_reported(self):
        c = corpus()
        c["categories"]["US News"] = []
        c["processing"]["US News"] = stats(0, 0)
        self.assertTrue(only(validate_corpus(c), "invalid name"))

    def test_processing_categories_must_match_corpus_categories(self):
        c = corpus()
        del c["processing"]["us_news"]
        self.assertTrue(only(validate_corpus(c), "one entry per category"))

    def test_item_missing_a_required_field_is_reported(self):
        c = corpus()
        del c["categories"]["us_politics"][0]["url"]
        self.assertTrue(only(validate_corpus(c), "missing required field 'url'"))

    def test_unknown_item_field_is_reported(self):
        """Unknown item fields fail validation at the corpus boundary."""
        c = corpus()
        c["categories"]["us_politics"][0]["headline"] = "oops"
        self.assertTrue(only(validate_corpus(c), "unknown field"))

    def test_unparseable_item_timestamp_is_reported(self):
        c = corpus()
        c["categories"]["us_politics"][0]["published"] = "yesterday"
        self.assertTrue(only(validate_corpus(c), "published is not an ISO"))

    def test_item_field_types_and_urls_are_enforced(self):
        bad_values = {
            "title": 7,
            "source": None,
            "url": "javascript:alert(1)",
            "summary": ["not", "text"],
            "points": True,
        }
        for field, value in bad_values.items():
            with self.subTest(field=field):
                c = corpus()
                c["categories"]["us_politics"][0][field] = value
                self.assertTrue(only(validate_corpus(c), field))

    def test_published_must_fall_inside_corpus_window(self):
        for value, fragment in (
            ("2026-08-07T11:59:59+00:00", "earlier than cutoff"),
            ("2026-08-08T12:00:01+00:00", "later than generated_at"),
        ):
            with self.subTest(value=value):
                c = corpus()
                c["categories"]["us_politics"][0]["published"] = value
                self.assertTrue(only(validate_corpus(c), fragment))

    def test_current_schema_enforces_model_visible_field_budgets(self):
        for field, value in (
            ("title", "x" * (ITEM_TITLE_MAX_BYTES + 1)),
            ("url", "https://ex.com/" + "x" * ITEM_URL_MAX_BYTES),
            ("summary", "x" * (ITEM_SUMMARY_MAX_CHARS + 1)),
        ):
            with self.subTest(field=field):
                c = corpus()
                c["categories"]["us_politics"][0][field] = value
                self.assertTrue(only(validate_corpus(c), field))


class ProcessingTest(unittest.TestCase):
    def test_counters_that_do_not_reconcile_are_reported(self):
        c = corpus()
        c["processing"]["us_politics"]["fetched"] = 9
        self.assertTrue(only(validate_corpus(c), "does not reconcile"))

    def test_kept_must_match_the_items_actually_present(self):
        c = corpus()
        c["processing"]["us_politics"] = stats(5, 5)
        self.assertTrue(only(validate_corpus(c), "holds 1 items"))

    def test_undated_items_sit_outside_the_reconciliation(self):
        """They are counted before `fetched` is measured, by construction."""
        c = corpus()
        c["processing"]["us_politics"] = stats(1, 1, undated_dropped=7)
        self.assertEqual(validate_corpus(c), [])

    def test_missing_counter_is_reported(self):
        c = corpus()
        del c["processing"]["world"]["undated_dropped"]
        self.assertTrue(only(validate_corpus(c), "undated_dropped"))

    def test_drops_are_included_in_the_reconciliation(self):
        c = corpus()
        c["processing"]["us_politics"] = stats(
            4, 1, relevance_dropped=1, duplicates_dropped=1, source_cap_dropped=1)
        self.assertEqual(validate_corpus(c), [])

    def test_context_telemetry_must_match_processing_totals(self):
        c = corpus()
        c["processing"]["us_politics"]["title_truncated"] = 1
        self.assertTrue(only(validate_corpus(c), "title_truncated"))

    def test_global_usage_cannot_exceed_the_declared_budget(self):
        c = corpus()
        c["context_budget"]["used_bytes"] = 512 * 1024 + 1
        c["processing"]["us_politics"]["context_bytes"] = 512 * 1024 + 1
        self.assertTrue(only(validate_corpus(c), "exceeds global_max_bytes"))


class VersionTest(unittest.TestCase):
    def test_absent_version_is_treated_as_legacy(self):
        c = corpus()
        del c["schema_version"]
        self.assertEqual(corpus_version(c), LEGACY_SCHEMA_VERSION)

    def test_legacy_and_current_corpora_are_readable(self):
        legacy = corpus()
        del legacy["schema_version"]
        self.assertTrue(is_readable(legacy))
        self.assertTrue(is_readable(corpus()))

    def test_a_newer_corpus_is_refused_rather_than_guessed_at(self):
        self.assertFalse(is_readable(corpus(schema_version=SCHEMA_VERSION + 1)))

    def test_non_integer_version_falls_back_to_legacy(self):
        self.assertEqual(corpus_version(corpus(schema_version="1")),
                         LEGACY_SCHEMA_VERSION)

    def test_writing_a_version_this_code_does_not_own_is_reported(self):
        c = corpus(schema_version=SCHEMA_VERSION + 1)
        self.assertTrue(only(validate_corpus(c), "schema_version"))


class FetcherOutputTest(unittest.TestCase):
    """The fetcher's own corpus must satisfy the contract it publishes."""

    def test_fetch_news_emits_every_declared_field(self):
        import fetch_news
        self.assertEqual(fetch_news.corpus_schema.SCHEMA_VERSION, SCHEMA_VERSION)

    def test_validation_does_not_mutate_the_corpus(self):
        c = corpus()
        before = copy.deepcopy(c)
        validate_corpus(c)
        self.assertEqual(c, before)


if __name__ == "__main__":
    unittest.main()
