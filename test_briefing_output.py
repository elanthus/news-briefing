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
    repair_structural_output,
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


def unused_hn_item(projected, output):
    used_items = {
        projected.citations[ref].item_ref
        for bucket in output["sections"].values()
        for topic in bucket["topics"]
        for ref in topic["citation_refs"]
    } | {
        projected.citations[ref].item_ref
        for rows in output["excluded_topics"].values()
        for topic in rows
        for ref in topic["citation_refs"]
    }
    return next(
        item
        for items in projected.document["categories"].values()
        for item in items
        if item["item_ref"] not in used_items
        and {citation["kind"] for citation in item["citations"]}
        == {"article", "discussion"}
    )


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
        schema = build_output_schema(config, projected.citations)
        self.assertEqual(
            schema["properties"]["schema_version"],
            {"type": "integer", "minimum": 1, "maximum": 1},
        )
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
        self.assertTrue(refs["uniqueItems"])
        eligible = {
            ref
            for ref, citation in projected.citations.items()
            if citation.category in first.corpus_categories
        }
        self.assertEqual(set(refs["items"]["enum"]), eligible)
        accountable = next(section for section in config.sections if section.excluded_stories)
        excluded = schema["properties"]["excluded_topics"]["properties"][accountable.name]
        self.assertEqual(excluded["minItems"], 0)
        self.assertEqual(excluded["maxItems"], accountable.excluded_stories)
        excluded_refs = excluded["items"]["properties"]["citation_refs"]
        self.assertTrue(excluded_refs["uniqueItems"])
        accountable_eligible = {
            ref
            for ref, citation in projected.citations.items()
            if citation.category in accountable.corpus_categories
        }
        self.assertEqual(
            set(excluded_refs["items"]["enum"]), accountable_eligible
        )
        self.assertTrue(projected.citations)

    def test_valid_output_renders_checker_clean_with_exact_urls(self):
        corpus, config, projected, output = fixture_contract()
        self.assertEqual(validate_output(output, config, projected.citations), [])
        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertEqual(eval_briefing.evaluate(corpus, briefing, config), [])
        first_ref = next(iter(projected.citations))
        self.assertIn(projected.citations[first_ref].url, briefing)
        self.assertNotIn(first_ref, briefing)

    def test_report_date_overrides_exclusive_window_end_in_title(self):
        corpus, config, projected, output = fixture_contract()
        corpus["report_date"] = "2026-08-10"
        corpus["generated_at"] = "2026-08-11T04:00:00+00:00"

        briefing = render_briefing(output, corpus, config, projected.citations)

        self.assertIn("# Daily Briefing — August 10, 2026", briefing)
        self.assertNotIn("# Daily Briefing — August 11, 2026", briefing)

    def test_renderer_adds_hn_discussion_link_from_article_ref(self):
        corpus, config, projected, output = fixture_contract()
        item = unused_hn_item(projected, output)
        refs = {citation["kind"]: citation["ref"] for citation in item["citations"]}
        topic = output["sections"]["AI Dev Tools"]["topics"][0]
        topic.update({
            "headline": item["title"],
            "summary": item.get("summary") or item["title"],
            "citation_refs": [refs["article"]],
        })

        briefing = render_briefing(output, corpus, config, projected.citations)
        article_url = projected.citations[refs["article"]].url
        discussion_url = projected.citations[refs["discussion"]].url
        self.assertEqual(briefing.count(article_url), 1)
        self.assertEqual(briefing.count(discussion_url), 1)
        self.assertNotIn(
            "missing_discussion_link",
            {finding.check for finding in eval_briefing.evaluate(corpus, briefing, config)},
        )

    def test_renderer_deduplicates_explicit_hn_pair_and_self_post(self):
        corpus, config, projected, output = fixture_contract()
        item = unused_hn_item(projected, output)
        refs = {citation["kind"]: citation["ref"] for citation in item["citations"]}
        topic = output["sections"]["AI Dev Tools"]["topics"][0]
        topic["citation_refs"] = [refs["article"], refs["discussion"]]
        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertEqual(
            briefing.count(projected.citations[refs["discussion"]].url), 1
        )

        self_post_citations = dict(projected.citations)
        discussion = projected.citations[refs["discussion"]]
        article = projected.citations[refs["article"]]
        self_post_citations[refs["article"]] = type(article)(
            article.ref,
            article.item_ref,
            article.category,
            article.kind,
            discussion.url,
        )
        topic["citation_refs"] = [refs["article"]]
        self_post = render_briefing(output, corpus, config, self_post_citations)
        self.assertEqual(self_post.count(discussion.url), 1)

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

    def test_structural_repair_drops_ineligible_and_repeated_entries(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        first = config.sections[0]
        second = config.sections[1]
        first_topic = broken["sections"][first.name]["topics"][0]
        repeated = copy.deepcopy(first_topic)
        broken["sections"][second.name]["topics"].insert(0, repeated)
        broken["excluded_topics"][first.name].insert(0, copy.deepcopy(first_topic))
        ineligible_ref = next(
            ref
            for ref, citation in projected.citations.items()
            if citation.category not in first.corpus_categories
        )
        broken["sections"][first.name]["topics"][1]["citation_refs"] = [ineligible_ref]

        repaired, actions = repair_structural_output(
            broken,
            config,
            projected.citations,
        )

        findings = validate_output(repaired, config, projected.citations)
        self.assertNotIn(
            "category_ineligible_ref",
            {finding.check for finding in findings},
        )
        self.assertNotIn("duplicate_item", {finding.check for finding in findings})
        self.assertEqual(repaired["sections"][first.name]["topics"][0], first_topic)
        self.assertTrue(any(action["path"].startswith(f"topics.{second.name}") for action in actions))
        self.assertTrue(any(action["path"].startswith("excluded_topics.") for action in actions))

    def test_structural_repair_clears_repairable_findings_and_is_idempotent(self):
        repairable_checks = {
            "category_ineligible_ref",
            "duplicate_citation_ref",
            "duplicate_item",
        }
        _corpus, config, projected, output = fixture_contract()
        section_names = [section.name for section in config.sections]
        categories_by_section = {
            section.name: set(section.corpus_categories) for section in config.sections
        }

        def ineligible_ref_for(section):
            return next(
                ref
                for ref, citation in projected.citations.items()
                if citation.category not in section.corpus_categories
            )

        def corruptions():
            duplicated = copy.deepcopy(output)
            donor_topics = duplicated["sections"][section_names[0]]["topics"]
            duplicated["sections"][section_names[-1]]["topics"].append(
                copy.deepcopy(donor_topics[0])
            )
            yield "duplicate entry across sections", duplicated, {"duplicate_item"}

            repeated = copy.deepcopy(output)
            entry = repeated["sections"][section_names[0]]["topics"][0]
            entry["citation_refs"] = entry["citation_refs"] + [entry["citation_refs"][0]]
            yield "repeated ref inside one entry", repeated, {"duplicate_citation_ref"}

            first = config.sections[0]
            ineligible = copy.deepcopy(output)
            ineligible["sections"][first.name]["topics"][0]["citation_refs"] = [
                ineligible_ref_for(first)
            ]
            yield "ineligible ref in included topic", ineligible, {"category_ineligible_ref"}

            accountable = next(
                section for section in config.sections if section.excluded_stories
            )
            excluded_dup = copy.deepcopy(output)
            source = excluded_dup["sections"][accountable.name]["topics"][0]
            excluded_dup["excluded_topics"][accountable.name].append({
                "headline": source["headline"],
                "reason": "Repeats an included story.",
                "citation_refs": list(source["citation_refs"]),
            })
            yield "included item repeated in excluded topics", excluded_dup, {"duplicate_item"}

            excluded_ineligible = copy.deepcopy(output)
            excluded_ineligible["excluded_topics"][accountable.name][0]["citation_refs"] = [
                ineligible_ref_for(accountable)
            ]
            yield (
                "ineligible ref in excluded topic",
                excluded_ineligible,
                {"category_ineligible_ref"},
            )

            for donor_name in section_names:
                for target_name in section_names:
                    if donor_name == target_name:
                        continue
                    crossed = copy.deepcopy(output)
                    donor_topics = crossed["sections"][donor_name]["topics"]
                    target_topics = crossed["sections"][target_name]["topics"]
                    if not donor_topics or not target_topics:
                        continue
                    donor_ref = donor_topics[0]["citation_refs"][0]
                    target_topics[0]["citation_refs"] = (
                        target_topics[0]["citation_refs"] + [donor_ref]
                    )
                    expected = {"duplicate_item"}
                    donor_category = projected.citations[donor_ref].category
                    if donor_category not in categories_by_section[target_name]:
                        expected.add("category_ineligible_ref")
                    yield f"ref shared from {donor_name} into {target_name}", crossed, expected

        for label, corrupted, expected_checks in corruptions():
            with self.subTest(corruption=label):
                before = {
                    finding.check
                    for finding in validate_output(corrupted, config, projected.citations)
                }
                self.assertLessEqual(
                    expected_checks,
                    before,
                    msg=f"corruption did not produce its intended findings: {before}",
                )
                repaired, actions = repair_structural_output(
                    corrupted, config, projected.citations
                )
                self.assertTrue(actions)
                residual = {
                    finding.check
                    for finding in validate_output(repaired, config, projected.citations)
                }
                self.assertFalse(
                    residual & repairable_checks,
                    msg=f"actions={actions} residual={residual}",
                )
                again, second_actions = repair_structural_output(
                    repaired, config, projected.citations
                )
                self.assertEqual(again, repaired)
                self.assertEqual(second_actions, [])


if __name__ == "__main__":
    unittest.main()
