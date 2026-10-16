"""Aggregate verified schema-3 daily audits without consuming private review fields.

Run ``python3 -S -m agent_runner.audit_metrics ROOT --output metrics.json``.
ROOT contains YYYY-MM-DD/{run,review} (or jev-review); run may be a daily
fallback chain. Missing reviews stay in the coverage frame. Invalid audits abort.
Only the existing verification boundary reads private binding artifacts; metric
calculation consumes the public projection and parsed integrity record alone.
"""

from __future__ import annotations

import argparse
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import write_json_atomic
from agent_runner.integrity import position_key
from agent_runner.jev_review import _read_json
from agent_runner.semantic_repairs import load_public_audit
from publication_schema import SEMANTIC_CHECKS, parse_integrity

MAX_DAYS = 3660


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


def load_days(root: Path) -> list[dict[str, Any]]:
    """Validate every envelope before returning any rows; omit private fields."""
    days: list[dict[str, Any]] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            raise ValueError("audit root must contain only dated day directories")
        if len(days) >= MAX_DAYS or date.fromisoformat(child.name).isoformat() != child.name:
            raise ValueError("invalid or excessive daily audit frame")
        day = _within(child, root)
        reviews = [day / name for name in ("review", "jev-review") if (day / name).exists()]
        if len(reviews) > 1:
            raise ValueError("ambiguous daily review directories")
        if not reviews or not (reviews[0] / "audit.json").exists():
            # An absent review is visible coverage loss, never an implicit success.
            status = "missing_review"
            log = day / "run" / "fallback-log.json"
            if log.exists():
                chain, _ = _read_json(_within(log, day))
                if (isinstance(chain, dict) and chain.get("status") == "failed"
                        and chain.get("selected_run_dir") is None):
                    status = "generation_failure"
            days.append({"report_date": child.name, "status": status, "audit": None,
                         "integrity": None, "review": None, "run": None, "audit_sha256": None})
            continue
        review = _within(reviews[0], day)
        envelope, digest = _read_json(_within(review / "audit.json", day))
        run = selected_run(day)
        audit, _ = load_public_audit(run, review)
        if audit is None:
            raise ValueError(f"daily audit verification failed for {child.name}")
        integrity = parse_integrity(envelope["integrity"], semantic_audit=audit, disposition="ready")
        if integrity is None:
            raise ValueError("daily audit has no verified integrity")
        status = (audit["status"] if audit["status"] != "complete" else
                  audit["post_status"] if audit["post_status"] in {"partial", "failed"} else "complete")
        days.append({"report_date": child.name, "status": status, "audit": audit, "integrity": integrity,
                     "review": review, "run": run, "audit_sha256": digest})
    if not days:
        raise ValueError("daily audit frame is empty")
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
    targets = {position_key(t["position"]) for t in audit["topics"] if t["repair_status"] != "unchanged"}
    outcomes: Counter[str] = Counter()
    causes: dict[str, Counter[str]] = {}
    status_names = {"applied": "apply_repair", "rejected": "withhold_repair", "failed": "failed", "skipped": "skipped"}
    for topic in audit["topics"]:
        status = topic["repair_status"]
        if status == "unchanged":
            continue
        outcome, key = status_names[status], position_key(topic["position"])
        outcomes[outcome] += 1
        reasons = {reason for action in integrity["actions"]
                   if any(position_key(p) == key for p in action["positions"])
                   and action["action"] in {"selection_repair", "prose_repair", "withhold_repair", "apply_repair"}
                   and action["outcome"] in {"rejected", "failed", "skipped"}
                   for reason in action["reasons"]}
        causes.setdefault(outcome, Counter()).update(reasons)
    blockers: Counter[str] = Counter()
    blocked_slots: dict[str, set[str]] = {kind: set() for kind in SEMANTIC_CHECKS}
    for check in audit.get("followup_checks", []):
        affected = targets & {position_key(p) for p in check["positions"]}
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


def aggregate(root: Path) -> dict[str, Any]:
    rows = load_days(root)
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
    flag_rates, confirmation_rates = {}, {}
    for kind in sorted(SEMANTIC_CHECKS):
        flags = total("flags", kind)
        flag_rates[kind] = rate(flags, total("checks", kind))
        confirmation_rates[kind] = {label: rate(sum(c["confirmations"][kind].get(label, 0) for c in counts), flags)
                                    for label in ("confirmed", "disputed", "unconfirmed")}
    targets = sum(c["repair_targets"] for c in counts)
    outcomes = {outcome: rate(total("slot_outcomes", outcome), targets)
                for outcome in ("apply_repair", "withhold_repair", "failed", "skipped")}
    causes = {outcome: {code: rate(sum(c["slot_causes"].get(outcome, {}).get(code, 0) for c in counts),
                                 total("slot_outcomes", outcome))
                        for code in sorted({code for c in counts for code in c["slot_causes"].get(outcome, {})})}
              for outcome in outcomes}
    return {"schema_version": 1, "coverage": rate(len(complete), len(rows)),
            "status_counts": dict(Counter(r["status"] for r in per_day)),
            "flag_rate_by_kind": flag_rates,
            "days_with_flags": rate(sum(bool(c["flags"]) for c in counts), len(complete)),
            "confirmation_rate_by_kind": confirmation_rates,
            "confirmation_rates": {label: rate(sum(c["confirmations"][kind].get(label, 0)
                                                   for c in counts for kind in SEMANTIC_CHECKS),
                                               sum(sum(c["flags"].values()) for c in counts))
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
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = aggregate(args.root)
        write_json_atomic(args.output, result)
    except (OSError, ValueError, KeyError, TypeError, RecursionError, OverflowError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
