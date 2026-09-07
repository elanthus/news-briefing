"""Evaluator comparison regression coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.comparison import compare_runs, markdown_comparison
from evaluator.metrics import percentile, rate, wilson_interval


class MetricTest(unittest.TestCase):
    def test_wilson_interval_and_trial_counts(self) -> None:
        metric = rate(7, 10)
        self.assertEqual(metric["successes"], 7)
        self.assertEqual(metric["trials"], 10)
        low, high = metric["ci95_wilson"]
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)
        self.assertIsNone(wilson_interval(0, 0))

    def test_percentile_interpolates_and_validates_input(self) -> None:
        self.assertEqual(percentile([0.0, 10.0], 0.25), 2.5)
        with self.assertRaisesRegex(ValueError, "at least one"):
            percentile([], 0.5)



class ComparisonTest(unittest.TestCase):
    @staticmethod
    def _provenance(*prompts: str) -> dict:
        return {
            "corpus_sha256": "corpus",
            "config_sha256": {"config.json": "config"},
            "protocol_sha256": "protocol",
            "execution_order": "prompt_interleaved_randomized",
            "execution_seed": 1729,
            "prompt_sha256": {prompt: f"hash-{prompt}" for prompt in prompts},
            "generation_controls": [{
                "provider": "provider", "model": "model", "temperature": 0,
            }],
        }

    @staticmethod
    def _row(prompt: str, case_id: str, trial: int, success: bool = True) -> dict:
        stage = {
            "contract_success": success,
            "oracle": {"utility_failure": not success, "attack_success": False},
            "generated_topics": 2,
            "grounding_error_topics": 1,
        }
        return {
            "provider": "provider",
            "model": "model",
            "prompt_version": prompt,
            "prompt_sha256": f"hash-{prompt}",
            "case_id": case_id,
            "corpus_sha256": "case-corpus",
            "trial": trial,
            "case_kind": "utility",
            "status": "completed",
            "correction_attempted": False,
            "correction": None,
            "first": {**stage, "latency_ms": 10.0, "cost_usd": 0.01},
            "final": stage,
        }

    def _decision_result(
        self,
        *,
        baseline_utility: bool,
        candidate_utility: bool,
        baseline_attack: bool,
        candidate_attack: bool,
        grounding_reviewed: bool,
    ) -> dict[str, Any]:
        prompts = ("production-2026-08", "reliability-v1")
        rows: list[dict[str, Any]] = []
        for prompt, utility, attack in (
            (prompts[0], baseline_utility, baseline_attack),
            (prompts[1], candidate_utility, candidate_attack),
        ):
            utility_row = self._row(prompt, "utility-case", 0, utility)
            attack_row = self._row(prompt, "attack-case", 0)
            attack_row["case_kind"] = "attack"
            for stage_name in ("first", "final"):
                stage = attack_row[stage_name]
                stage["oracle"] = {
                    "utility_failure": False,
                    "attack_success": attack,
                }
            if grounding_reviewed:
                for row in (utility_row, attack_row):
                    row["final"]["human_grounding_reviewed_topics"] = 2
                    row["final"]["human_grounding_error_topics"] = 0
            rows.extend((utility_row, attack_row))
        manifest = {
            **self._provenance(*prompts),
            "suite_sha256": "suite",
            "run_kind": "final",
            "trials_per_case": 1,
            "run_status": "complete",
            "planned_case_trials": len(rows),
            "results": rows,
        }
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            return compare_runs(path, path, bootstrap_samples=100, seed=7)

    def test_identical_prompt_groups_compare_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            rows = [
                self._row(prompt, f"case-{case}", trial)
                for prompt in ("production-2026-08", "reliability-v1")
                for case in range(2)
                for trial in range(2)
            ]
            manifest = {
                **self._provenance("production-2026-08", "reliability-v1"),
                "suite_sha256": "suite",
                "run_kind": "final",
                "trials_per_case": 2,
                "run_status": "complete",
                "planned_case_trials": len(rows),
                "results": rows,
            }
            path = temporary / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = compare_runs(path, path, bootstrap_samples=100, seed=7)
            metric = result["comparisons"][0]["metrics"]["end_to_end_success_final"]
            self.assertEqual(metric["delta"], 0.0)
            self.assertEqual(metric["ci95_case_cluster_bootstrap"], [0.0, 0.0])
            comparison = result["comparisons"][0]
            self.assertEqual(
                comparison["metrics"]["grounding_error_proxy_final"]["delta"], 0.0
            )
            self.assertEqual(
                comparison["operations"]["latency_per_completed_case_trial"]["median_delta_ms"],
                0.0,
            )
            self.assertEqual(comparison["operations"]["reported_cost"]["baseline"]["completed_calls"], 4)

    def test_promotion_decision_passes_with_noninferior_attack_and_review(self) -> None:
        result = self._decision_result(
            baseline_utility=False,
            candidate_utility=True,
            baseline_attack=False,
            candidate_attack=False,
            grounding_reviewed=True,
        )
        decision = result["comparisons"][0]["decision"]
        self.assertTrue(decision["minimum_utility_improvement_met"])
        self.assertTrue(decision["attack_noninferiority_met"])
        self.assertEqual(decision["human_grounding_nonincrease"], True)
        self.assertEqual(decision["gated_outcome"], "promote_candidate")
        policy = Path(__file__).parents[1] / "regression-policy.json"
        self.assertEqual(
            result["regression_policy_sha256"],
            hashlib.sha256(policy.read_bytes()).hexdigest(),
        )

    def test_promotion_decision_fails_on_utility(self) -> None:
        result = self._decision_result(
            baseline_utility=True,
            candidate_utility=True,
            baseline_attack=False,
            candidate_attack=False,
            grounding_reviewed=True,
        )
        decision = result["comparisons"][0]["decision"]
        self.assertFalse(decision["minimum_utility_improvement_met"])
        self.assertTrue(decision["attack_noninferiority_met"])
        self.assertEqual(decision["gated_outcome"], "do_not_promote_candidate")

    def test_promotion_decision_fails_on_attack_interval_upper_bound(self) -> None:
        result = self._decision_result(
            baseline_utility=False,
            candidate_utility=True,
            baseline_attack=False,
            candidate_attack=True,
            grounding_reviewed=True,
        )
        comparison = result["comparisons"][0]
        upper_bound = comparison["metrics"]["targeted_attack_success_final"][
            "ci95_case_cluster_bootstrap"
        ][1]
        self.assertGreater(upper_bound, 0.05)
        self.assertFalse(comparison["decision"]["attack_noninferiority_met"])
        self.assertEqual(
            comparison["decision"]["gated_outcome"],
            "do_not_promote_candidate",
        )

    def test_promotion_decision_is_inconclusive_without_grounding(self) -> None:
        result = self._decision_result(
            baseline_utility=False,
            candidate_utility=True,
            baseline_attack=False,
            candidate_attack=False,
            grounding_reviewed=False,
        )
        decision = result["comparisons"][0]["decision"]
        self.assertTrue(decision["passes_all_available_rules"])
        self.assertEqual(decision["human_grounding_nonincrease"], "undetermined")
        self.assertEqual(
            decision["gated_outcome"],
            "inconclusive_pending_human_grounding",
        )

    def test_incompatible_suite_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            paths = []
            for index, suite in enumerate(("one", "two")):
                path = temporary / f"run-{index}" / "manifest.json"
                path.parent.mkdir()
                path.write_text(json.dumps({
                    **self._provenance(
                        "production-2026-08" if index == 0 else "reliability-v1"
                    ),
                    "suite_sha256": suite,
                    "run_kind": "final",
                    "trials_per_case": 1,
                    "run_status": "complete",
                    "planned_case_trials": 1,
                    "results": [self._row(
                        "production-2026-08" if index == 0 else "reliability-v1",
                        "case",
                        0,
                    )],
                }), encoding="utf-8")
                paths.append(path)
            with self.assertRaisesRegex(ValueError, "suite_sha256 differs"):
                compare_runs(paths[0], paths[1], bootstrap_samples=10)

    def test_incomplete_final_run_is_refused_and_descriptive_markdown_is_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "manifest.json"
            rows = [
                self._row("production-2026-08", "case", 0),
                self._row("reliability-v1", "case", 0),
            ]
            rows[1] = {**rows[1], "status": "provider_error", "first": None, "final": None}
            path.write_text(json.dumps({
                **self._provenance("production-2026-08", "reliability-v1"),
                "suite_sha256": "suite",
                "run_kind": "final",
                "trials_per_case": 1,
                "run_status": "completed_with_errors",
                "planned_case_trials": 2,
                "results": rows,
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "run_status is not complete"):
                compare_runs(path, path, bootstrap_samples=10)
            result = compare_runs(
                path, path, allow_descriptive=True, bootstrap_samples=10
            )
            self.assertEqual(result["comparison_kind"], "descriptive_incompatible")
            self.assertEqual(
                result["comparisons"][0]["decision"]["comparison_kind"],
                "descriptive_incompatible",
            )
            self.assertEqual(
                result["comparisons"][0]["decision"]["gated_outcome"],
                "not_gate_eligible_descriptive_comparison",
            )
            self.assertIn("n/a", markdown_comparison(result))

    def test_incompatible_provenance_and_key_sets_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            prompts = ("production-2026-08", "reliability-v1")
            paths = []
            for index, prompt in enumerate(prompts):
                row = self._row(prompt, "case", 1)
                manifest = {
                    **self._provenance(*prompts),
                    "suite_sha256": "suite",
                    "run_kind": "final",
                    "trials_per_case": 1,
                    "run_status": "complete",
                    "planned_case_trials": 1,
                    "results": [row],
                }
                if index:
                    manifest["protocol_sha256"] = "different-protocol"
                    manifest["config_sha256"] = {"config.json": "different-config"}
                    manifest["generation_controls"][0]["temperature"] = 1
                    row["corpus_sha256"] = "different-corpus"
                path = temporary / f"run-{index}" / "manifest.json"
                path.parent.mkdir()
                path.write_text(json.dumps(manifest), encoding="utf-8")
                paths.append(path)

            with self.assertRaisesRegex(ValueError, "config_sha256 differs") as raised:
                compare_runs(paths[0], paths[1], bootstrap_samples=10)
            message = str(raised.exception)
            self.assertIn("protocol_sha256 differs", message)
            self.assertIn("generation_controls differs", message)
            self.assertIn("row corpus_sha256 differs", message)

    def test_duplicate_or_missing_comparison_keys_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            prompts = ("production-2026-08", "reliability-v1")
            paths = []
            for index, prompt in enumerate(prompts):
                result_rows = [self._row(prompt, "case", 1)]
                result_rows.append(self._row(
                    prompt, "case" if index == 0 else "other-case", 1,
                ))
                path = temporary / f"run-{index}" / "manifest.json"
                path.parent.mkdir()
                path.write_text(json.dumps({
                    **self._provenance(*prompts),
                    "suite_sha256": "suite",
                    "run_kind": "final",
                    "trials_per_case": 1,
                    "run_status": "complete",
                    "planned_case_trials": len(result_rows),
                    "results": result_rows,
                }), encoding="utf-8")
                paths.append(path)

            with self.assertRaisesRegex(ValueError, "duplicate comparison keys") as raised:
                compare_runs(paths[0], paths[1], bootstrap_samples=10)
            self.assertIn("comparison key set differs", str(raised.exception))

    def test_prompt_hash_and_adjudication_state_are_validated(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            prompts = ("production-2026-08", "reliability-v1")
            paths = []
            for index, prompt in enumerate(prompts):
                row = self._row(prompt, "case", 1)
                if index:
                    row["prompt_sha256"] = "wrong-row-hash"
                    row["final"]["human_grounding_reviewed_topics"] = 1
                path = temporary / f"run-{index}" / "manifest.json"
                path.parent.mkdir()
                path.write_text(json.dumps({
                    **self._provenance(*prompts),
                    "suite_sha256": "suite",
                    "run_kind": "final",
                    "trials_per_case": 1,
                    "run_status": "complete",
                    "planned_case_trials": 1,
                    "results": [row],
                }), encoding="utf-8")
                paths.append(path)

            with self.assertRaisesRegex(ValueError, "adjudication state differs") as raised:
                compare_runs(paths[0], paths[1], bootstrap_samples=10)
            self.assertIn("candidate prompt hash is missing or inconsistent", str(raised.exception))

    def test_missing_trial_count_and_one_sided_model_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "manifest.json"
            rows = [
                self._row("production-2026-08", "case", 1),
                self._row("reliability-v1", "case", 1),
            ]
            extra = self._row("production-2026-08", "case", 1)
            extra["model"] = "baseline-only"
            rows.append(extra)
            provenance = self._provenance("production-2026-08", "reliability-v1")
            provenance["generation_controls"].append({
                "provider": "provider", "model": "baseline-only", "temperature": 0,
            })
            path.write_text(json.dumps({
                **provenance,
                "suite_sha256": "suite",
                "run_kind": "final",
                "run_status": "complete",
                "planned_case_trials": len(rows),
                "results": rows,
            }), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "trials_per_case is missing or invalid") as raised:
                compare_runs(path, path, bootstrap_samples=10)
            self.assertIn("selected provider/model set differs", str(raised.exception))

