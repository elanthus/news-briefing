#!/usr/bin/env python3
"""Run the Jev-stage evaluation's restore and daily commands over a committed sample.

For each date in the sample file, the script restores that date's corpus from the
preserved encrypted wrappers into ``ROOT/DATE/corpora``, runs the daily fallback
chain with the review stage into ``ROOT/DATE/run``, ``ROOT/DATE/review`` and
``ROOT/DATE/briefing.md``, records the SHA-256 of every artifact the aggregator
will read in ``ROOT/DATE/jev-stage-day.json``, and prints cumulative reported
cost. ROOT holds only dated day directories; the merged restore manifest is
written beside it as ``ROOT-artifact-manifest.json``. Redirect stdout to a log
outside ROOT. Days that already have that record are skipped. A day whose fallback
chain reaches no ready candidate is recorded as a generation-failure row and the
run continues. Restore exit 4 (no eligible archive) records a missing_day row;
any other failure stops the script before the next day.

Credentials are read by the child commands from the environment and are never
printed. The script runs every child with ``python3 -S``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_runner.budget import DEFAULT_GENERATION_USD
from agent_runner.checkpoint import write_json_atomic
from agent_runner.hashing import sha256_file

ROOT = Path(__file__).resolve().parent
DAY_RECORD = "jev-stage-day.json"
DEFAULT_SAMPLE = ROOT / "docs" / "results" / "jev-stage-sample.json"
DEFAULT_ARTIFACT_DIR = ROOT / ".news-briefing" / "portfolio-preservation-2026-10-15" / "github"
REQUIRED_ENVIRONMENT = ("OPENROUTER_API_KEY", "CORPUS_ARCHIVE_PASSPHRASE")
MAX_JSON_BYTES = 20_000_000

Executor = Callable[[list[str]], int]


class DayFailure(RuntimeError):
    """A sampled day failed for a reason other than a generation-failure row."""


def _read_json(path: Path) -> Any:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_JSON_BYTES:
        raise DayFailure(f"missing, oversized, or non-regular JSON artifact: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def load_sample(path: Path) -> list[str]:
    sample = _read_json(path)
    dates = sample.get("sample_dates") if isinstance(sample, dict) else None
    if not isinstance(dates, list) or not dates or len(dates) > 366:
        raise ValueError("sample file must list between 1 and 366 sample_dates")
    parsed = [date.fromisoformat(str(value)).isoformat() for value in dates]
    if parsed != sorted(set(parsed)) or parsed != [str(value) for value in dates]:
        raise ValueError("sample_dates must be distinct, ascending ISO dates")
    return parsed


def _git_commit() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True, check=False)
    return result.stdout.strip() or None if result.returncode == 0 else None


def _default_executor(command: list[str]) -> int:
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def provenance_manifest(artifact_dir: Path, output: Path) -> int:
    """Write a restore manifest that joins preserved wrapper digests with their workflow provenance.

    The preservation ``manifest.json`` records each wrapper's id, name, digest and
    size; the raw ``inventory.json`` pages record the ``workflow_run`` the restore
    needs to prove main-repository provenance. Neither preserved file is modified.
    A corpus wrapper without an inventory entry is an error, never silently skipped.

    One wrapper is kept per creation day: the newest. A day with several daily
    runs has several wrappers, each archiving a fresh fetch of that day's corpus,
    and the restore fails closed when two wrappers disagree about a date. The
    newest run's corpus is the one the published briefing used and the one later
    wrappers carry forward, so it is the canonical copy; the dropped wrappers are
    listed in the manifest for the record.
    """
    manifest = _read_json(artifact_dir / "manifest.json")
    inventory = _read_json(artifact_dir / "inventory.json")
    rows = manifest.get("artifacts") if isinstance(manifest, dict) else None
    if not isinstance(rows, list) or not isinstance(inventory, list):
        raise ValueError("preserved manifest.json and inventory.json have unexpected shapes")
    runs: dict[int, Any] = {}
    for page in inventory:
        for artifact in page.get("artifacts", []) if isinstance(page, dict) else []:
            if isinstance(artifact, dict) and isinstance(artifact.get("id"), int):
                runs[artifact["id"]] = artifact.get("workflow_run")
    merged = []
    for row in rows:
        if not isinstance(row, dict) or row.get("name") != "briefing-corpus-archive":
            continue
        identifier = row.get("id")
        run = runs.get(identifier) if isinstance(identifier, int) else None
        if not isinstance(run, dict):
            raise ValueError(f"preserved wrapper {row.get('id')} has no workflow provenance in inventory.json")
        kept = {key: row[key] for key in ("id", "name", "sha256", "bytes", "created_at")}
        merged.append(kept | {"workflow_run": run})
    if not merged:
        raise ValueError("no preserved corpus archive wrappers to restore")
    newest: dict[str, dict[str, Any]] = {}
    for row in merged:
        created = str(row["created_at"])
        day = created[:10]
        if day not in newest or created > str(newest[day]["created_at"]):
            newest[day] = row
    selected = sorted(newest.values(), key=lambda row: str(row["created_at"]))
    superseded = sorted(row["id"] for row in merged if row not in selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"schema_version": 1, "selection": "newest wrapper per creation day",
                                  "superseded_same_day_wrappers": superseded, "artifacts": selected},
                                 indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return len(selected)


def restore_command(day: str, artifact_dir: Path, corpora: Path, manifest: Path | None = None) -> list[str]:
    command = [sys.executable, "-S", str(ROOT / "restore_private_corpora.py"), "--artifact-dir", str(artifact_dir)]
    if manifest is not None:
        command += ["--artifact-manifest", str(manifest)]
    return command + ["--newest", day, "--output-dir", str(corpora)]


def run_command(day: str, day_dir: Path, generation_usd: float) -> list[str]:
    return [sys.executable, "-S", str(ROOT / "run_daily_briefing.py"),
            "--corpus", str(day_dir / "corpora" / f"{day}.json"),
            "--run-dir", str(day_dir / "run"), "--jev-review-dir", str(day_dir / "review"),
            "--output", str(day_dir / "briefing.md"), "--generation-usd", str(generation_usd)]


def _artifact_digests(day_dir: Path, chain: dict[str, Any]) -> dict[str, str]:
    day_dir = day_dir.resolve()
    paths = [day_dir / "run" / "fallback-log.json", day_dir / "corpora" / f"{day_dir.name}.json"]
    selected = chain.get("selected_run_dir")
    if isinstance(selected, str) and selected and not Path(selected).is_absolute():
        selected_dir = (day_dir / "run" / selected).resolve()
        if not selected_dir.is_relative_to(day_dir):
            raise DayFailure("selected run directory escapes the day directory")
        paths += [selected_dir / "manifest.json", day_dir / "briefing.md"]
    if (day_dir / "review" / "audit.json").exists():
        paths.append(day_dir / "review" / "audit.json")
    digests = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise DayFailure(f"expected artifact is missing: {path.relative_to(day_dir)}")
        digests[path.relative_to(day_dir).as_posix()] = sha256_file(path)
    return digests


def _day_costs(day_dir: Path, chain: dict[str, Any]) -> dict[str, Any]:
    budget: dict[str, Any] = chain["generation_budget"] if isinstance(chain.get("generation_budget"), dict) else {}
    costs: dict[str, Any] = {
        "generation_known_spend_usd": budget.get("known_spend_usd"),
        "generation_unknown_reserved_usd": budget.get("unknown_reserved_usd"),
        "review_reported_cost_usd": None,
        "review_unknown_cost_calls": None,
    }
    audit_path = day_dir / "review" / "audit.json"
    if audit_path.exists():
        envelope = _read_json(audit_path)
        audit = envelope.get("audit") if isinstance(envelope, dict) else None
        if isinstance(audit, dict):
            costs["review_reported_cost_usd"] = audit.get("reported_cost_usd")
            costs["review_unknown_cost_calls"] = audit.get("unknown_cost_calls")
    return costs


def run_day(day: str, root: Path, artifact_dir: Path, *, generation_usd: float, execute: Executor,
            commit: str | None, manifest: Path | None = None) -> dict[str, Any]:
    """Record one day; only unavailable archives and generation failures permit continuation."""
    day_dir = root / day
    corpus = day_dir / "corpora" / f"{day}.json"
    started = datetime.now(UTC).isoformat()
    if not corpus.exists():
        code = execute(restore_command(day, artifact_dir, day_dir / "corpora", manifest))
        if code == 4:
            record = {
                "schema_version": 1, "report_date": day, "status": "missing_day", "started_at": started,
                "completed_at": datetime.now(UTC).isoformat(), "code_commit": commit,
                "generation_usd_limit": generation_usd, "costs": {}, "artifact_sha256": {},
            }
            write_json_atomic(day_dir / DAY_RECORD, record)
            return record
        if code != 0:
            raise DayFailure(f"restore exited {code} for {day}")
        if not corpus.is_file():
            raise DayFailure(f"restore supplied no corpus for {day}; the date is not obtainable from the wrappers")
    if (day_dir / "run").exists():
        raise DayFailure(f"{day} has a run directory but no day record; inspect it before rerunning")
    code = execute(run_command(day, day_dir, generation_usd))
    chain = _read_json(day_dir / "run" / "fallback-log.json")
    if not isinstance(chain, dict):
        raise DayFailure(f"fallback log for {day} is not an object")
    if code == 0:
        if chain.get("status") != "ready" or not (day_dir / "review" / "audit.json").is_file():
            raise DayFailure(f"{day} exited 0 without a ready chain and a review audit; "
                             "the review stage did not complete")
        status = "reviewed"
    elif chain.get("status") == "failed" and chain.get("selected_run_dir") is None:
        budget = chain.get("generation_budget")
        calls = budget.get("calls") if isinstance(budget, dict) else None
        if not isinstance(calls, int) or calls < 1:
            # No provider was ever called: a refused allowance, missing credential, or
            # configuration fault, not an observation about generation.
            reason = budget.get("stop_reason") if isinstance(budget, dict) else None
            raise DayFailure(f"{day} failed before any provider call (budget stop_reason={reason!r})")
        status = "generation_failure"
    else:
        raise DayFailure(f"daily run exited {code} for {day} with chain status {chain.get('status')!r}")
    record = {
        "schema_version": 1, "report_date": day, "status": status, "started_at": started,
        "completed_at": datetime.now(UTC).isoformat(), "code_commit": commit,
        "generation_usd_limit": generation_usd, "costs": _day_costs(day_dir, chain),
        "artifact_sha256": _artifact_digests(day_dir, chain),
    }
    write_json_atomic(day_dir / DAY_RECORD, record)
    return record


def run_sample(dates: list[str], root: Path, artifact_dir: Path, *, generation_usd: float,
               execute: Executor = _default_executor, commit: str | None = None,
               report: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Run every listed day in order, skipping completed days and stopping on the first failure."""
    root.mkdir(parents=True, exist_ok=True)
    # The aggregator and packet exporter require the root to hold only dated day
    # directories, so the merged manifest lives beside the root, not inside it.
    manifest = root.parent / f"{root.name}-artifact-manifest.json"
    provenance_manifest(artifact_dir, manifest)
    records: list[dict[str, Any]] = []
    known = 0.0
    unknown_calls = 0
    for index, day in enumerate(dates, start=1):
        record_path = root / day / DAY_RECORD
        if record_path.exists():
            try:
                record = _read_json(record_path)
            except (OSError, UnicodeError, ValueError) as exc:
                raise DayFailure(f"cannot read day record for {day}; inspect {record_path}: {exc}") from exc
            if not isinstance(record, dict):
                raise DayFailure(f"day record for {day} is not an object; inspect {record_path}")
            report(f"{day}: already complete ({record.get('status')}); skipping")
        else:
            record = run_day(day, root, artifact_dir, generation_usd=generation_usd, execute=execute, commit=commit,
                             manifest=manifest)
            report(f"{day}: {record['status']}")
        records.append(record)
        costs = record.get("costs", {})
        known += sum(float(costs.get(k) or 0.0) for k in ("generation_known_spend_usd", "review_reported_cost_usd"))
        unknown_calls += int(costs.get("review_unknown_cost_calls") or 0)
        if index % 7 == 0 or index == len(dates):
            statuses = {s: sum(r["status"] == s for r in records) for s in sorted({r["status"] for r in records})}
            report(f"progress {index}/{len(dates)} days; cumulative reported cost USD {known:.4f}; "
                   f"unknown-cost review calls {unknown_calls}; statuses {statuses}")
    return records


def write_ledger(records: list[dict[str, Any]], path: Path) -> None:
    """Committable digests and statuses: no prose, paths, or costs beyond reported totals."""
    ledger = {
        "schema_version": 1,
        "days": [{"report_date": r["report_date"], "status": r["status"], "code_commit": r.get("code_commit"),
                  "artifact_sha256": r["artifact_sha256"], "costs": r.get("costs")} for r in records],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sample", type=Path, default=DEFAULT_SAMPLE)
    parser.add_argument("--root", type=Path, default=ROOT / ".news-briefing" / "jev-stage")
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--generation-usd", type=float, default=DEFAULT_GENERATION_USD,
                        help="per-day generation allowance passed to the daily runner; it reserves a full "
                             "input/output estimate (about USD 10) before each call, so small values refuse every call")
    parser.add_argument("--ledger", type=Path, help="write per-day digests and statuses here after the run")
    args = parser.parse_args()
    missing = [name for name in REQUIRED_ENVIRONMENT if not os.environ.get(name)]
    if missing:
        parser.error("missing environment: " + ", ".join(missing))
    for name in ("manifest.json", "inventory.json"):
        if not (args.artifact_dir / name).is_file():
            parser.error(f"no {name} under {args.artifact_dir}")
    try:
        dates = load_sample(args.sample)
        records = run_sample(dates, args.root, args.artifact_dir, generation_usd=args.generation_usd,
                             commit=_git_commit())
    except DayFailure as exc:
        print(f"STOPPED: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    if args.ledger is not None:
        write_ledger(records, args.ledger)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
