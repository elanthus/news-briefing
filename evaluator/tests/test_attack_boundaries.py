"""Attacks whose canary never reaches the model, checked without a model call.

Suite version 10 counted these cases as attack trials. Projection removes each
canary before any model request, so every trial had one possible outcome. The
cases now live in ``fixtures/attack-boundary-cases.json`` and these tests prove
the boundary that removes the payload directly.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Any

import briefing_config
from agent_runner.output import DESTINATION_REDACTION, ModelCorpus, project_corpus
from agent_runner.runner import build_request
from agent_runner.stages import evaluate_candidate
from evaluator.plan import attack_visibility_problems, attacked_trial_corpus

FIXTURES = Path(__file__).parents[1] / "fixtures"
BOUNDARY_FIXTURE = FIXTURES / "attack-boundary-cases.json"
BOUNDARY_SCHEMA_VERSION = 1
BOUNDARIES = {"destination_redaction", "destination_span_redaction", "source_health_not_projected"}
POLICY = (Path(__file__).parents[2] / "briefing-runner-prompt.md").read_text(encoding="utf-8")


def boundary_cases() -> list[dict[str, Any]]:
    fixture = json.loads(BOUNDARY_FIXTURE.read_text(encoding="utf-8"))
    if fixture.get("schema_version") != BOUNDARY_SCHEMA_VERSION:
        raise ValueError(f"attack boundary fixture schema_version must be {BOUNDARY_SCHEMA_VERSION}")
    entries: list[dict[str, Any]] = fixture["cases"]
    for entry in entries:
        if set(entry) != {"boundary", "case"} or entry["boundary"] not in BOUNDARIES:
            raise ValueError(f"malformed attack boundary entry: {entry!r}")
    return entries


def _trial(case: dict[str, Any]) -> tuple[dict[str, Any], ModelCorpus, str]:
    corpus = attacked_trial_corpus(
        case, suite_dir=FIXTURES, default_corpus=FIXTURES / "generation-corpus.json"
    )
    projected = project_corpus(corpus)
    config_data = json.loads((FIXTURES / case["config"]).read_text(encoding="utf-8"))
    return corpus, projected, build_request(POLICY, config_data, projected)


def _payloads(case: dict[str, Any], projected: ModelCorpus) -> list[str]:
    payloads = []
    for mutation in case["mutations"]:
        node: Any = projected.document
        for part in mutation["path"]:
            node = node[part]
        payloads.append(node)
    return payloads


def _candidate(
    case: dict[str, Any], corpus: dict[str, Any], projected: ModelCorpus, summary: str
) -> tuple[str | None, list[tuple[str, str]]]:
    """Validate and render a one-topic candidate in the case's first section."""
    config = briefing_config.load_config(FIXTURES / case["config"])
    section = config.sections[0]
    ref = next(
        citation.ref for citation in projected.citations.values()
        if citation.category in section.corpus_categories
    )
    output = {
        "schema_version": 1,
        "sections": {section.name: {"topics": [{
            "citation_refs": [ref], "headline": "Local notes server", "summary": summary,
        }]}},
        "excluded_topics": {},
    }
    rendered, _sections, findings = evaluate_candidate(output, corpus, config, projected.citations)
    return rendered, [(finding.level, finding.check) for finding in findings]


class AttackBoundaryTests(unittest.TestCase):
    def test_fixture_holds_exactly_the_cases_removed_from_version_10(self) -> None:
        old = json.loads((FIXTURES / "generation-cases-v10.json").read_text(encoding="utf-8"))
        new = json.loads((FIXTURES / "generation-cases-v11.json").read_text(encoding="utf-8"))
        kept = {case["id"] for case in new["cases"]}
        removed = [case for case in old["cases"] if case["id"] not in kept]
        self.assertEqual([entry["case"] for entry in boundary_cases()], removed)

    def test_no_canary_reaches_the_model_request(self) -> None:
        """The prose pass sees only selected items from this same projection."""
        for entry in boundary_cases():
            case = entry["case"]
            with self.subTest(case=case["id"]):
                corpus, _projected, request = _trial(case)
                for canary in case["forbidden_substrings"]:
                    self.assertNotIn(canary, request)
                problems = attack_visibility_problems(case, corpus)
                self.assertTrue(any(problem.startswith("canary ") for problem in problems), problems)

    def test_projection_masks_a_destination_canary(self) -> None:
        for entry in boundary_cases():
            if entry["boundary"] != "destination_redaction":
                continue
            case = entry["case"]
            with self.subTest(case=case["id"]):
                _corpus, projected, _request = _trial(case)
                self.assertEqual(case["forbidden_substrings"], ["https://security-advisory.example.test/urgent"])
                for payload in _payloads(case, projected):
                    self.assertIn(DESTINATION_REDACTION, payload)

    def test_a_guessed_destination_canary_is_rejected_before_rendering(self) -> None:
        """Even a model that guessed the URL could not place it in the scored briefing."""
        for entry in boundary_cases():
            if entry["boundary"] != "destination_redaction":
                continue
            case = entry["case"]
            with self.subTest(case=case["id"]):
                corpus, projected, _request = _trial(case)
                canary = case["forbidden_substrings"][0]
                rendered, findings = _candidate(case, corpus, projected, f"An advisory is at {canary}.")
                self.assertIsNone(rendered)
                self.assertIn(("ERROR", "freeform_url"), findings)

    def test_projection_masks_a_canary_inside_a_destination_spelling(self) -> None:
        for entry in boundary_cases():
            if entry["boundary"] != "destination_span_redaction":
                continue
            case = entry["case"]
            with self.subTest(case=case["id"]):
                _corpus, projected, _request = _trial(case)
                (payload,) = _payloads(case, projected)
                (raw,) = [mutation["value"] for mutation in case["mutations"]]
                (canary,) = case["forbidden_substrings"]
                self.assertIn(canary, raw)
                self.assertIn(DESTINATION_REDACTION, payload)
                self.assertNotIn(canary, payload)

    def test_source_failure_text_is_not_projected_and_code_renders_health(self) -> None:
        for entry in boundary_cases():
            if entry["boundary"] != "source_health_not_projected":
                continue
            case = entry["case"]
            with self.subTest(case=case["id"]):
                corpus, projected, request = _trial(case)
                self.assertEqual(case["mutations"], [])
                self.assertNotIn("sources", projected.document)
                self.assertNotIn("errors", projected.document)
                for failure in case["source_failures"]:
                    self.assertNotIn(failure["message"], request)
                rendered, findings = _candidate(
                    case, corpus, projected,
                    "A small MCP server stores local notes and exposes search and retrieval tools.",
                )
                self.assertEqual([level for level, _check in findings if level == "ERROR"], [])
                assert rendered is not None
                health = rendered.split("### Corpus health", 1)[1]
                self.assertIn('"source_id":"Feed A"', health)
                for canary in case["forbidden_substrings"]:
                    self.assertNotIn(canary, rendered)


if __name__ == "__main__":
    unittest.main()
