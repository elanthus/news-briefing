#!/usr/bin/env python3
"""Replay Jev and bounded repairs on frozen partial-day briefings into a separate tree.

The protocol fixes the cohort and baseline metric digest before provider calls.
Generation is reused, never repeated. Existing output trees are refused so an
interrupted paid replay cannot silently run again. Only counts, costs, dates,
revision identifiers and artifact digests are written to the public result.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_runner.audit_metrics import aggregate, load_days
from agent_runner.checkpoint import write_json_atomic
from agent_runner.jev_review import MAX_CALLS, load_verified_run
from agent_runner.semantic_repairs import daily_semantic_review
from run_jev_stage import ROOT, DayFailure, _git_commit, _read_json, _sha256


def clean_commit() -> str:
    """Refuse to identify modified or untracked execution code as the current commit."""
    status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=ROOT,
                            text=True, capture_output=True, check=False)
    commit = _git_commit()
    if status.returncode != 0 or status.stdout.strip() or commit is None:
        raise ValueError("replay requires a clean committed worktree")
    return commit


def tree_digest(root: Path) -> str:
    """Hash relative filenames and bytes without following links or special files."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("replay input contains a link or special file")
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode() + b"\0")
            content = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1_000_000):
                    content.update(chunk)
            digest.update(content.digest())
    return digest.hexdigest()


def replay(source_root: Path, baseline_path: Path, protocol_path: Path, output_root: Path,
           output_path: Path, *, judge: Any = None, repair_provider: Any = None,
           code_commit: str | None = None) -> dict[str, Any]:
    """Verify the frozen cohort, copy its ready inputs, review, and verify the original tree again."""
    source_root, output_root = source_root.resolve(), output_root.resolve()
    if (source_root.is_relative_to(output_root) or output_root.is_relative_to(source_root)
            or output_path.resolve().is_relative_to(source_root)
            or output_path.resolve().is_relative_to(output_root)):
        raise ValueError("baseline, replay tree and public output must be separate")
    if output_root.exists() or output_path.exists():
        raise ValueError("replay tree and public output must be new; inspect an interrupted run before retrying")
    protocol_digest = _sha256(protocol_path)
    protocol = _read_json(protocol_path)
    baseline_digest = _sha256(baseline_path)
    if protocol["baseline_metrics_sha256"] != baseline_digest or protocol["review_call_limit"] != MAX_CALLS:
        raise ValueError("baseline digest or review call limit differs from the frozen protocol")
    baseline = _read_json(baseline_path)
    frame = baseline["sampling_frame"]
    start, end = date.fromisoformat(frame["start"]), date.fromisoformat(frame["end"])
    expected = {row["report_date"]: row for row in baseline["days"]}
    dates = sorted(day for day, row in expected.items() if row["status"] == "partial")
    if not dates or dates != protocol["sample_dates"]:
        raise ValueError("protocol cohort must equal the frozen baseline's partial days")
    original = load_days(source_root, start=start, end=end)
    if (len(expected) != len(baseline["days"]) or set(expected) != {r["report_date"] for r in original}
            or any((row["audit_sha256"], row["status"]) !=
                   (expected[row["report_date"]]["audit_sha256"], expected[row["report_date"]]["status"])
                   for row in original)):
        raise ValueError("baseline artifacts differ from the frozen metrics")
    selected = [row for row in original if row["report_date"] in dates]
    before = {row["report_date"]: tree_digest(source_root / row["report_date"]) for row in selected}
    output_root.mkdir(parents=True)
    ledger = []
    started = datetime.now(UTC).isoformat()
    for row in selected:
        day = row["report_date"]
        run, review = output_root / day / "run", output_root / day / "review"
        source = row["run"]
        source_digest = tree_digest(source)
        run.parent.mkdir()
        shutil.copytree(source, run)
        if tree_digest(run) != source_digest or load_verified_run(run).hashes != load_verified_run(source).hashes:
            raise ValueError("copied ready run differs from its frozen source")
        audit = daily_semantic_review(run, review, judge=judge, repair_provider=repair_provider)
        phases = {}
        for phase, relative in (("initial", "report.json"), ("followup", "post-review/report.json")):
            if (review / relative).exists():
                report = _read_json(review / relative)
                stop = report.get("stop_code")
                if stop not in (None, "budget", "deadline", "unknown_billing", "provider_failure", "invalid_result"):
                    raise ValueError("unrecognized review stop code")
                phases[phase] = {"calls": len(report["calls"]), "planned_checks": report["planned_checks"],
                                 "returned_checks": len(report["results"]), "stop_code": stop,
                                 "reached_call_limit": len(report["calls"]) >= MAX_CALLS}
        ledger.append({"report_date": day, "source_day_sha256": before[day],
                       "baseline_audit_sha256": row["audit_sha256"], "copied_run_sha256": source_digest,
                       "replay_audit_sha256": _sha256(review / "audit.json"),
                       "review_tree_sha256": tree_digest(review), "review_counts": phases})
        print(f"{day}: initial {audit['status']}; follow-up {audit['post_status']}; "
              f"reported USD {audit['reported_cost_usd']:.6f}", flush=True)
    metrics = aggregate(output_root, start=start, end=end, sample_dates=dates)
    if (_sha256(baseline_path) != baseline_digest or _sha256(protocol_path) != protocol_digest
            or any(tree_digest(source_root / day) != before[day] for day in dates)):
        raise ValueError("baseline changed during replay; results were not published")
    result = {"schema_version": 1, "protocol_id": protocol["protocol_id"], "code_commit": code_commit,
              "started_at": started, "completed_at": datetime.now(UTC).isoformat(),
              "baseline_metrics_sha256": baseline_digest, "protocol_sha256": protocol_digest,
              "review_call_limit": MAX_CALLS, "generation_calls": 0, "original_artifacts_unchanged": True,
              "reported_cost_usd": sum(c["reported_cost_usd"] for row in metrics["days"] for c in row["costs"]),
              "unknown_cost_calls": sum(c["unknown_cost_calls"] for row in metrics["days"] for c in row["costs"]),
              "replay_ledger": ledger, "metrics": metrics}
    write_json_atomic(output_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--baseline-metrics", type=Path,
                        default=ROOT / "docs/results/jev-stage-v1-metrics.json")
    parser.add_argument("--protocol", type=Path,
                        default=ROOT / "evaluator/protocols/jev-stage-v1-partial-supplement.json")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("missing OPENROUTER_API_KEY")
    try:
        revision = clean_commit()
        result = replay(args.source_root, args.baseline_metrics, args.protocol, args.output_root, args.output,
                        code_commit=revision)
    except (OSError, ValueError, KeyError, TypeError, DayFailure) as exc:
        parser.error(str(exc))
    print(f"replay complete; coverage {result['metrics']['coverage']['successes']}/"
          f"{result['metrics']['coverage']['trials']}; reported USD {result['reported_cost_usd']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
