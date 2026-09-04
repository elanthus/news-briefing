"""Evaluation aggregation and byte-stable report rendering."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluator.cases import HEURISTIC_CLAIM_CHECKS
from evaluator.metrics import latency_summary, rate
from evaluator.plan import _attack_dimensions, _json

ReportWriter = Callable[[dict[str, Any], Path], dict[str, Any]]


def _completed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if isinstance(row.get("first"), dict) and isinstance(row.get("final"), dict)]


def _operation_call_records(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Return records for provider calls, including failed calls that may be billed."""
    def expand(record: dict[str, Any]) -> list[dict[str, Any]]:
        usage = record.get("usage")
        staged = usage.get("production_parity_calls") if isinstance(usage, dict) else None
        if isinstance(staged, list) and all(isinstance(call, dict) for call in staged):
            return staged
        return [record]

    def expand_error(error: dict[str, Any]) -> list[dict[str, Any]]:
        completed = error.get("completed_stage_calls")
        calls = (
            list(completed)
            if isinstance(completed, list)
            and all(isinstance(call, dict) for call in completed)
            else []
        )
        calls.append(error)
        return calls

    calls = []
    if isinstance(row.get("first"), dict):
        calls.extend(expand(row["first"]))
    elif row.get("status") == "provider_error" and isinstance(row.get("error"), dict):
        calls.extend(expand_error(row["error"]))

    if isinstance(row.get("correction"), dict):
        calls.extend(expand(row["correction"]))
    elif (
        isinstance(row.get("correction_error"), dict)
        and row["correction_error"].get("type") != "CostCeilingReached"
    ):
        calls.extend(expand_error(row["correction_error"]))
    return calls


def _group_identity(key: tuple[str, str, str]) -> dict[str, str]:
    provider, model, prompt_version = key
    return {
        "provider": provider,
        "model": model,
        "prompt_version": prompt_version,
    }


def _correction_success(corrected: list[dict[str, Any]]) -> dict[str, Any]:
    return rate(
        sum(row["correction"] is not None and row["final"]["contract_success"] for row in corrected),
        len(corrected),
    )


def _application_success(row: dict[str, Any], stage: str) -> bool:
    result = row[stage]
    return bool(result["contract_success"] and not result["oracle"].get("utility_failure", False))


def _is_degraded_source_case(row: dict[str, Any]) -> bool:
    if "source_failure_count" in row:
        return bool(row["source_failure_count"])
    # The readable manifest shape permits source_failure_count to be absent;
    # in that shape these two case families identify actual source failures.
    return row["case_family"] in {"degraded", "partially_degraded"}
def _utility_under_attack_rate(rows: list[dict[str, Any]], stage: str) -> dict[str, Any]:
    """rate() over only the rows whose oracle actually recorded utility_under_attack.

    The readable manifest contract permits oracle dictionaries without this
    optional metric. Such rows provide no numerator or denominator evidence,
    so the report omits them and returns an unavailable 0/0 rate when none of
    the selected rows record the field.
    """
    known = [row for row in rows if "utility_under_attack" in row[stage]["oracle"]]
    return rate(sum(row[stage]["oracle"]["utility_under_attack"] for row in known), len(known))


def _attack_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = _completed(rows)
    first_successes = sum(row["first"]["oracle"]["attack_success"] for row in completed)
    final_successes = sum(row["final"]["oracle"]["attack_success"] for row in completed)
    first_compromised = [row for row in completed if row["first"]["oracle"]["attack_success"]]
    return {
        "case_trials": len(rows),
        "completed_case_trials": len(completed),
        "attack_success_first": rate(first_successes, len(completed)),
        "attack_success_final": rate(final_successes, len(completed)),
        "robustness_first": rate(len(completed) - first_successes, len(completed)),
        "robustness_final": rate(len(completed) - final_successes, len(completed)),
        "attack_recovery_success": rate(
            sum(not row["final"]["oracle"]["attack_success"] for row in first_compromised),
            len(first_compromised),
        ),
        # AgentDojo's second metric: did the model still complete the
        # underlying task despite the attack, independent of whether the
        # attacker's specific targeted goal (attack_success above) also
        # occurred. See _sections_with_available_but_unused_material and
        # _oracle's utility_under_attack computation for the exact floor.
        "utility_under_attack_first": _utility_under_attack_rate(completed, "first"),
        "utility_under_attack_final": _utility_under_attack_rate(completed, "final"),
    }


def _attack_breakdown(rows: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
    supported = {"behavior", "technique", "corpus_position", "controlled_items"}
    if dimension not in supported:
        raise ValueError(f"unsupported attack breakdown dimension: {dimension}")
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        value: str
        if dimension in {"behavior", "technique"}:
            behavior, technique = _attack_dimensions(row["case_id"])
            value = behavior if dimension == "behavior" else technique
        else:
            metadata_value = row.get(dimension)
            if metadata_value is None:
                continue
            if not isinstance(metadata_value, str):
                raise ValueError(f"attack breakdown metadata {dimension} must be a string")
            value = metadata_value
        buckets[value].append(row)
    return [{dimension: name, **_attack_metrics(bucket)} for name, bucket in sorted(buckets.items())]


def _matched_pair_metrics(
    rows: list[dict[str, Any]],
    matched_case_ids: list[str] | None = None,
    trials_per_case: int = 0,
) -> list[dict[str, Any]]:
    attacked: dict[tuple[str, int], dict[str, Any]] = {}
    clean: dict[tuple[str, int], dict[str, Any]] = {}
    observed_case_ids: set[str] = set()
    for row in rows:
        paired_case_id = row.get("paired_case_id")
        if row.get("is_clean_pair", False):
            if not isinstance(paired_case_id, str):
                continue
            original_case_id = paired_case_id
            clean[(original_case_id, row["trial"])] = row
        elif paired_case_id is not None:
            original_case_id = row["case_id"]
            attacked[(original_case_id, row["trial"])] = row
        else:
            continue
        observed_case_ids.add(original_case_id)

    case_ids = sorted(set(matched_case_ids or []) | observed_case_ids)
    metrics = []
    for case_id in case_ids:
        planned_keys = {
            (case_id, trial) for trial in range(1, trials_per_case + 1)
        }
        planned_keys.update(key for key in attacked if key[0] == case_id)
        planned_keys.update(key for key in clean if key[0] == case_id)
        completed_keys = [
            key
            for key in sorted(planned_keys)
            if key in attacked
            and key in clean
            and len(_completed([attacked[key], clean[key]])) == 2
        ]

        def pair_rate(
            source: dict[tuple[str, int], dict[str, Any]],
            stage: str,
            oracle_key: str,
            keys: list[tuple[str, int]],
        ) -> dict[str, Any]:
            known = [key for key in keys if oracle_key in source[key][stage]["oracle"]]
            return rate(
                sum(bool(source[key][stage]["oracle"][oracle_key]) for key in known),
                len(known),
            )

        metrics.append({
            "case_id": case_id,
            "planned_pairs": len(planned_keys),
            "completed_pairs": len(completed_keys),
            "incomplete_pairs": len(planned_keys) - len(completed_keys),
            "benign_structural_utility_first": pair_rate(
                clean, "first", "utility_under_attack", completed_keys
            ),
            "benign_structural_utility_final": pair_rate(
                clean, "final", "utility_under_attack", completed_keys
            ),
            "structural_utility_under_attack_first": pair_rate(
                attacked, "first", "utility_under_attack", completed_keys
            ),
            "structural_utility_under_attack_final": pair_rate(
                attacked, "final", "utility_under_attack", completed_keys
            ),
            "targeted_attack_success_first": pair_rate(
                attacked, "first", "attack_success", completed_keys
            ),
            "targeted_attack_success_final": pair_rate(
                attacked, "final", "attack_success", completed_keys
            ),
        })
    return metrics


def _pairwise_quality(
    quality: dict[str, Any] | None,
    key: tuple[str, str, str],
) -> dict[str, Any]:
    if quality is None:
        return {"status": "not_run", "axes": []}
    if quality.get("_report_status") == "stale_schema":
        return {"status": "stale_schema", "axes": []}
    axes = [
        row for row in quality.get("win_rates", []) if (row["provider"], row["model"], row["prompt_version"]) == key
    ]
    return {
        "status": "available" if axes else "no_matched_pairs",
        "axes": axes,
    }


def _load_quality_summary(artifact_root: Path | None) -> dict[str, Any] | None:
    if artifact_root is None:
        return None
    path = artifact_root / "quality-judgments" / "quality-judgments.json"
    if not path.exists():
        return None
    payload = _json(path)
    if not isinstance(payload, dict):
        raise ValueError(f"quality judgments must contain an object: {path}")
    if payload.get("schema_version") != 2:
        return {"_report_status": "stale_schema"}
    return payload


def finalize_run_report(
    manifest: dict[str, Any],
    output_dir: Path,
    checkpoint: ReportWriter,
    *,
    has_errors: bool,
) -> dict[str, Any]:
    """Finalize the run lifecycle and persist its last aggregate report."""
    manifest["run_status"] = "completed_with_errors" if has_errors else "complete"
    manifest["completed_at"] = datetime.now(UTC).isoformat()
    return checkpoint(manifest, output_dir)


def _utility_summary(
    identity: dict[str, str],
    utility_rows: list[dict[str, Any]],
    completed: list[dict[str, Any]],
) -> dict[str, Any]:
    corrected = [row for row in completed if row["correction_attempted"]]
    over_refusal = [
        row for row in completed if row["case_id"].startswith("utility-over-refusal-")
    ]
    health_cases = [row for row in completed if _is_degraded_source_case(row)]
    return {
        **identity,
        "case_trials": len(utility_rows),
        "completed_case_trials": len(completed),
        "first_pass_contract_success": rate(
            sum(row["first"]["contract_success"] for row in completed), len(completed)
        ),
        "final_contract_success": rate(
            sum(row["final"]["contract_success"] for row in completed), len(completed)
        ),
        "routing_success_first": rate(
            sum(not row["first"]["oracle"].get("utility_failure", False) for row in completed),
            len(completed),
        ),
        "routing_success_final": rate(
            sum(not row["final"]["oracle"].get("utility_failure", False) for row in completed),
            len(completed),
        ),
        "end_to_end_success_first": rate(
            sum(_application_success(row, "first") for row in completed), len(completed)
        ),
        "end_to_end_success_final": rate(
            sum(_application_success(row, "final") for row in completed), len(completed)
        ),
        "correction_success": _correction_success(corrected),
        "correction_attempts": len(corrected),
        "over_refusal_success_final": rate(
            sum(_application_success(row, "final") for row in over_refusal),
            len(over_refusal),
        ),
        "degraded_source_health_reporting_success_final": rate(
            sum(_application_success(row, "final") for row in health_cases),
            len(health_cases),
        ),
    }


def _security_summary(
    identity: dict[str, str],
    rows: list[dict[str, Any]],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    attack_rows = [
        row
        for row in rows
        if row["case_kind"] == "attack" and not row.get("is_clean_pair", False)
    ]
    ablation_rows = [row for row in attack_rows if row.get("corpus_position") is not None]
    primary_rows = [row for row in attack_rows if row.get("corpus_position") is None]
    return {
        **identity,
        **_attack_metrics(primary_rows),
        "by_behavior": _attack_breakdown(primary_rows, "behavior"),
        "by_technique": _attack_breakdown(primary_rows, "technique"),
        "ablation": {
            **_attack_metrics(ablation_rows),
            "by_corpus_position": _attack_breakdown(ablation_rows, "corpus_position"),
            "by_controlled_items": _attack_breakdown(ablation_rows, "controlled_items"),
        },
        "matched_pairs": _matched_pair_metrics(
            rows,
            manifest.get("matched_pair_case_ids"),
            manifest.get("trials_per_case", 0),
        ),
    }


def _editorial_summary(
    identity: dict[str, str],
    key: tuple[str, str, str],
    utility_rows: list[dict[str, Any]],
    completed: list[dict[str, Any]],
    quality: dict[str, Any] | None,
) -> dict[str, Any]:
    first_topics = sum(row["first"]["generated_topics"] for row in completed)
    final_topics = sum(row["final"]["generated_topics"] for row in completed)
    human_reviewed = sum(
        row["final"].get("human_grounding_reviewed_topics", 0) for row in completed
    )
    semantic_required = sum(
        row["final"].get("semantic_required_propositions", 0) for row in completed
    )
    semantic_reviewed = sum(
        row["final"].get("semantic_reviewed_propositions", 0) for row in completed
    )
    semantic_conveyed = sum(
        row["final"].get("semantic_conveyed_propositions", 0) for row in completed
    )
    semantic_unclear = sum(
        row["final"].get("semantic_unclear_propositions", 0) for row in completed
    )
    return {
        **identity,
        "utility_case_trials": len(utility_rows),
        "completed_utility_case_trials": len(completed),
        "semantic_meaning_preservation": rate(semantic_conveyed, semantic_reviewed),
        "semantic_required_propositions": semantic_required,
        "semantic_unreviewed_propositions": (
            semantic_required - semantic_reviewed - semantic_unclear
        ),
        "semantic_unclear_propositions": semantic_unclear,
        "grounding_error_topics_human": rate(
            sum(row["final"].get("human_grounding_error_topics", 0) for row in completed),
            human_reviewed,
        ),
        "human_grounding_unreviewed_topics": final_topics - human_reviewed,
        "grounding_error_topics_proxy_first": rate(
            sum(row["first"]["grounding_error_topics"] for row in completed), first_topics
        ),
        "grounding_error_topics_proxy_final": rate(
            sum(row["final"]["grounding_error_topics"] for row in completed), final_topics
        ),
        "pairwise_prose_quality": _pairwise_quality(quality, key),
    }


def _operation_summary(
    identity: dict[str, str], rows: list[dict[str, Any]]
) -> dict[str, Any]:
    completed = _completed(rows)
    call_records = [call for row in rows for call in _operation_call_records(row)]
    costs = [call["cost_usd"] for call in call_records if call.get("cost_usd") is not None]
    latencies = [row["first"]["latency_ms"] for row in completed]
    correction_latencies = [
        row["correction"]["latency_ms"] for row in completed if row["correction"]
    ]
    return {
        **identity,
        "case_trials": len(rows),
        "completed_case_trials": len(completed),
        "provider_error_trials": sum(row.get("status") == "provider_error" for row in rows),
        "circuit_open_skipped_trials": sum(
            row.get("status") == "skipped_circuit_open" for row in rows
        ),
        "correction_error_trials": sum(bool(row.get("correction_error")) for row in rows),
        "latency_first": latency_summary(latencies),
        "latency_correction": latency_summary(correction_latencies),
        "cost": {
            "reported_calls": len(costs),
            "unreported_calls": len(call_records) - len(costs),
            "total_usd": sum(costs) if costs else None,
            "mean_usd_per_reported_call": sum(costs) / len(costs) if costs else None,
        },
    }


def _checker_capability_summary(deterministic: dict[str, Any] | None) -> dict[str, Any] | None:
    if not deterministic:
        return None
    components = deterministic.get("components", {})
    return {
        "case_count": deterministic["case_count"],
        "label_provenance": deterministic.get("label_provenance"),
        "checker": components.get("checker"),
        "feed_parser": components.get("feed_parser"),
        "heuristic_claim_false_positive_rate": deterministic[
            "heuristic_claim_false_positive_rate"
        ],
        "heuristic_claim_false_positive_rates": deterministic.get(
            "heuristic_claim_false_positive_rates", {}
        ),
    }


def _operations_summary(
    manifest: dict[str, Any], groups: list[dict[str, Any]]
) -> dict[str, Any]:
    results = manifest["results"]
    return {
        "run_status": manifest.get("run_status", "complete"),
        "planned_case_trials": manifest.get("planned_case_trials", len(results)),
        "recorded_case_trials": len(results),
        "provider_error_trials": sum(row.get("status") == "provider_error" for row in results),
        "circuit_open_skipped_trials": sum(
            row.get("status") == "skipped_circuit_open" for row in results
        ),
        "correction_error_trials": sum(bool(row.get("correction_error")) for row in results),
        "groups": groups,
    }


def _pairwise_summary(quality: dict[str, Any] | None) -> dict[str, Any]:
    status = "not_run" if quality is None else quality.get("_report_status", "available")
    return {
        "status": status,
        "judge": None if quality is None else quality.get("judge"),
        "pairs_available": 0 if quality is None else quality.get("pairs_available", 0),
        "pairs_judged": 0 if quality is None else quality.get("pairs_judged", 0),
        "position_consistency": None if quality is None else quality.get("position_consistency"),
    }


def summarize(manifest: dict[str, Any], artifact_root: Path | None = None) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in manifest["results"]:
        grouped[(row["provider"], row["model"], row["prompt_version"])].append(row)

    quality = _load_quality_summary(artifact_root)
    utility_groups: list[dict[str, Any]] = []
    security_groups: list[dict[str, Any]] = []
    editorial_groups: list[dict[str, Any]] = []
    operation_groups: list[dict[str, Any]] = []
    for key, rows in sorted(grouped.items()):
        identity = _group_identity(key)
        utility_rows = [row for row in rows if row["case_kind"] == "utility"]
        completed_utility = _completed(utility_rows)
        utility_groups.append(_utility_summary(identity, utility_rows, completed_utility))
        security_groups.append(_security_summary(identity, rows, manifest))
        editorial_groups.append(
            _editorial_summary(identity, key, utility_rows, completed_utility, quality)
        )
        operation_groups.append(_operation_summary(identity, rows))

    checker_capability = _checker_capability_summary(manifest.get("deterministic_summary"))
    operations = _operations_summary(manifest, operation_groups)
    pairwise_summary = _pairwise_summary(quality)
    return {
        "schema_version": 9,
        "generated_at": datetime.now(UTC).isoformat(),
        "generation_path": manifest.get("generation_path", "markdown"),
        "generation_controls": manifest.get("generation_controls", []),
        "score_families": {
            "checker_capability": checker_capability,
            "application_utility": {
                "scope": "Completed utility case-trials only.",
                "groups": utility_groups,
            },
            "security_robustness": {
                "scope": (
                    "Completed primary attack case-trials only; position/count ablation replicates are "
                    "excluded from headline, behavior, and technique denominators and reported separately. "
                    "Robustness is one minus targeted attack success. "
                    "utility_under_attack reports whether the underlying task was still completed despite "
                    "the attack (AgentDojo's second metric), independent of whether the attacker's specific "
                    "goal also occurred — it is a structural-validity-and-non-empty-output floor, not parity "
                    "with application_utility's stricter end_to_end_success_final, so the two are not "
                    "directly comparable and this report does not subtract one from the other."
                ),
                "groups": security_groups,
            },
            "editorial_quality": {
                "scope": "Generated topics and propositions from completed utility case-trials only.",
                "grounding_measure": manifest["grounding_measure"],
                "pairwise_judging": pairwise_summary,
                "groups": editorial_groups,
            },
        },
        "operations": operations,
    }


def _pct(metric: dict[str, Any]) -> str:
    if metric["rate"] is None:
        return "n/a"
    low, high = metric["ci95_wilson"]
    return f"{metric['rate'] * 100:.1f}% ({low * 100:.1f}–{high * 100:.1f}%; {metric['successes']}/{metric['trials']})"


def _render_group_label(group: dict[str, Any]) -> str:
    return f"{group['provider']} / {group['model']} / {group['prompt_version']}"


def _pairwise_overall(group: dict[str, Any]) -> str:
    quality = group["pairwise_prose_quality"]
    for axis in quality["axes"]:
        if axis["axis"] == "overall":
            return _pct(axis["win_rate_excluding_ties"])
    return "n/a"


def _is_baseline(group: dict[str, Any]) -> bool:
    """Whether a report group is an offline reference strategy, not a live model.

    Baseline rows are real trial data (see evaluator/adapters.py's
    BaselineAdapter), so they belong in score_families/summarize's output.
    This predicate only controls how markdown_report presents them: separated
    from cross-model tables so a reader cannot mistake a zero-cost floor or
    positive control for a live-model result.
    """
    return group["provider"] == "baseline"


def _partition_baseline(groups: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    live = [group for group in groups if not _is_baseline(group)]
    baseline = [group for group in groups if _is_baseline(group)]
    return live, baseline


def _utility_row(group: dict[str, Any]) -> str:
    return (
        f"| {_render_group_label(group)} | "
        f"{_pct(group['end_to_end_success_first'])} → {_pct(group['end_to_end_success_final'])} | "
        f"{_pct(group['first_pass_contract_success'])} → {_pct(group['final_contract_success'])} | "
        f"{_pct(group['routing_success_first'])} → {_pct(group['routing_success_final'])} | "
        f"{_pct(group['correction_success'])} | {_pct(group['over_refusal_success_final'])} | "
        f"{_pct(group['degraded_source_health_reporting_success_final'])} | "
        f"{group['completed_case_trials']}/{group['case_trials']} |"
    )


_UTILITY_HEADER = [
    "| Provider / model / prompt | End-to-end (first → final) | Contract (first → final) | "
    "Routing (first → final) | Correction success | Over-refusal success | "
    "Degraded-source health reporting | "
    "Completed utility trials |",
    "|---|---:|---:|---:|---:|---:|---:|---:|",
]


def _security_row(group: dict[str, Any]) -> str:
    return (
        f"| {_render_group_label(group)} | "
        f"{_pct(group['robustness_first'])} → {_pct(group['robustness_final'])} | "
        f"{_pct(group['attack_success_first'])} → {_pct(group['attack_success_final'])} | "
        f"{_pct(group['utility_under_attack_first'])} → {_pct(group['utility_under_attack_final'])} | "
        f"{_pct(group['attack_recovery_success'])} | "
        f"{group['completed_case_trials']}/{group['case_trials']} |"
    )


_SECURITY_HEADER = [
    "| Provider / model / prompt | Robustness (first → final) | "
    "Attack success (first → final) | Utility under attack (first → final) | "
    "Attack recovery | Completed primary attack trials |",
    "|---|---:|---:|---:|---:|---:|",
]


def _security_detail_lines(group: dict[str, Any]) -> list[str]:
    by_behavior = group.get("by_behavior", [])
    by_technique = group.get("by_technique", [])
    ablation = group.get("ablation", {})
    by_corpus_position = ablation.get("by_corpus_position", [])
    by_controlled_items = ablation.get("by_controlled_items", [])
    matched_pairs = group.get("matched_pairs", [])
    if not any((by_behavior, by_technique, by_corpus_position, by_controlled_items, matched_pairs)):
        return []

    lines = [
        "",
        f"### Security breakdown — {_render_group_label(group)}",
    ]
    if by_behavior:
        lines += [
            "",
            "| Behavior | Final attack success | Final robustness | Completed trials |",
            "|---|---:|---:|---:|",
        ]
        for row in by_behavior:
            lines.append(
                f"| {row['behavior']} | {_pct(row['attack_success_final'])} | "
                f"{_pct(row['robustness_final'])} | "
                f"{row['completed_case_trials']}/{row['case_trials']} |"
            )
    if by_technique:
        lines += [
            "",
            "| Attack technique | Final attack success | Final robustness | Completed trials |",
            "|---|---:|---:|---:|",
        ]
        for row in by_technique:
            lines.append(
                f"| {row['technique']} | {_pct(row['attack_success_final'])} | "
                f"{_pct(row['robustness_final'])} | "
                f"{row['completed_case_trials']}/{row['case_trials']} |"
            )
    if matched_pairs:
        lines += [
            "",
            "#### Matched clean/attack pairs",
            "",
            "| Case | Stage | Benign structural utility | Structural utility under attack | "
            "Targeted attack success | Completed pairs |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for row in matched_pairs:
            for stage in ("first", "final"):
                lines.append(
                    f"| {row['case_id']} | {stage} | "
                    f"{_pct(row[f'benign_structural_utility_{stage}'])} | "
                    f"{_pct(row[f'structural_utility_under_attack_{stage}'])} | "
                    f"{_pct(row[f'targeted_attack_success_{stage}'])} | "
                    f"{row['completed_pairs']}/{row['planned_pairs']} |"
                )
    if by_corpus_position or by_controlled_items:
        lines += [
            "",
            "#### Production-corpus ablation replicates",
            "",
            f"Completed replicate trials: {ablation.get('completed_case_trials', 0)}/"
            f"{ablation.get('case_trials', 0)}. These rows are excluded from the headline, "
            "behavior, and technique denominators above.",
            "",
            "Position means location within the serialized `dev_community` array, not merged "
            "eligible-pool rank or relative prompt-token position. The same selected carrier "
            "items retain their timestamps while being relocated, so recency selection stays "
            "constant across positions. Controlled item count means one versus three mutated "
            "items, not controlled token fraction.",
        ]
    if by_corpus_position:
        lines += [
            "",
            "#### Attack success by category-array position",
            "",
            "| Position | Final attack success | Final robustness | Completed trials |",
            "|---|---:|---:|---:|",
        ]
        for row in by_corpus_position:
            lines.append(
                f"| {row['corpus_position']} | {_pct(row['attack_success_final'])} | "
                f"{_pct(row['robustness_final'])} | "
                f"{row['completed_case_trials']}/{row['case_trials']} |"
            )
    if by_controlled_items:
        lines += [
            "",
            "#### Attack success by attacker-controlled item count",
            "",
            "| Controlled items | Final attack success | Final robustness | Completed trials |",
            "|---|---:|---:|---:|",
        ]
        for row in by_controlled_items:
            lines.append(
                f"| {row['controlled_items']} | {_pct(row['attack_success_final'])} | "
                f"{_pct(row['robustness_final'])} | "
                f"{row['completed_case_trials']}/{row['case_trials']} |"
            )
    return lines


def _editorial_row(group: dict[str, Any]) -> str:
    semantic = _pct(group["semantic_meaning_preservation"])
    unresolved = group["semantic_unreviewed_propositions"] + group["semantic_unclear_propositions"]
    if unresolved:
        semantic += f" ({unresolved} unresolved)"
    proxy = f"{_pct(group['grounding_error_topics_proxy_first'])} → {_pct(group['grounding_error_topics_proxy_final'])}"
    human_grounding = _pct(group["grounding_error_topics_human"])
    if group.get("human_grounding_unreviewed_topics"):
        human_grounding += f" ({group['human_grounding_unreviewed_topics']} unreviewed)"
    return (
        f"| {_render_group_label(group)} | {semantic} | "
        f"{human_grounding} | {proxy} | "
        f"{_pairwise_overall(group)} | "
        f"{group['completed_utility_case_trials']}/{group['utility_case_trials']} |"
    )


_EDITORIAL_HEADER = [
    "| Provider / model / prompt | Meaning preserved | Human grounding errors | "
    "Proxy grounding errors (first → final) | Pairwise overall win rate | "
    "Completed utility trials |",
    "|---|---:|---:|---:|---:|---:|",
]


def _operations_row(group: dict[str, Any]) -> str:
    latency = group["latency_first"]
    latency_text = (
        f"{latency['median_ms']:.0f} / {latency['p95_ms']:.0f} ms (n={latency['trials']})"
        if latency["median_ms"] is not None and latency["p95_ms"] is not None
        else "n/a"
    )
    total = group["cost"]["total_usd"]
    cost_text = f"${total:.4f}" if total is not None else "not reported"
    if group["cost"]["unreported_calls"]:
        cost_text += f" ({group['cost']['unreported_calls']} call(s) missing)"
    return (
        f"| {_render_group_label(group)} | "
        f"{group['completed_case_trials']}/{group['case_trials']} | "
        f"{group['provider_error_trials']} | {group['circuit_open_skipped_trials']} | "
        f"{group['correction_error_trials']} | {latency_text} | {cost_text} |"
    )


_OPERATIONS_HEADER = [
    "| Provider / model / prompt | Completed trials | Provider errors | Circuit skips | "
    "Correction errors | First latency median / p95 | Cost |",
    "|---|---:|---:|---:|---:|---:|---:|",
]


def _baseline_summary_callout(
    utility_baseline: list[dict[str, Any]], security_baseline: list[dict[str, Any]]
) -> list[str]:
    """A sentence pairing empty/echo's robustness against their utility, sourced from real numbers.

    This is the concrete artifact for the AgentDojo-derived posture
    evaluator/README.md already cites: robustness is meaningless unpaired
    with utility. Only speaks about models actually present in this run.
    Keyed by the full (provider, model, prompt_version) identity, not model
    name alone — a bare-model key would silently collide across prompt
    versions when more than one is compared in the same run.
    """
    identity_key = lambda group: (group["provider"], group["model"], group["prompt_version"])  # noqa: E731
    security_by_identity = {identity_key(group): group for group in security_baseline}
    utility_by_identity = {identity_key(group): group for group in utility_baseline}
    lines: list[str] = []
    for key, utility in sorted(utility_by_identity.items()):
        _provider, model, prompt_version = key
        if model not in {"empty", "echo"}:
            continue
        security = security_by_identity.get(key)
        if security is None:
            continue
        # Report the numbers rather than asserting a fixed characterization
        # ("far more robust than useful") that does not hold for every
        # baseline — echo's robustness and utility can land close together
        # with overlapping confidence intervals; let the reader compare.
        lines.append(
            f"- `{model}` ({prompt_version}): {_pct(security['robustness_final'])} robustness, "
            f"{_pct(utility['end_to_end_success_final'])} end-to-end utility, "
            f"{_pct(security['utility_under_attack_final'])} utility preserved under attack — "
            "robustness alone does not show whether the system is worth deploying."
        )
    return lines


def _generation_controls_lines(controls: list[dict[str, Any]]) -> list[str]:
    if not controls:
        return []
    lines = [
        "## Generation controls",
        "",
        "| Provider / model | Temperature | Seed | Reasoning | Reproducibility disclosure |",
        "|---|---:|---:|---:|---|",
    ]
    for control in controls:
        temperature = control["temperature"]
        seed = control["seed"]
        reasoning = control.get("reasoning_enabled")
        reasoning_effort = control.get("reasoning_effort")
        lines.append(
            f"| {control['provider']} / {control['model']} | "
            f"{'uncontrolled' if temperature is None else temperature} | "
            f"{'uncontrolled' if seed is None else seed} | "
            f"{'provider-default' if reasoning is None else reasoning}"
            f"{'' if reasoning_effort is None else f'/{reasoning_effort}'} | "
            f"{control['disclosure']} |"
        )
    lines.append("")
    return lines


def _checker_capability_lines(checker_family: dict[str, Any] | None) -> list[str]:
    if not checker_family:
        return []
    checker = checker_family["checker"]
    feed = checker_family["feed_parser"]
    provenance = checker_family.get("label_provenance") or {}
    lines = [
        "## Score family 1: Checker capability",
        "",
        f"Label review status: {provenance.get('review_status', 'not recorded')}",
        "",
        f"- Heuristic claim false-positive rate: "
        f"{_pct(checker_family['heuristic_claim_false_positive_rate'])}",
    ]
    per_check_rates = checker_family.get("heuristic_claim_false_positive_rates", {})
    if per_check_rates:
        lines += [
            "",
            "| Heuristic check | False positives / eligible negatives | Rate (95% Wilson CI) |",
            "|---|---:|---:|",
        ]
        for check in HEURISTIC_CLAIM_CHECKS:
            row = per_check_rates.get(check)
            if row is not None:
                lines.append(f"| `{check}` | {row['successes']}/{row['trials']} | {_pct(row)} |")
    if checker:
        lines += [
            f"- Checker precision: {_pct(checker['precision'])}",
            f"- Checker recall: {_pct(checker['recall'])}",
        ]
    else:
        lines.append("- Checker metrics: not present in this deterministic suite")
    if feed:
        lines += [
            f"- Feed-parser precision: {_pct(feed['precision'])}",
            f"- Feed-parser recall: {_pct(feed['recall'])}",
        ]
    else:
        lines.append("- Feed-parser metrics: not present in this deterministic suite")
    lines.append("")
    return lines


def _utility_section_lines(family: dict[str, Any], groups: list[dict[str, Any]]) -> list[str]:
    return [
        "## Score family 2: Application utility",
        "",
        family["scope"] + " Offline reference baselines are reported "
        "separately below, not in this cross-model table.",
        "",
        *_UTILITY_HEADER,
        *(_utility_row(group) for group in groups),
    ]


def _security_section_lines(family: dict[str, Any], groups: list[dict[str, Any]]) -> list[str]:
    lines = [
        "",
        "## Score family 3: Security robustness",
        "",
        family["scope"] + " Offline reference baselines are reported "
        "separately below, not in this cross-model table.",
        "",
        *_SECURITY_HEADER,
        *(_security_row(group) for group in groups),
    ]
    for group in groups:
        lines += _security_detail_lines(group)
    return lines


def _editorial_section_lines(family: dict[str, Any], groups: list[dict[str, Any]]) -> list[str]:
    pairwise = family["pairwise_judging"]
    return [
        "",
        "## Score family 4: Editorial quality",
        "",
        family["scope"] + " Offline reference baselines are reported "
        "separately below, not in this cross-model table.",
        "",
        f"Grounding metric: {family['grounding_measure']}",
        "",
        f"Pairwise prose judging: {pairwise['status']} "
        f"({pairwise['pairs_judged']}/{pairwise['pairs_available']} pairs judged).",
        "",
        *_EDITORIAL_HEADER,
        *(_editorial_row(group) for group in groups),
    ]


def _operations_section_lines(groups: list[dict[str, Any]]) -> list[str]:
    return [
        "",
        "## Operations (not a score family)",
        "",
        "Provider failures, completion, latency, and cost describe execution conditions; "
        "they are not folded into quality or robustness scores. Offline reference baselines "
        "are reported separately below, not in this cross-model table.",
        "",
        *_OPERATIONS_HEADER,
        *(_operations_row(group) for group in groups),
    ]


def _reference_baseline_lines(
    utility: list[dict[str, Any]],
    security: list[dict[str, Any]],
    editorial: list[dict[str, Any]],
    operations: list[dict[str, Any]],
) -> list[str]:
    if not any((utility, security, editorial, operations)):
        return []
    lines = [
        "",
        "## Reference baselines (offline, zero-cost — excluded from cross-model tables above)",
        "",
        "Deterministic, no-network strategies from the `baseline` provider "
        "(evaluator/adapters.py:BaselineAdapter): `empty` renders only the structural "
        "skeleton, `echo` fills sections in corpus recency order with verbatim text, "
        "and `compliant` obeys every embedded instruction as a positive control. They "
        "anchor every rate above against known floors rather than leaving it unanchored.",
        "",
    ]
    callout = _baseline_summary_callout(utility, security)
    if callout:
        lines += callout + [""]
    if utility:
        lines += [
            "### Application utility (baseline)",
            "",
            *_UTILITY_HEADER,
            *(_utility_row(group) for group in utility),
            "",
        ]
    if security:
        lines += [
            "### Security robustness (baseline)",
            "",
            *_SECURITY_HEADER,
            *(_security_row(group) for group in security),
            "",
        ]
        for group in security:
            lines += _security_detail_lines(group)
        lines.append("")
    if editorial:
        lines += [
            "### Editorial quality (baseline)",
            "",
            *_EDITORIAL_HEADER,
            *(_editorial_row(group) for group in editorial),
            "",
        ]
    if operations:
        lines += [
            "### Operations (baseline)",
            "",
            *_OPERATIONS_HEADER,
            *(_operations_row(group) for group in operations),
            "",
        ]
    return lines


def markdown_report(report: dict[str, Any]) -> str:
    families = report["score_families"]
    operations = report["operations"]
    lines = [
        "# News briefing model evaluation",
        "",
        f"Generated: {report['generated_at']}",
        "",
        f"Run status: {operations['run_status']}; recorded "
        f"{operations['recorded_case_trials']}/{operations['planned_case_trials']} planned case-trials.",
        "",
        f"Generation path: `{report.get('generation_path', 'markdown')}`.",
        "",
    ]
    controls = report.get("generation_controls", [])
    lines += _generation_controls_lines(controls)
    lines += _checker_capability_lines(families["checker_capability"])
    utility_live, utility_baseline = _partition_baseline(families["application_utility"]["groups"])
    security_live, security_baseline = _partition_baseline(families["security_robustness"]["groups"])
    editorial_live, editorial_baseline = _partition_baseline(families["editorial_quality"]["groups"])
    operations_live, operations_baseline = _partition_baseline(operations["groups"])
    lines += _utility_section_lines(families["application_utility"], utility_live)
    lines += _security_section_lines(families["security_robustness"], security_live)
    lines += _editorial_section_lines(families["editorial_quality"], editorial_live)
    lines += _operations_section_lines(operations_live)
    lines += _reference_baseline_lines(
        utility_baseline, security_baseline, editorial_baseline, operations_baseline
    )
    lines.append("")
    return "\n".join(lines)
