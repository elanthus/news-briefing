"""Aggregate verified schema-3 daily audits without consuming private review fields.

Run ``python3 -S -m agent_runner.audit_metrics ROOT --start YYYY-MM-DD --end YYYY-MM-DD --output metrics.json``.
ROOT contains YYYY-MM-DD/{run,review} (or jev-review); run may be a daily
fallback chain. Missing reviews stay in the coverage frame. Invalid audits abort.
Only the existing verification boundary reads private binding artifacts; metric
calculation consumes the public projection and parsed integrity record alone.
"""

from __future__ import annotations

import argparse
import math
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import write_json_atomic
from agent_runner.jev_review import _read_json
from agent_runner.positions import StoryPosition
from agent_runner.semantic_repairs import load_public_audit
from publication_schema import SEMANTIC_CHECKS, parse_integrity

MAX_DAYS = 3660
BOOTSTRAP_SAMPLES = 10_000
BOOTSTRAP_SEED = 1729


def rate(successes: int, trials: int) -> dict[str, Any]:
    """Two-sided 95% Wilson interval, identical to evaluator.metrics.wilson_interval."""
    interval = None
    if trials:
        p, z = successes / trials, 1.959963984540054
        z2 = z * z
        denominator = 1 + z2 / trials
        center = (p + z2 / (2 * trials)) / denominator
        margin = z * math.sqrt((p * (1 - p) + z2 / (4 * trials)) / trials) / denominator
        interval = [max(0.0, center - margin), min(1.0, center + margin)]
    return {"successes": successes, "trials": trials,
            "rate": successes / trials if trials else None, "ci95_wilson": interval}


def clustered_rate(per_day: list[tuple[int, int]], *, samples: int = BOOTSTRAP_SAMPLES,
                   seed: int = BOOTSTRAP_SEED) -> dict[str, Any]:
    """Pooled rate with a Wilson interval and a day-clustered percentile bootstrap interval.

    Topics and checks within a day are not independent, so the bootstrap
    resamples whole days with replacement and recomputes the pooled ratio. The
    draw order is fixed by ``seed`` so a committed aggregate reproduces. A resample
    whose denominator is zero is dropped and counted; the interval is null when no
    resample has a denominator or there are no contributing days.
    """
    result = rate(sum(n for n, _ in per_day), sum(d for _, d in per_day))
    days = [(n, d) for n, d in per_day if d]
    ratios: list[float] = []
    dropped = 0
    if days:
        rng = random.Random(seed)
        for _ in range(samples):
            drawn = [rng.choice(days) for _ in days]
            denominator = sum(d for _, d in drawn)
            if denominator:
                ratios.append(sum(n for n, _ in drawn) / denominator)
            else:
                dropped += 1
    ratios.sort()
    def percentile(fraction: float) -> float:
        return ratios[min(len(ratios) - 1, max(0, math.ceil(fraction * len(ratios)) - 1))]
    result["ci95_day_cluster_bootstrap"] = [percentile(0.025), percentile(0.975)] if ratios else None
    result["bootstrap"] = {"contributing_days": len(days), "samples": samples, "seed": seed,
                           "dropped_zero_denominator_resamples": dropped}
    return result


def _within(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("daily audit path escapes its day directory")
    return resolved


def selected_run(day: Path) -> Path:
    """Resolve only a contained daily-chain selection, never a metadata path."""
    run = _within(day / "run", day)
    log = run / "fallback-log.json"
    if log.exists():
        value, _ = _read_json(_within(log, day))
        selected = value.get("selected_run_dir") if isinstance(value, dict) else None
        if not isinstance(selected, str) or not selected or Path(selected).is_absolute():
            raise ValueError("daily chain has no contained selected run")
        run = _within(run / selected, day)
    return run


def load_days(root: Path, *, start: date, end: date) -> list[dict[str, Any]]:
    """Validate an explicit sampling frame and every envelope before returning rows."""
    span = (end - start).days + 1
    if not 1 <= span <= MAX_DAYS:
        raise ValueError("invalid or excessive daily audit frame")
    directories: dict[str, Path] = {}
    for child in root.iterdir():
        if not child.is_dir() or date.fromisoformat(child.name).isoformat() != child.name:
            raise ValueError("audit root must contain only dated day directories")
        if not start <= date.fromisoformat(child.name) <= end:
            raise ValueError("day directory is outside the declared sampling frame")
        directories[child.name] = _within(child, root)
    days: list[dict[str, Any]] = []
    seen_audits: set[str] = set()
    for offset in range(span):
        report_date = (start + timedelta(days=offset)).isoformat()
        day = directories.get(report_date)
        if day is None:
            days.append({"report_date": report_date, "status": "missing_day", "audit": None,
                         "integrity": None, "review": None, "run": None, "audit_sha256": None})
            continue
        reviews = [day / name for name in ("review", "jev-review") if (day / name).exists()]
        if len(reviews) > 1:
            raise ValueError("ambiguous daily review directories")
        if not reviews or not (reviews[0] / "audit.json").exists():
            status = "missing_review"
            log = day / "run" / "fallback-log.json"
            if log.exists():
                chain, _ = _read_json(_within(log, day))
                if (isinstance(chain, dict) and chain.get("status") == "failed"
                        and chain.get("selected_run_dir") is None):
                    status = "generation_failure"
            days.append({"report_date": report_date, "status": status, "audit": None,
                         "integrity": None, "review": None, "run": None, "audit_sha256": None})
            continue
        review = _within(reviews[0], day)
        envelope, digest = _read_json(_within(review / "audit.json", day))
        if digest in seen_audits:
            raise ValueError("duplicate daily audit in the sampling frame")
        seen_audits.add(digest)
        run = selected_run(day)
        audit, _ = load_public_audit(run, review, expected_report_date=report_date)
        if audit is None:
            raise ValueError(f"daily audit verification or corpus date binding failed for {report_date}")
        integrity = parse_integrity(envelope["integrity"], semantic_audit=audit, disposition="ready")
        if integrity is None:
            raise ValueError("daily audit has no verified integrity")
        status = (audit["status"] if audit["status"] != "complete" else
                  audit["post_status"] if audit["post_status"] in {"partial", "failed"} else "complete")
        days.append({"report_date": report_date, "status": status, "audit": audit, "integrity": integrity,
                     "review": review, "run": run, "audit_sha256": digest})
    return days


def flagged(check: dict[str, Any], audit: dict[str, Any]) -> bool:
    return bool(check["probability"] >= (audit["citation_threshold"] if check["check"] == "irrelevant_citation"
                                         else audit["threshold"]))


def observations(audit: dict[str, Any], integrity: dict[str, Any]) -> dict[str, Any]:
    """Counts for one day, including incomplete days; no conditioned rates here."""
    flags: Counter[str] = Counter()
    checks: Counter[str] = Counter()
    confirmations: dict[str, Counter[str]] = {kind: Counter() for kind in sorted(SEMANTIC_CHECKS)}
    for check in audit["checks"]:
        kind = check["check"]
        checks[kind] += 1
        if flagged(check, audit):
            flags[kind] += 1
            confirmations[kind][check["confirmation_label"]] += 1
    targets = {StoryPosition.from_payload(t["position"]) for t in audit["topics"] if t["repair_status"] != "unchanged"}
    outcomes: Counter[str] = Counter()
    causes: dict[str, Counter[str]] = {}
    status_names = {"applied": "apply_repair", "rejected": "withhold_repair", "failed": "failed", "skipped": "skipped"}
    for topic in audit["topics"]:
        status = topic["repair_status"]
        if status == "unchanged":
            continue
        outcome, key = status_names[status], StoryPosition.from_payload(topic["position"])
        outcomes[outcome] += 1
        reasons = {reason for action in integrity["actions"]
                   if any(StoryPosition.from_payload(p) == key for p in action["positions"])
                   and action["action"] in {"selection_repair", "prose_repair", "withhold_repair", "apply_repair"}
                   and action["outcome"] in {"rejected", "failed", "skipped"}
                   for reason in action["reasons"]}
        causes.setdefault(outcome, Counter()).update(reasons)
    blockers: Counter[str] = Counter()
    blocked_slots: dict[str, set[StoryPosition]] = {kind: set() for kind in SEMANTIC_CHECKS}
    for check in audit.get("followup_checks", []):
        affected = targets & {StoryPosition.from_payload(p) for p in check["positions"]}
        if affected and flagged(check, audit):
            blockers[check["check"]] += 1
            blocked_slots[check["check"]].update(affected)
    budget_stop = any(integrity[field] is not None and integrity[field]["stop_reason"] == "budget"
                      for field in ("initial_review", "followup_review"))
    return {"checks": dict(checks), "flags": dict(flags),
            "confirmations": {k: dict(v) for k, v in confirmations.items()},
            "repair_targets": len(targets), "slot_outcomes": dict(outcomes),
            "slot_causes": {k: dict(v) for k, v in causes.items()},
            "followup_blockers": dict(blockers),
            "blocked_slots": {k: len(v) for k, v in blocked_slots.items()}, "estimated_spending_guard": budget_stop}


def aggregate(root: Path, *, start: date, end: date, sample_dates: list[str] | None = None) -> dict[str, Any]:
    rows = load_days(root, start=start, end=end)
    if sample_dates is not None:
        parsed = [date.fromisoformat(value) for value in sample_dates]
        if (not parsed or sample_dates != [d.isoformat() for d in sorted(set(parsed))]
                or any(not start <= day <= end for day in parsed)):
            raise ValueError("sample dates must be nonempty, distinct, ascending and inside the frame")
        if any(child.name not in sample_dates for child in root.iterdir()):
            raise ValueError("audit root contains a day outside the selected sample")
        rows = [row for row in rows if row["report_date"] in sample_dates]
    per_day = []
    for row in rows:
        audit, integrity = row["audit"], row["integrity"]
        per_day.append({"report_date": row["report_date"], "status": row["status"],
                        "audit_sha256": row["audit_sha256"],
                        "publication_outcome": integrity["decision"] if integrity else None,
                        "costs": integrity["costs"] if integrity else [],
                        "counts": observations(audit, integrity) if audit is not None else None})
    complete = [r for r in per_day if r["status"] == "complete"]
    counts = [r["counts"] for r in complete]
    def total(field: str, key: str) -> int:
        return sum(c[field].get(key, 0) for c in counts)
    # Check-, flag- and slot-level rates pool observations across days, so they carry a
    # day-clustered bootstrap interval beside the Wilson interval. Day-level rates do not.
    flag_rates, confirmation_rates = {}, {}
    for kind in sorted(SEMANTIC_CHECKS):
        flag_rates[kind] = clustered_rate([(c["flags"].get(kind, 0), c["checks"].get(kind, 0)) for c in counts])
        confirmation_rates[kind] = {
            label: clustered_rate([(c["confirmations"][kind].get(label, 0), c["flags"].get(kind, 0)) for c in counts])
            for label in ("confirmed", "disputed", "unconfirmed")}
    outcomes = {outcome: clustered_rate([(c["slot_outcomes"].get(outcome, 0), c["repair_targets"]) for c in counts])
                for outcome in ("apply_repair", "withhold_repair", "failed", "skipped")}
    causes = {outcome: {code: clustered_rate([(c["slot_causes"].get(outcome, {}).get(code, 0),
                                               c["slot_outcomes"].get(outcome, 0)) for c in counts])
                        for code in sorted({code for c in counts for code in c["slot_causes"].get(outcome, {})})}
              for outcome in outcomes}
    return {"schema_version": 1,
            "sampling_frame": {"start": start.isoformat(), "end": end.isoformat(), "planned_days": len(rows),
                               **({"sample_dates": sample_dates} if sample_dates is not None else {})},
            "coverage": rate(len(complete), len(rows)),
            "status_counts": dict(Counter(r["status"] for r in per_day)),
            "flag_rate_by_kind": flag_rates,
            "days_with_flags": rate(sum(bool(c["flags"]) for c in counts), len(complete)),
            "confirmation_rate_by_kind": confirmation_rates,
            "confirmation_rates": {label: clustered_rate([(sum(c["confirmations"][kind].get(label, 0)
                                                               for kind in SEMANTIC_CHECKS),
                                                           sum(c["flags"].values())) for c in counts])
                                   for label in ("confirmed", "disputed", "unconfirmed")},
            "repair_round_rate": rate(sum(c["repair_targets"] > 0 for c in counts), len(complete)),
            "per_slot_acceptance": outcomes, "slot_causes": causes,
            "publication_outcomes": {outcome: rate(sum(r["publication_outcome"] == outcome for r in complete),
                                                     len(complete))
                                     for outcome in ("original_retained", "repaired_applied", "partial_applied")},
            "followup_blockers_by_kind": {kind: total("followup_blockers", kind) for kind in sorted(SEMANTIC_CHECKS)},
            "blocked_slots_by_kind": {kind: total("blocked_slots", kind) for kind in sorted(SEMANTIC_CHECKS)},
            "days_stopped_by_estimated_spending_guard": sum(
                r["counts"] is not None and r["counts"]["estimated_spending_guard"] for r in per_day),
            "days": per_day}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = aggregate(args.root, start=args.start, end=args.end)
        write_json_atomic(args.output, result)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
