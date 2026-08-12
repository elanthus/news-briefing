from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.adapters import Adapter, Generation, adapter_for
from evaluator.cases import run_deterministic_suite
from evaluator.label_review import _parse_reviews, _portable_path, blinded_cases, run_label_review
from evaluator.metrics import rate, wilson_interval
from evaluator.runner import DEFAULT_CORPUS, _mutate, apply_adjudications, run_evaluation, summarize


class FixedSuiteTest(unittest.TestCase):
    def test_committed_suite_has_expected_scope_and_metrics(self) -> None:
        result = run_deterministic_suite()
        self.assertEqual(result["case_count"], 54)
        self.assertEqual(result["components"]["checker"]["cases"], 42)
        self.assertEqual(result["components"]["feed_parser"]["cases"], 12)
        families = {case["family"] for case in result["cases"]}
        for required in {
            "url", "encoding", "feed_shape", "malformed", "degraded",
            "partially_degraded", "thin_evidence", "conflicting_evidence",
            "over_consolidation", "category", "valid_edge",
        }:
            self.assertIn(required, families)

    def test_known_checker_limits_are_reported_not_hidden(self) -> None:
        result = run_deterministic_suite()
        misses = {
            label
            for case in result["cases"] if case["component"] == "checker"
            for label in case["missed"]
        }
        self.assertIn("conflicting_evidence", misses)
        self.assertIn("over_consolidation", misses)
        self.assertIn("unsupported_claim", misses)
        self.assertEqual(result["heuristic_claim_false_positive_rate"]["trials"], 1)


class MetricTest(unittest.TestCase):
    def test_wilson_interval_and_trial_counts(self) -> None:
        metric = rate(7, 10)
        self.assertEqual(metric["successes"], 7)
        self.assertEqual(metric["trials"], 10)
        low, high = metric["ci95_wilson"]
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)
        self.assertIsNone(wilson_interval(0, 0))


class FakeAdapter(Adapter):
    provider = "offline-fixture"

    def generate(self, prompt: str) -> Generation:
        self.last_prompt = prompt
        return Generation(
            text=(
                "# Daily Briefing — August 11, 2026\n\n"
                "## AI Dev Tools\n\n"
                "**Third-party models as subagents** — The author built a patch so subagents can use other providers.\n"
                "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
            ),
            latency_ms=12.5,
            input_tokens=100,
            output_tokens=30,
            cost_usd=0.001,
        )


class LabelReviewAdapter(Adapter):
    provider = "offline-label-review"

    def __init__(self, model: str, labels: dict[str, list[str]]):
        super().__init__(model)
        self.labels = labels
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        self.last_prompt = prompt
        present = [case_id for case_id in self.labels if f'"case": "{case_id}"' in prompt]
        reviews = [
            {"case": case_id, "labels": self.labels[case_id], "rationale": "fixture rationale"}
            for case_id in present
        ]
        return Generation(text=json.dumps({"reviews": reviews}), latency_ms=1.0)


class FailOnceAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary provider timeout")
        return super().generate(prompt)


class FailCorrectionAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        if self.calls == 1:
            generation = super().generate(prompt)
            return Generation(
                text=generation.text.replace(
                    "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                    "https://invented.example.test/story",
                ),
                latency_ms=generation.latency_ms,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                cost_usd=generation.cost_usd,
            )
        raise TimeoutError("temporary correction timeout")


class RunnerTest(unittest.TestCase):
    def test_offline_run_preserves_artifacts_and_reports_all_requested_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text((Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                              encoding="utf-8")
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "offline",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"
            report = run_evaluation(
                [FakeAdapter("fixture-1")], {"v1": prompt}, output,
                suite_path=suite, corpus_path=DEFAULT_CORPUS,
            )

            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "report.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            self.assertEqual(report["deterministic_summary"]["case_count"], 54)
            group = report["groups"][0]
            for field in (
                "first_pass_contract_success", "correction_success", "attack_success_first",
                "grounding_error_topics_final", "grounding_error_topics_human", "latency_first", "cost",
            ):
                self.assertIn(field, group)
            self.assertEqual(group["first_pass_contract_success"]["trials"], 1)
            self.assertEqual(group["grounding_error_topics_human"]["trials"], 0)
            self.assertEqual(group["cost"]["total_usd"], 0.001)

            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            adjudication_path = output / manifest["results"][0]["grounding_adjudication"]
            adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
            adjudication["topics"][0]["grounding_error"] = True
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            apply_adjudications(manifest, output)
            reviewed = summarize(manifest)["groups"][0]["grounding_error_topics_human"]
            self.assertEqual(reviewed["successes"], 1)
            self.assertEqual(reviewed["trials"], 1)

    def test_provider_failure_is_checkpointed_and_remaining_trials_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "flaky",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            report = run_evaluation(
                [FailOnceAdapter("fixture-1")],
                {"v1": prompt},
                output,
                trials=2,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_status"], "complete")
            self.assertEqual(len(manifest["results"]), 2)
            self.assertEqual(manifest["results"][0]["status"], "provider_error")
            self.assertEqual(manifest["results"][0]["error"]["stage"], "first")
            self.assertEqual(manifest["results"][1]["status"], "completed")
            self.assertEqual(report["provider_error_trials"], 1)
            group = report["groups"][0]
            self.assertEqual(group["case_trials"], 2)
            self.assertEqual(group["completed_case_trials"], 1)
            self.assertEqual(group["first_pass_contract_success"]["trials"], 1)

    def test_correction_failure_preserves_first_generation_and_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "correction-failure",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            report = run_evaluation(
                [FailCorrectionAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            self.assertEqual(row["status"], "completed_with_correction_error")
            self.assertEqual(row["correction_error"]["stage"], "correction")
            self.assertIsNone(row["correction"])
            self.assertTrue((output / row["artifact_dir"] / "first.md").is_file())
            self.assertEqual(report["correction_error_trials"], 1)
            self.assertEqual(report["groups"][0]["correction_success"]["successes"], 0)
            self.assertEqual(report["groups"][0]["correction_success"]["trials"], 1)

    def test_mutations_report_invalid_input_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "path must be a non-empty array"):
            _mutate({}, [{"path": [], "value": "x"}])
        with self.assertRaisesRegex(ValueError, "path does not exist"):
            _mutate({}, [{"path": ["missing", 0], "value": "x"}])

    def test_local_env_is_ignored_and_template_keeps_model_provenance(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        ignored = (evaluator_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/.env", ignored)
        template = (evaluator_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("Model catalog provenance: 2026-08-11", template)
        for key in ("CODEX_MODEL=", "CLAUDE_CODE_MODEL=", "OPENROUTER_MODEL=", "NVIDIA_MODEL="):
            self.assertIn(key, template)

    def test_all_required_provider_adapters_are_available(self) -> None:
        for provider, model in (
            ("codex-cli", "gpt-5.6-terra"),
            ("claude-code-cli", "claude-sonnet-5"),
            ("openrouter", "openai/gpt-5.6-terra"),
            ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ):
            self.assertEqual(adapter_for(provider, model).provider, provider)


class LabelReviewTest(unittest.TestCase):
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
            adjudicator = LabelReviewAdapter("opus", {"case-001": []})
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


if __name__ == "__main__":
    unittest.main()
