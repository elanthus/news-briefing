#!/usr/bin/env python3
"""Drive daily publication workflow orchestration with injectable subprocesses.

The GitHub Actions environment supplies ``GITHUB_ENV``, ``GITHUB_EVENT_NAME``,
``MANUAL_MODE``, ``MANUAL_REPORT_DATE``, ``REPORT_TODAY``, ``WINDOW_START``, and
``WINDOW_END``. Equivalent command-line flags are available for local testing.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol
from zoneinfo import ZoneInfo

PUBLIC_ROOT = "https://elanthus.github.io/news-briefing"
NEW_YORK = ZoneInfo("America/New_York")


class CommandResult(Protocol):
    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[[Sequence[str]], CommandResult]


def _run(command: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, capture_output=True, check=False)


def _invoke(command: Sequence[str], runner: CommandRunner) -> CommandResult:
    result = runner(command)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(
            result.stderr,
            end="" if result.stderr.endswith("\n") else "\n",
            file=sys.stderr,
        )
    return result


def _calendar_date(value: str) -> date | None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def _validated_target(value: str, today: date) -> date | None:
    parsed = _calendar_date(value)
    if parsed is None:
        print("::error::report_date must be a real calendar date in YYYY-MM-DD form")
        return None
    oldest = today - timedelta(days=13)
    if not oldest <= parsed <= today:
        print(
            "::error::report_date must be inside the retained private corpus "
            f"window ({oldest.isoformat()} through {today.isoformat()})"
        )
        return None
    return parsed


def capture_window(
    *,
    snapshot_end_epoch: int,
    github_env: Path,
    root: Path = Path("."),
) -> int:
    for name in ("briefing-history", "corpora", "reports", "runs"):
        (root / name).mkdir(parents=True, exist_ok=True)
    window_end = datetime.fromtimestamp(snapshot_end_epoch, UTC)
    window_start = window_end - timedelta(days=1)
    today = window_end.astimezone(NEW_YORK).date()
    values = (
        ("SNAPSHOT_END_EPOCH", str(snapshot_end_epoch)),
        ("WINDOW_START", window_start.isoformat(timespec="seconds")),
        ("WINDOW_END", window_end.isoformat(timespec="seconds")),
        ("REPORT_TODAY", today.isoformat()),
    )
    with github_env.open("a", encoding="utf-8") as destination:
        for key, value in values:
            destination.write(f"{key}={value}\n")
    return 0


def download_public_file(
    url: str,
    output: Path,
    *,
    runner: CommandRunner = _run,
) -> int:
    result = runner([
        "curl",
        "--location",
        "--max-time", "30",
        "--max-filesize", "50000000",
        "--remove-on-error",
        "--silent",
        "--show-error",
        "--write-out", "%{http_code}",
        url,
        "--output", str(output),
    ])
    if result.returncode != 0:
        if result.stderr:
            print(
                result.stderr,
                end="" if result.stderr.endswith("\n") else "\n",
                file=sys.stderr,
            )
        print(f"::error::Could not download {url}")
        return 2
    status = result.stdout.strip()
    if status == "200":
        return 0
    output.unlink(missing_ok=True)
    if status == "404":
        return 1
    print(f"::error::Unexpected HTTP {status} from {url}")
    return 2


def _targeted_report_date(
    *, event_name: str, manual_mode: str, manual_report_date: str, today: date
) -> tuple[bool, str]:
    if event_name != "workflow_dispatch" or manual_mode != "single-day":
        return True, ""
    if not manual_report_date:
        return True, today.isoformat()
    parsed = _validated_target(manual_report_date, today)
    return (parsed is not None, parsed.isoformat() if parsed is not None else "")


def restore_corpus(
    *,
    today: date,
    event_name: str,
    manual_mode: str,
    manual_report_date: str,
    root: Path = Path("."),
    runner: CommandRunner = _run,
) -> int:
    valid, targeted_report_date = _targeted_report_date(
        event_name=event_name,
        manual_mode=manual_mode,
        manual_report_date=manual_report_date,
        today=today,
    )
    if not valid:
        return 1
    corpora = root / "corpora"
    corpora.mkdir(parents=True, exist_ok=True)
    restore = _invoke(
        ["python", "restore_private_corpora.py", "--output-dir", str(corpora)],
        runner,
    )
    if restore.returncode == 4:
        marker = root / "published-corpus-storage.json"
        marker_status = download_public_file(
            f"{PUBLIC_ROOT}/corpus-storage.json", marker, runner=runner
        )
        if marker_status == 0:
            validation = _invoke([
                "python", "corpus_storage.py", "validate", str(marker)
            ], runner)
            if validation.returncode != 0:
                return validation.returncode
            print("No unexpired private archive remains; starting a fresh corpus window")
        elif marker_status == 2:
            return 1
        else:
            history = root / "existing-history.json"
            history_status = download_public_file(
                f"{PUBLIC_ROOT}/history.json", history, runner=runner
            )
            if history_status == 0:
                print(
                    "No private corpus archive exists yet; migrating all retained "
                    "corpora from Pages"
                )
                legacy = root / "legacy-corpora"
                legacy.mkdir(parents=True, exist_ok=True)
                downloaded = 0
                for days_ago in range(1, 14):
                    report_date = (today - timedelta(days=days_ago)).isoformat()
                    status = download_public_file(
                        f"{PUBLIC_ROOT}/corpora/{report_date}.json",
                        legacy / f"{report_date}.json",
                        runner=runner,
                    )
                    downloaded += status == 0
                targeted_available = bool(targeted_report_date) and (
                    targeted_report_date == today.isoformat()
                    or (legacy / f"{targeted_report_date}.json").is_file()
                )
                if downloaded != 13 and not targeted_available:
                    print(
                        f"::error::Downloaded only {downloaded} of 13 retained public "
                        "corpora; leaving Pages unchanged for a complete retry"
                    )
                    return 1
                if downloaded != 13:
                    print(
                        f"::warning::Downloaded {downloaded} of 13 retained public "
                        f"corpora; continuing targeted repair for {targeted_report_date} "
                        "without regenerating other dates"
                    )
                pruned = _invoke([
                    "python", "private_archive.py", "prune-corpora", str(legacy),
                    "--newest", today.isoformat(),
                ], runner)
                if pruned.returncode != 0:
                    return pruned.returncode
                for source in legacy.glob("*.json"):
                    shutil.copy2(source, corpora / source.name)
            elif history_status == 1:
                print("No published history exists; starting the first corpus window")
            else:
                return 1
    elif restore.returncode != 0:
        print("::error::Private corpus restoration failed")
        return restore.returncode
    prune = _invoke([
        "python", "private_archive.py", "prune-corpora", str(corpora),
        "--newest", today.isoformat(),
    ], runner)
    return prune.returncode


def _report_dates(
    *, event_name: str, manual_mode: str, manual_report_date: str, today: date
) -> list[date] | None:
    if event_name == "workflow_dispatch" and manual_mode == "single-day" and manual_report_date:
        parsed = _validated_target(manual_report_date, today)
        return [parsed] if parsed is not None else None
    if event_name == "workflow_dispatch" and manual_mode == "backfill-7-days":
        return [today - timedelta(days=days_ago) for days_ago in range(6, -1, -1)]
    return [today]


def generate_reports(
    *,
    today: date,
    window_start: str,
    window_end: str,
    event_name: str,
    manual_mode: str,
    manual_report_date: str,
    root: Path = Path("."),
    runner: CommandRunner = _run,
) -> int:
    report_dates = _report_dates(
        event_name=event_name,
        manual_mode=manual_mode,
        manual_report_date=manual_report_date,
        today=today,
    )
    if report_dates is None:
        return 1
    for value in report_dates:
        report_date = value.isoformat()
        corpus = root / "corpora" / f"{report_date}.json"
        run_dir = root / "runs" / report_date
        report = root / "reports" / f"{report_date}.md"
        corpus_ready = False
        if value == today:
            fetched = _invoke([
                "python", "fetch_news.py",
                "--window-start", window_start,
                "--window-end", window_end,
                "--report-date", report_date,
                "--output", str(corpus),
            ], runner)
            if fetched.returncode == 0:
                corpus_ready = True
            else:
                print(f"::warning::Corpus fetch failed for {report_date}")
        elif corpus.is_file():
            print(f"Reusing privately restored corpus for {report_date}")
            corpus_ready = True
        else:
            print(
                f"::warning::Skipping {report_date}: no private stored corpus is available"
            )
            continue
        if corpus_ready:
            generated = _invoke([
                "python", "run_daily_briefing.py",
                "--corpus", str(corpus),
                "--run-dir", str(run_dir),
                "--output", str(report),
                "--force",
                "--max-corrections", "3",
                "--max-tokens", "100000",
            ], runner)
            if generated.returncode != 0:
                print(
                    f"::warning::All briefing models failed for {report_date}; "
                    f"see {run_dir}/fallback.log"
                )
        else:
            print(
                f"::warning::Skipping briefing generation for {report_date}: "
                "no corpus is available"
            )
        prepared = _invoke([
            "python", "prepare_publication.py",
            "--run-dir", str(run_dir),
            "--corpus", str(corpus),
            "--history-dir", str(root / "briefing-history"),
            "--date", report_date,
        ], runner)
        if prepared.returncode != 0:
            print(f"::warning::Publication preparation failed for {report_date}")
    return 0


def _date_argument(value: str) -> date:
    parsed = _calendar_date(value)
    if parsed is None:
        raise argparse.ArgumentTypeError("must be a real YYYY-MM-DD date")
    return parsed


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    capture = subparsers.add_parser("capture-window")
    capture.add_argument("--snapshot-end-epoch", type=int, default=None)
    capture.add_argument("--github-env", type=Path, default=None)
    restore = subparsers.add_parser("restore-corpus")
    restore.add_argument("--today", type=_date_argument, default=None)
    generate = subparsers.add_parser("generate-reports")
    generate.add_argument("--today", type=_date_argument, default=None)
    args = parser.parse_args(argv)
    if args.command == "capture-window":
        github_env = args.github_env or Path(os.environ["GITHUB_ENV"])
        return capture_window(
            snapshot_end_epoch=args.snapshot_end_epoch or int(time.time()),
            github_env=github_env,
        )
    today = args.today if isinstance(args.today, date) else _date_argument(
        os.environ["REPORT_TODAY"]
    )
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    manual_mode = os.environ.get("MANUAL_MODE", "")
    manual_report_date = os.environ.get("MANUAL_REPORT_DATE", "")
    if args.command == "restore-corpus":
        return restore_corpus(
            today=today,
            event_name=event_name,
            manual_mode=manual_mode,
            manual_report_date=manual_report_date,
        )
    return generate_reports(
        today=today,
        event_name=event_name,
        manual_mode=manual_mode,
        manual_report_date=manual_report_date,
        window_start=os.environ["WINDOW_START"],
        window_end=os.environ["WINDOW_END"],
    )


if __name__ == "__main__":
    sys.exit(main())
