#!/usr/bin/env python3
"""Re-dispatch the daily briefing when GitHub drops its scheduled run.

GitHub can create a scheduled run and never give it a job; the 2026-11-11 run
sat that way for four days. No step of the daily workflow runs in that case, so
nothing inside it can notice. This watchdog runs from its own schedule and
decides from two facts it reads fresh each time:

* whether the deployed ``metrics.json`` ledger has a row for the current
  ``America/New_York`` date, which every run that prepares a date writes,
  including blocked and unpublished days; and
* the Daily briefing workflow runs whose current attempt started since the
  start of the previous Eastern date, with a job count for each unfinished
  one. The daily workflow's run-name tells which date a manual run prepares.

A day can only be recovered while it is still the current Eastern date: an
earlier date is rebuilt only from a corpus its own run stored, and a run that
never started stored none. So the watchdog dispatches a manual ``single-day``
run while fewer than ``MAX_MANUAL_RUNS`` manual runs have tried the date,
cancels a run that has had no job for an hour, and opens one issue per date it
cannot recover.

``decide`` is pure. ``main`` reads the ledger and runs, then applies the
decision through the GitHub REST API with the workflow's ``GITHUB_TOKEN``.
``--dry-run`` prints the decision and changes nothing.
"""

from __future__ import annotations

import argparse
import email.message
import json
import os
import re
import sys
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import IO, Any, Protocol
from zoneinfo import ZoneInfo

import site_metrics

NEW_YORK = ZoneInfo("America/New_York")
WORKFLOW_FILE = "daily-briefing.yml"
METRICS_URL = "https://elanthus.github.io/news-briefing/metrics.json"
API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
USER_AGENT = "news-briefing-watchdog/1.0"
MAX_API_BYTES = 2_000_000
MAX_LEDGER_BYTES = 50_000_000

# A run GitHub created but never gave a job is treated as dead after this long.
STUCK_AFTER = timedelta(minutes=60)
# Pages can take a few minutes to serve a run's deploy; a run that finished
# more recently than this is not yet evidence that the date went unrecorded.
SETTLE_AFTER = timedelta(minutes=20)
# Scheduled runs have started as late as 18:06 Eastern, so with no run at all
# the watchdog waits until DISPATCH_FROM before treating the day as dropped.
DISPATCH_FROM = time(19, 0)
# A run dispatched later than this could start after midnight and capture the
# next date instead of the missing one.
DISPATCH_UNTIL = time(23, 15)
# Manual runs allowed per date, a person's and the watchdog's together. A
# recovery run can fail too: on 2026-11-19 the scheduled run and the first
# manual run both generated a briefing and then failed to deploy it. The cap
# bounds the extra model spend.
MAX_MANUAL_RUNS = 3
UNFINISHED = frozenset({"queued", "in_progress", "waiting", "requested", "pending"})
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


SINGLE_DAY_PREFIX = "Daily briefing single-day "
_SINGLE_DAY_TITLE = re.compile(re.escape(SINGLE_DAY_PREFIX) + r"(\d{4}-\d{2}-\d{2})")
BACKFILL_TITLE = "Daily briefing backfill-7-days"
BACKFILL_DAYS = 7


@dataclass(frozen=True)
class Run:
    id: int
    event: str
    status: str
    # The run-name the daily workflow sets, which names a manual run's target.
    title: str
    # The current attempt's start. A re-run keeps created_at but captures its
    # window, and so its date, when the new attempt starts.
    started_at: datetime
    updated_at: datetime
    # Counted only for unfinished runs; None for a completed one.
    jobs: int | None


def covers(run: Run, day: date) -> bool:
    """Whether ``run`` prepares ``day``, from its run-name and start date.

    Scheduled runs and manual runs for "today" prepare the Eastern date they
    start on, and a backfill also prepares the six dates before it. A manual
    run with an explicit report date prepares only that date. A title the
    daily workflow did not write is treated as a run for its start date.
    """
    started = _eastern_date(run.started_at)
    if run.event == "workflow_dispatch":
        explicit = _SINGLE_DAY_TITLE.fullmatch(run.title)
        if explicit is not None:
            try:
                return date.fromisoformat(explicit.group(1)) == day
            except ValueError:
                return False
        if run.title.startswith(SINGLE_DAY_PREFIX) and run.title != f"{SINGLE_DAY_PREFIX}today":
            # A report date the daily workflow will refuse prepares no date.
            return False
        if run.title == BACKFILL_TITLE:
            return started - timedelta(days=BACKFILL_DAYS - 1) <= day <= started
    return started == day


@dataclass(frozen=True)
class Decision:
    target: date
    dispatch: bool
    cancel: tuple[int, ...]
    alerts: tuple[date, ...]
    reason: str

    def payload(self) -> dict[str, object]:
        return {
            "target": self.target.isoformat(),
            "dispatch": self.dispatch,
            "cancel": list(self.cancel),
            "alerts": [day.isoformat() for day in self.alerts],
            "reason": self.reason,
        }


def _eastern_date(moment: datetime) -> date:
    return moment.astimezone(NEW_YORK).date()


def _stuck(run: Run, now: datetime) -> bool:
    return run.status in UNFINISHED and run.jobs == 0 and now - run.started_at >= STUCK_AFTER


def decide(*, now: datetime, recorded: frozenset[date], runs: Sequence[Run]) -> Decision:
    """Choose what to do about the current Eastern date and the one before it."""
    local = now.astimezone(NEW_YORK)
    target, yesterday = local.date(), local.date() - timedelta(days=1)
    stuck = tuple(sorted(run.id for run in runs if _stuck(run, now)))
    live = [run for run in runs if run.status in UNFINISHED and run.id not in stuck]
    settling = [run for run in runs if run.status == "completed" and now - run.updated_at < SETTLE_AFTER]

    alerts: list[date] = []
    # The previous date is past recovering; say so once if no run recorded it.
    # A run for it that is still going or whose deploy Pages may not serve yet
    # holds the alert back. The span check keeps a new ledger from reporting
    # dates before it.
    if (
        recorded and min(recorded) <= yesterday and yesterday not in recorded
        and not any(covers(run, yesterday) for run in live + settling)
    ):
        alerts.append(yesterday)

    def outcome(reason: str, *, dispatch: bool = False, cancel: tuple[int, ...] = (),
                missing: bool = False) -> Decision:
        return Decision(target, dispatch, cancel, tuple(alerts + ([target] if missing else [])), reason)

    if target in recorded:
        return outcome(f"{target} is recorded")
    if live:
        # Includes a run waiting on the daily concurrency group behind another.
        return outcome(f"run {live[0].id} is {live[0].status}")
    today = [run for run in runs if covers(run, target)]
    settling_today = [run for run in settling if covers(run, target)]
    if settling_today:
        return outcome(f"run {settling_today[0].id} finished less than {SETTLE_AFTER} ago")
    manual = sum(run.event == "workflow_dispatch" for run in today)
    if manual >= MAX_MANUAL_RUNS:
        return outcome(f"{manual} manual runs for {target} already ran without recording it",
                       cancel=stuck, missing=True)
    if local.time() >= DISPATCH_UNTIL:
        return outcome(f"too late to recover {target}: a run started now could capture the next date",
                       cancel=stuck, missing=True)
    if stuck:
        return outcome(f"run {', '.join(map(str, stuck))} has had no job for {STUCK_AFTER}",
                       dispatch=True, cancel=stuck)
    finished = [run for run in today if run.status == "completed"]
    if finished:
        return outcome(f"run {finished[0].id} finished without recording {target}", dispatch=True)
    if local.time() < DISPATCH_FROM:
        return outcome(f"no run for {target} yet; the scheduled run may still start")
    return outcome(f"no run recorded {target} by {DISPATCH_FROM:%H:%M} Eastern", dispatch=True)


# I/O. Everything below talks to GitHub or Pages; tests replace it with fakes.


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: email.message.Message,
        newurl: str,
    ) -> urllib.request.Request | None:
        return None


def _read_bounded(response: Any, limit: int) -> bytes:
    payload = response.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"response exceeded {limit} bytes")
    return bytes(payload)


class Api(Protocol):
    def get(self, path: str, query: Mapping[str, str] | None = None) -> Any: ...

    def post(self, path: str, body: Mapping[str, object] | None = None) -> None: ...


class GitHubApi:
    """Repository-scoped REST calls; the token never follows a redirect."""

    def __init__(self, repository: str, token: str) -> None:
        if not _REPOSITORY.fullmatch(repository):
            raise ValueError("GITHUB_REPOSITORY must be owner/name")
        if not token:
            raise ValueError("GITHUB_TOKEN is required")
        self._base = f"{API_ROOT}/repos/{repository}/"
        self._token = token
        self._opener = urllib.request.build_opener(_NoRedirect)

    def _request(self, path: str, query: Mapping[str, str] | None, data: bytes | None) -> urllib.request.Request:
        url = self._base + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
        return urllib.request.Request(
            url,
            data=data,
            method="POST" if data is not None else "GET",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
                "Content-Type": "application/json",
            },
        )

    def get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        with self._opener.open(self._request(path, query, None), timeout=30) as response:
            return json.loads(_read_bounded(response, MAX_API_BYTES))

    def post(self, path: str, body: Mapping[str, object] | None = None) -> None:
        data = json.dumps(body or {}).encode()
        with self._opener.open(self._request(path, None, data), timeout=30) as response:
            _read_bounded(response, MAX_API_BYTES)


def _int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must carry a UTC offset")
    return parsed


def list_runs(api: Api, since: datetime) -> list[Run]:
    """Daily briefing runs on main whose current attempt started at or after ``since``, plus any unfinished run.

    The listing is filtered here rather than by the API's ``created`` filter,
    because a re-run keeps its original creation time. The newest hundred runs
    span months of this once-a-day workflow.
    """
    payload = api.get(f"actions/workflows/{WORKFLOW_FILE}/runs", {"branch": "main", "per_page": "100"})
    rows = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("GitHub run response has no workflow_runs array")
    runs = []
    for row in rows:
        if not isinstance(row, dict) or not all(
            isinstance(row.get(field), str) for field in ("event", "status", "display_title")
        ):
            raise ValueError("GitHub run response contains a malformed run")
        run_id = _int(row.get("id"), "run id")
        started_at = _timestamp(row.get("run_started_at"), "run_started_at")
        if started_at < since and row["status"] not in UNFINISHED:
            continue
        jobs = None
        if row["status"] in UNFINISHED:
            # The jobs listing defaults to the latest attempt.
            listing = api.get(f"actions/runs/{run_id}/jobs", {"per_page": "1"})
            jobs = _int(listing.get("total_count") if isinstance(listing, dict) else None, "job count")
        runs.append(Run(
            id=run_id,
            event=row["event"],
            status=row["status"],
            title=row["display_title"],
            started_at=started_at,
            updated_at=_timestamp(row.get("updated_at"), "updated_at"),
            jobs=jobs,
        ))
    return runs


def recorded_dates(raw: bytes) -> frozenset[date]:
    """Dates in the deployed ledger; any schema but the current one is refused."""
    rows = site_metrics.parse_ledger(json.loads(raw))
    return frozenset(date.fromisoformat(row["date"]) for row in rows)


def fetch_ledger(url: str = METRICS_URL) -> bytes:
    request = urllib.request.Request(
        f"{url}?v={int(clock.time())}",
        headers={"Cache-Control": "no-cache", "User-Agent": USER_AGENT},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return _read_bounded(response, MAX_LEDGER_BYTES)


def _issue_title(day: date) -> str:
    return f"Daily briefing missing for {day.isoformat()}"


def _issue_body(day: date, reason: str, run_url: str) -> str:
    return (
        f"No workflow run recorded a briefing for {day.isoformat()} (America/New_York), and the "
        "watchdog can no longer recover it.\n\n"
        f"Watchdog reason: {reason}.\n\n"
        "An earlier date is rebuilt only from a corpus its own run stored, so this date cannot be "
        "regenerated. The metrics page counts it as a date with no record.\n\n"
        f"Watchdog run: {run_url}\n"
    )


ISSUE_PAGE_SIZE = 100
MAX_ISSUE_PAGES = 50


def _open_issue_titles(api: Api) -> set[str]:
    """Titles of every open issue; the endpoint pages and also lists pull requests."""
    titles: set[str] = set()
    for page in range(1, MAX_ISSUE_PAGES + 1):
        issues = api.get("issues", {"state": "open", "per_page": str(ISSUE_PAGE_SIZE), "page": str(page)})
        if not isinstance(issues, list):
            raise ValueError("GitHub issue response is not an array")
        titles.update(
            issue["title"] for issue in issues
            if isinstance(issue, dict) and "pull_request" not in issue and isinstance(issue.get("title"), str)
        )
        if len(issues) < ISSUE_PAGE_SIZE:
            return titles
    # Opening an issue without seeing them all could duplicate one.
    raise ValueError(f"more than {MAX_ISSUE_PAGES * ISSUE_PAGE_SIZE} open issues and pull requests")


def apply(decision: Decision, api: Api, *, run_url: str) -> None:
    for run_id in decision.cancel:
        try:
            api.post(f"actions/runs/{run_id}/cancel")
            print(f"Cancelled stuck run {run_id}")
        except urllib.error.HTTPError as error:
            # A run GitHub already finished answers 409; the dispatch still matters more.
            print(f"::warning::Could not cancel run {run_id}: HTTP {error.code}")
            error.close()
    if decision.dispatch:
        api.post(f"actions/workflows/{WORKFLOW_FILE}/dispatches", {"ref": "main", "inputs": {"mode": "single-day"}})
        print(f"::notice::Dispatched a single-day Daily briefing run for {decision.target}: {decision.reason}")
    if decision.alerts:
        open_titles = _open_issue_titles(api)
        for day in decision.alerts:
            if _issue_title(day) in open_titles:
                continue
            api.post("issues", {"title": _issue_title(day), "body": _issue_body(day, decision.reason, run_url)})
            print(f"::warning::Opened an issue: {_issue_title(day)}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--dry-run", action="store_true", help="print the decision without acting on it")
    args = parser.parse_args(argv)
    now = datetime.now(UTC)
    api = GitHubApi(os.environ.get("GITHUB_REPOSITORY", ""), os.environ.get("GITHUB_TOKEN", ""))
    recorded = recorded_dates(fetch_ledger())
    start_of_yesterday = datetime.combine(_eastern_date(now) - timedelta(days=1), time(0), NEW_YORK)
    decision = decide(now=now, recorded=recorded, runs=list_runs(api, start_of_yesterday))
    print(json.dumps(decision.payload(), sort_keys=True))
    if args.dry_run:
        return 0
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    run_url = f"{server}/{os.environ.get('GITHUB_REPOSITORY', '')}/actions/runs/{os.environ.get('GITHUB_RUN_ID', '')}"
    apply(decision, api, run_url=run_url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
