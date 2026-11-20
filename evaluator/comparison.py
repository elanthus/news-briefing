"""Compatible, paired comparisons for frozen evaluator runs."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_runner.hashing import is_sha256

from evaluator.metrics import percentile
from evaluator.report import _operation_call_records

Metric = Callable[[dict[str, Any]], float]
REGRESSION_POLICY = Path(__file__).with_name("regression-policy.json")
# The one readable policy version. A comparison records the policy file's
# SHA-256; a comparison made under an earlier policy is reproduced from the
# repository commit that recorded it, not by this code.
REGRESSION_POLICY_VERSION = 4
REGRESSION_POLICY_SCHEMA_VERSION = 4
# The comparison output's own format. Version 4 adds the ablation injection-effect
# metric and rule beside the version 3 provider-call cost and contract rule.
COMPARISON_SCHEMA_VERSION = 4
# The one evaluator manifest format the comparator reads; plan._identity writes it.
MANIFEST_SCHEMA_VERSION = 9
PROMOTION_SCOPES = ("all_models", "any_model")
# Prompt versions preferred when a manifest holds several and none is named:
# the historical production baseline and the reliability candidate it was
# compared against.
DEFAULT_BASELINE_PROMPT = "production-2026-08"
DEFAULT_CANDIDATE_PROMPT = "reliability-v1"


def _regression_policy(path: Path) -> tuple[dict[str, Any], str]:
    payload = path.read_bytes()
    policy = json.loads(payload)
    if not isinstance(policy, dict) or not isinstance(
        policy.get("promotion_rule"), dict
    ):
        raise ValueError(f"regression policy is invalid: {path}")
    return policy, hashlib.sha256(payload).hexdigest()


def _check_policy_version(policy: dict[str, Any]) -> None:
    if "policy_version" not in policy:
        raise ValueError("regression policy_version is required")
    version = policy["policy_version"]
    if type(version) is not int or version != REGRESSION_POLICY_VERSION:
        raise ValueError(f"unsupported regression policy_version: {version!r}")
    schema = policy.get("schema_version")
    if type(schema) is not int or schema != REGRESSION_POLICY_SCHEMA_VERSION:
        raise ValueError(f"unsupported regression policy schema_version: {schema!r}")


def _promotion_scope(rule: dict[str, Any]) -> str:
    scope = rule.get("promotion_requires")
    if scope not in PROMOTION_SCOPES:
        raise ValueError(
            "regression policy promotion_rule.promotion_requires must be one of "
            + ", ".join(PROMOTION_SCOPES)
        )
    return str(scope)


def _promotion_decision(outcomes: list[str], scope: str) -> str:
    """Combine per-model gated outcomes into one candidate decision.

    ``all_models`` promotes only when every evaluated model would promote, so
    one failing model blocks the candidate. ``any_model`` promotes when at least
    one model would. A descriptive comparison is never gate-eligible.
    """
    if not outcomes or "not_gate_eligible_descriptive_comparison" in outcomes:
        return "not_gate_eligible_descriptive_comparison"
    if scope == "all_models":
        if "do_not_promote_candidate" in outcomes:
            return "do_not_promote_candidate"
        if "inconclusive_pending_human_grounding" in outcomes:
            return "inconclusive_pending_human_grounding"
        return "promote_candidate"
    if "promote_candidate" in outcomes:
        return "promote_candidate"
    if "inconclusive_pending_human_grounding" in outcomes:
        return "inconclusive_pending_human_grounding"
    return "do_not_promote_candidate"


def _interval_bound(interval: Any, index: int) -> float | None:
    if (
        isinstance(interval, list)
        and len(interval) == 2
        and isinstance(interval[index], (int, float))
        and not isinstance(interval[index], bool)
    ):
        return float(interval[index])
    return None


def _percentage_point_threshold(rule: dict[str, Any], key: str) -> float:
    value = rule.get(key)
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        # json.loads accepts NaN and Infinity. NaN fails every comparison and an
        # infinite margin passes every one, so neither is a usable threshold.
        or not math.isfinite(value)
        or value < 0
    ):
        raise ValueError(f"regression policy promotion_rule.{key} must be finite and non-negative")
    return float(value) / 100.0


def _load_manifest(path: Path) -> tuple[Path, dict[str, Any]]:
    candidate = path if path.name == "manifest.json" else path.parent / "manifest.json"
    if not candidate.is_file():
        raise ValueError(f"no sibling manifest.json for comparison input: {path}")
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise ValueError(f"comparison manifest is invalid: {candidate}")
    schema = payload.get("schema_version")
    if type(schema) is not int or schema != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"comparison manifest schema_version must be {MANIFEST_SCHEMA_VERSION}, got {schema!r}: "
            f"{candidate}; compare an older run at the commit that wrote it"
        )
    return candidate, payload


def _cluster(case_id: str) -> str:
    return case_id.removesuffix("__clean")


def _complete(row: dict[str, Any]) -> bool:
    return isinstance(row.get("first"), dict) and isinstance(row.get("final"), dict)


def _scored_flag(row: dict[str, Any], stage: str, key: str) -> bool:
    """Read a recorded boolean outcome; an absent or non-boolean value refuses the comparison.

    Reading a missing outcome as False would score an unscored attack as resisted
    and an unscored utility row as passing.
    """
    result = row[stage]
    holder = result if key == "contract_success" else result.get("oracle")
    value = holder.get(key) if isinstance(holder, dict) else None
    if type(value) is not bool:
        raise ValueError(
            f"row {row.get('case_id')!r} trial {row.get('trial')!r} {stage} has no boolean {key}"
        )
    return value


def _application(row: dict[str, Any], stage: str) -> float:
    return float(
        _scored_flag(row, stage, "contract_success")
        and not _scored_flag(row, stage, "utility_failure")
    )


def _attack(row: dict[str, Any], stage: str) -> float:
    return float(_scored_flag(row, stage, "attack_success"))


def _contract(row: dict[str, Any], stage: str) -> float:
    return float(_scored_flag(row, stage, "contract_success"))


def _primary_attack(row: dict[str, Any]) -> bool:
    return (
        row.get("case_kind") == "attack"
        and not row.get("is_clean_pair", False)
        and row.get("corpus_position") is None
    )


def _correction_success(row: dict[str, Any]) -> float:
    return float(isinstance(row.get("correction"), dict) and _scored_flag(row, "final", "contract_success"))


def _generated_topics(row: dict[str, Any]) -> int:
    return int(row["final"].get("generated_topics", 0))


def _grounding_proxy_errors(row: dict[str, Any]) -> int:
    return int(row["final"].get("grounding_error_topics", 0))


def _attempts(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        call for call in (row.get("first"), row.get("correction"))
        if isinstance(call, dict)
    ]


def _row_latency_ms(row: dict[str, Any]) -> float:
    return sum(float(call["latency_ms"]) for call in _attempts(row))


def _paired_rows(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, int]]:
    left = {(row["case_id"], row["trial"]): row for row in baseline if predicate(row) and _complete(row)}
    right = {(row["case_id"], row["trial"]): row for row in candidate if predicate(row) and _complete(row)}
    shared = sorted(left.keys() & right.keys())
    return (
        [(left[key], right[key]) for key in shared],
        {
            "baseline_only": len(left.keys() - right.keys()),
            "candidate_only": len(right.keys() - left.keys()),
            "complete_pairs": len(shared),
        },
    )


def _ablation_attack(row: dict[str, Any]) -> bool:
    return (
        row.get("case_kind") == "attack"
        and not row.get("is_clean_pair", False)
        and row.get("corpus_position") is not None
    )


def _injection_effect_pairs(
    baseline: list[dict[str, Any]], candidate: list[dict[str, Any]]
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], dict[str, int]]:
    """Pair each ablation trial's injection effect across the two prompt groups.

    One side's effect for an ablation case and trial is its final attack oracle
    on the attacked row minus the same oracle on that trial's clean twin: -1, 0,
    or +1. A unit counts only when the attacked row and its twin are complete on
    both sides; an ablation without a twin can never pair.
    """
    def by_key(rows: list[dict[str, Any]]) -> dict[tuple[Any, Any], dict[str, Any]]:
        return {(row["case_id"], row["trial"]): row for row in rows if _complete(row)}

    left, right = by_key(baseline), by_key(candidate)
    units = sorted({
        (row["case_id"], row["trial"]) for row in [*baseline, *candidate] if _ablation_attack(row)
    })
    pairs = []
    for case_id, trial in units:
        keys = ((case_id, trial), (f"{case_id}__clean", trial))
        if not all(key in side for side in (left, right) for key in keys):
            continue
        baseline_effect, candidate_effect = (
            {"case_id": case_id, "trial": trial,
             "injection_effect": _attack(side[keys[0]], "final") - _attack(side[keys[1]], "final")}
            for side in (left, right)
        )
        pairs.append((baseline_effect, candidate_effect))
    return pairs, {"complete_pairs": len(pairs), "incomplete_units": len(units) - len(pairs)}


def _cluster_bootstrap(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    metric: Callable[[dict[str, Any]], Any],
    statistic: Callable[[list[Any]], float],
    *,
    samples: int,
    seed: int,
) -> tuple[list[tuple[Any, Any]], int, list[float]]:
    """Resample authored case clusters; return paired values, cluster count, and deltas.

    Each delta is the candidate statistic minus the baseline statistic over one
    resample. The draw order is fixed so committed comparisons reproduce.
    """
    by_cluster: dict[str, list[tuple[Any, Any]]] = defaultdict(list)
    values = []
    for left, right in pairs:
        pair = (metric(left), metric(right))
        by_cluster[_cluster(left["case_id"])].append(pair)
        values.append(pair)
    clusters = sorted(by_cluster)
    deltas = []
    if clusters:
        rng = random.Random(seed)
        for _ in range(samples):
            drawn = [rng.choice(clusters) for _ in clusters]
            selected = [pair for cluster in drawn for pair in by_cluster[cluster]]
            deltas.append(
                statistic([value for _, value in selected])
                - statistic([value for value, _ in selected])
            )
    return values, len(clusters), deltas


def _topic_ratio(values: list[tuple[float, float]]) -> float:
    return sum(numerator for numerator, _ in values) / sum(
        denominator for _, denominator in values
    )


def _paired_rate(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    metric: Callable[[dict[str, Any]], Any],
    statistic: Callable[[list[Any]], float] = statistics.fmean,
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    values, clusters, deltas = _cluster_bootstrap(
        pairs, metric, statistic, samples=samples, seed=seed
    )
    if not clusters:
        return {
            "baseline_rate": None,
            "candidate_rate": None,
            "delta": None,
            "ci95_case_cluster_bootstrap": None,
            "pairs": 0,
            "case_clusters": 0,
        }
    baseline_rate = statistic([value for value, _ in values])
    candidate_rate = statistic([value for _, value in values])
    return {
        "baseline_rate": baseline_rate,
        "candidate_rate": candidate_rate,
        "delta": candidate_rate - baseline_rate,
        "ci95_case_cluster_bootstrap": [percentile(deltas, 0.025), percentile(deltas, 0.975)],
        "pairs": len(pairs),
        "case_clusters": clusters,
    }


def _paired_median_ms(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    metric: Metric,
    *,
    samples: int,
    seed: int,
) -> dict[str, Any]:
    values, clusters, deltas = _cluster_bootstrap(
        pairs, metric, statistics.median, samples=samples, seed=seed
    )
    if not clusters:
        return {
            "baseline_median_ms": None,
            "candidate_median_ms": None,
            "median_delta_ms": None,
            "ci95_median_delta_case_cluster_bootstrap_ms": None,
            "baseline_p95_ms": None,
            "candidate_p95_ms": None,
            "pairs": 0,
            "case_clusters": 0,
        }
    baseline_values = [value for value, _ in values]
    candidate_values = [value for _, value in values]
    baseline_median = statistics.median(baseline_values)
    candidate_median = statistics.median(candidate_values)
    return {
        "baseline_median_ms": baseline_median,
        "candidate_median_ms": candidate_median,
        "median_delta_ms": candidate_median - baseline_median,
        "ci95_median_delta_case_cluster_bootstrap_ms": [
            percentile(deltas, 0.025), percentile(deltas, 0.975)
        ],
        "baseline_p95_ms": percentile(baseline_values, 0.95),
        "candidate_p95_ms": percentile(candidate_values, 0.95),
        "pairs": len(pairs),
        "case_clusters": clusters,
    }


def _cost_summary(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]]
) -> dict[str, Any]:
    def side(rows: list[dict[str, Any]]) -> dict[str, Any]:
        calls = [call for row in rows for call in _operation_call_records(row, include_failed=False)]
        known = [float(call["cost_usd"]) for call in calls if call.get("cost_usd") is not None]
        # Python 3.11 and newer sum floats differently. Frozen evidence must
        # reproduce the same billed total on every supported interpreter.
        total = math.fsum(known) if len(known) == len(calls) else None
        return {
            "completed_calls": len(calls),
            "known_cost_calls": len(known),
            "total_usd": total,
            "per_completed_call_usd": total / len(calls) if total is not None and calls else None,
        }

    return {
        "baseline": side([left for left, _ in pairs]),
        "candidate": side([right for _, right in pairs]),
        "paired_case_trials": len(pairs),
    }


def _required_provenance(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    problems = []
    for field in ("suite_sha256", "corpus_sha256", "config_sha256", "protocol_sha256"):
        values = (left.get(field), right.get(field))
        valid = (
            all(isinstance(value, str) and bool(value) for value in values)
            if field != "config_sha256"
            else all(
                isinstance(value, dict)
                and bool(value)
                and all(
                    isinstance(path, str) and path
                    and isinstance(digest, str) and digest
                    for path, digest in value.items()
                )
                for value in values
            )
        )
        if not valid:
            problems.append(f"{field} is missing or invalid")
        elif left[field] != right[field]:
            problems.append(f"{field} differs")
    execution_orders = (left.get("execution_order"), right.get("execution_order"))
    if not all(isinstance(value, str) and value for value in execution_orders):
        problems.append("execution_order is missing or invalid")
    elif execution_orders[0] != execution_orders[1]:
        problems.append("execution_order differs")
    execution_seeds = (left.get("execution_seed"), right.get("execution_seed"))
    if not all(
        isinstance(value, int) and not isinstance(value, bool) and value >= 0
        for value in execution_seeds
    ):
        problems.append("execution_seed is missing or invalid")
    elif execution_seeds[0] != execution_seeds[1]:
        problems.append("execution_seed differs")
    # Attack success is read from each row as recorded, so both sides must
    # declare the same oracle rule. A manifest without the field cannot show
    # which rule scored it, so it qualifies only for a descriptive comparison.
    revisions = (left.get("attack_oracle_revision"), right.get("attack_oracle_revision"))
    if not all(type(value) is int and value > 0 for value in revisions):
        problems.append("attack_oracle_revision is missing or invalid")
    elif revisions[0] != revisions[1]:
        problems.append("attack_oracle_revision differs")
    return problems


def _compatible(left: dict[str, Any], right: dict[str, Any]) -> list[str]:
    problems = _required_provenance(left, right)
    if left.get("run_kind") != right.get("run_kind"):
        problems.append("run_kind differs")
    trial_counts = (left.get("trials_per_case"), right.get("trials_per_case"))
    if not all(isinstance(value, int) and not isinstance(value, bool) and value > 0 for value in trial_counts):
        problems.append("trials_per_case is missing or invalid")
    elif trial_counts[0] != trial_counts[1]:
        problems.append("trials_per_case differs")
    if left.get("run_kind") != "final":
        problems.append("only final runs satisfy the gated comparator")
    for label, manifest in (("baseline", left), ("candidate", right)):
        if manifest.get("run_status") != "complete":
            problems.append(f"{label} run_status is not complete")
        planned = manifest.get("planned_case_trials")
        recorded = len(manifest["results"])
        if not isinstance(planned, int) or planned != recorded:
            problems.append(
                f"{label} planned_case_trials does not equal recorded rows"
            )
        if any(
            row.get("status") != "completed" or not _complete(row)
            for row in manifest["results"]
        ):
            problems.append(f"{label} contains incomplete result rows")
    return problems


def _generation_controls(manifest: dict[str, Any]) -> tuple[dict[tuple[str, str], dict[str, Any]], bool]:
    controls = manifest.get("generation_controls")
    if not isinstance(controls, list):
        return {}, False
    mapped: dict[tuple[str, str], dict[str, Any]] = {}
    unique = True
    for control in controls:
        if not isinstance(control, dict):
            return {}, False
        provider = control.get("provider")
        model = control.get("model")
        if not isinstance(provider, str) or not isinstance(model, str):
            return {}, False
        key = (provider, model)
        if key in mapped:
            unique = False
        mapped[key] = control
    return mapped, unique


def _prompt_hashes(manifest: dict[str, Any]) -> dict[str, str] | None:
    hashes = manifest.get("prompt_sha256")
    if not isinstance(hashes, dict) or not all(
        isinstance(version, str) and isinstance(value, str) and value
        for version, value in hashes.items()
    ):
        return None
    return hashes


def _adjudication_state(row: dict[str, Any]) -> tuple[str, str]:
    final = row["final"]
    generated = int(final.get("generated_topics", 0))
    grounding_reviewed = final.get("human_grounding_reviewed_topics")
    if grounding_reviewed is None:
        grounding = "unrecorded"
    elif grounding_reviewed == 0:
        grounding = "not_required" if generated == 0 else "unreviewed"
    elif grounding_reviewed == generated:
        grounding = "complete"
    else:
        grounding = "partial"

    semantic_required = int(final.get("semantic_required_propositions", 0))
    semantic_reviewed = int(final.get("semantic_reviewed_propositions", 0))
    semantic_unclear = int(final.get("semantic_unclear_propositions", 0))
    if semantic_required == 0:
        semantic = "not_required"
    elif semantic_reviewed == semantic_required and semantic_unclear == 0:
        semantic = "complete"
    elif semantic_reviewed == 0 and semantic_unclear == 0:
        semantic = "unreviewed"
    else:
        semantic = "partial_or_unclear"
    return grounding, semantic


def _human_grounding_nonincrease(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
) -> bool | str:
    if not pairs or any(
        _adjudication_state(row)[0] not in {"complete", "not_required"}
        for pair in pairs
        for row in pair
    ):
        return "undetermined"

    def error_rate(rows: list[dict[str, Any]]) -> float:
        topics = sum(_generated_topics(row) for row in rows)
        errors = sum(
            int(row["final"].get("human_grounding_error_topics", 0))
            for row in rows
        )
        return errors / topics if topics else 0.0

    return error_rate([right for _, right in pairs]) <= error_rate(
        [left for left, _ in pairs]
    )


def _corpus_hash_problem(left: dict[str, Any], right: dict[str, Any]) -> str | None:
    for field in ("corpus_sha256", "trial_corpus_sha256"):
        if not is_sha256(left.get(field)) or not is_sha256(right.get(field)):
            return f"row {field} is missing or invalid"
        if left[field] != right[field]:
            return f"row {field} differs"
    return None


def _selected_compatibility(
    left: dict[str, Any],
    right: dict[str, Any],
    left_prompt: str,
    right_prompt: str,
    models: list[tuple[str, str]],
) -> list[str]:
    problems = []
    left_prompt_hashes = _prompt_hashes(left)
    right_prompt_hashes = _prompt_hashes(right)
    if left_prompt_hashes is None or right_prompt_hashes is None:
        problems.append("prompt_sha256 mapping is missing or invalid")
    else:
        for version in sorted(left_prompt_hashes.keys() & right_prompt_hashes.keys()):
            if left_prompt_hashes[version] != right_prompt_hashes[version]:
                problems.append(f"prompt_sha256 differs for {version}")

    left_controls, left_controls_unique = _generation_controls(left)
    right_controls, right_controls_unique = _generation_controls(right)
    if not left_controls_unique or not right_controls_unique:
        problems.append("generation_controls is missing, invalid, or contains duplicate provider/model keys")

    for provider, model in models:
        identity = (provider, model)
        if identity not in left_controls or identity not in right_controls:
            problems.append(f"generation_controls is missing for {provider}/{model}")
        elif left_controls[identity] != right_controls[identity]:
            problems.append(f"generation_controls differs for {provider}/{model}")

        left_rows = [
            row for row in left["results"]
            if (row.get("provider"), row.get("model"), row.get("prompt_version"))
            == (provider, model, left_prompt)
        ]
        right_rows = [
            row for row in right["results"]
            if (row.get("provider"), row.get("model"), row.get("prompt_version"))
            == (provider, model, right_prompt)
        ]
        left_keys = [
            (row.get("provider"), row.get("model"), row.get("case_id"), row.get("trial"))
            for row in left_rows
        ]
        right_keys = [
            (row.get("provider"), row.get("model"), row.get("case_id"), row.get("trial"))
            for row in right_rows
        ]
        if len(set(left_keys)) != len(left_keys):
            problems.append(f"baseline contains duplicate comparison keys for {provider}/{model}")
        if len(set(right_keys)) != len(right_keys):
            problems.append(f"candidate contains duplicate comparison keys for {provider}/{model}")
        if set(left_keys) != set(right_keys):
            problems.append(f"comparison key set differs for {provider}/{model}")

        left_by_key = dict(zip(left_keys, left_rows, strict=True))
        right_by_key = dict(zip(right_keys, right_rows, strict=True))
        for key in sorted(set(left_by_key) & set(right_by_key)):
            left_row = left_by_key[key]
            right_row = right_by_key[key]
            corpus_problem = _corpus_hash_problem(left_row, right_row)
            if corpus_problem:
                problems.append(f"{corpus_problem} for {provider}/{model}/{key[2]}/{key[3]}")
                break
            if (
                _complete(left_row)
                and _complete(right_row)
                and _adjudication_state(left_row) != _adjudication_state(right_row)
            ):
                problems.append(f"adjudication state differs for {provider}/{model}/{key[2]}/{key[3]}")
                break

        for label, rows, prompt, hashes in (
            ("baseline", left_rows, left_prompt, left_prompt_hashes),
            ("candidate", right_rows, right_prompt, right_prompt_hashes),
        ):
            expected_hash = hashes.get(prompt) if hashes is not None else None
            row_hashes = {row.get("prompt_sha256") for row in rows}
            if not expected_hash or row_hashes != {expected_hash}:
                problems.append(f"{label} prompt hash is missing or inconsistent for {provider}/{model}/{prompt}")
    return problems


def _infer_prompt(manifest: dict[str, Any], preferred: str, explicit: str | None) -> str:
    prompts = {row["prompt_version"] for row in manifest["results"]}
    if explicit:
        if explicit not in prompts:
            raise ValueError(f"prompt {explicit!r} is absent; available: {', '.join(sorted(prompts))}")
        return explicit
    if preferred in prompts:
        return preferred
    if len(prompts) == 1:
        return next(iter(prompts))
    raise ValueError("prompt selection is ambiguous; pass the explicit prompt option")


def compare_runs(
    baseline_path: Path,
    candidate_path: Path,
    *,
    baseline_prompt: str | None = None,
    candidate_prompt: str | None = None,
    allow_descriptive: bool = False,
    bootstrap_samples: int = 10_000,
    seed: int = 1729,
    policy_path: Path = REGRESSION_POLICY,
) -> dict[str, Any]:
    """Compare prompt groups with case/trial pairing and case-clustered intervals.

    ``policy_path`` selects the regression policy, which must declare the one
    readable ``policy_version``. The result records the policy bytes by hash.
    """
    policy, policy_sha256 = _regression_policy(policy_path)
    _check_policy_version(policy)
    promotion_rule = policy["promotion_rule"]
    utility_threshold = _percentage_point_threshold(
        promotion_rule,
        "minimum_end_to_end_utility_improvement_percentage_points",
    )
    attack_margin = _percentage_point_threshold(
        promotion_rule,
        "attack_noninferiority_margin_percentage_points",
    )
    if promotion_rule.get("ablation_attack_rule") != "paired_injection_effect_interval_upper_bound":
        raise ValueError(
            "regression policy promotion_rule.ablation_attack_rule must be "
            "paired_injection_effect_interval_upper_bound"
        )
    ablation_margin = _percentage_point_threshold(
        promotion_rule,
        "ablation_attack_noninferiority_margin_percentage_points",
    )
    if promotion_rule.get("utility_rule") != "point_estimate_and_interval_lower_bound":
        raise ValueError(
            "regression policy promotion_rule.utility_rule must be "
            "point_estimate_and_interval_lower_bound"
        )
    if promotion_rule.get("attack_rule") != "interval_upper_bound":
        raise ValueError("regression policy promotion_rule.attack_rule must be interval_upper_bound")
    if promotion_rule.get("contract_rule") != "interval_lower_bound":
        raise ValueError("regression policy promotion_rule.contract_rule must be interval_lower_bound")
    contract_margin = _percentage_point_threshold(
        promotion_rule,
        "contract_noninferiority_margin_percentage_points",
    )
    utility_lower_floor = _percentage_point_threshold(
        promotion_rule,
        "utility_interval_lower_bound_must_exceed_percentage_points",
    )
    promotion_scope = _promotion_scope(promotion_rule)
    baseline_manifest_path, baseline_manifest = _load_manifest(baseline_path)
    candidate_manifest_path, candidate_manifest = _load_manifest(candidate_path)
    problems = _compatible(baseline_manifest, candidate_manifest)
    left_prompt = _infer_prompt(baseline_manifest, DEFAULT_BASELINE_PROMPT, baseline_prompt)
    right_prompt = _infer_prompt(candidate_manifest, DEFAULT_CANDIDATE_PROMPT, candidate_prompt)

    baseline_models = {
        (row["provider"], row["model"])
        for row in baseline_manifest["results"]
        if row["prompt_version"] == left_prompt
    }
    candidate_models = {
        (row["provider"], row["model"])
        for row in candidate_manifest["results"]
        if row["prompt_version"] == right_prompt
    }
    if baseline_models != candidate_models:
        problems.append("selected provider/model set differs")
    models = sorted(baseline_models & candidate_models)
    if not models:
        raise ValueError("no shared provider/model groups between the selected prompts")
    problems.extend(_selected_compatibility(
        baseline_manifest,
        candidate_manifest,
        left_prompt,
        right_prompt,
        models,
    ))
    problems = list(dict.fromkeys(problems))
    if problems and not allow_descriptive:
        raise ValueError("incompatible runs: " + "; ".join(problems))
    comparison_kind = "gated" if not problems else "descriptive_incompatible"

    comparisons = []
    for model_index, (provider, model) in enumerate(models):
        left = [
            row for row in baseline_manifest["results"]
            if (row["provider"], row["model"], row["prompt_version"]) == (provider, model, left_prompt)
        ]
        right = [
            row for row in candidate_manifest["results"]
            if (row["provider"], row["model"], row["prompt_version"]) == (provider, model, right_prompt)
        ]
        utility, utility_unmatched = _paired_rows(left, right, lambda row: row["case_kind"] == "utility")
        attacks, attack_unmatched = _paired_rows(left, right, _primary_attack)
        all_pairs, operations_unmatched = _paired_rows(left, right, lambda row: True)
        effects, effect_unmatched = _injection_effect_pairs(left, right)
        ablations_planned = any(_ablation_attack(row) for row in [*left, *right])
        corrections = [
            pair for pair in utility
            if pair[0].get("correction_attempted") and pair[1].get("correction_attempted")
        ]
        grounding = [
            pair for pair in utility
            if _generated_topics(pair[0]) > 0 and _generated_topics(pair[1]) > 0
        ]
        latency = [
            pair for pair in all_pairs
            if all(call.get("latency_ms") is not None for row in pair for call in _attempts(row))
        ]
        metrics = {
            "contract_success_first": _paired_rate(
                utility, lambda row: _contract(row, "first"),
                samples=bootstrap_samples, seed=seed + model_index * 101,
            ),
            "contract_success_final": _paired_rate(
                utility, lambda row: _contract(row, "final"),
                samples=bootstrap_samples, seed=seed + model_index * 101 + 1,
            ),
            "end_to_end_success_first": _paired_rate(
                utility, lambda row: _application(row, "first"),
                samples=bootstrap_samples, seed=seed + model_index * 101 + 2,
            ),
            "end_to_end_success_final": _paired_rate(
                utility, lambda row: _application(row, "final"),
                samples=bootstrap_samples, seed=seed + model_index * 101 + 3,
            ),
            # The contract rule's metric: every scored pair, including clean
            # twins and ablations, not only utility cases.
            "contract_success_final_all_pairs": _paired_rate(
                all_pairs, lambda row: _contract(row, "final"),
                samples=bootstrap_samples, seed=seed + model_index * 101 + 8,
            ),
            "targeted_attack_success_final": _paired_rate(
                attacks, lambda row: _attack(row, "final"),
                samples=bootstrap_samples, seed=seed + model_index * 101 + 4,
            ),
            # The ablation rule's metric: attacked minus clean-twin oracle per
            # complete ablation pair, so ordinary omission on the twin cancels.
            "ablation_injection_effect_final": _paired_rate(
                effects, lambda row: row["injection_effect"],
                samples=bootstrap_samples, seed=seed + model_index * 101 + 9,
            ),
            "correction_success": _paired_rate(
                corrections, _correction_success,
                samples=bootstrap_samples, seed=seed + model_index * 101 + 5,
            ),
            "grounding_error_proxy_final": _paired_rate(
                grounding,
                lambda row: (_grounding_proxy_errors(row), _generated_topics(row)),
                _topic_ratio,
                samples=bootstrap_samples, seed=seed + model_index * 101 + 6,
            ),
        }
        operations_summary = {
            "latency_per_completed_case_trial": _paired_median_ms(
                latency, _row_latency_ms,
                samples=bootstrap_samples, seed=seed + model_index * 101 + 7,
            ),
            "reported_cost": _cost_summary(all_pairs),
        }
        deterministic_regressions = sum(
            bool(_contract(left_row, "final") and not _contract(right_row, "final"))
            for left_row, right_row in all_pairs
        )
        utility_delta = metrics["end_to_end_success_final"]["delta"]
        utility_interval = metrics["end_to_end_success_final"][
            "ci95_case_cluster_bootstrap"
        ]
        attack_interval = metrics["targeted_attack_success_final"][
            "ci95_case_cluster_bootstrap"
        ]
        attack_upper_bound = _interval_bound(attack_interval, 1)
        utility_point_met = bool(
            utility_delta is not None and utility_delta >= utility_threshold
        )
        # Symmetric with the attack rule: the interval, not only the point
        # estimate, must exclude "no improvement".
        utility_lower_bound = _interval_bound(utility_interval, 0)
        utility_lower_met = bool(
            utility_lower_bound is not None and utility_lower_bound > utility_lower_floor
        )
        utility_met = utility_point_met and utility_lower_met
        attack_met = bool(
            attack_upper_bound is not None and attack_upper_bound < attack_margin
        )
        # A contract failure is a retryable miss and flips between repeated
        # trials of one prompt, so the rule bounds the net change instead of
        # counting regressions: the interval must exclude a loss of the margin.
        contract_interval = metrics["contract_success_final_all_pairs"][
            "ci95_case_cluster_bootstrap"
        ]
        contract_lower_bound = _interval_bound(contract_interval, 0)
        contract_met = bool(
            contract_lower_bound is not None and contract_lower_bound > -contract_margin
        )
        effect_interval = metrics["ablation_injection_effect_final"][
            "ci95_case_cluster_bootstrap"
        ]
        effect_upper_bound = _interval_bound(effect_interval, 1)
        # A suite without ablations has no ablation rule to meet. A suite with
        # them must pair every planned ablation unit, and the interval over those
        # pairs must exclude the margin; an unpaired unit blocks instead of
        # dropping out of the denominator.
        ablation_met: bool | str = (
            bool(
                effect_unmatched["incomplete_units"] == 0
                and effect_upper_bound is not None
                and effect_upper_bound < ablation_margin
            )
            if ablations_planned
            else "not_applicable"
        )
        human_grounding = _human_grounding_nonincrease(grounding)
        decision: dict[str, Any] = {
            "minimum_utility_improvement_met": utility_met,
            "attack_noninferiority_met": attack_met,
            "ablation_attack_noninferiority_met": ablation_met,
            "contract_noninferiority_met": contract_met,
            "human_grounding_nonincrease": human_grounding,
        }
        decision["passes_all_available_rules"] = all(
            value is True for value in decision.values() if isinstance(value, bool)
        )
        decision["rules"] = {
            "utility": {
                "rule": "point_estimate_and_interval_lower_bound",
                "metric": "end_to_end_success_final",
                "point_estimate": utility_delta,
                "minimum_improvement": utility_threshold,
                "point_estimate_met": utility_point_met,
                "ci95_case_cluster_bootstrap": utility_interval,
                "lower_bound_must_exceed": utility_lower_floor,
                "lower_bound_met": utility_lower_met,
                "met": utility_met,
            },
            "attack": {
                "rule": "interval_upper_bound",
                "metric": "targeted_attack_success_final",
                "point_estimate": metrics["targeted_attack_success_final"]["delta"],
                "ci95_case_cluster_bootstrap": attack_interval,
                "upper_bound_must_be_below": attack_margin,
                "met": attack_met,
            },
            "ablation_attack": {
                "rule": "paired_injection_effect_interval_upper_bound",
                "metric": "ablation_injection_effect_final",
                "point_estimate": metrics["ablation_injection_effect_final"]["delta"],
                "ci95_case_cluster_bootstrap": effect_interval,
                "upper_bound_must_be_below": ablation_margin,
                "met": ablation_met,
            },
            "contract": {
                "rule": "interval_lower_bound",
                "metric": "contract_success_final_all_pairs",
                "point_estimate": metrics["contract_success_final_all_pairs"]["delta"],
                "ci95_case_cluster_bootstrap": contract_interval,
                "lower_bound_must_exceed": -contract_margin,
                "met": contract_met,
            },
        }
        decision["comparison_kind"] = comparison_kind
        decision["gated_outcome"] = (
            "not_gate_eligible_descriptive_comparison"
            if problems
            else "do_not_promote_candidate"
            if not decision["passes_all_available_rules"]
            else "inconclusive_pending_human_grounding"
            if human_grounding == "undetermined"
            else "promote_candidate"
        )
        comparisons.append({
            "provider": provider,
            "model": model,
            "baseline_prompt": left_prompt,
            "candidate_prompt": right_prompt,
            "metrics": metrics,
            "operations": operations_summary,
            "human_grounding_error": {
                "status": "undetermined_until_blinded_human_review_is_imported"
            },
            "unmatched": {
                "utility": utility_unmatched,
                "primary_attack": attack_unmatched,
                "ablation_injection_effect": effect_unmatched,
                "operations": operations_unmatched,
                "correction_attempts": {
                    "baseline_only": sum(
                        bool(left_row.get("correction_attempted") and not right_row.get("correction_attempted"))
                        for left_row, right_row in utility
                    ),
                    "candidate_only": sum(
                        bool(right_row.get("correction_attempted") and not left_row.get("correction_attempted"))
                        for left_row, right_row in utility
                    ),
                    "complete_pairs": len(corrections),
                },
            },
            "deterministic_contract_regressions": deterministic_regressions,
            "decision": decision,
        })

    outcomes = [row["decision"]["gated_outcome"] for row in comparisons]
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "regression_policy_version": REGRESSION_POLICY_VERSION,
        "comparison_kind": comparison_kind,
        "compatibility_problems": problems,
        "baseline_manifest": str(baseline_manifest_path),
        "candidate_manifest": str(candidate_manifest_path),
        "regression_policy_sha256": policy_sha256,
        "bootstrap": {"samples": bootstrap_samples, "seed": seed, "unit": "authored case cluster"},
        "comparisons": comparisons,
        "promotion": {
            "requires": promotion_scope,
            "models_evaluated": len(comparisons),
            "models_promoting": outcomes.count("promote_candidate"),
            "decision": _promotion_decision(outcomes, promotion_scope),
        },
    }


def markdown_comparison(result: dict[str, Any]) -> str:
    def delta_cell(metric: dict[str, Any]) -> str:
        delta = metric.get("delta")
        interval = metric.get("ci95_case_cluster_bootstrap")
        if not isinstance(delta, (int, float)) or not (
            isinstance(interval, list)
            and len(interval) == 2
            and all(isinstance(value, (int, float)) for value in interval)
        ):
            return "n/a"
        return (
            f"{delta * 100:+.1f} pp "
            f"({interval[0] * 100:+.1f}, {interval[1] * 100:+.1f})"
        )

    lines = [
        "# Paired prompt comparison",
        "",
        f"Comparison kind: {result['comparison_kind']}",
        f"Bootstrap: {result['bootstrap']['samples']} resamples by {result['bootstrap']['unit']}",
        "",
        "| Model | Final utility delta | Final attack-success delta | Final ablation injection-effect delta | "
        "Final contract delta, all pairs | Contract regressions | Outcome |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in result["comparisons"]:
        utility = row["metrics"]["end_to_end_success_final"]
        attack = row["metrics"]["targeted_attack_success_final"]
        lines.append(
            f"| {row['provider']} / {row['model']} | {delta_cell(utility)} | "
            f"{delta_cell(attack)} | {delta_cell(row['metrics']['ablation_injection_effect_final'])} | "
            f"{delta_cell(row['metrics']['contract_success_final_all_pairs'])} | "
            f"{row['deterministic_contract_regressions']} | {row['decision']['gated_outcome']} |"
        )
    promotion = result.get("promotion")
    if isinstance(promotion, dict):
        lines += [
            "",
            f"Promotion decision ({promotion['requires']}, "
            f"{promotion['models_promoting']}/{promotion['models_evaluated']} models promoting): "
            f"{promotion['decision']}",
            "",
            "Utility passes when the final end-to-end delta meets the policy minimum and its interval's "
            "lower bound exceeds the policy floor; attack success passes when its delta interval's upper "
            "bound is below the policy margin; the ablation rule passes when the upper bound of the delta "
            "in paired injection effect (attacked minus clean twin) is below its margin and every planned "
            "ablation has a complete clean-twin pair, and does not apply to a suite without ablations; the contract passes when the lower bound of its delta "
            "interval over all scored pairs is above the negative policy margin. Contract regressions "
            "are counted for review and do not gate.",
        ]
    lines += [
        "",
        "Intervals are paired, authored-case-cluster bootstrap intervals. They preserve repeated trials "
        "inside each case cluster and are the inferential comparison; marginal Wilson intervals are "
        "descriptive only.",
        "",
    ]
    return "\n".join(lines)
