"""Blinded monitoring of verified, deployed production artifacts.

Shared publication resolution includes accepted semantic repairs. Frozen evidence,
references, corpus, candidate and Markdown hashes are verified before sampling.
Legacy diagnostics require separately verified live-history and deployment proof.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import sha256_bytes, write_json_atomic, write_text_atomic
from agent_runner.output import redact_destinations, redact_opaque_references
from agent_runner.publication import read_json, resolve_publication_run, verified_publication_artifacts

from evaluator.adapters import Adapter
from evaluator.grounding_machine_review import run_grounding_machine_review
from evaluator.grounding_review import double_sample, packet
from evaluator.judge_io import portable_path
from evaluator.metrics import rate

RUBRIC: dict[str, str] = {
    "grounding_error_true": (
        "The topic lacks supporting evidence, cites evidence that does not support a material "
        "claim, or adds, reverses, or strengthens a claim beyond the supplied feed evidence."
    ),
    "grounding_error_false": (
        "Every material claim is supported by at least one supplied feed-evidence item; faithful "
        "paraphrase is allowed."
    ),
    "scope": (
        "Use only the supplied feed evidence. Do not use outside knowledge or infer facts from any "
        "implied source."
    ),
    "evidence_source": (
        "Each evidence item is the code-selected feed excerpt frozen before the prose was "
        "generated for this exact position; it carries no URL or internal reference handle."
    ),
}


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def resolve_ready_run_dir(day_dir: Path) -> Path | None:
    """Resolve the publication policy's ready generation (not proof of deployment)."""
    _audit, selected, _original = resolve_publication_run(day_dir)
    if selected is None:
        return None
    manifest = _load_json(selected / "manifest.json")
    if (not isinstance(manifest, dict) or manifest.get("status") != "complete"
            or not isinstance(manifest.get("final"), dict) or manifest["final"].get("status") != "ready"):
        return None
    return selected


@dataclass(frozen=True)
class ProductionRun:
    run_id: str
    run_dir: Path
    receipt_path: Path | None = None
    report_date: str | None = None


def verified_run_topics(run: ProductionRun) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, topics, hashes = verified_publication_artifacts(run.run_dir)
    receipt = read_json(run.receipt_path or run.run_dir / "publication-receipt.json")
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
            or receipt.get("deployment_status") != "success"
            or type(receipt.get("workflow_run_id")) is not int or receipt["workflow_run_id"] <= 0
            or type(receipt.get("run_attempt")) is not int or receipt["run_attempt"] <= 0
            or not isinstance(receipt.get("reports"), list)):
        raise ValueError("missing or invalid successful deployment receipt")
    deployed_at = receipt.get("deployment_completed_at")
    if receipt.get("actions_deployment_verified") is not True or not isinstance(deployed_at, str):
        raise ValueError("receipt has no independently verified Actions deployment proof")
    deployment_time = datetime.fromisoformat(deployed_at.replace("Z", "+00:00"))
    if deployment_time.utcoffset() is None:
        raise ValueError("deployment completion timestamp has no timezone")
    report_date = date.fromisoformat(run.report_date or run.run_dir.name).isoformat()
    matches = [row for row in receipt["reports"] if isinstance(row, dict) and row.get("date") == report_date]
    if len(matches) != 1:
        raise ValueError("publication receipt does not identify this report exactly once")
    proof = matches[0]
    if (proof.get("artifact_hashes") != hashes
            or proof.get("selected_run") != selected.relative_to(run.run_dir).as_posix()):
        raise ValueError("published artifact identity differs from verified diagnostics")
    records: list[dict[str, Any]] = []
    for topic in topics:
        if not topic["included"]:
            continue
        public = redact_opaque_references(redact_destinations({
            "section": topic["position"]["section"], "title": topic["headline"],
            "prose": topic["prose"], "evidence": topic["evidence"],
        }), include_citations=True)
        records.append({"artifact_dir": run.run_id, "topic_index": len(records) + 1,
                        "stratum": topic["position"]["section"], "private": {}, "public": public})
    return records, {"report_date": report_date, "workflow_run_id": receipt["workflow_run_id"],
                     "run_attempt": receipt["run_attempt"], "artifact_hashes": hashes,
                     "deployment_completed_at": deployment_time.isoformat(),
                     "selected_run": selected.relative_to(run.run_dir).as_posix(),
                     "evidence_source": receipt.get("evidence_source", "post_deployment_receipt")}


def production_run_topics(run_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Return only verified published included topics; invalid diagnostics yield no topics."""
    try:
        return verified_run_topics(ProductionRun(run_id, run_dir))[0]
    except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError, StopIteration):
        return []


def week_window(week_label: str) -> tuple[date, date]:
    """The label identifies an ISO Monday-through-Sunday report-date window."""
    if re.fullmatch(r"[0-9]{4}-W[0-9]{2}", week_label) is None:
        raise ValueError("week label must have the form YYYY-Www")
    year, week = week_label.split("-W")
    start = date.fromisocalendar(int(year), int(week), 1)
    return start, start + timedelta(days=7)


def sample_published_runs(
    runs: Sequence[ProductionRun], week_label: str,
) -> tuple[list[ProductionRun], list[dict[str, str]]]:
    """One latest successfully deployed version per report date in the requested ISO week."""
    start, end = week_window(week_label)
    excluded: list[dict[str, str]] = []
    by_day: dict[str, tuple[ProductionRun, dict[str, Any]]] = {}
    for run in runs:
        try:
            _topics, identity = verified_run_topics(run)
            day = identity["report_date"]
            if not start <= date.fromisoformat(day) < end:
                excluded.append({"run_id": run.run_id, "reason": "outside_requested_report_week"})
                continue
            previous = by_day.get(day)
            if previous is not None:
                previous_order = (datetime.fromisoformat(previous[1]["deployment_completed_at"]),
                                  previous[1]["workflow_run_id"], previous[1]["run_attempt"])
                order = (datetime.fromisoformat(identity["deployment_completed_at"]),
                         identity["workflow_run_id"], identity["run_attempt"])
                if order == previous_order and identity["artifact_hashes"] != previous[1]["artifact_hashes"]:
                    raise ValueError("conflicting artifacts for the same deployment identity")
                if order <= previous_order:
                    excluded.append({"run_id": run.run_id, "reason": "duplicate_or_superseded_report_date"})
                    continue
                excluded.append({"run_id": previous[0].run_id, "reason": "duplicate_or_superseded_report_date"})
            by_day[day] = run, identity
        except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError, StopIteration) as exc:
            excluded.append({"run_id": run.run_id, "reason": str(exc) or type(exc).__name__})
    present = set(by_day)
    for offset in range(7):
        day = (start + timedelta(days=offset)).isoformat()
        if day not in present:
            excluded.append({"run_id": day, "reason": "no_verified_published_report_for_date"})
    return [by_day[day][0] for day in sorted(by_day)], excluded


def _weekly_exclusions(
    runs: Sequence[ProductionRun], exclusions: Sequence[dict[str, str]], start: date, end: date,
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, str]]]:
    """Separate known in-week sampling outcomes from outside or undated candidates."""
    report_dates: dict[str, date] = {}
    for run in runs:
        try:
            report_dates[run.run_id] = date.fromisoformat(run.report_date or run.run_dir.name)
        except (ValueError, TypeError):
            pass
    weekly, outside, undated = [], [], []
    for row in exclusions:
        day = report_dates.get(row["run_id"])
        if row["reason"] == "no_verified_published_report_for_date":
            day = date.fromisoformat(row["run_id"])
        if row["reason"] == "outside_requested_report_week" or (day is not None and not start <= day < end):
            outside.append(row)
        elif day is None:
            undated.append(row)
        else:
            weekly.append(row)
    return weekly, outside, undated


def export_production_grounding_packets(
    runs: Sequence[ProductionRun],
    output_dir: Path,
    *,
    seed: int = 8142026,
    double_fraction: float = 0.20,
) -> dict[str, Any]:
    """Export primary/double review packets sourced from production run directories.

    Writes the same `reviewer-primary.json` / `reviewer-double.json` /
    `review-map.json` triple as `evaluator.grounding_review.export_grounding_review_packets`,
    plus a synthetic `production-manifest.json` recording one row per measured
    run so `run_grounding_machine_review()` can run unmodified against it.
    """
    if not 0 < double_fraction <= 1:
        raise ValueError("double_fraction must be greater than zero and at most one")
    records: list[dict[str, Any]] = []
    manifest_results: list[dict[str, Any]] = []
    skipped: list[str] = []
    exclusions: list[dict[str, str]] = []
    for run in runs:
        try:
            topics, identity = verified_run_topics(run)
            if not topics:
                raise ValueError("published report contains no included topics")
        except (ValueError, OSError, KeyError, TypeError, RecursionError, OverflowError, StopIteration) as exc:
            skipped.append(run.run_id)
            exclusions.append({"run_id": run.run_id, "reason": str(exc) or type(exc).__name__})
            continue
        records.extend(topics)
        selected = resolve_ready_run_dir(run.run_dir)
        run_manifest = _load_json(selected / "manifest.json") if selected is not None else None
        provider_info = run_manifest.get("provider") if isinstance(run_manifest, dict) else None
        provider_name = provider_info.get("provider") if isinstance(provider_info, dict) else None
        model_name = provider_info.get("model") if isinstance(provider_info, dict) else None
        manifest_results.append({
            "artifact_dir": run.run_id,
            "provider": provider_name if isinstance(provider_name, str) else "unknown",
            "model": model_name if isinstance(model_name, str) else "unknown",
            "prompt_version": "production",
            "publication_identity": identity,
        })

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "production-manifest.json"
    write_json_atomic(manifest_path, {"schema_version": 1, "results": manifest_results})
    manifest_bytes = manifest_path.read_bytes()

    rng = random.Random(seed)
    double_count = max(1, round(len(records) * double_fraction)) if records else 0
    double_records = double_sample(records, double_count, rng)
    primary, primary_map = packet(records, "prodground", rng)
    secondary, secondary_map = packet(double_records, "proddouble", rng)

    packet_meta = {
        "schema_version": 1,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "rubric": RUBRIC,
    }
    write_json_atomic(output_dir / "reviewer-primary.json", {**packet_meta, "reviews": primary})
    write_json_atomic(output_dir / "reviewer-double.json", {**packet_meta, "reviews": secondary})
    write_json_atomic(output_dir / "review-map.json", {
        "schema_version": 1,
        "manifest": portable_path(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "seed": seed,
        "double_fraction": double_fraction,
        "primary": primary_map,
        "double": secondary_map,
    })
    return {
        "topic_count": len(records),
        "double_review_count": len(double_records),
        "skipped_runs": skipped,
        "exclusions": exclusions,
        "manifest_path": manifest_path,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "output_dir": portable_path(output_dir),
    }


def unverified_grounding_rate(review_result: dict[str, Any]) -> dict[str, Any]:
    """Invert the judge's primary error rate into a published 'grounded' rate.

    Only the primary pass (which reviews every topic) feeds the published
    rate; the audit pass exists to measure agreement, not to change the count.
    """
    errors = review_result["primary"]["grounding_errors"]
    trials = errors["trials"]
    grounded = trials - errors["successes"]
    return rate(grounded, trials)


WEEKLY_LOG_HEADER = (
    "# Weekly unverified machine grounding monitor\n\n"
    "Automated, non-gating measurement of already-published briefings. It never changes a day's "
    "publication decision. See "
    "[Evaluation methodology](../evaluation-methodology.md#unverified-machine-grounding-monitor) "
    "for what this rate can and cannot be used to claim; per-topic verdicts stay in the encrypted "
    "review artifact, not this log.\n\n"
    "| Week | Runs reviewed | Runs skipped | Topics reviewed | Unverified grounding rate (95% CI) | "
    "Audit agreement | Primary judge | Cost (USD) |\n"
    "|---|---:|---:|---:|---:|---:|---|---:|\n"
)


def render_weekly_log_row(
    *,
    week_label: str,
    runs_reviewed: int,
    runs_skipped: int,
    grounding_rate: dict[str, Any],
    audit_agreement: dict[str, Any] | None,
    primary_judge: str,
    cost_usd: float,
) -> str:
    """Render one Markdown table row for the committed weekly log."""
    rate_value = grounding_rate.get("rate")
    ci = grounding_rate.get("ci95_wilson")
    if rate_value is None or ci is None:
        rate_cell = "no topics reviewed"
    else:
        rate_cell = (
            f"{grounding_rate['successes']}/{grounding_rate['trials']}; "
            f"{rate_value * 100:.1f}% [{ci[0] * 100:.1f}, {ci[1] * 100:.1f}]"
        )
    agreement_value = audit_agreement.get("rate") if audit_agreement else None
    agreement_cell = "n/a" if agreement_value is None else f"{agreement_value * 100:.1f}%"
    return (
        f"| {week_label} | {runs_reviewed} | {runs_skipped} | {grounding_rate['trials']} | "
        f"{rate_cell} | {agreement_cell} | {primary_judge} | {cost_usd:.4f} |\n"
    )


def upsert_weekly_log(path: Path, week_label: str, row: str) -> str:
    """Append a measurement; retain historical rows and label repeated assessments."""
    text = path.read_text(encoding="utf-8") if path.is_file() else WEEKLY_LOG_HEADER
    marker = f"| {week_label} |"
    lines = text.splitlines(keepends=True)
    previous_count = sum(line.startswith(marker) or line.startswith(f"| {week_label} (assessment ") for line in lines)
    if previous_count:
        row = row.replace(marker, f"| {week_label} (assessment {previous_count + 1}) |", 1)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(row)
    updated = "".join(lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(path, updated)
    return updated


def run_weekly_monitor(
    runs: Sequence[ProductionRun],
    *,
    week_label: str,
    packet_dir: Path,
    review_output_dir: Path,
    log_path: Path,
    primary_judge: Adapter,
    audit_judge: Adapter,
    seed: int = 8142026,
    double_fraction: float = 0.20,
    batch_size: int = 25,
    cost_ceiling_usd: float = 7.0,
    cost_headroom_usd: float = 0.10,
    progress: Any = None,
    artifact_exclusions: Sequence[dict[str, str]] = (),
) -> dict[str, Any]:
    """Build packets, run the judge, and publish one week's aggregate row.

    Returns aggregate counts only. Per-topic verdicts and rationale live in
    `review_output_dir/machine-grounding-review.json`, a private artifact this
    function never prints or writes into the committed log.

    Skipped outcomes include rejected in-week candidates and missing report
    dates. Out-of-week and undated candidates, including artifact-scan exclusions
    with no verified report date, remain separate from that weekly count.
    Candidate IDs and missing-date sentinels count as separate outcomes, so the
    skipped count is not a distinct-date denominator and can exceed seven.
    """
    ready, exclusions = sample_published_runs(runs, week_label)
    if any(not isinstance(row, dict) or set(row) != {"run_id", "reason"}
           or any(not isinstance(value, str) for value in row.values()) for row in artifact_exclusions):
        raise ValueError("invalid artifact exclusion records")
    packets = export_production_grounding_packets(
        ready, packet_dir, seed=seed, double_fraction=double_fraction
    )
    start, end = week_window(week_label)
    weekly, outside, undated = _weekly_exclusions(runs, [*exclusions, *packets["exclusions"]], start, end)
    exclusions = [*exclusions, *artifact_exclusions, *packets["exclusions"]]
    all_skipped = sorted({row["run_id"] for row in weekly})
    measured = len(ready) - len(packets["skipped_runs"])
    exclusion_details = {
        "weekly_exclusions": weekly, "out_of_window_exclusions": outside,
        "undated_exclusions": undated, "artifact_exclusions": list(artifact_exclusions),
        "skipped_count_basis": "unique in-week candidate IDs plus missing-date sentinels; not distinct report dates",
    }
    write_json_atomic(packet_dir / "sampling.json", {
        "schema_version": 1, "week_label": week_label,
        "report_date_start_inclusive": start.isoformat(), "report_date_end_exclusive": end.isoformat(),
        "deduplication": "latest successful deployment completion per report date; workflow identity breaks ties",
        "included_runs": [run.run_id for run in ready], "exclusions": exclusions,
        "runs_skipped": all_skipped, **exclusion_details,
    })

    if packets["topic_count"] == 0:
        grounding = rate(0, 0)
        cost_usd = 0.0
        audit_agreement = None
    else:
        review_result = run_grounding_machine_review(
            packets["manifest_path"],
            packet_dir,
            primary_judge,
            audit_judge,
            review_output_dir,
            batch_size=batch_size,
            cost_ceiling_usd=cost_ceiling_usd,
            cost_headroom_usd=cost_headroom_usd,
            progress=progress,
        )
        grounding = unverified_grounding_rate(review_result)
        cost_usd = review_result["observed_cost_usd"]
        audit_agreement = review_result["audit"]["agreement_with_primary"]

    primary_label = f"{primary_judge.provider}/{primary_judge.model}"
    row = render_weekly_log_row(
        week_label=week_label,
        runs_reviewed=measured,
        runs_skipped=len(all_skipped),
        grounding_rate=grounding,
        audit_agreement=audit_agreement,
        primary_judge=primary_label,
        cost_usd=cost_usd,
    )
    upsert_weekly_log(log_path, week_label, row)
    return {
        "week_label": week_label,
        "runs_reviewed": measured,
        "runs_skipped": all_skipped,
        "exclusions": exclusions,
        **exclusion_details,
        "topic_count": packets["topic_count"],
        "grounding_rate": grounding,
        "audit_agreement": audit_agreement,
        "observed_cost_usd": cost_usd,
        "log_path": portable_path(log_path),
    }
