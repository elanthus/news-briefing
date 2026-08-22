#!/usr/bin/env python3
"""Tests for the trusted briefing structure configuration."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import briefing_config
from fetch_news import DEFAULT_SOURCES_PATH, load_sources


def raw_config():
    return json.loads(Path("briefing-config.json").read_text(encoding="utf-8"))


class ValidConfigTest(unittest.TestCase):
    def test_default_config_loads(self):
        config = briefing_config.load_config()
        self.assertEqual(config.schema_version, briefing_config.SCHEMA_VERSION)
        self.assertGreater(len(config.sections), 0)

    def test_config_preserves_section_order(self):
        expected = [section["name"] for section in raw_config()["sections"]]
        config = briefing_config.load_config()
        self.assertEqual([section.name for section in config.sections], expected)

    def test_default_config_leads_with_ai_and_ends_with_us_politics(self):
        config = briefing_config.load_config()
        self.assertEqual(config.sections[0].group, "AI/Tech")
        self.assertEqual(config.sections[-1].name, "US Politics")

    def test_zero_exclusions_are_allowed(self):
        raw = raw_config()
        raw["sections"][0]["excluded_stories"] = 0
        config = briefing_config.parse_config(raw)
        self.assertEqual(config.sections[0].excluded_stories, 0)

    def test_default_config_references_declared_corpus_categories(self):
        config = briefing_config.load_config()
        self.assertEqual(
            briefing_config.validate_corpus_categories(
                config, set(load_sources(DEFAULT_SOURCES_PATH).categories)),
            [])

    def test_general_news_is_eligible_for_every_broad_news_section(self):
        config = briefing_config.load_config()
        broad_sections = {"US Politics", "US News", "World Events", "AI News"}
        self.assertTrue(
            broad_sections <= {section.name for section in config.sections}
        )
        for section in config.sections:
            if section.name in broad_sections:
                self.assertIn("general_news", section.corpus_categories)
        sources = load_sources(DEFAULT_SOURCES_PATH)
        self.assertEqual(
            sources.rss_feeds["general_news"],
            [("Axios", "https://api.axios.com/feed/")],
        )
        self.assertNotIn(
            "Axios",
            {name for name, _url in sources.rss_feeds["us_politics"]},
        )


class InvalidConfigTest(unittest.TestCase):
    def assert_problem(self, mutate, message):
        raw = copy.deepcopy(raw_config())
        mutate(raw)
        with self.assertRaisesRegex(ValueError, message):
            briefing_config.parse_config(raw)

    def test_unknown_top_level_field_is_rejected(self):
        self.assert_problem(lambda raw: raw.update(extra=True), "unknown field")

    def test_duplicate_section_name_is_rejected_case_insensitively(self):
        self.assert_problem(
            lambda raw: raw["sections"][1].update(
                name=raw["sections"][0]["name"].swapcase()),
            "section names must be unique")

    def test_target_must_be_positive(self):
        self.assert_problem(
            lambda raw: raw["sections"][0].update(target_stories=0),
            "target_stories must be a positive integer")

    def test_reserved_section_name_is_rejected(self):
        self.assert_problem(
            lambda raw: raw["sections"][0].update(name="Corpus health"),
            "name is reserved")

    def test_group_cannot_collide_with_a_section_name(self):
        self.assert_problem(
            lambda raw: raw["sections"][0].update(
                group=raw["sections"][1]["name"]),
            "group collides with a section or reserved heading")

    def test_corpus_category_names_are_validated(self):
        self.assert_problem(
            lambda raw: raw["sections"][0].update(corpus_categories=["US Politics"]),
            "corpus_categories must be")

    def test_invalid_json_reports_its_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "briefing.json"
            path.write_text('{"sections": [}', encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid JSON at line 1, column"):
                briefing_config.load_config(path)


class CorpusReferenceTest(unittest.TestCase):
    def test_missing_referenced_category_is_reported(self):
        config = briefing_config.load_config()
        section = config.sections[0]
        missing = section.corpus_categories[0]
        available = set(load_sources(DEFAULT_SOURCES_PATH).categories) - {missing}
        problems = briefing_config.validate_corpus_categories(config, available)
        self.assertTrue(any(section.name in problem and missing in problem
                            for problem in problems))


if __name__ == "__main__":
    unittest.main()
