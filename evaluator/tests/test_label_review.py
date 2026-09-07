"""Evaluator label review regression coverage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import evaluator.__main__ as evaluator_cli
import evaluator.label_review as label_review
from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.label_review import (
    _parse_reviews,
    _portable_path,
    blinded_cases,
    export_human_review_packet,
    run_label_review,
)


class RawLabelReviewAdapter(Adapter):
    provider = "offline-label-review"

    def __init__(self, model: str, response: dict[str, Any]):
        super().__init__(model)
        self.response = response
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        return Generation(text=json.dumps(self.response), latency_ms=1.0)



class LabelReviewAdapter(Adapter):
    provider = "offline-label-review"

    def __init__(
        self,
        model: str,
        labels: dict[str, list[str]],
        controls: dict[str, object] | None = None,
    ):
        super().__init__(model)
        self.labels = labels
        self.controls = controls
        self.calls = 0

    def generation_controls(self) -> dict[str, object]:
        return self.controls or super().generation_controls()

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        self.last_prompt = prompt
        present = [case_id for case_id in self.labels if f'"case": "{case_id}"' in prompt]
        reviews = [
            {"case": case_id, "labels": self.labels[case_id], "rationale": "fixture rationale"}
            for case_id in present
        ]
        return Generation(text=json.dumps({"reviews": reviews}), latency_ms=1.0)



class LabelReviewTest(unittest.TestCase):
    def test_human_review_export_blinds_and_randomizes_provisional_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite = {
                "schema_version": 1,
                "cases": [
                    {
                        "id": "revealing-one",
                        "component": "checker",
                        "family": "valid_edge",
                        "variant": "valid-baseline",
                        "human_labels": ["ungrounded_link"],
                        "label_status": "provisional",
                    },
                    {
                        "id": "already-gold",
                        "component": "checker",
                        "family": "valid_edge",
                        "variant": "valid-baseline",
                        "human_labels": [],
                    },
                ],
            }
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            output = temporary / "packet"
            manifest = export_human_review_packet(output, suite_path, seed=7)

            packet_text = (output / "reviewer-packet.json").read_text(encoding="utf-8")
            self.assertEqual(manifest["case_count"], 1)
            self.assertNotIn("revealing-one", packet_text)
            self.assertNotIn("already-gold", packet_text)
            self.assertNotIn("human_labels", packet_text)
            self.assertIn("review-", packet_text)
            answer_key = json.loads(
                (output / "coordinator-only" / "answer-key.json").read_text(encoding="utf-8")
            )
            self.assertIn("revealing-one", answer_key["mapping"].values())

    def test_human_review_export_names_unknown_case_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(
                json.dumps({"cases": [{"id": "known", "label_status": "provisional"}]}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown case IDs: missing"):
                export_human_review_packet(
                    temporary / "packet",
                    suite_path,
                    case_ids={"missing"},
                )

    def test_repository_paths_are_recorded_relative_to_the_checkout(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        self.assertEqual(
            _portable_path(evaluator_dir / "fixtures" / "checker-cases.json"),
            "./evaluator/fixtures/checker-cases.json",
        )

    def test_review_parser_accepts_a_prefaced_json_object(self) -> None:
        parsed = _parse_reviews(
            'Result follows:\n{"reviews":[{"case":"case-001","labels":[],"rationale":"valid"}]}',
            {"case-001"},
        )
        self.assertEqual(parsed["case-001"]["labels"], [])

    def test_review_parser_accepts_figure_supported_elsewhere(self) -> None:
        parsed = _parse_reviews(
            '{"reviews":[{"case":"case-001","labels":["figure_supported_elsewhere"],'
            '"rationale":"the exact figure appears in a matching corpus item"}]}',
            {"case-001"},
        )
        self.assertEqual(
            parsed["case-001"]["labels"], ["figure_supported_elsewhere"]
        )

    def test_review_parser_rejects_every_non_string_label_with_value_error(self) -> None:
        malformed_labels: tuple[object, ...] = ({}, [], 1, True, None)
        for label in malformed_labels:
            with self.subTest(label=label), self.assertRaisesRegex(
                    ValueError, "contains non-string labels"):
                _parse_reviews(json.dumps({
                    "reviews": [{
                        "case": "case-001",
                        "labels": [label],
                        "rationale": "fixture rationale",
                    }],
                }), {"case-001"})

    def test_review_parser_preserves_unknown_and_duplicate_label_errors(self) -> None:
        for labels, message in (
            (["not-in-rubric"], "contains invalid labels"),
            (["unsupported_claim", "unsupported_claim"], "has duplicate labels"),
        ):
            with self.subTest(labels=labels), self.assertRaisesRegex(ValueError, message):
                _parse_reviews(json.dumps({
                    "reviews": [{
                        "case": "case-001",
                        "labels": labels,
                        "rationale": "fixture rationale",
                    }],
                }), {"case-001"})

    def test_malformed_fresh_response_is_saved_then_retried_as_value_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }), encoding="utf-8")
            output_dir = temporary / "output"
            malformed = RawLabelReviewAdapter("reviewer", {
                "reviews": [{
                    "case": "case-001",
                    "labels": [{}],
                    "rationale": "fixture rationale",
                }],
            })

            with self.assertRaisesRegex(ValueError, "contains non-string labels"):
                run_label_review(malformed, None, output_dir, suite_path)

            checkpoint = output_dir / "reviewer-batch-01.json"
            self.assertTrue(checkpoint.is_file())
            good = LabelReviewAdapter("reviewer", {"case-001": []})
            result = run_label_review(good, None, output_dir, suite_path)
            self.assertEqual(malformed.calls, 1)
            self.assertEqual(good.calls, 1)
            self.assertFalse(result["reviewer_calls"][0]["resumed"])
            self.assertEqual(result["cases"][0]["reviewer_labels"], [])

    def test_blinded_payload_omits_fixture_metadata_and_human_labels(self) -> None:
        suite = {
            "cases": [{
                "id": "revealing-name",
                "component": "checker",
                "family": "revealing-family",
                "variant": "valid-baseline",
                "human_labels": ["ungrounded_link"],
            }]
        }
        payloads, mapping = blinded_cases(suite)
        encoded = json.dumps(payloads)
        self.assertNotIn("revealing-name", encoded)
        self.assertNotIn("revealing-family", encoded)
        self.assertNotIn("human_labels", encoded)
        self.assertNotIn("ungrounded_link", encoded)
        self.assertEqual(mapping, {"case-001": "revealing-name"})

    def test_disagreements_are_adjudicated_without_rewriting_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite = {
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            reviewer = LabelReviewAdapter("sonnet", {"case-001": ["unsupported_claim"]})
            adjudicator = LabelReviewAdapter(
                "opus",
                {"case-001": []},
                {"reasoning_enabled": True, "reasoning_effort": "high"},
            )
            result = run_label_review(reviewer, adjudicator, temporary / "output", suite_path)

            self.assertEqual(result["exact_agreements"], 0)
            self.assertEqual(result["disagreements_adjudicated"], 1)
            self.assertEqual(result["cases"][0]["machine_consensus_labels"], [])
            self.assertIn("human approval", result["notice"])
            self.assertNotIn("provisional_labels", reviewer.last_prompt)
            self.assertIn("provisional_labels", adjudicator.last_prompt)
            self.assertEqual(json.loads(suite_path.read_text(encoding="utf-8")), suite)
            self.assertTrue((temporary / "output" / "label-review.json").is_file())
            self.assertTrue((temporary / "output" / "reviewer-batch-01.json").is_file())
            self.assertTrue((temporary / "output" / "adjudicator-batch-01.json").is_file())

            resumed = run_label_review(reviewer, adjudicator, temporary / "output", suite_path)
            self.assertEqual(reviewer.calls, 1)
            self.assertEqual(adjudicator.calls, 1)
            self.assertTrue(resumed["reviewer_calls"][0]["resumed"])
            self.assertTrue(resumed["adjudicator_calls"][0]["resumed"])
            self.assertEqual(
                result["adjudicator"]["generation_controls"],
                {"reasoning_enabled": True, "reasoning_effort": "high"},
            )

    def test_label_review_can_select_only_named_cases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite = {
                "schema_version": 1,
                "cases": [
                    {
                        "id": case_id,
                        "component": "checker",
                        "family": "valid_edge",
                        "variant": "valid-baseline",
                        "human_labels": [],
                    }
                    for case_id in ("selected", "omitted")
                ],
            }
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            reviewer = LabelReviewAdapter("reviewer", {"case-001": []})
            result = run_label_review(
                reviewer,
                None,
                temporary / "output",
                suite_path,
                case_ids={"selected"},
            )
            self.assertEqual(result["case_count"], 1)
            self.assertEqual(result["cases"][0]["fixture_id"], "selected")
            identity = json.loads(
                (temporary / "output" / "label-review-run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(identity["selected_case_ids"], ["selected"])

            changed_adjudicator = LabelReviewAdapter(
                "opus",
                {"case-001": []},
                {"reasoning_enabled": False, "reasoning_effort": None},
            )
            with self.assertRaisesRegex(ValueError, "different label-review run"):
                run_label_review(
                    reviewer,
                    changed_adjudicator,
                    temporary / "output",
                    suite_path,
                )

    def test_checkpoints_are_bound_to_reviewer_generation_controls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }), encoding="utf-8")
            enabled = LabelReviewAdapter(
                "reviewer",
                {"case-001": []},
                {"reasoning_enabled": True, "reasoning_effort": "high"},
            )
            result = run_label_review(enabled, None, temporary / "output", suite_path)

            identity = json.loads(
                (temporary / "output" / "label-review-run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(identity["schema_version"], 4)
            self.assertEqual(
                result["reviewer"]["generation_controls"],
                {"reasoning_enabled": True, "reasoning_effort": "high"},
            )

            disabled = LabelReviewAdapter(
                "reviewer",
                {"case-001": []},
                {"reasoning_enabled": False, "reasoning_effort": None},
            )
            with self.assertRaisesRegex(ValueError, "different label-review run"):
                run_label_review(disabled, None, temporary / "output", suite_path)

    def test_checkpoints_are_bound_to_prompts_and_label_rubric(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }), encoding="utf-8")
            reviewer = LabelReviewAdapter(
                "reviewer", {"case-001": ["unsupported_claim"]}
            )
            adjudicator = LabelReviewAdapter("adjudicator", {"case-001": []})
            output_dir = temporary / "output"

            run_label_review(reviewer, adjudicator, output_dir, suite_path)
            identity = json.loads(
                (output_dir / "label-review-run.json").read_text(encoding="utf-8")
            )
            self.assertEqual(identity["schema_version"], 4)
            self.assertEqual(len(identity["reviewer"]["prompt_template_sha256"]), 64)
            self.assertEqual(len(identity["adjudicator"]["prompt_template_sha256"]), 64)
            self.assertNotEqual(
                identity["reviewer"]["prompt_template_sha256"],
                identity["adjudicator"]["prompt_template_sha256"],
            )

            review_prompt = label_review._review_prompt
            with patch.object(
                    label_review, "_review_prompt",
                    side_effect=lambda cases: review_prompt(cases) + "\nChanged review prose.\n"):
                with self.assertRaisesRegex(ValueError, "different label-review run"):
                    run_label_review(reviewer, adjudicator, output_dir, suite_path)

            adjudication_prompt = label_review._adjudication_prompt
            with patch.object(
                    label_review, "_adjudication_prompt",
                    side_effect=lambda cases: adjudication_prompt(cases)
                    + "\nChanged adjudication prose.\n"):
                with self.assertRaisesRegex(ValueError, "different label-review run"):
                    run_label_review(reviewer, adjudicator, output_dir, suite_path)

            with patch.dict(
                    label_review.LABEL_RUBRIC,
                    {"new_label": "A changed rubric entry."}):
                with self.assertRaisesRegex(ValueError, "different label-review run"):
                    run_label_review(reviewer, adjudicator, output_dir, suite_path)

            self.assertEqual(reviewer.calls, 1)
            self.assertEqual(adjudicator.calls, 1)

    def test_batch_checkpoints_are_bound_to_exact_effective_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }), encoding="utf-8")

            reviewer = LabelReviewAdapter(
                "reviewer", {"case-001": ["unsupported_claim"]}
            )
            adjudicator = LabelReviewAdapter("adjudicator", {"case-001": []})
            reviewer_output = temporary / "reviewer-change"
            run_label_review(reviewer, adjudicator, reviewer_output, suite_path)
            review_prompt = label_review._review_prompt
            with patch.object(
                    label_review, "_review_prompt",
                    side_effect=lambda cases: review_prompt(cases)
                    + ("\nChanged real-batch review prose.\n" if cases else "")):
                changed = run_label_review(
                    reviewer, adjudicator, reviewer_output, suite_path
                )
            self.assertEqual(reviewer.calls, 2)
            self.assertEqual(adjudicator.calls, 1)
            self.assertFalse(changed["reviewer_calls"][0]["resumed"])
            self.assertTrue(changed["adjudicator_calls"][0]["resumed"])

            reviewer = LabelReviewAdapter(
                "reviewer", {"case-001": ["unsupported_claim"]}
            )
            adjudicator = LabelReviewAdapter("adjudicator", {"case-001": []})
            adjudicator_output = temporary / "adjudicator-change"
            run_label_review(reviewer, adjudicator, adjudicator_output, suite_path)
            adjudication_prompt = label_review._adjudication_prompt
            with patch.object(
                    label_review, "_adjudication_prompt",
                    side_effect=lambda cases: adjudication_prompt(cases)
                    + ("\nChanged real-batch adjudication prose.\n" if cases else "")):
                changed = run_label_review(
                    reviewer, adjudicator, adjudicator_output, suite_path
                )
            self.assertEqual(reviewer.calls, 1)
            self.assertEqual(adjudicator.calls, 2)
            self.assertTrue(changed["reviewer_calls"][0]["resumed"])
            self.assertFalse(changed["adjudicator_calls"][0]["resumed"])

            checkpoint = json.loads(
                (adjudicator_output / "adjudicator-batch-01.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(len(checkpoint["prompt_sha256"]), 64)

    def test_review_labels_loads_env_file_before_provider_preflight(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            env_file = temporary / ".env"
            env_file.write_text("OPENROUTER_API_KEY=from-file\n", encoding="utf-8")
            reviewer = LabelReviewAdapter("reviewer", {})
            result = {
                "status": "complete",
                "case_count": 0,
                "exact_agreements": 0,
                "disagreements_found": 0,
                "disagreements_adjudicated": 0,
            }

            def assert_credentials_loaded(_providers: object) -> None:
                self.assertEqual(os.environ.get("OPENROUTER_API_KEY"), "from-file")

            argv = [
                "evaluator",
                "review-labels",
                "--reviewer-provider", "openrouter",
                "--reviewer-model", "deepseek/deepseek-v4-flash",
                "--review-only",
                "--env-file", str(env_file),
                "--output-dir", str(temporary / "output"),
            ]
            with (
                patch.dict(os.environ, {}, clear=True),
                patch.object(sys, "argv", argv),
                patch.object(evaluator_cli, "_preflight", side_effect=assert_credentials_loaded),
                patch.object(evaluator_cli, "adapter_for", return_value=reviewer),
                patch.object(evaluator_cli, "run_label_review", return_value=result),
                patch("builtins.print"),
            ):
                self.assertEqual(evaluator_cli.main(), 0)

    def test_review_only_preserves_disagreements_for_human_adjudication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite_path.write_text(json.dumps({
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }), encoding="utf-8")
            reviewer = LabelReviewAdapter("reviewer", {"case-001": ["unsupported_claim"]})

            result = run_label_review(reviewer, None, temporary / "output", suite_path)

            self.assertEqual(result["disagreements_found"], 1)
            self.assertEqual(result["disagreements_adjudicated"], 0)
            self.assertIsNone(result["adjudicator"])
            self.assertIsNone(result["cases"][0]["machine_consensus_labels"])
            self.assertEqual(result["adjudicator_calls"], [])
            self.assertIn("adjudication_not_run", result["status"])

