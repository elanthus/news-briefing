from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from evaluator.adapters import Adapter, Generation, adapter_for
from evaluator.cases import run_deterministic_suite
from evaluator.metrics import rate, wilson_interval
from evaluator.runner import DEFAULT_CORPUS, apply_adjudications, run_evaluation, summarize


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
        self.assertEqual(result["heuristic_claim_false_positive_rate"]["trials"], 3)


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

    def test_all_required_provider_adapters_are_available(self) -> None:
        for provider, model in (
            ("codex-cli", "gpt-5.6-terra"),
            ("claude-code-cli", "claude-sonnet-5"),
            ("openrouter", "openai/gpt-5.6-terra"),
            ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ):
            self.assertEqual(adapter_for(provider, model).provider, provider)


if __name__ == "__main__":
    unittest.main()
