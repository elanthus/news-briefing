"""Evaluator comparison regression coverage."""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.comparison import (
    REGRESSION_POLICY,
    REGRESSION_POLICY_VERSION,
    _promotion_decision,
    compare_runs,
    markdown_comparison,
)
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

    def test_wilson_interval_boundaries_match_closed_form(self) -> None:
        # At 0/n and n/n the Wilson bounds reduce to z²/(n+z²) and n/(n+z²),
        # with z = 1.959963984540054 (two-sided 95%).
        for trials, outer in ((10, 0.2775327998628892), (30, 0.11351339317396875)):
            with self.subTest(trials=trials):
                low, high = wilson_interval(0, trials) or [math.nan, math.nan]
                self.assertEqual(low, 0.0)
                self.assertAlmostEqual(high, outer, places=12)
                low, high = wilson_interval(trials, trials) or [math.nan, math.nan]
                self.assertAlmostEqual(low, 1.0 - outer, places=12)
                self.assertAlmostEqual(high, 1.0, places=12)
        # Published Parity v3 cells: 0/30 → [0.0, 11.4] and 20/26 → [57.9, 89.0].
        self.assertEqual([round(value * 100, 1) for value in wilson_interval(0, 30) or []], [0.0, 11.4])
        self.assertEqual([round(value * 100, 1) for value in wilson_interval(20, 26) or []], [57.9, 89.0])

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

    def _multi_model_result(
        self,
        outcomes: dict[str, list[tuple[bool, bool]]],
        *,
        policy_path: Path = REGRESSION_POLICY,
        samples: int = 2_000,
    ) -> dict[str, Any]:
        """Compare per-model utility outcomes given as (baseline, candidate) per authored case."""
        prompts = ("production-2026-08", "reliability-v1")
        rows: list[dict[str, Any]] = []
        for model, cases in outcomes.items():
            for index, pair in enumerate(cases):
                for prompt, success in zip(prompts, pair, strict=True):
                    row = self._row(prompt, f"utility-{index}", 0, success)
                    row["model"] = model
                    row["final"]["human_grounding_reviewed_topics"] = 2
                    row["final"]["human_grounding_error_topics"] = 0
                    rows.append(row)
            # Attack non-inferiority needs an attack interval; no attack succeeds.
            for prompt in prompts:
                attack = self._row(prompt, "attack-case", 0)
                attack["model"] = model
                attack["case_kind"] = "attack"
                rows.append(attack)
        manifest = {
            **self._provenance(*prompts),
            "generation_controls": [
                {"provider": "provider", "model": model, "temperature": 0} for model in outcomes
            ],
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
            return compare_runs(path, path, bootstrap_samples=samples, seed=7, policy_path=policy_path)

    def test_utility_point_estimate_above_threshold_fails_when_interval_includes_zero(self) -> None:
        # Two of ten cases improve and one regresses: +10 pp, but resampling the
        # authored cases puts the lower bound at or below zero.
        cases = [(False, True), (False, True), (True, False)] + [(True, True)] * 7
        result = self._multi_model_result({"model": cases})
        decision = result["comparisons"][0]["decision"]
        utility = decision["rules"]["utility"]
        self.assertAlmostEqual(utility["point_estimate"], 0.1)
        self.assertTrue(utility["point_estimate_met"])
        self.assertLessEqual(utility["ci95_case_cluster_bootstrap"][0], 0.0)
        self.assertFalse(utility["lower_bound_met"])
        self.assertFalse(decision["minimum_utility_improvement_met"])
        self.assertEqual(decision["gated_outcome"], "do_not_promote_candidate")
        self.assertEqual(result["regression_policy_version"], REGRESSION_POLICY_VERSION)
        self.assertEqual(result["schema_version"], 2)

    def test_utility_rule_passes_when_interval_excludes_zero(self) -> None:
        result = self._multi_model_result({"model": [(False, True)] * 4})
        utility = result["comparisons"][0]["decision"]["rules"]["utility"]
        self.assertEqual(utility["ci95_case_cluster_bootstrap"], [1.0, 1.0])
        self.assertTrue(utility["met"])
        self.assertEqual(result["promotion"]["decision"], "promote_candidate")
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["decision"]["rules"]["attack"], {
            "rule": "interval_upper_bound",
            "metric": "targeted_attack_success_final",
            "point_estimate": 0.0,
            "ci95_case_cluster_bootstrap": comparison["metrics"]["targeted_attack_success_final"][
                "ci95_case_cluster_bootstrap"
            ],
            "upper_bound_must_be_below": 0.05,
            "met": True,
        })

    def _policy_variant(self, directory: str, **promotion_rule: Any) -> Path:
        policy = json.loads(REGRESSION_POLICY.read_text(encoding="utf-8"))
        policy["promotion_rule"].update(promotion_rule)
        path = Path(directory) / "policy.json"
        path.write_text(json.dumps(policy), encoding="utf-8")
        return path

    def test_utility_lower_bound_must_strictly_exceed_the_policy_floor(self) -> None:
        # One improving case in ten: +10 pp, and a third of resamples omit it, so
        # the lower bound is exactly zero. Equality with the floor is not a pass.
        result = self._multi_model_result({"model": [(False, True)] + [(True, True)] * 9})
        utility = result["comparisons"][0]["decision"]["rules"]["utility"]
        self.assertTrue(utility["point_estimate_met"])
        self.assertEqual(utility["ci95_case_cluster_bootstrap"][0], 0.0)
        self.assertEqual(utility["lower_bound_must_exceed"], 0.0)
        self.assertFalse(utility["lower_bound_met"])

        # The floor comes from the policy: a 100 pp floor fails an interval of [1, 1].
        with tempfile.TemporaryDirectory() as directory:
            policy = self._policy_variant(
                directory, utility_interval_lower_bound_must_exceed_percentage_points=100.0
            )
            raised = self._multi_model_result({"model": [(False, True)] * 4}, policy_path=policy)
        utility = raised["comparisons"][0]["decision"]["rules"]["utility"]
        self.assertEqual(utility["lower_bound_must_exceed"], 1.0)
        self.assertFalse(utility["lower_bound_met"])
        self.assertEqual(raised["promotion"]["decision"], "do_not_promote_candidate")

    def test_malformed_version_2_policies_are_refused(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"utility_rule": "point_estimate"}, "utility_rule"),
            ({"attack_rule": "point_estimate"}, "attack_rule"),
            ({"utility_interval_lower_bound_must_exceed_percentage_points": -1}, "non-negative"),
        ]
        for rule, message in cases:
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as directory:
                variant = self._policy_variant(directory, **rule)
                with self.assertRaisesRegex(ValueError, message):
                    self._multi_model_result({"model": [(False, True)] * 4}, policy_path=variant)
        for version in (1, 3, True, 2.0):
            with self.subTest(policy_version=version), tempfile.TemporaryDirectory() as directory:
                policy_path = Path(directory) / "policy.json"
                policy = json.loads(REGRESSION_POLICY.read_text(encoding="utf-8"))
                policy["policy_version"] = version
                policy_path.write_text(json.dumps(policy), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unsupported regression policy_version"):
                    self._multi_model_result({"model": [(False, True)] * 4}, policy_path=policy_path)

    def test_missing_policy_version_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.json"
            policy = json.loads(REGRESSION_POLICY.read_text(encoding="utf-8"))
            del policy["policy_version"]
            policy_path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "policy_version is required"):
                self._multi_model_result({"model": [(False, True)] * 4}, policy_path=policy_path)

    def test_result_records_the_policy_bytes_by_hash(self) -> None:
        result = self._multi_model_result({"model": [(False, True)] * 4})
        self.assertEqual(
            result["regression_policy_sha256"],
            hashlib.sha256(REGRESSION_POLICY.read_bytes()).hexdigest(),
        )
        self.assertEqual(result["regression_policy_version"], REGRESSION_POLICY_VERSION)

    def test_attack_scores_from_different_oracle_revisions_are_not_gated(self) -> None:
        prompts = ("production-2026-08", "reliability-v1")
        rows = []
        for prompt in prompts:
            for kind, case_id in (("utility", "utility-case"), ("attack", "attack-case")):
                row = self._row(prompt, case_id, 0)
                row["case_kind"] = kind
                rows.append(row)
        base = {
            **self._provenance(*prompts),
            "suite_sha256": "suite",
            "run_kind": "final",
            "trials_per_case": 1,
            "run_status": "complete",
            "planned_case_trials": len(rows),
            "results": rows,
        }
        with tempfile.TemporaryDirectory() as directory:
            old = Path(directory) / "old" / "manifest.json"
            new = Path(directory) / "new" / "manifest.json"
            old.parent.mkdir()
            new.parent.mkdir()
            old.write_text(json.dumps(base), encoding="utf-8")
            new.write_text(json.dumps({**base, "attack_oracle_revision": 2}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "attack_oracle_revision differs"):
                compare_runs(old, new, bootstrap_samples=100, seed=7)
            descriptive = compare_runs(old, new, bootstrap_samples=100, seed=7, allow_descriptive=True)
            self.assertIn("attack_oracle_revision differs", descriptive["compatibility_problems"])
            same = compare_runs(new, new, bootstrap_samples=100, seed=7)
            self.assertNotIn("attack_oracle_revision differs", same["compatibility_problems"])

    def test_promotion_requires_every_model_under_all_models(self) -> None:
        outcomes = {"passes": [(False, True)] * 4, "fails": [(True, True)] * 4}
        result = self._multi_model_result(outcomes)
        per_model = {row["model"]: row["decision"]["gated_outcome"] for row in result["comparisons"]}
        self.assertEqual(per_model, {"passes": "promote_candidate", "fails": "do_not_promote_candidate"})
        self.assertEqual(result["promotion"], {
            "requires": "all_models",
            "models_evaluated": 2,
            "models_promoting": 1,
            "decision": "do_not_promote_candidate",
        })
        self.assertIn(
            "Promotion decision (all_models, 1/2 models promoting): do_not_promote_candidate",
            markdown_comparison(result),
        )

        with tempfile.TemporaryDirectory() as temporary_dir:
            policy = json.loads(REGRESSION_POLICY.read_text(encoding="utf-8"))
            policy["promotion_rule"]["promotion_requires"] = "any_model"
            any_policy = Path(temporary_dir) / "policy.json"
            any_policy.write_text(json.dumps(policy), encoding="utf-8")
            any_result = self._multi_model_result(outcomes, policy_path=any_policy)
            self.assertEqual(any_result["promotion"]["requires"], "any_model")
            self.assertEqual(any_result["promotion"]["decision"], "promote_candidate")

            policy["promotion_rule"]["promotion_requires"] = "most_models"
            any_policy.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "promotion_requires"):
                self._multi_model_result(outcomes, policy_path=any_policy)

    def test_promotion_decision_combines_outcomes(self) -> None:
        promote, block, pending, descriptive = (
            "promote_candidate",
            "do_not_promote_candidate",
            "inconclusive_pending_human_grounding",
            "not_gate_eligible_descriptive_comparison",
        )
        cases = [
            ([promote, promote], promote, promote),
            ([promote, block], block, promote),
            ([promote, pending], pending, promote),
            ([block, pending], block, pending),
            ([block, block], block, block),
            ([descriptive, promote], descriptive, descriptive),
            ([], descriptive, descriptive),
        ]
        for outcomes, all_models, any_model in cases:
            with self.subTest(outcomes=outcomes):
                self.assertEqual(_promotion_decision(outcomes, "all_models"), all_models)
                self.assertEqual(_promotion_decision(outcomes, "any_model"), any_model)

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


ROOT = Path(__file__).resolve().parents[2]
EVIDENCE = ROOT / ".news-briefing" / "evidence"
COMMITTED_COMPARISON = ROOT / "docs" / "results" / "parity-v2-comparison-production-runner-policy-v2.json"


class CommittedComparisonTest(unittest.TestCase):
    """Regenerate a published comparison so bootstrap draw order cannot drift.

    The fixture was produced under the current policy, so it must record that
    policy's bytes by hash; a policy change means regenerating the fixture.
    """

    def test_committed_parity_v2_comparison_reproduces(self) -> None:
        if not all(
            (EVIDENCE / bundle / "manifest.json").is_file()
            for bundle in ("parity-v1-evidence", "parity-v2-evidence")
        ):
            message = "fetch the release bundles first: python3 -S -m evaluator.evidence_assets fetch"
            # CI sets REQUIRE_EVIDENCE=1 after fetching, so a missing bundle fails there.
            if os.environ.get("REQUIRE_EVIDENCE") == "1":
                self.fail(message)
            self.skipTest(message)
        committed = json.loads(COMMITTED_COMPARISON.read_text(encoding="utf-8"))
        self.assertEqual(committed["schema_version"], 2)
        self.assertEqual(
            committed["regression_policy_sha256"],
            hashlib.sha256(REGRESSION_POLICY.read_bytes()).hexdigest(),
            "the committed comparison records a policy other than the current one",
        )
        row = committed["comparisons"][0]
        result = compare_runs(
            EVIDENCE / "parity-v1-evidence" / "manifest.json",
            EVIDENCE / "parity-v2-evidence" / "manifest.json",
            baseline_prompt=row["baseline_prompt"],
            candidate_prompt=row["candidate_prompt"],
            allow_descriptive=True,
            bootstrap_samples=committed["bootstrap"]["samples"],
            seed=committed["bootstrap"]["seed"],
        )
        # The published file records repository-relative manifest paths.
        result["baseline_manifest"] = committed["baseline_manifest"]
        result["candidate_manifest"] = committed["candidate_manifest"]
        self.assertEqual(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            COMMITTED_COMPARISON.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            markdown_comparison(result),
            COMMITTED_COMPARISON.with_suffix(".md").read_text(encoding="utf-8"),
        )
