import copy
import json
import re
import unittest
from pathlib import Path

import briefing_config
import corpus_schema
import eval_briefing
from agent_runner.outcomes import classify_outcome
from agent_runner.output import (
    REPAIRABLE_CHECKS,
    Citation,
    attach_frozen_selection,
    build_prose_schema,
    build_selection_schema,
    detach_prose,
    project_corpus,
    project_selected_evidence,
    promote_excluded_to_underfilled,
    redact_destinations,
    redact_opaque_references,
    redact_preview_value,
    render_briefing,
    render_candidate_preview,
    render_validation_status,
    repair_structural_output,
    validate_output,
    validate_prose_output,
    validate_selection,
)

ROOT = Path(__file__).resolve().parent.parent


def fixture_contract():
    corpus = json.loads((ROOT / "fixtures/current-corpus.json").read_text(encoding="utf-8"))
    config = briefing_config.load_config(ROOT / "fixtures/briefing-config-2026-08-11.json")
    projected = project_corpus(corpus)
    items = {}
    for category in projected.document["categories"].values():
        for item in category:
            citation = projected.citations[item["citation_ref"]]
            items[citation.item_ref] = item
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


def used_item_refs(projected, output):
    """Every corpus item an output already cites, across sections and exclusions."""
    return {
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


def selection_from_output(output):
    """Strip prose fields, keeping only the frozen citation-ref selection."""
    return {
        "schema_version": output["schema_version"],
        "sections": {
            name: {
                "topics": [
                    {"citation_refs": copy.deepcopy(entry["citation_refs"])}
                    for entry in section["topics"]
                ]
            }
            for name, section in output["sections"].items()
        },
        "excluded_topics": {
            name: [
                {"citation_refs": copy.deepcopy(entry["citation_refs"])}
                for entry in entries
            ]
            for name, entries in output["excluded_topics"].items()
        },
    }


def unused_hn_item(projected, output):
    used_items = used_item_refs(projected, output)
    return next(
        item
        for items in projected.document["categories"].values()
        for item in items
        if projected.citations[item["citation_ref"]].item_ref not in used_items
        and projected.citations[item["citation_ref"]].discussion_url is not None
    )


class BriefingOutputTests(unittest.TestCase):
    def test_projection_removes_all_urls_and_keeps_reference_map(self):
        corpus, _config, projected, _output = fixture_contract()
        rendered = json.dumps(projected.document)
        self.assertNotIn("https://", rendered)
        self.assertTrue(projected.citations)
        self.assertTrue(
            all(citation.article_url.startswith("http") for citation in projected.citations.values())
        )
        visible_refs = [
            item["citation_ref"]
            for items in projected.document["categories"].values()
            for item in items
        ]
        retained_count = sum(len(items) for items in corpus["categories"].values())
        self.assertEqual(visible_refs, list(projected.citations))
        self.assertEqual(len(visible_refs), retained_count)
        self.assertEqual(
            len(re.findall(r"citation_\d+", rendered)),
            retained_count,
        )
        self.assertNotIn("item_", rendered)

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

    def test_hn_companion_url_does_not_shift_following_item_handles(self):
        corpus = {
            "schema_version": 7,
            "generated_at": "2026-08-27T12:00:00+00:00",
            "cutoff": "2026-08-26T12:00:00+00:00",
            "window_hours": 24,
            "categories": {
                "dev_community": [
                    {
                        "title": "HN article",
                        "url": "https://example.test/hn-article",
                        "discussion": "https://news.ycombinator.com/item?id=1",
                    },
                    {"title": "Ordinary second item", "url": "https://example.test/second"},
                    {"title": "Ordinary third item", "url": "https://example.test/third"},
                ]
            },
        }

        projected = project_corpus(corpus)
        items = projected.document["categories"]["dev_community"]

        self.assertEqual(
            [item["citation_ref"] for item in items],
            ["citation_0001", "citation_0002", "citation_0003"],
        )
        self.assertEqual(
            [citation.item_ref for citation in projected.citations.values()],
            ["item_0001", "item_0002", "item_0003"],
        )
        first = projected.citations["citation_0001"]
        self.assertEqual(first.article_url, "https://example.test/hn-article")
        self.assertEqual(
            first.discussion_url,
            "https://news.ycombinator.com/item?id=1",
        )

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

    def test_two_pass_contract_freezes_refs_and_removes_them_from_prose_schema(self):
        _corpus, config, projected, output = fixture_contract()
        selection = {
            "schema_version": 1,
            "sections": {
                name: {
                    "topics": [
                        {"citation_refs": copy.deepcopy(entry["citation_refs"])}
                        for entry in section["topics"]
                    ]
                }
                for name, section in output["sections"].items()
            },
            "excluded_topics": {
                name: [
                    {"citation_refs": copy.deepcopy(entry["citation_refs"])}
                    for entry in entries
                ]
                for name, entries in output["excluded_topics"].items()
            },
        }
        self.assertEqual(validate_selection(selection, config, projected.citations), [])
        selection_schema = build_selection_schema(config, projected.citations)
        first_name = config.sections[0].name
        selection_fields = selection_schema["properties"]["sections"]["properties"][
            first_name
        ]["properties"]["topics"]["items"]["properties"]
        self.assertEqual(set(selection_fields), {"citation_refs"})

        evidence = project_selected_evidence(selection, projected)
        self.assertNotRegex(json.dumps(evidence), r"\b(?:citation|item)_\d+\b")
        prose = detach_prose(output, config)
        prose_schema = build_prose_schema(config, selection)
        prose_fields = prose_schema["properties"]["sections"]["properties"][first_name][
            "properties"
        ]["topics"]["items"]["properties"]
        self.assertEqual(set(prose_fields), {"headline", "summary"})
        self.assertEqual(validate_prose_output(prose, config, selection), [])
        self.assertEqual(attach_frozen_selection(selection, prose, config), output)

        mutated = copy.deepcopy(prose)
        mutated["sections"][first_name]["topics"][0]["citation_refs"] = [
            next(reversed(projected.citations))
        ]
        checks = {
            finding.check for finding in validate_prose_output(mutated, config, selection)
        }
        self.assertIn("structured_unknown_field", checks)
        attached = attach_frozen_selection(selection, mutated, config)
        self.assertEqual(
            attached["sections"][first_name]["topics"][0]["citation_refs"],
            selection["sections"][first_name]["topics"][0]["citation_refs"],
        )

    def test_all_empty_exclusion_logs_block_the_selection(self):
        """A model may satisfy the schema with `[]` for every exclusion array."""
        _corpus, config, projected, output = fixture_contract()
        selection = selection_from_output(output)
        for name in selection["excluded_topics"]:
            selection["excluded_topics"][name] = []
        checks = {
            finding.check
            for finding in validate_selection(selection, config, projected.citations)
        }
        self.assertIn("exclusion_log_empty", checks)

    def test_partially_empty_exclusion_log_does_not_block_the_selection(self):
        """One accountable section returning nothing is not the all-empty case."""
        _corpus, config, projected, output = fixture_contract()
        selection = selection_from_output(output)
        accountable = [name for name, rows in selection["excluded_topics"].items() if rows]
        selection["excluded_topics"][accountable[0]] = []
        findings = validate_selection(selection, config, projected.citations)
        self.assertNotIn("exclusion_log_empty", {finding.check for finding in findings})

    def test_exhausted_eligible_pool_does_not_block_the_selection(self):
        """A section with nothing left to exclude passes with no finding."""
        citations = {
            "citation_0001": Citation("citation_0001", "item_0001", "cat",
                                       "https://ex.com/1", None),
            "citation_0002": Citation("citation_0002", "item_0002", "cat",
                                       "https://ex.com/2", None),
        }
        config = briefing_config.BriefingConfig(1, (
            briefing_config.BriefingSection(
                "Only", None, 2, ("cat",), "guidance", 2
            ),
        ))
        selection = {
            "schema_version": 1,
            "sections": {
                "Only": {
                    "topics": [
                        {"citation_refs": ["citation_0001"]},
                        {"citation_refs": ["citation_0002"]},
                    ]
                }
            },
            "excluded_topics": {"Only": []},
        }
        self.assertEqual(validate_selection(selection, config, citations), [])

    def test_exclusion_schema_minitems_at_the_target_stories_boundary(self):
        """Eligible count equal to target_stories: a full report leaves nothing
        to exclude, so the schema must not demand a non-empty log."""
        citations = {
            "citation_0001": Citation("citation_0001", "item_0001", "cat",
                                       "https://ex.com/1", None),
            "citation_0002": Citation("citation_0002", "item_0002", "cat",
                                       "https://ex.com/2", None),
        }
        config = briefing_config.BriefingConfig(1, (
            briefing_config.BriefingSection(
                "Only", None, 2, ("cat",), "guidance", 2
            ),
        ))
        schema = build_selection_schema(config, citations)
        excluded_schema = schema["properties"]["excluded_topics"]["properties"]["Only"]
        self.assertEqual(excluded_schema["minItems"], 0)

    def test_exclusion_schema_minitems_past_the_target_stories_boundary(self):
        """One more eligible item than target_stories: a full report always
        leaves at least one item to exclude, so the schema may require it."""
        citations = {
            "citation_0001": Citation("citation_0001", "item_0001", "cat",
                                       "https://ex.com/1", None),
            "citation_0002": Citation("citation_0002", "item_0002", "cat",
                                       "https://ex.com/2", None),
            "citation_0003": Citation("citation_0003", "item_0003", "cat",
                                       "https://ex.com/3", None),
        }
        config = briefing_config.BriefingConfig(1, (
            briefing_config.BriefingSection(
                "Only", None, 2, ("cat",), "guidance", 2
            ),
        ))
        schema = build_selection_schema(config, citations)
        excluded_schema = schema["properties"]["excluded_topics"]["properties"]["Only"]
        self.assertEqual(excluded_schema["minItems"], 1)

    def test_exclusion_schema_minitems_accounts_for_overlapping_sections(self):
        """Two sections sharing a category can jointly exhaust it. The schema
        may demand an exclusion only when the pool survives the worst case:
        every overlapping section fills its topics and logs one exclusion."""
        def citations(count):
            return {
                f"citation_{n:04d}": Citation(f"citation_{n:04d}", f"item_{n:04d}", "cat",
                                              f"https://ex.com/{n}", None)
                for n in range(1, count + 1)
            }

        def config(second_excluded):
            return briefing_config.BriefingConfig(1, (
                briefing_config.BriefingSection("A", None, 2, ("cat",), "guidance", 2),
                briefing_config.BriefingSection("B", None, 2, ("cat",), "guidance", second_excluded),
            ))

        def min_items(schema, name):
            return schema["properties"]["excluded_topics"]["properties"][name]["minItems"]

        # Four topics plus one exclusion each need six items; five is not enough.
        schema = build_selection_schema(config(2), citations(5))
        self.assertEqual((min_items(schema, "A"), min_items(schema, "B")), (0, 0))
        schema = build_selection_schema(config(2), citations(6))
        self.assertEqual((min_items(schema, "A"), min_items(schema, "B")), (1, 1))
        # A non-accountable sibling only consumes topics, so five suffice for A.
        schema = build_selection_schema(config(0), citations(5))
        self.assertEqual(min_items(schema, "A"), 1)
        self.assertNotIn("B", schema["properties"]["excluded_topics"]["properties"])

    def test_selected_evidence_redacts_opaque_references_from_corpus_text(self):
        _corpus, _config, projected, _output = fixture_contract()
        selected_ref = next(iter(projected.citations))
        selected_item = next(
            item
            for items in projected.document["categories"].values()
            for item in items
            if item["citation_ref"] == selected_ref
        )
        selected_item["title"] = "Untrusted citation_10000 and item_12345 tokens"
        selection = {
            "schema_version": 1,
            "sections": {
                "Example": {"topics": [{"citation_refs": [selected_ref]}]},
            },
            "excluded_topics": {},
        }

        evidence = project_selected_evidence(selection, projected)

        self.assertNotRegex(json.dumps(evidence), r"\b(?:citation|item)_\d+\b")
        self.assertIn("[opaque reference omitted]", json.dumps(evidence))

    def test_valid_output_renders_checker_clean_with_exact_urls(self):
        corpus, config, projected, output = fixture_contract()
        self.assertEqual(validate_output(output, config, projected.citations), [])
        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertEqual(eval_briefing.evaluate(corpus, briefing, config), [])
        first_ref = next(iter(projected.citations))
        self.assertIn(projected.citations[first_ref].article_url, briefing)
        self.assertNotIn(first_ref, briefing)
        self.assertNotRegex(briefing, r"\b(?:citation|item)_\d+\b")

    def test_renderer_reports_undated_source_drops(self):
        corpus, config, projected, output = fixture_contract()
        corpus["errors"] = []
        corpus["sources"] = [
            source for source in corpus["sources"] if source["status"] == "ok"
        ]
        source = corpus["sources"][0]
        source["parsed_entries"] += 2
        corpus["processing"][source["category"]]["undated_dropped"] += 2

        briefing = render_briefing(output, corpus, config, projected.citations)

        self.assertIn('"undated_sources"', briefing)
        self.assertIn(f'"source_id":"{source["source_id"]}"', briefing)
        self.assertIn('"count":2', briefing)
        self.assertEqual(corpus_schema.validate_corpus(corpus), [])
        self.assertEqual(eval_briefing.evaluate(corpus, briefing, config), [])

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
        ref = item["citation_ref"]
        citation = projected.citations[ref]
        topic = output["sections"]["AI Dev Tools"]["topics"][0]
        topic.update({
            "headline": item["title"],
            "summary": item.get("summary") or item["title"],
            "citation_refs": [ref],
        })

        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertEqual(briefing.count(citation.article_url), 1)
        self.assertIsNotNone(citation.discussion_url)
        self.assertEqual(briefing.count(citation.discussion_url), 1)
        self.assertNotIn(
            "missing_discussion_link",
            {finding.check for finding in eval_briefing.evaluate(corpus, briefing, config)},
        )

    def test_renderer_deduplicates_hn_self_post(self):
        corpus, config, projected, output = fixture_contract()
        item = unused_hn_item(projected, output)
        ref = item["citation_ref"]
        citation = projected.citations[ref]
        topic = output["sections"]["AI Dev Tools"]["topics"][0]
        self_post_citations = dict(projected.citations)
        self.assertIsNotNone(citation.discussion_url)
        self_post_citations[ref] = type(citation)(
            ref=citation.ref,
            item_ref=citation.item_ref,
            category=citation.category,
            article_url=citation.discussion_url,
            discussion_url=citation.discussion_url,
        )
        topic["citation_refs"] = [ref]
        self_post = render_briefing(output, corpus, config, self_post_citations)
        self.assertEqual(self_post.count(citation.discussion_url), 1)

    def test_unknown_reference_and_url_in_summary_fail(self):
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = broken["sections"][config.sections[0].name]["topics"][0]
        topic["summary"] += " https://attacker.invalid/"
        topic["citation_refs"] = ["citation_9999"]
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("freeform_url", checks)
        self.assertIn("unknown_citation_ref", checks)

    def test_opaque_references_in_model_authored_prose_are_blocking(self):
        _corpus, config, projected, output = fixture_contract()
        cases = (
            ("headline", "Headline leaks citation_0001"),
            ("summary", "Summary leaks item_0002"),
        )
        for field, value in cases:
            with self.subTest(field=field):
                broken = copy.deepcopy(output)
                broken["sections"][config.sections[0].name]["topics"][0][field] = value
                findings = validate_output(broken, config, projected.citations)
                opaque = [
                    finding
                    for finding in findings
                    if finding.check == "opaque_reference_in_prose"
                ]
                self.assertTrue(opaque)
                self.assertTrue(all(finding.level == "ERROR" for finding in opaque))

        accountable = next(section for section in config.sections if section.excluded_stories)
        broken = copy.deepcopy(output)
        broken["excluded_topics"][accountable.name][0]["reason"] = (
            "Lower impact than citation_0042."
        )
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertIn("opaque_reference_in_prose", checks)

    def test_opaque_references_wider_than_four_digits_are_blocking(self):
        corpus = {
            "schema_version": 7,
            "generated_at": "2026-08-27T12:00:00+00:00",
            "cutoff": "2026-08-26T12:00:00+00:00",
            "window_hours": 24,
            "categories": {
                "dev_community": [
                    {
                        "title": f"Story {number}",
                        "url": f"https://example.test/{number}",
                    }
                    for number in range(1, 10_001)
                ]
            },
        }
        config = briefing_config.parse_config({
            "schema_version": 1,
            "sections": [{
                "name": "AI Dev Tools",
                "group": None,
                "target_stories": 1,
                "corpus_categories": ["dev_community"],
                "guidance": "Select one item.",
                "excluded_stories": 0,
            }],
        })
        projected = project_corpus(corpus)
        self.assertIn("citation_10000", projected.citations)
        self.assertEqual(
            projected.citations["citation_10000"].item_ref,
            "item_10000",
        )
        selection = {
            "schema_version": 1,
            "sections": {
                "AI Dev Tools": {
                    "topics": [{"citation_refs": ["citation_10000"]}]
                }
            },
            "excluded_topics": {},
        }
        prose = {
            "schema_version": 1,
            "sections": {
                "AI Dev Tools": {
                    "topics": [{
                        "headline": "Opaque citation_10000",
                        "summary": "Opaque item_10000",
                    }]
                }
            },
            "excluded_topics": {},
        }

        prose_checks = {
            finding.check
            for finding in validate_prose_output(prose, config, selection)
        }
        output = attach_frozen_selection(selection, prose, config)
        output_checks = {
            finding.check
            for finding in validate_output(output, config, projected.citations)
        }

        self.assertIn("opaque_reference_in_prose", prose_checks)
        self.assertIn("opaque_reference_in_prose", output_checks)
        redacted = redact_opaque_references(prose, include_citations=True)
        self.assertNotIn("citation_10000", json.dumps(redacted))
        self.assertNotIn("item_10000", json.dumps(redacted))
        selection_redacted = redact_opaque_references(
            {"message": "Keep citation_10000 but remove item_10000"},
            include_citations=False,
        )
        self.assertIn("citation_10000", json.dumps(selection_redacted))
        self.assertNotIn("item_10000", json.dumps(selection_redacted))

    def test_scheme_less_bare_domain_in_summary_is_allowed(self):
        # A bare "attacker.com/x" (no scheme, no "www.") is intentionally not a
        # freeform_url: the site renderer no longer linkifies prose, so it
        # cannot become a live link, and flagging it would false-positive on
        # ordinary software prose like "see setup.py/foo". Grounding is enforced
        # by the renderer linking only code-owned citation URLs.
        _corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = broken["sections"][config.sections[0].name]["topics"][0]
        topic["summary"] += " See attacker.com/details for more."
        checks = {finding.check for finding in validate_output(broken, config, projected.citations)}
        self.assertNotIn("freeform_url", checks)

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

    @staticmethod
    def selection_view(output, config):
        """The first-pass shape: evidence choices with no prose attached."""
        return {
            "schema_version": output["schema_version"],
            "sections": {
                section.name: {
                    "topics": [
                        {"citation_refs": list(entry["citation_refs"])}
                        for entry in output["sections"][section.name]["topics"]
                    ]
                }
                for section in config.sections
            },
            "excluded_topics": {
                section.name: [
                    {"citation_refs": list(entry["citation_refs"])}
                    for entry in output["excluded_topics"][section.name]
                ]
                for section in config.sections
                if section.excluded_stories
            },
        }

    def test_promotion_fills_a_short_section_from_the_front_of_its_log(self):
        _corpus, config, projected, output = fixture_contract()
        short = self.selection_view(output, config)
        section = config.sections[0]
        dropped = short["sections"][section.name]["topics"].pop()
        head = short["excluded_topics"][section.name][0]
        tail = short["excluded_topics"][section.name][1:]

        promoted, actions = promote_excluded_to_underfilled(
            short, config, projected.citations
        )

        self.assertEqual(
            len(promoted["sections"][section.name]["topics"]), section.target_stories
        )
        self.assertEqual(promoted["sections"][section.name]["topics"][-1], head)
        self.assertEqual(promoted["excluded_topics"][section.name], tail)
        self.assertEqual(
            [action["action"] for action in actions], ["promote_excluded_entry"]
        )
        # The action names the destination it tags; the origin is in the reason.
        self.assertEqual(
            actions[0]["path"],
            f"topics.{section.name}[{section.target_stories - 1}]",
        )
        self.assertIn(f"excluded_topics.{section.name}[0]", actions[0]["reason"])
        self.assertNotIn(dropped, promoted["sections"][section.name]["topics"])
        self.assertEqual(
            validate_selection(promoted, config, projected.citations), []
        )

    def test_promotion_leaves_a_full_selection_untouched(self):
        _corpus, config, projected, output = fixture_contract()
        selection = self.selection_view(output, config)
        promoted, actions = promote_excluded_to_underfilled(
            selection, config, projected.citations
        )
        self.assertEqual(actions, [])
        self.assertIs(promoted, selection)

    def test_promotion_stops_when_the_log_runs_out(self):
        """A genuinely thin section keeps the gap rather than inventing one."""
        _corpus, config, projected, output = fixture_contract()
        short = self.selection_view(output, config)
        section = config.sections[0]
        short["sections"][section.name]["topics"] = []
        short["excluded_topics"][section.name] = short["excluded_topics"][section.name][:1]

        promoted, actions = promote_excluded_to_underfilled(
            short, config, projected.citations
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(len(promoted["sections"][section.name]["topics"]), 1)
        self.assertEqual(promoted["excluded_topics"][section.name], [])

    def test_promotion_skips_an_entry_ineligible_for_the_section(self):
        _corpus, config, projected, output = fixture_contract()
        short = self.selection_view(output, config)
        section = config.sections[0]
        short["sections"][section.name]["topics"].pop()
        ineligible_ref = next(
            ref
            for ref, citation in projected.citations.items()
            if citation.category not in section.corpus_categories
        )
        short["excluded_topics"][section.name][0]["citation_refs"] = [ineligible_ref]
        runner_up = short["excluded_topics"][section.name][1]

        promoted, actions = promote_excluded_to_underfilled(
            short, config, projected.citations
        )

        self.assertEqual(len(actions), 1)
        self.assertEqual(promoted["sections"][section.name]["topics"][-1], runner_up)
        self.assertEqual(
            promoted["excluded_topics"][section.name][0]["citation_refs"],
            [ineligible_ref],
        )

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
            "structured_item_limit",
        }
        _corpus, config, projected, output = fixture_contract()
        section_names = [section.name for section in config.sections]
        categories_by_section = {
            section.name: set(section.corpus_categories) for section in config.sections
        }
        used_items = used_item_refs(projected, output)

        def ineligible_ref_for(section):
            return next(
                ref
                for ref, citation in projected.citations.items()
                if citation.category not in section.corpus_categories
            )

        def spare_eligible_ref_for(section):
            return next(
                ref
                for ref, citation in projected.citations.items()
                if citation.category in section.corpus_categories
                and citation.item_ref not in used_items
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

            first = config.sections[0]
            over_limit = copy.deepcopy(output)
            over_limit["sections"][first.name]["topics"].append({
                "headline": "One valid story too many",
                "summary": "A valid entry that only pushes the section over target.",
                "citation_refs": [spare_eligible_ref_for(first)],
            })
            yield "section exceeds target count", over_limit, {"structured_item_limit"}

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

    def test_repair_trims_over_limit_sections(self):
        _corpus, config, projected, output = fixture_contract()
        first = config.sections[0]
        used_items = used_item_refs(projected, output)
        extra_ref = next(
            ref
            for ref, citation in projected.citations.items()
            if citation.category in first.corpus_categories
            and citation.item_ref not in used_items
        )
        over = copy.deepcopy(output)
        over["sections"][first.name]["topics"].append({
            "headline": "Extra valid story beyond the section limit",
            "summary": "A wholly valid entry that only offends the target count.",
            "citation_refs": [extra_ref],
        })

        before = {
            finding.check
            for finding in validate_output(over, config, projected.citations)
        }
        self.assertIn("structured_item_limit", before)

        repaired, actions = repair_structural_output(over, config, projected.citations)

        self.assertEqual(
            len(repaired["sections"][first.name]["topics"]), first.target_stories
        )
        residual = {
            finding.check
            for finding in validate_output(repaired, config, projected.citations)
        }
        self.assertNotIn("structured_item_limit", residual)
        self.assertTrue(
            any(
                action["action"] == "drop_entry"
                and "maximum" in action["reason"]
                and action["path"].startswith(f"topics.{first.name}")
                for action in actions
            ),
            msg=actions,
        )

    def test_trim_never_drops_entries_preserved_for_rejection(self):
        _corpus, config, projected, output = fixture_contract()
        first = config.sections[0]
        used_items = used_item_refs(projected, output)
        spare_entry = {
            "headline": "Valid story beyond the section limit",
            "summary": "A wholly valid entry that only offends the target count.",
            "citation_refs": [
                next(
                    ref
                    for ref, citation in projected.citations.items()
                    if citation.category in first.corpus_categories
                    and citation.item_ref not in used_items
                )
            ],
        }
        unknown_entry = {
            "headline": "Fabricated story past the limit",
            "summary": "Cites evidence that is not in the corpus.",
            "citation_refs": ["citation_9999"],
        }
        over = copy.deepcopy(output)
        over["sections"][first.name]["topics"].extend([spare_entry, unknown_entry])

        repaired, actions = repair_structural_output(over, config, projected.citations)

        topics = repaired["sections"][first.name]["topics"]
        self.assertIn(unknown_entry, topics)
        self.assertNotIn(spare_entry, topics)
        self.assertEqual(
            [action["path"] for action in actions],
            [f"topics.{first.name}[{first.target_stories}]"],
        )
        residual = {
            finding.check
            for finding in validate_output(repaired, config, projected.citations)
        }
        self.assertIn("unknown_citation_ref", residual)
        self.assertIn("structured_item_limit", residual)

    def _cited_excerpt(self, projected, refs, evidence):
        """The deduplicated cited evidence text a swapped summary must equal."""
        texts = dict.fromkeys(
            text
            for ref in refs
            if (text := evidence.get(
                corpus_schema.canonicalize_url(projected.citations[ref].article_url)))
        )
        return " ".join(" ".join(texts).split())

    def _bloat(self, excerpt):
        """A summary guaranteed to exceed the claim/evidence ratio."""
        return "word " * (int(eval_briefing.CLAIM_EVIDENCE_RATIO * len(excerpt)) + 5)

    def test_repair_swaps_summary_exceeding_evidence_for_excerpt(self):
        corpus, config, projected, output = fixture_contract()
        evidence = eval_briefing.corpus_evidence(corpus)
        first = config.sections[0]
        broken = copy.deepcopy(output)
        entry = broken["sections"][first.name]["topics"][0]
        excerpt = self._cited_excerpt(projected, entry["citation_refs"], evidence)
        self.assertTrue(excerpt)
        entry["summary"] = self._bloat(excerpt)

        repaired, actions = repair_structural_output(
            broken, config, projected.citations, evidence=evidence
        )

        self.assertEqual(
            repaired["sections"][first.name]["topics"][0]["summary"], excerpt
        )
        swap_actions = [
            action for action in actions
            if action["action"] == "replace_summary_with_excerpt"
        ]
        self.assertEqual(len(swap_actions), 1)
        self.assertEqual(swap_actions[0]["path"], f"topics.{first.name}[0]")
        self.assertTrue(swap_actions[0]["reason"])

    def test_repair_renders_231_character_claim_from_58_character_evidence_verbatim(self):
        corpus, config, projected, output = fixture_contract()
        first = config.sections[0]
        entry = output["sections"][first.name]["topics"][0]
        entry["summary"] = "x" * 231
        cited_items = {
            projected.citations[ref].item_ref
            for ref in entry["citation_refs"]
        }
        support = "e" * 58
        evidence = {
            corpus_schema.canonicalize_url(citation.article_url): support
            for citation in projected.citations.values()
            if citation.item_ref in cited_items
        }

        repaired, actions = repair_structural_output(
            output, config, projected.citations, evidence=evidence
        )
        rendered = render_briefing(
            repaired, corpus, config, projected.citations, repair_actions=actions
        )

        self.assertEqual(
            repaired["sections"][first.name]["topics"][0]["summary"], support
        )
        self.assertIn(
            f"**{entry['headline']}** [verbatim] — {support}", rendered
        )
        self.assertNotIn("claim_exceeds_evidence", {
            finding.check for finding in eval_briefing.evaluate(corpus, rendered, config)
        })

    def test_repair_swap_skips_short_summaries_unknown_refs_and_no_evidence(self):
        corpus, config, projected, output = fixture_contract()
        evidence = eval_briefing.corpus_evidence(corpus)
        first = config.sections[0]

        # (a) summaries within the ratio are untouched and produce no actions.
        repaired, actions = repair_structural_output(
            output, config, projected.citations, evidence=evidence
        )
        self.assertEqual(repaired, output)
        self.assertEqual(actions, [])

        # (b) an unknown ref is an evidence-boundary violation: preserved.
        unknown = copy.deepcopy(output)
        unknown_entry = unknown["sections"][first.name]["topics"][0]
        unknown_entry["citation_refs"] = ["citation_9999"]
        unknown_entry["summary"] = "word " * 500
        repaired, actions = repair_structural_output(
            unknown, config, projected.citations, evidence=evidence
        )
        self.assertEqual(
            repaired["sections"][first.name]["topics"][0], unknown_entry
        )
        self.assertEqual(actions, [])

        # (c) citations without corpus evidence cannot ground a swap: untouched.
        no_evidence = copy.deepcopy(output)
        bloated_entry = no_evidence["sections"][first.name]["topics"][0]
        bloated_entry["summary"] = "word " * 500
        cited_items = {
            projected.citations[ref].item_ref
            for ref in bloated_entry["citation_refs"]
        }
        cited_urls = {
            corpus_schema.canonicalize_url(citation.article_url)
            for citation in projected.citations.values()
            if citation.item_ref in cited_items
        }
        pruned_evidence = {
            url: text for url, text in evidence.items() if url not in cited_urls
        }
        self.assertTrue(pruned_evidence)
        repaired, actions = repair_structural_output(
            no_evidence, config, projected.citations, evidence=pruned_evidence
        )
        self.assertEqual(
            repaired["sections"][first.name]["topics"][0], bloated_entry
        )
        self.assertEqual(actions, [])

        # (d) evidence=None (default) behaves exactly as before: no swap pass.
        repaired, actions = repair_structural_output(
            no_evidence, config, projected.citations
        )
        self.assertEqual(repaired, no_evidence)
        self.assertEqual(actions, [])

    def test_repair_swap_skips_url_bearing_excerpts(self):
        # Feed blurbs routinely carry bare URLs; swapping one into a summary
        # would create a non-repairable freeform_url ERROR. Fail closed: leave
        # the entry untouched and preserved for review.
        corpus, config, projected, output = fixture_contract()
        first = config.sections[0]
        broken = copy.deepcopy(output)
        entry = broken["sections"][first.name]["topics"][0]
        cited_urls = {
            corpus_schema.canonicalize_url(projected.citations[ref].article_url)
            for ref in entry["citation_refs"]
        }
        tainted = False
        for items in corpus["categories"].values():
            for item in items:
                item_urls = {
                    corpus_schema.canonicalize_url(url)
                    for key in ("url", "discussion")
                    if (url := (item.get(key) or "").strip())
                }
                if item_urls & cited_urls:
                    item["summary"] = (
                        f"{item.get('summary', '')} See https://example.com/story"
                    ).strip()
                    tainted = True
        self.assertTrue(tainted)
        evidence = eval_briefing.corpus_evidence(corpus)
        excerpt = self._cited_excerpt(projected, entry["citation_refs"], evidence)
        self.assertTrue(eval_briefing.output_urls(excerpt))
        entry["summary"] = self._bloat(excerpt)

        repaired, actions = repair_structural_output(
            broken, config, projected.citations, evidence=evidence
        )

        self.assertEqual(repaired["sections"][first.name]["topics"][0], entry)
        self.assertEqual(actions, [])

    def test_repair_swap_skips_opaque_reference_bearing_excerpts(self):
        # Corpus evidence is untrusted and can contain text that resembles an
        # internal handle. Deterministic repair must not copy that spelling
        # into prose, where it is a non-repairable ERROR.
        corpus, config, projected, output = fixture_contract()
        first = config.sections[0]
        broken = copy.deepcopy(output)
        entry = broken["sections"][first.name]["topics"][0]
        cited_urls = {
            corpus_schema.canonicalize_url(projected.citations[ref].article_url)
            for ref in entry["citation_refs"]
        }
        tainted = False
        for items in corpus["categories"].values():
            for item in items:
                item_urls = {
                    corpus_schema.canonicalize_url(url)
                    for key in ("url", "discussion")
                    if (url := (item.get(key) or "").strip())
                }
                if item_urls & cited_urls:
                    item["summary"] = (
                        f"{item.get('summary', '')} Source mentions citation_0042 "
                        "and item_0017."
                    ).strip()
                    tainted = True
        self.assertTrue(tainted)
        evidence = eval_briefing.corpus_evidence(corpus)
        excerpt = self._cited_excerpt(projected, entry["citation_refs"], evidence)
        self.assertRegex(excerpt, r"\b(?:citation|item)_\d+\b")
        entry["summary"] = self._bloat(excerpt)
        original_entry = copy.deepcopy(entry)

        repaired, actions = repair_structural_output(
            broken, config, projected.citations, evidence=evidence
        )

        self.assertEqual(
            repaired["sections"][first.name]["topics"][0], original_entry
        )
        self.assertNotRegex(
            repaired["sections"][first.name]["topics"][0]["summary"],
            r"\b(?:citation|item)_\d+\b",
        )
        self.assertEqual(actions, [])

    def test_repair_swap_runs_after_structural_drops_and_uses_final_paths(self):
        corpus, config, projected, output = fixture_contract()
        evidence = eval_briefing.corpus_evidence(corpus)
        first = config.sections[0]
        second = config.sections[1]
        broken = copy.deepcopy(output)
        # entry[0] repeats an item already used by an earlier section: dropped.
        repeated = copy.deepcopy(broken["sections"][first.name]["topics"][0])
        broken["sections"][second.name]["topics"].insert(0, repeated)
        # entry[1] is bloated; after the drop it sits at index 0.
        bloated = broken["sections"][second.name]["topics"][1]
        excerpt = self._cited_excerpt(projected, bloated["citation_refs"], evidence)
        self.assertTrue(excerpt)
        bloated["summary"] = self._bloat(excerpt)

        repaired, actions = repair_structural_output(
            broken, config, projected.citations, evidence=evidence
        )

        self.assertTrue(any(
            action["action"] == "drop_entry"
            and action["path"] == f"topics.{second.name}[0]"
            for action in actions
        ), msg=actions)
        swap_actions = [
            action for action in actions
            if action["action"] == "replace_summary_with_excerpt"
        ]
        self.assertEqual(
            [action["path"] for action in swap_actions],
            [f"topics.{second.name}[0]"],
        )
        self.assertEqual(
            repaired["sections"][second.name]["topics"][0]["summary"], excerpt
        )

    def test_claim_exceeds_evidence_is_repairable(self):
        self.assertIn("claim_exceeds_evidence", REPAIRABLE_CHECKS)

    def _consolidate_first_two_topics(self, output, section):
        """Fold topic [1]'s item into topic [0] so it cites >1 distinct item."""
        topics = output["sections"][section.name]["topics"]
        donor = topics.pop(1)
        entry = topics[0]
        entry["citation_refs"] = list(
            dict.fromkeys(entry["citation_refs"] + donor["citation_refs"])
        )
        return entry

    def test_render_marks_promoted_entries_and_the_checker_accepts_them(self):
        corpus, config, projected, output = fixture_contract()
        ungrouped = config.sections[0]
        grouped = next(s for s in config.sections if s.group is not None)
        promoted_entry = output["sections"][ungrouped.name]["topics"][0]
        also_swapped = output["sections"][grouped.name]["topics"][0]
        actions = [
            {
                "action": "promote_excluded_entry",
                "path": f"topics.{ungrouped.name}[0]",
                "reason": f"promoted from excluded_topics.{ungrouped.name}[0]",
            },
            {
                "action": "promote_excluded_entry",
                "path": f"topics.{grouped.name}[0]",
                "reason": f"promoted from excluded_topics.{grouped.name}[0]",
            },
            {
                "action": "replace_summary_with_excerpt",
                "path": f"topics.{grouped.name}[0]",
                "reason": "oversized summary",
            },
        ]

        markdown = render_briefing(
            output, corpus, config, projected.citations, repair_actions=actions
        )

        lines = markdown.splitlines()
        promoted_line = next(
            line for line in lines if promoted_entry["headline"] in line
        )
        self.assertIn(
            f"**{promoted_entry['headline']}** "
            "[promoted from the accountability log] — ",
            promoted_line,
        )
        # Both producer tags can land on one topic, in a fixed order.
        both_line = next(line for line in lines if also_swapped["headline"] in line)
        self.assertIn(
            "[promoted from the accountability log] [verbatim] — ", both_line
        )
        # The tags are producer-owned, so the checker's grammar must still
        # parse the topic rather than reading the line as a sub-header.
        sections = eval_briefing.parse_briefing(markdown, config)
        self.assertEqual(
            len(sections[ungrouped.name]["topics"]), ungrouped.target_stories
        )
        self.assertEqual(
            [
                finding for finding in eval_briefing.evaluate(corpus, markdown, config)
                if finding.level == eval_briefing.ERROR
            ],
            [],
        )

    def test_render_marks_swapped_entries_as_verbatim(self):
        corpus, config, projected, output = fixture_contract()
        ungrouped = config.sections[0]
        grouped = next(s for s in config.sections if s.group is not None)
        self.assertIsNone(ungrouped.group)
        consolidated = self._consolidate_first_two_topics(output, grouped)
        actions = [
            {
                "action": "replace_summary_with_excerpt",
                "path": f"topics.{ungrouped.name}[0]",
                "reason": "oversized summary",
            },
            {
                "action": "replace_summary_with_excerpt",
                "path": f"topics.{grouped.name}[0]",
                "reason": "oversized summary",
            },
            # Other action kinds never produce a marker, even on a real path.
            {
                "action": "drop_entry",
                "path": f"topics.{ungrouped.name}[1]",
                "reason": "unrelated",
            },
        ]

        markdown = render_briefing(
            output, corpus, config, projected.citations, repair_actions=actions
        )

        lines = markdown.splitlines()
        plain_entry = output["sections"][ungrouped.name]["topics"][0]
        plain_line = next(
            line for line in lines if plain_entry["headline"] in line
        )
        self.assertIn(
            f"**{plain_entry['headline']}** [verbatim] — ", plain_line
        )
        combined_line = next(
            line for line in lines if consolidated["headline"] in line
        )
        # Consolidation keeps its existing marker; evidence substitution has
        # a separate literal provenance tag.
        self.assertIn(
            f"**{consolidated['headline']}** *(consolidated)* [verbatim] — ",
            combined_line,
        )
        self.assertEqual(combined_line.count("*("), 1)
        self.assertEqual(markdown.count("[verbatim]"), 2)

    def test_verbatim_marker_is_excluded_from_checker_prose(self):
        corpus, config, projected, output = fixture_contract()
        evidence = eval_briefing.corpus_evidence(corpus)
        first = config.sections[0]
        broken = copy.deepcopy(output)
        # A consolidated entry (two distinct cited items) exercises the
        # dedup-by-text join in both the swap and the checker.
        entry = self._consolidate_first_two_topics(broken, first)
        excerpt = self._cited_excerpt(projected, entry["citation_refs"], evidence)
        self.assertTrue(excerpt)
        entry["summary"] = self._bloat(excerpt)

        repaired, actions = repair_structural_output(
            broken, config, projected.citations, evidence=evidence
        )
        swap_paths = [
            action["path"] for action in actions
            if action["action"] == "replace_summary_with_excerpt"
        ]
        self.assertEqual(swap_paths, [f"topics.{first.name}[0]"])

        rendered = render_briefing(
            repaired, corpus, config, projected.citations, repair_actions=actions
        )
        self.assertIn("*(consolidated)* [verbatim]", rendered)
        findings = eval_briefing.evaluate(corpus, rendered, config)
        self.assertFalse(
            {finding.check for finding in findings}
            & {"claim_exceeds_evidence", "unsupported_figure",
               "unsupported_quotation"},
            msg=findings,
        )

    def test_rendered_briefing_carries_story_anchors(self):
        corpus, config, projected, output = fixture_contract()
        markdown = render_briefing(output, corpus, config, projected.citations)
        self.assertIn("<!-- story: topics.", markdown)
        accountable = [s for s in config.sections if s.excluded_stories]
        if accountable:
            self.assertIn("<!-- story: excluded_topics.", markdown)
        for section in config.sections:
            for index in range(len(output["sections"][section.name]["topics"])):
                anchor = f"<!-- story: topics.{section.name}[{index}] -->"
                self.assertIn(anchor, markdown, msg=f"missing anchor {anchor}")
            if section.excluded_stories:
                for index in range(len(output["excluded_topics"][section.name])):
                    anchor = f"<!-- story: excluded_topics.{section.name}[{index}] -->"
                    self.assertIn(anchor, markdown, msg=f"missing anchor {anchor}")

    def test_story_anchors_precede_their_headlines(self):
        corpus, config, projected, output = fixture_contract()
        markdown = render_briefing(output, corpus, config, projected.citations)
        lines = markdown.splitlines()
        for section in config.sections:
            for index, topic in enumerate(output["sections"][section.name]["topics"]):
                anchor = f"<!-- story: topics.{section.name}[{index}] -->"
                anchor_line = next(
                    i for i, candidate in enumerate(lines) if candidate == anchor
                )
                headline_line = next(
                    i for i in range(anchor_line, len(lines))
                    if topic["headline"] in lines[i]
                )
                self.assertEqual(
                    headline_line, anchor_line + 1,
                    msg=f"anchor {anchor} should be directly above headline",
                )

    def test_story_anchors_do_not_break_eval_briefing(self):
        corpus, config, projected, output = fixture_contract()
        self.assertEqual(validate_output(output, config, projected.citations), [])
        briefing = render_briefing(output, corpus, config, projected.citations)
        self.assertIn("<!-- story:", briefing)
        self.assertEqual(eval_briefing.evaluate(corpus, briefing, config), [])

    def test_candidate_preview_carries_story_anchors_before_headlines(self):
        # Findings render only on review_required pages, whose public artifact
        # is the candidate preview — so the preview must carry the same anchors
        # as the final briefing for path-based finding attachment to work.
        corpus, config, projected, output = fixture_contract()
        outcome = classify_outcome([], corpus.get("errors", []))
        preview = render_candidate_preview(
            output, corpus, config, projected.citations, [], outcome
        )
        lines = preview.splitlines()
        for section in config.sections:
            for index in range(len(output["sections"][section.name]["topics"])):
                anchor = f"<!-- story: topics.{section.name}[{index}] -->"
                anchor_line = lines.index(anchor)
                self.assertTrue(
                    lines[anchor_line + 1].startswith("**"),
                    msg=f"{anchor} must directly precede its headline line",
                )
            if section.excluded_stories:
                for index in range(len(output["excluded_topics"][section.name])):
                    anchor = f"<!-- story: excluded_topics.{section.name}[{index}] -->"
                    anchor_line = lines.index(anchor)
                    self.assertTrue(
                        lines[anchor_line + 1].startswith("- **"),
                        msg=f"{anchor} must directly precede its excluded entry",
                    )

    def test_render_validation_status_redacts_source_issue_messages(self):
        # Source-fetch error messages come from the same untrusted network
        # responses as briefing prose, so a model-hostile message that spells
        # out a destination must be redacted just like a finding message.
        corpus = {
            "errors": [
                {
                    "source_type": "reddit",
                    "source_id": "test",
                    "status": "error",
                    "error_type": "HTTPError",
                    "message": "fetch failed: [www.reddit.com](https://www.reddit.com)",
                }
            ]
        }
        rendered = render_validation_status([], corpus)
        self.assertNotIn("https://www.reddit.com", rendered)
        self.assertIn(redact_destinations("[www.reddit.com](https://www.reddit.com)"), rendered)

    def test_render_validation_status_tolerates_malformed_source_issues(self):
        # The renderer also runs on failure paths where the corpus never
        # passed schema validation, so a record missing keys must render its
        # fallbacks instead of raising KeyError.
        rendered = render_validation_status([], {"errors": [{}]})
        self.assertIn("message=[missing source issue detail]", rendered)
        self.assertIn("source_type=[missing]", rendered)
        self.assertIn("status=[missing]", rendered)

    def test_quiet_threshold_degradation_never_demands_a_health_section(self):
        # Issue #172 R1: threshold-exceeding quiet sources in one category
        # flip the outcome's coverage axis to degraded (this call site) but
        # never enter `errors`, so the briefing's corpus-health section is
        # legitimately absent -- eval_briefing must not contradict that by
        # demanding one.
        quiet_count = corpus_schema.QUIET_SOURCE_DEGRADED_THRESHOLD + 2
        corpus = {
            "schema_version": corpus_schema.SCHEMA_VERSION,
            "errors": [],
            "processing": {"ai_tech": {"undated_dropped": 0}},
            "sources": [
                {
                    "source_type": "hacker_news", "source_id": f"prompt {n}",
                    "category": "ai_tech", "status": "quiet",
                    "requested": True, "http_success": True,
                    "parsed_entries": 5, "dated_entries": 5, "retained_entries": 0,
                    "retained_bytes": 0, "estimated_tokens": 0, "duration_ms": 12,
                    "error_type": "NoWindowEntries",
                    "message": "response contained zero usable entries in the requested window",
                }
                for n in range(quiet_count)
            ],
        }
        self.assertTrue(corpus_schema.corpus_health_degraded(corpus))

        rendered = render_validation_status([], corpus)
        self.assertIn("Coverage: `degraded`", rendered)

        self.assertEqual(eval_briefing.check_corpus_health_reported({}, corpus), [])


if __name__ == "__main__":
    unittest.main()
