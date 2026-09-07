"""Blinded grounding packets from a production run directory.

`evaluator/grounding_review.py` builds its packets from an evaluator manifest
plus its case configs and corpus fixtures. This module builds the same
packet shape from one completed `agent_runner` run directory instead, so the
periodic monitoring workflow (see `docs/evaluation-methodology.md`) can send
already-published production topics through the existing
`evaluator/grounding_machine_review.py` judge machinery.

Every public packet field is built from `selected-evidence.json` and the
finalized structured candidate's `headline`/`summary` text -- both already
free of destinations and opaque `citation_`/`item_` handles because
`agent_runner.output.project_selected_evidence()` and `validate_output()`
enforce that before a run can reach a `ready` disposition. `redact_destinations()`
and `redact_opaque_references()` are still applied defensively, so the packet
builder itself proves the boundary rather than trusting an upstream run.

A structural repair (`agent_runner.output.repair_structural_output()`) can
drop an included topic from a section after its evidence was frozen, which
would shift every later position out of alignment with `selected-evidence.json`.
`production_run_topics()` detects that by comparing entry counts per section
and skips the whole run rather than risk pairing prose with the wrong
evidence; this is a disclosed limitation of the position-keyed design, not a
crash.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_runner.output import redact_destinations, redact_opaque_references

from evaluator.adapters import Adapter
from evaluator.grounding_machine_review import run_grounding_machine_review
from evaluator.grounding_review import double_sample, packet
from evaluator.judge_io import portable_path, sha256_bytes, write_json_atomic, write_text_atomic
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
    """Resolve one day's captured directory to its selected, ready run.

    A daily run directory holds `manifest.json` directly when a single
    provider produced the result, or a `fallback-log.json` naming the
    selected candidate subdirectory when `run_daily_briefing.py`'s model
    chain produced one. Returns ``None`` when no candidate reached a ready
    disposition, mirroring `triage_run.py`'s path-traversal guard on the
    recorded subdirectory name.
    """
    if (day_dir / "manifest.json").is_file():
        return day_dir
    chain = _load_json(day_dir / "fallback-log.json")
    if not isinstance(chain, dict) or chain.get("status") != "ready":
        return None
    selected = chain.get("selected_run_dir")
    if not isinstance(selected, str) or not selected or Path(selected).name != selected:
        return None
    candidate_dir = day_dir / selected
    return candidate_dir if (candidate_dir / "manifest.json").is_file() else None


def production_run_topics(run_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Extract blinded, position-verified grounding records from one ready run.

    Returns one record per published topic (never the accountability-log
    exclusions, matching `evaluator.grounding_review`'s human packets), shaped
    for `evaluator.grounding_review.packet()`. Returns an empty list for a
    run that is not a published `ready` result, or whose artifacts cannot be
    read, or whose finalized candidate's topic counts no longer match its
    frozen evidence.
    """
    manifest = _load_json(run_dir / "manifest.json")
    if not isinstance(manifest, dict) or manifest.get("status") != "complete":
        return []
    final = manifest.get("final")
    if not isinstance(final, dict) or final.get("status") != "ready":
        return []
    attempt_index = final.get("attempt")
    attempts = manifest.get("attempts")
    if type(attempt_index) is not int or not isinstance(attempts, list):
        return []
    matching = [
        row for row in attempts
        if isinstance(row, dict) and row.get("index") == attempt_index
    ]
    if len(matching) != 1:
        return []
    structured_name = matching[0].get("structured_artifact")
    if not isinstance(structured_name, str):
        return []
    candidate = _load_json(run_dir / structured_name)
    evidence_doc = _load_json(run_dir / "selected-evidence.json")
    if not isinstance(candidate, dict) or not isinstance(evidence_doc, dict):
        return []
    sections = candidate.get("sections")
    evidence_sections = evidence_doc.get("sections")
    if not isinstance(sections, dict) or not isinstance(evidence_sections, dict):
        return []

    records: list[dict[str, Any]] = []
    topic_index = 0
    for name in sorted(sections):
        section = sections[name]
        evidence_section = evidence_sections.get(name)
        topics = section.get("topics") if isinstance(section, dict) else None
        evidence_topics = (
            evidence_section.get("topics") if isinstance(evidence_section, dict) else None
        )
        if not isinstance(topics, list) or not isinstance(evidence_topics, list):
            return []
        if len(topics) != len(evidence_topics):
            # A structural repair dropped or reordered an entry after evidence
            # was frozen; positions can no longer be proven aligned.
            return []
        for entry, evidence_entry in zip(topics, evidence_topics, strict=True):
            if not isinstance(entry, dict) or not isinstance(evidence_entry, dict):
                return []
            topic_index += 1
            public = redact_opaque_references(
                redact_destinations({
                    "section": name,
                    "title": entry.get("headline"),
                    "prose": entry.get("summary"),
                    "evidence": evidence_entry.get("evidence"),
                }),
                include_citations=True,
            )
            if not isinstance(public, dict):
                return []
            records.append({
                "artifact_dir": run_id,
                "topic_index": topic_index,
                "stratum": name,
                "private": {},
                "public": public,
            })
    return records


@dataclass(frozen=True)
class ProductionRun:
    run_id: str
    run_dir: Path


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
    for run in runs:
        topics = production_run_topics(run.run_dir, run.run_id)
        if not topics:
            skipped.append(run.run_id)
            continue
        records.extend(topics)
        run_manifest = _load_json(run.run_dir / "manifest.json")
        provider_info = run_manifest.get("provider") if isinstance(run_manifest, dict) else None
        provider_name = provider_info.get("provider") if isinstance(provider_info, dict) else None
        model_name = provider_info.get("model") if isinstance(provider_info, dict) else None
        manifest_results.append({
            "artifact_dir": run.run_id,
            "provider": provider_name if isinstance(provider_name, str) else "unknown",
            "model": model_name if isinstance(model_name, str) else "unknown",
            "prompt_version": "production",
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
    """Insert one week's row, replacing a prior row for the same week (a rerun)."""
    text = path.read_text(encoding="utf-8") if path.is_file() else WEEKLY_LOG_HEADER
    marker = f"| {week_label} |"
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.startswith(marker):
            lines[index] = row
            break
    else:
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
) -> dict[str, Any]:
    """Build packets, run the judge, and publish one week's aggregate row.

    Returns aggregate counts only. Per-topic verdicts and rationale live in
    `review_output_dir/machine-grounding-review.json`, a private artifact this
    function never prints or writes into the committed log.
    """
    resolved = [
        (run.run_id, resolve_ready_run_dir(run.run_dir)) for run in runs
    ]
    unready = sorted(run_id for run_id, run_dir in resolved if run_dir is None)
    ready = [
        ProductionRun(run_id, run_dir)
        for run_id, run_dir in resolved
        if run_dir is not None
    ]
    packets = export_production_grounding_packets(
        ready, packet_dir, seed=seed, double_fraction=double_fraction
    )
    all_skipped = sorted(set(unready) | set(packets["skipped_runs"]))
    measured = len(ready) - len(packets["skipped_runs"])

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
        "topic_count": packets["topic_count"],
        "grounding_rate": grounding,
        "audit_agreement": audit_agreement,
        "observed_cost_usd": cost_usd,
        "log_path": portable_path(log_path),
    }
