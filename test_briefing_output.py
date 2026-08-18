import copy
import json
import unittest
from pathlib import Path

import briefing_config
import eval_briefing
from agent_runner.output import (
    build_output_schema,
    project_corpus,
    redact_destinations,
    redact_preview_value,
    render_briefing,
    validate_output,
)

ROOT = Path(__file__).resolve().parent


def fixture_contract():
    corpus = json.loads((ROOT / "fixtures/corpus-2026-08-11.json").read_text(encoding="utf-8"))
    config = briefing_config.load_config(ROOT / "fixtures/briefing-config-2026-08-11.json")
    projected = project_corpus(corpus)
    items = {
        item["item_ref"]: item
        for category in projected.document["categories"].values()
        for item in category
    }
    refs_by_item = {}
    for ref, citation in projected.citations.items():
        refs_by_item.setdefault(citation.item_ref, []).append(ref)
    used = set()
    sections = {}
    excluded = {}
    for section in config.sections:
        topics = []
        for citation in projected.citations.values():
            if citation.category not in section.corpus_categories or citation.item_ref in used:
                continue
            item = items[citation.item_ref]
            topics.append({
                "headline": item["title"],
                "summary": item.get("summary") or item["title"],
                "citation_refs": refs_by_item[citation.item_ref],
            })
            used.add(citation.item_ref)
            if len(topics) == section.target_stories:
                break
        sections[section.name] = {"topics": topics}
        if section.excluded_stories:
            rows = []
            for citation in projected.citations.values():
                if citation.category not in section.corpus_categories or citation.item_ref in used:
                    continue
                item = items[citation.item_ref]
                rows.append({
                    "headline": item["title"],
                    "reason": "Lower immediate impact.",
                    "citation_refs": refs_by_item[citation.item_ref],
                })
                used.add(citation.item_ref)
                if len(rows) == section.excluded_stories:
                    break
            excluded[section.name] = rows
    output = {"schema_version": 1, "sections": sections, "excluded_topics": excluded}
    return corpus, config, projected, output


class BriefingOutputTests(unittest.TestCase):
    def test_projection_removes_all_urls_and_keeps_reference_map(self):
        corpus, _config, projected, _output = fixture_contract()
        rendered = json.dumps(projected.document)
        self.assertNotIn("https://", rendered)
        self.assertTrue(projected.citations)
        self.assertTrue(all(citation.url.startswith("http") for citation in projected.citations.values()))

        injected = copy.deepcopy(corpus)
        first_category = next(iter(injected["categories"].values()))
        first_category[0]["summary"] = (
            "Read https://EXAMPLE.com/path and https://example.com/path/ "
            "plus https&#x3A;//encoded.example/instruction and "
            "https&amp;#x3A;//double-encoded.example/instruction"
        )
        reprojected = project_corpus(injected)
        reprojected_text = json.dumps(reprojected.document)
        self.assertNotIn("EXAMPLE.com", reprojected_text)
        self.assertNotIn("example.com", reprojected_text)
        self.assertNotIn("encoded.example", reprojected_text)
        self.assertNotIn("double-encoded.example", reprojected_text)
        self.assertGreaterEqual(reprojected_text.count("destination omitted"), 4)

    def test_projection_omits_mutable_hn_engagement_but_raw_corpus_keeps_it(self):
        corpus, _config, projected, _output = fixture_contract()
        raw_hn_items = [
            item
            for items in corpus["categories"].values()
            for item in items
            if "points" in item or "comments" in item
        ]
        self.assertTrue(raw_hn_items)

        projected_hn_items = [
            item
            for items in projected.document["categories"].values()
            for item in items
            if item.get("source") == "Hacker News"
        ]
        self.assertTrue(projected_hn_items)
        for item in projected_hn_items:
            self.assertNotIn("points", item)
            self.assertNotIn("comments", item)

    def test_redaction_rejects_destination_bearing_dictionary_keys(self):
        with self.assertRaisesRegex(ValueError, "destination-bearing dictionary key"):
            redact_destinations({"https&amp;#x3A;//attacker.invalid": "value"})

        corpus, _config, _projected, _output = fixture_contract()
        injected = copy.deepcopy(corpus)
        category, items = next(iter(injected["categories"].items()))
        del injected["categories"][category]
        injected["categories"]["https://attacker.invalid/category"] = items
        with self.assertRaisesRegex(ValueError, "destination-bearing dictionary key"):
            project_corpus(injected)

    def test_preview_redaction_handles_destinations_in_keys_and_values(self):
        preview = redact_preview_value({
            "https://attacker.invalid/key": "https://attacker.invalid/value",
        })
        rendered = json.dumps(preview)
        self.assertNotIn("attacker.invalid", rendered)
        self.assertGreaterEqual(rendered.count("destination omitted"), 2)

    def test_schema_restricts_sections_and_matches_runtime_constraints(self):
        _corpus, config, projected, _output = fixture_contract()
        schema = build_output_schema(config)
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(
            schema["properties"]["sections"]["required"],
            [section.name for section in config.sections],
        )
        self.assertNotIn("$schema", schema)
        first = config.sections[0]
        topics = schema["properties"]["sections"]["properties"][first.name]["properties"]["topics"]
        self.assertEqual(topics["minItems"], 0)
        self.assertEqual(topics["maxItems"], first.target_stories)
        headline = topics["items"]["properties"]["headline"]
        self.assertEqual(headline["minLength"], 1)
        self.assertEqual(headline["maxLength"], 300)
        refs = topics["items"]["properties"]["citation_refs"]
        self.assertEqual(refs["minItems"], 1)
        self.assertNotIn("uniqueItems", refs)
        accountable = next(section for section in config.sections if section.excluded_stories)
        excluded = schema["properties"]["excluded_topics"]["properties"][accountable.name]
        self.assertEqual(excluded["minItems"], 0)
        self.assertEqual(excluded["maxItems"], accountable.excluded_stories)
        excluded_refs = excluded["items"]["properties"]["citation_refs"]
        self.assertNotIn("uniqueItems", excluded_refs)
        self.assertTrue(projected.citations)

    def test_valid_output_renders_checker_clean_with_exact_urls(self):
        corpus, config, projected, output = fixture_contract()
        self.assertEqual(validate_output(output, config, projected.citations), [])
        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertEqual(eval_briefing.evaluate(corpus, briefing, config), [])
        first_ref = next(iter(projected.citations))
        self.assertIn(projected.citations[first_ref].url, briefing)
        self.assertNotIn(first_ref, briefing)

    def test_unknown_reference_and_url_in_summary_fail(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = broken["sections"][config.sections[0].name]["topics"][0]
        topic["summary"] += " https://attacker.invalid/"
        topic["citation_refs"] = ["citation_9999"]
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("freeform_url", checks)
        self.assertIn("unknown_citation_ref", checks)

    def test_duplicate_citation_reference_is_enforced_in_code(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = broken["sections"][config.sections[0].name]["topics"][0]
        topic["citation_refs"].append(topic["citation_refs"][0])
        checks = {
            finding.check
            for finding in validate_output(broken, config, projected.citations)
        }
        self.assertIn("duplicate_citation_ref", checks)

    def test_same_item_cannot_be_reported_twice(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        first_section = config.sections[0].name
        second_section = config.sections[1].name
        repeated = copy.deepcopy(broken["sections"][first_section]["topics"][0])
        broken["sections"][second_section]["topics"][0] = repeated
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("duplicate_item", checks)

    def test_structured_item_limit_is_enforced_in_code(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        section = config.sections[0]
        topics = broken["sections"][section.name]["topics"]
        topics.append(copy.deepcopy(topics[0]))
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("structured_item_limit", checks)

    def test_citation_must_be_eligible_for_its_section(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        section = config.sections[0]
        ineligible_ref = next(
            ref
            for ref, citation in projected.citations.items()
            if citation.category not in section.corpus_categories
        )
        broken["sections"][section.name]["topics"][0]["citation_refs"] = [ineligible_ref]
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("category_ineligible_ref", checks)


if __name__ == "__main__":
    unittest.main()
