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
    MANIFEST_SCHEMA_VERSION,
    REGRESSION_POLICY,
    REGRESSION_POLICY_VERSION,
    _promotion_decision,
    compare_runs,
    markdown_comparison,
)
from evaluator.metrics import percentile, rate, wilson_interval
from evaluator.plan import ATTACK_ORACLE_REVISION


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
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "attack_oracle_revision": ATTACK_ORACLE_REVISION,
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
            "corpus_sha256": "a" * 64,
            "trial_corpus_sha256": "b" * 64,
            "trial": trial,
            "case_kind": "utility",
            "status": "completed",
            "correction_attempted": False,
            "correction": None,
            "first": {**stage, "latency_ms": 10.0, "cost_usd": 0.01},
            "final": stage,
        }

    def _write_comparison(self, path: Path, rows: list[dict[str, Any]]) -> None:
        manifest = {
            **self._provenance("production-2026-08", "reliability-v1"),
            "suite_sha256": "suite",
            "run_kind": "final",
            "trials_per_case": 1,
            "run_status": "complete",
            "planned_case_trials": len(rows),
            "results": rows,
        }
        path.write_text(json.dumps(manifest), encoding="utf-8")

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
        with tempfile.TemporaryDirectory() as temporary_dir:
            path = Path(temporary_dir) / "manifest.json"
            self._write_comparison(path, rows)
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

    def test_gating_requires_valid_matching_fixture_and_effective_corpus_digests(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            for field in ("corpus_sha256", "trial_corpus_sha256"):
                for left_hash, right_hash, reason in (
                    ("a" * 64, "b" * 64, "differs"),
                    ("a" * 64, None, "is missing or invalid"),
                    (None, "b" * 64, "is missing or invalid"),
                    (None, None, "is missing or invalid"),
                    ("g" * 64, "g" * 64, "is missing or invalid"),
                ):
                    with self.subTest(field=field, hashes=(left_hash, right_hash)):
                        left = self._row("production-2026-08", "case", 0)
                        right = self._row("reliability-v1", "case", 0)
                        for row, digest in ((left, left_hash), (right, right_hash)):
                            if digest is None:
                                del row[field]
                            else:
                                row[field] = digest
                        self._write_comparison(path, [left, right])
                        problem = f"row {field} {reason} for provider/model/case/0"
                        with self.assertRaisesRegex(ValueError, problem):
                            compare_runs(path, path)
                        result = compare_runs(path, path, allow_descriptive=True, bootstrap_samples=10)
                        self.assertIn(problem, result["compatibility_problems"])
                        self.assertEqual(result["promotion"]["decision"], "not_gate_eligible_descriptive_comparison")

    def test_contract_net_loss_blocks_promotion_across_all_attack_variants(self) -> None:
        rows = []
        for prompt in ("production-2026-08", "reliability-v1"):
            candidate = prompt == "reliability-v1"
            utility = self._row(prompt, "utility", 0, success=candidate)
            utility["final"].update(human_grounding_reviewed_topics=2, human_grounding_error_topics=0)
            rows.append(utility)
            for case in ("primary", "clean", "ablation"):
                row = self._row(prompt, case, 0, success=not candidate)
                row.update(case_kind="attack", is_clean_pair=case == "clean",
                           corpus_position="early" if case == "ablation" else None)
                rows.append(row)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            self._write_comparison(path, rows)
            result = compare_runs(path, path, bootstrap_samples=10)
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["deterministic_contract_regressions"], 3)
        self.assertTrue(comparison["decision"]["minimum_utility_improvement_met"])
        self.assertTrue(comparison["decision"]["attack_noninferiority_met"])
        contract = comparison["decision"]["rules"]["contract"]
        self.assertEqual(contract["metric"], "contract_success_final_all_pairs")
        self.assertEqual(contract["point_estimate"], -0.5)
        self.assertFalse(contract["met"])
        self.assertFalse(comparison["decision"]["contract_noninferiority_met"])
        self.assertEqual(result["promotion"]["decision"], "do_not_promote_candidate")

    def test_cost_counts_provider_calls_within_attempts_and_partial_corrections(self) -> None:
        left = self._row("production-2026-08", "case", 0)
        right = self._row("reliability-v1", "case", 0)
        left["first"].update(cost_usd=0.75, usage={"production_parity_calls": [
            {"stage": "selection", "cost_usd": 0.25}, {"stage": "prose", "cost_usd": 0.50},
        ]})
        left["correction_attempted"] = True
        left["correction"] = {"cost_usd": 0.25, "latency_ms": 1, "usage": {"production_parity_calls": [
            {"stage": "selection", "cost_usd": 0}, {"stage": "prose", "cost_usd": 0.25},
        ]}}
        right.update(status="completed_with_correction_error", correction_attempted=True, correction_error={
            "cost_usd": 0.30, "completed_stage_calls": [{"stage": "selection", "cost_usd": None}],
        })
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            self._write_comparison(path, [left, right])
            result = compare_runs(path, path, allow_descriptive=True, bootstrap_samples=10)
        cost = result["comparisons"][0]["operations"]["reported_cost"]
        self.assertEqual(cost["paired_case_trials"], 1)
        self.assertEqual(cost["baseline"], {
            "completed_calls": 4, "known_cost_calls": 4, "total_usd": 1.0, "per_completed_call_usd": 0.25,
        })
        self.assertEqual(cost["candidate"], {
            "completed_calls": 2, "known_cost_calls": 1, "total_usd": None, "per_completed_call_usd": None,
        })

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
        self.assertEqual(result["schema_version"], 4)

    def test_contract_rule_tolerates_a_regression_outweighed_by_fixes(self) -> None:
        # Ten cases are fixed and one breaks. The net contract change over all
        # pairs is clearly positive, so the one regression is counted, not gated.
        cases = [(False, True)] * 10 + [(True, False)] + [(True, True)] * 9
        result = self._multi_model_result({"model": cases})
        comparison = result["comparisons"][0]
        contract = comparison["decision"]["rules"]["contract"]
        self.assertEqual(comparison["deterministic_contract_regressions"], 1)
        self.assertEqual(comparison["metrics"]["contract_success_final_all_pairs"]["pairs"], 21)
        self.assertAlmostEqual(contract["point_estimate"], 9 / 21)
        self.assertEqual(contract["lower_bound_must_exceed"], -0.03)
        self.assertGreater(contract["ci95_case_cluster_bootstrap"][0], 0.0)
        self.assertTrue(comparison["decision"]["contract_noninferiority_met"])
        self.assertEqual(result["promotion"]["decision"], "promote_candidate")

    def test_contract_lower_bound_must_strictly_exceed_the_negative_margin(self) -> None:
        # No pair changes, so the contract interval is exactly [0, 0]. A zero
        # margin makes the bound equal to the limit, which is not a pass.
        with tempfile.TemporaryDirectory() as directory:
            policy = self._policy_variant(directory, contract_noninferiority_margin_percentage_points=0)
            result = self._multi_model_result({"model": [(True, True)] * 4}, policy_path=policy)
        decision = result["comparisons"][0]["decision"]
        self.assertEqual(decision["rules"]["contract"]["ci95_case_cluster_bootstrap"], [0.0, 0.0])
        self.assertFalse(decision["contract_noninferiority_met"])
        default = self._multi_model_result({"model": [(True, True)] * 4})
        self.assertTrue(default["comparisons"][0]["decision"]["contract_noninferiority_met"])

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

    def test_malformed_current_policies_are_refused(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"utility_rule": "point_estimate"}, "utility_rule"),
            ({"attack_rule": "point_estimate"}, "attack_rule"),
            ({"contract_rule": "zero_regressions"}, "contract_rule"),
            ({"ablation_attack_rule": "interval_upper_bound"}, "ablation_attack_rule"),
            ({"contract_noninferiority_margin_percentage_points": -1}, "contract_noninferiority_margin"),
            ({"utility_interval_lower_bound_must_exceed_percentage_points": -1}, "non-negative"),
        ]
        # json.dumps writes NaN and Infinity, and json.loads reads them back.
        cases += [
            ({key: value}, f"{key} must be finite and non-negative")
            for key in (
                "minimum_end_to_end_utility_improvement_percentage_points",
                "utility_interval_lower_bound_must_exceed_percentage_points",
                "attack_noninferiority_margin_percentage_points",
                "ablation_attack_noninferiority_margin_percentage_points",
                "contract_noninferiority_margin_percentage_points",
            )
            for value in (float("nan"), float("inf"), float("-inf"))
        ]
        for rule, message in cases:
            with self.subTest(rule=rule), tempfile.TemporaryDirectory() as directory:
                variant = self._policy_variant(directory, **rule)
                with self.assertRaisesRegex(ValueError, message):
                    self._multi_model_result({"model": [(False, True)] * 4}, policy_path=variant)
        for version in (1, 2, 3, 5, True, 4.0):
            with self.subTest(policy_version=version), tempfile.TemporaryDirectory() as directory:
                policy_path = Path(directory) / "policy.json"
                policy = json.loads(REGRESSION_POLICY.read_text(encoding="utf-8"))
                policy["policy_version"] = version
                policy_path.write_text(json.dumps(policy), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "unsupported regression policy_version"):
                    self._multi_model_result({"model": [(False, True)] * 4}, policy_path=policy_path)

    def test_current_policy_requires_the_current_file_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            policy = json.loads(REGRESSION_POLICY.read_bytes())
            policy["schema_version"] = 2
            path.write_text(json.dumps(policy), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "unsupported regression policy schema_version"):
                self._multi_model_result({"model": [(False, True)]}, policy_path=path)

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

    def _ablation_result(
        self, outcomes: dict[str, tuple[bool, bool]], *, twins: bool = True, ablations: bool = True,
    ) -> dict[str, Any]:
        """Compare one ablation case; ``outcomes`` maps prompt to (attacked fires, twin fires)."""
        rows: list[dict[str, Any]] = []
        for prompt, (attacked, clean) in outcomes.items():
            rows.append(self._row(prompt, "utility-case", 0))
            primary = self._row(prompt, "attack-prose", 0)
            primary["case_kind"] = "attack"
            primary["first"]["oracle"] = primary["final"]["oracle"] = {
                "utility_failure": False, "attack_success": False,
            }
            rows.append(primary)
            if not ablations:
                continue
            for case_id, fired, is_clean in (
                ("attack-selection-suppression-early-single", attacked, False),
                ("attack-selection-suppression-early-single__clean", clean, True),
            ):
                if is_clean and not twins:
                    continue
                row = self._row(prompt, case_id, 0)
                row.update(case_kind="attack", corpus_position="early", is_clean_pair=is_clean)
                for stage in ("first", "final"):
                    row[stage]["oracle"] = {"utility_failure": False, "attack_success": fired}
                    row[stage]["human_grounding_reviewed_topics"] = 2
                    row[stage]["human_grounding_error_topics"] = 0
                rows.append(row)
        for row in rows:
            row["final"]["human_grounding_reviewed_topics"] = 2
            row["final"]["human_grounding_error_topics"] = 0
            # Utility improves so only the ablation rule can block promotion.
            if row["case_kind"] == "utility" and row["prompt_version"] == "production-2026-08":
                row["final"]["oracle"]["utility_failure"] = True
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            self._write_comparison(path, rows)
            return compare_runs(path, path, bootstrap_samples=100, seed=7)

    def test_ablation_rule_compares_the_paired_injection_effect(self) -> None:
        # The candidate fires on the attacked trial and on its clean twin: ordinary
        # omission, no injection effect. The raw attacked rate would rise 100 pp.
        result = self._ablation_result({"production-2026-08": (False, False), "reliability-v1": (True, True)})
        comparison = result["comparisons"][0]
        rule = comparison["decision"]["rules"]["ablation_attack"]
        self.assertEqual(rule["rule"], "paired_injection_effect_interval_upper_bound")
        self.assertEqual(rule["point_estimate"], 0.0)
        self.assertIs(rule["met"], True)
        self.assertEqual(comparison["metrics"]["ablation_injection_effect_final"]["pairs"], 1)
        self.assertEqual(comparison["metrics"]["targeted_attack_success_final"]["pairs"], 1)
        self.assertEqual(comparison["decision"]["gated_outcome"], "promote_candidate")
        # The candidate fires only under attack: an injection effect of +1 blocks promotion.
        result = self._ablation_result({"production-2026-08": (False, False), "reliability-v1": (True, False)})
        decision = result["comparisons"][0]["decision"]
        self.assertEqual(decision["rules"]["ablation_attack"]["point_estimate"], 1.0)
        self.assertIs(decision["ablation_attack_noninferiority_met"], False)
        self.assertEqual(decision["gated_outcome"], "do_not_promote_candidate")
        self.assertIn("Final ablation injection-effect delta", markdown_comparison(result))

    def test_ablation_rule_fails_closed_without_clean_twins_and_is_inapplicable_without_ablations(self) -> None:
        result = self._ablation_result(
            {"production-2026-08": (False, False), "reliability-v1": (False, False)}, twins=False,
        )
        comparison = result["comparisons"][0]
        self.assertEqual(comparison["unmatched"]["ablation_injection_effect"],
                         {"complete_pairs": 0, "incomplete_units": 1})
        self.assertIs(comparison["decision"]["ablation_attack_noninferiority_met"], False)
        self.assertEqual(comparison["decision"]["gated_outcome"], "do_not_promote_candidate")
        result = self._ablation_result(
            {"production-2026-08": (False, False), "reliability-v1": (False, False)}, ablations=False,
        )
        decision = result["comparisons"][0]["decision"]
        self.assertEqual(decision["ablation_attack_noninferiority_met"], "not_applicable")
        self.assertEqual(decision["gated_outcome"], "promote_candidate")

    def test_absent_recorded_outcomes_refuse_the_comparison(self) -> None:
        prompts = ("production-2026-08", "reliability-v1")
        for key, kind in (("attack_success", "attack"), ("utility_failure", "utility"),
                          ("contract_success", "utility")):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as directory:
                rows = [self._row(prompt, f"{kind}-case", 0) for prompt in prompts]
                for row in rows:
                    row["case_kind"] = kind
                holder = rows[1]["final"] if key == "contract_success" else rows[1]["final"]["oracle"]
                del holder[key]
                path = Path(directory) / "manifest.json"
                self._write_comparison(path, rows)
                with self.assertRaisesRegex(ValueError, f"has no boolean {key}"):
                    compare_runs(path, path, bootstrap_samples=10, seed=7, allow_descriptive=True)

    def test_other_manifest_schema_versions_are_refused(self) -> None:
        rows = [self._row(prompt, "utility-case", 0) for prompt in ("production-2026-08", "reliability-v1")]
        for version in (8, 10, None, "9", True):
            with self.subTest(schema_version=version), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "manifest.json"
                self._write_comparison(path, rows)
                manifest = json.loads(path.read_text(encoding="utf-8"))
                manifest["schema_version"] = version
                path.write_text(json.dumps(manifest), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "comparison manifest schema_version must be 9"):
                    compare_runs(path, path, bootstrap_samples=10, seed=7, allow_descriptive=True)

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
            old.write_text(json.dumps({**base, "attack_oracle_revision": 1}), encoding="utf-8")
            new.write_text(json.dumps(base), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "attack_oracle_revision differs"):
                compare_runs(old, new, bootstrap_samples=100, seed=7)
            descriptive = compare_runs(old, new, bootstrap_samples=100, seed=7, allow_descriptive=True)
            self.assertIn("attack_oracle_revision differs", descriptive["compatibility_problems"])
            same = compare_runs(new, new, bootstrap_samples=100, seed=7)
            self.assertNotIn("attack_oracle_revision differs", same["compatibility_problems"])
            # A manifest that does not declare its oracle revision cannot be gated:
            # absence is not read as revision 1.
            undeclared = {key: value for key, value in base.items() if key != "attack_oracle_revision"}
            old.write_text(json.dumps(undeclared), encoding="utf-8")
            for left, right in ((old, new), (old, old)):
                with self.assertRaisesRegex(ValueError, "attack_oracle_revision is missing or invalid"):
                    compare_runs(left, right, bootstrap_samples=100, seed=7)

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
                    row["corpus_sha256"] = "c" * 64
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
COMMITTED_COMPARISON = ROOT / "docs" / "results" / "parity-v4-comparison-prompts-policy-v4.json"


class CommittedComparisonTest(unittest.TestCase):
    """Regenerate a published comparison so bootstrap draw order cannot drift.

    The fixture was produced under the current policy, so it must record that
    policy's bytes by hash; a policy change means regenerating the fixture.
    """

    def test_committed_parity_v4_comparison_reproduces(self) -> None:
        manifest = EVIDENCE / "parity-v4-evidence" / "manifest.json"
        if not manifest.is_file():
            message = "fetch the release bundles first: python3 -S -m evaluator.evidence_assets fetch"
            # CI sets REQUIRE_EVIDENCE=1 after fetching, so a missing bundle fails there.
            if os.environ.get("REQUIRE_EVIDENCE") == "1":
                self.fail(message)
            self.skipTest(message)
        committed = json.loads(COMMITTED_COMPARISON.read_text(encoding="utf-8"))
        self.assertEqual(committed["schema_version"], 4)
        self.assertEqual(
            committed["regression_policy_sha256"],
            hashlib.sha256(REGRESSION_POLICY.read_bytes()).hexdigest(),
            "the committed comparison records a policy other than the current one",
        )
        row = committed["comparisons"][0]
        result = compare_runs(
            manifest, manifest,
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
