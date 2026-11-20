"""The watchdog re-dispatches a dropped daily run while its date can still be recovered."""

from __future__ import annotations

import io
import json
import re
import unittest
import urllib.error
from collections.abc import Mapping
from contextlib import redirect_stdout
from datetime import UTC, date, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any

import briefing_watchdog as watchdog
import site_metrics

REPOSITORY = Path(__file__).resolve().parent.parent
WORKFLOW = (REPOSITORY / ".github/workflows/daily-briefing-watchdog.yml").read_text(encoding="utf-8")
DAILY_WORKFLOW = (REPOSITORY / ".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")

TODAY = date(2026, 11, 16)
YESTERDAY = date(2026, 11, 15)
# November dates are on Eastern Standard Time, UTC-5.
AFTERNOON = datetime(2026, 11, 16, 21, 0, tzinfo=UTC)  # 16:00 Eastern
EVENING = datetime(2026, 11, 17, 0, 41, tzinfo=UTC)  # 19:41 Eastern
TOO_LATE = datetime(2026, 11, 17, 4, 30, tzinfo=UTC)  # 23:30 Eastern
HISTORY = frozenset({YESTERDAY - timedelta(days=1), YESTERDAY})


def _run(run_id: int, started: datetime, *, status: str = "completed", event: str = "schedule",
         jobs: int | None = None, updated: datetime | None = None, title: str | None = None) -> watchdog.Run:
    if jobs is None and status in watchdog.UNFINISHED:
        jobs = 1
    if title is None:
        title = "Daily briefing single-day today" if event == "workflow_dispatch" else "Daily briefing"
    return watchdog.Run(run_id, event, status, title, started, updated or started + timedelta(minutes=7), jobs)


class DecideTests(unittest.TestCase):
    def test_recorded_date_needs_nothing(self) -> None:
        decision = watchdog.decide(now=EVENING, recorded=HISTORY | {TODAY}, runs=[])
        self.assertEqual((decision.dispatch, decision.cancel, decision.alerts), (False, (), ()))
        self.assertEqual(decision.target, TODAY)

    def test_waits_for_a_late_scheduled_run_before_the_evening_window(self) -> None:
        decision = watchdog.decide(now=AFTERNOON, recorded=HISTORY, runs=[])
        self.assertFalse(decision.dispatch)
        self.assertEqual(decision.alerts, ())
        self.assertIn("may still start", decision.reason)

    def test_dispatches_when_no_run_appeared_by_the_evening(self) -> None:
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[])
        self.assertTrue(decision.dispatch)
        self.assertEqual(decision.alerts, ())

    def test_cancels_a_jobless_run_and_dispatches_at_once(self) -> None:
        # The 2026-11-11 failure: created, never given a job.
        orphan = _run(7, AFTERNOON - timedelta(minutes=61), status="queued", jobs=0)
        decision = watchdog.decide(now=AFTERNOON, recorded=HISTORY, runs=[orphan])
        self.assertTrue(decision.dispatch)
        self.assertEqual(decision.cancel, (7,))

    def test_a_jobless_run_younger_than_the_threshold_counts_as_live(self) -> None:
        fresh = _run(7, AFTERNOON - timedelta(minutes=30), status="queued", jobs=0)
        decision = watchdog.decide(now=AFTERNOON, recorded=HISTORY, runs=[fresh])
        self.assertEqual((decision.dispatch, decision.cancel), (False, ()))

    def test_any_live_run_blocks_dispatch_including_one_from_yesterday(self) -> None:
        # A run queued on the daily concurrency group behind a long one has no job yet either.
        long_running = _run(5, AFTERNOON - timedelta(hours=20), status="in_progress")
        waiting = _run(6, AFTERNOON - timedelta(hours=2), status="pending", jobs=0)
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[long_running, waiting])
        self.assertEqual((decision.dispatch, decision.cancel), (False, ()))
        self.assertIn("run 5", decision.reason)

    def test_failed_scheduled_run_is_retried_once_without_waiting_for_evening(self) -> None:
        failed = _run(8, AFTERNOON - timedelta(hours=1))
        self.assertTrue(watchdog.decide(now=AFTERNOON, recorded=HISTORY, runs=[failed]).dispatch)

    def test_a_just_finished_run_gets_time_for_pages_to_serve_it(self) -> None:
        finished = _run(8, EVENING - timedelta(minutes=12), updated=EVENING - timedelta(minutes=5))
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[finished])
        self.assertFalse(decision.dispatch)
        self.assertIn("finished less than", decision.reason)

    def test_a_failed_manual_run_is_retried_while_under_the_cap(self) -> None:
        # The 2026-11-19 failure: the recovery run also failed to deploy.
        manual = [_run(9 + n, EVENING - timedelta(hours=1 + n), event="workflow_dispatch")
                  for n in range(watchdog.MAX_MANUAL_RUNS - 1)]
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=manual)
        self.assertTrue(decision.dispatch)
        self.assertEqual(decision.alerts, ())

    def test_stops_dispatching_at_the_manual_run_cap(self) -> None:
        manual = [_run(9 + n, EVENING - timedelta(hours=1 + n), event="workflow_dispatch")
                  for n in range(watchdog.MAX_MANUAL_RUNS)]
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=manual)
        self.assertFalse(decision.dispatch)
        self.assertEqual(decision.alerts, (TODAY,))

    def test_a_stuck_manual_run_is_cancelled_and_replaced_under_the_cap(self) -> None:
        stuck = _run(9, EVENING - timedelta(hours=2), event="workflow_dispatch", status="queued", jobs=0)
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[stuck])
        self.assertEqual((decision.dispatch, decision.cancel, decision.alerts), (True, (9,), ()))

    def test_a_stuck_manual_run_at_the_cap_is_cancelled_but_not_replaced(self) -> None:
        earlier = [_run(20 + n, EVENING - timedelta(hours=4 + n), event="workflow_dispatch")
                   for n in range(watchdog.MAX_MANUAL_RUNS - 1)]
        stuck = _run(9, EVENING - timedelta(hours=2), event="workflow_dispatch", status="queued", jobs=0)
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[stuck, *earlier])
        self.assertEqual((decision.dispatch, decision.cancel, decision.alerts), (False, (9,), (TODAY,)))

    def test_late_evening_alerts_instead_of_dispatching(self) -> None:
        decision = watchdog.decide(now=TOO_LATE, recorded=HISTORY, runs=[])
        self.assertFalse(decision.dispatch)
        self.assertEqual(decision.alerts, (TODAY,))
        self.assertIn("next date", decision.reason)

    def test_unrecorded_yesterday_is_reported_once_it_cannot_be_recovered(self) -> None:
        recorded = frozenset({YESTERDAY - timedelta(days=1)})
        decision = watchdog.decide(now=AFTERNOON, recorded=recorded, runs=[])
        self.assertEqual(decision.alerts, (YESTERDAY,))
        self.assertFalse(decision.dispatch)

    def test_yesterday_is_not_reported_while_its_run_is_still_going(self) -> None:
        just_after_midnight = datetime(2026, 11, 16, 5, 10, tzinfo=UTC)  # 00:10 Eastern
        late_run = _run(4, datetime(2026, 11, 16, 4, 55, tzinfo=UTC), status="in_progress")
        decision = watchdog.decide(now=just_after_midnight, recorded=frozenset({YESTERDAY - timedelta(days=1)}),
                                   runs=[late_run])
        self.assertEqual(decision.alerts, ())

    def test_a_new_ledger_does_not_report_dates_before_it(self) -> None:
        self.assertEqual(watchdog.decide(now=AFTERNOON, recorded=frozenset(), runs=[]).alerts, ())
        self.assertEqual(watchdog.decide(now=AFTERNOON, recorded=frozenset({TODAY}), runs=[]).alerts, ())

    def test_a_rerun_gets_the_full_hour_from_its_own_start(self) -> None:
        # GitHub keeps a re-run's created_at; the watchdog measures from the attempt's start.
        rerun = _run(7, AFTERNOON - timedelta(minutes=10), status="queued", jobs=0)
        decision = watchdog.decide(now=AFTERNOON, recorded=HISTORY, runs=[rerun])
        self.assertEqual((decision.dispatch, decision.cancel), (False, ()))

    def test_unrecorded_yesterday_waits_for_pages_after_its_run_finishes(self) -> None:
        after_midnight = datetime(2026, 11, 16, 5, 41, tzinfo=UTC)  # 00:41 Eastern
        late_run = _run(4, datetime(2026, 11, 16, 4, 50, tzinfo=UTC),
                        updated=datetime(2026, 11, 16, 5, 35, tzinfo=UTC))
        recorded = frozenset({YESTERDAY - timedelta(days=1)})
        self.assertEqual(watchdog.decide(now=after_midnight, recorded=recorded, runs=[late_run]).alerts, ())
        later = after_midnight + watchdog.SETTLE_AFTER
        self.assertEqual(watchdog.decide(now=later, recorded=recorded, runs=[late_run]).alerts, (YESTERDAY,))

    def test_a_manual_run_for_another_date_does_not_stand_in_for_today(self) -> None:
        other = _run(9, EVENING - timedelta(hours=1), event="workflow_dispatch",
                     title="Daily briefing single-day 2026-11-14")
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=[other])
        self.assertTrue(decision.dispatch)
        self.assertEqual(decision.alerts, ())

    def test_manual_runs_naming_today_count_toward_the_cap(self) -> None:
        explicit = [_run(9 + n, EVENING - timedelta(hours=1 + n), event="workflow_dispatch",
                         title="Daily briefing single-day 2026-11-16")
                    for n in range(watchdog.MAX_MANUAL_RUNS)]
        decision = watchdog.decide(now=EVENING, recorded=HISTORY, runs=explicit)
        self.assertFalse(decision.dispatch)
        self.assertEqual(decision.alerts, (TODAY,))


class CoversTests(unittest.TestCase):
    STARTED = AFTERNOON

    def _covers(self, title: str, day: date, event: str = "workflow_dispatch") -> bool:
        return watchdog.covers(_run(1, self.STARTED, event=event, title=title), day)

    def test_scheduled_and_today_runs_prepare_their_start_date(self) -> None:
        for title, event in (("Daily briefing", "schedule"), ("Daily briefing single-day today", "workflow_dispatch"),
                             ("Daily briefing", "workflow_dispatch")):
            with self.subTest(title=title, event=event):
                self.assertTrue(self._covers(title, TODAY, event))
                self.assertFalse(self._covers(title, YESTERDAY, event))

    def test_explicit_date_prepares_only_that_date(self) -> None:
        self.assertTrue(self._covers("Daily briefing single-day 2026-11-10", date(2026, 11, 10)))
        self.assertFalse(self._covers("Daily briefing single-day 2026-11-10", TODAY))
        for refused in ("Daily briefing single-day 2026-02-30", "Daily briefing single-day soon"):
            with self.subTest(title=refused):
                self.assertFalse(self._covers(refused, TODAY))

    def test_backfill_prepares_seven_dates_ending_on_its_start(self) -> None:
        self.assertTrue(self._covers(watchdog.BACKFILL_TITLE, TODAY))
        self.assertTrue(self._covers(watchdog.BACKFILL_TITLE, TODAY - timedelta(days=6)))
        self.assertFalse(self._covers(watchdog.BACKFILL_TITLE, TODAY - timedelta(days=7)))

    def test_titles_match_the_daily_workflow_run_name(self) -> None:
        run_name = DAILY_WORKFLOW.split("run-name: >-\n", 1)[1].split("\n\n", 1)[0]
        self.assertIn("github.event_name != 'workflow_dispatch' && 'Daily briefing'", run_name)
        self.assertIn(f"inputs.mode == 'backfill-7-days' && '{watchdog.BACKFILL_TITLE}'", run_name)
        self.assertIn(f"format('{watchdog.SINGLE_DAY_PREFIX}{{0}}', inputs.report_date || 'today')", run_name)


class DaylightTests(unittest.TestCase):
    def test_daylight_time_uses_the_eastern_date(self) -> None:
        # 02:30 UTC on 2026-07-02 is 22:30 EDT on 2026-07-01.
        decision = watchdog.decide(now=datetime(2026, 7, 2, 2, 30, tzinfo=UTC), recorded=frozenset(), runs=[])
        self.assertEqual(decision.target, date(2026, 7, 1))
        self.assertTrue(decision.dispatch)


class FakeApi:
    def __init__(self, responses: Mapping[str, Any], fail_posts: Mapping[str, int] | None = None) -> None:
        self.responses = dict(responses)
        self.fail_posts = dict(fail_posts or {})
        self.gets: list[tuple[str, dict[str, str]]] = []
        self.posts: list[tuple[str, Mapping[str, object] | None]] = []

    def get(self, path: str, query: Mapping[str, str] | None = None) -> Any:
        self.gets.append((path, dict(query or {})))
        response = self.responses[path]
        return response(dict(query or {})) if callable(response) else response

    def post(self, path: str, body: Mapping[str, object] | None = None) -> None:
        if path in self.fail_posts:
            raise urllib.error.HTTPError(path, self.fail_posts[path], "refused", Message(), None)
        self.posts.append((path, body))


def _api_run(run_id: int, status: str, started: str = "2026-11-16T20:21:04Z") -> dict[str, object]:
    return {"id": run_id, "event": "schedule", "status": status, "display_title": "Daily briefing",
            "created_at": "2026-11-10T20:21:04Z", "run_started_at": started, "updated_at": "2026-11-16T20:30:00Z"}


class ListRunsTests(unittest.TestCase):
    def test_counts_jobs_only_for_unfinished_runs_and_filters_by_attempt_start(self) -> None:
        api = FakeApi({
            "actions/workflows/daily-briefing.yml/runs": {"workflow_runs": [
                _api_run(1, "completed"), _api_run(2, "queued"),
                _api_run(3, "completed", started="2026-11-14T20:00:00Z"),
                _api_run(4, "in_progress", started="2026-11-13T20:00:00Z"),
            ]},
            "actions/runs/2/jobs": {"total_count": 0, "jobs": []},
            "actions/runs/4/jobs": {"total_count": 1, "jobs": []},
        })
        since = datetime(2026, 11, 15, 5, 0, tzinfo=UTC)
        runs = watchdog.list_runs(api, since)
        # Run 3 finished before ``since``; run 4 is older but still going.
        self.assertEqual([(run.id, run.jobs) for run in runs], [(1, None), (2, 0), (4, 1)])
        self.assertEqual(runs[0].started_at, datetime(2026, 11, 16, 20, 21, 4, tzinfo=UTC))
        self.assertEqual(runs[0].title, "Daily briefing")
        self.assertEqual(api.gets[0][1], {"branch": "main", "per_page": "100"})

    def test_refuses_a_malformed_response(self) -> None:
        since = datetime(2026, 11, 15, tzinfo=UTC)
        for payload in ({}, {"workflow_runs": [{**_api_run(1, "queued"), "id": True}]},
                        {"workflow_runs": [{**_api_run(1, "completed"), "display_title": None}]},
                        {"workflow_runs": [{**_api_run(1, "completed"), "run_started_at": "2026-11-16T20:21:04"}]}):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                watchdog.list_runs(FakeApi({"actions/workflows/daily-briefing.yml/runs": payload}), since)


class LedgerTests(unittest.TestCase):
    def test_reads_dates_from_the_current_ledger_schema(self) -> None:
        row = site_metrics.metrics_row(day=TODAY, disposition="blocked", provenance=None, semantic_audit=None,
                                       integrity=None, generation_failures=3, findings_count=0, advisory_findings=0)
        raw = json.dumps(site_metrics.ledger_payload([row])).encode()
        self.assertEqual(watchdog.recorded_dates(raw), frozenset({TODAY}))

    def test_refuses_another_ledger_schema(self) -> None:
        with self.assertRaises(ValueError):
            watchdog.recorded_dates(json.dumps({"schema_version": 1, "rows": []}).encode())


def _decision(**changes: object) -> watchdog.Decision:
    base: dict[str, Any] = {"target": TODAY, "dispatch": False, "cancel": (), "alerts": (), "reason": "test"}
    return watchdog.Decision(**{**base, **changes})


class ApplyTests(unittest.TestCase):
    def _apply(self, decision: watchdog.Decision, api: FakeApi) -> str:
        output = io.StringIO()
        with redirect_stdout(output):
            watchdog.apply(decision, api, run_url="https://github.com/o/r/actions/runs/1")
        return output.getvalue()

    def test_nothing_to_do_makes_no_calls(self) -> None:
        api = FakeApi({})
        self._apply(_decision(), api)
        self.assertEqual((api.gets, api.posts), ([], []))

    def test_dispatches_a_single_day_run_on_main(self) -> None:
        api = FakeApi({})
        self._apply(_decision(dispatch=True), api)
        self.assertEqual(api.posts, [("actions/workflows/daily-briefing.yml/dispatches",
                                      {"ref": "main", "inputs": {"mode": "single-day"}})])

    def test_a_refused_cancel_does_not_stop_the_dispatch(self) -> None:
        api = FakeApi({}, fail_posts={"actions/runs/7/cancel": 409})
        output = self._apply(_decision(dispatch=True, cancel=(7,)), api)
        self.assertIn("Could not cancel run 7: HTTP 409", output)
        self.assertEqual([path for path, _ in api.posts], ["actions/workflows/daily-briefing.yml/dispatches"])

    def test_opens_one_issue_per_missing_date(self) -> None:
        api = FakeApi({"issues": [
            {"title": "Daily briefing missing for 2026-11-15"},
            {"title": "Daily briefing missing for 2026-11-16", "pull_request": {}},
        ]})
        self._apply(_decision(alerts=(YESTERDAY, TODAY)), api)
        self.assertEqual([body["title"] for _, body in api.posts if body],
                         ["Daily briefing missing for 2026-11-16"])
        body = api.posts[0][1]
        assert body is not None
        self.assertIn("cannot be regenerated", str(body["body"]))
        self.assertIn("https://github.com/o/r/actions/runs/1", str(body["body"]))

    def test_finds_an_existing_issue_on_a_later_page(self) -> None:
        def pages(query: dict[str, str]) -> list[dict[str, object]]:
            if query["page"] == "1":
                return [{"title": f"Unrelated {n}"} for n in range(watchdog.ISSUE_PAGE_SIZE)]
            return [{"title": "Daily briefing missing for 2026-11-16"}]
        api = FakeApi({"issues": pages})
        self._apply(_decision(alerts=(TODAY,)), api)
        self.assertEqual(api.posts, [])
        self.assertEqual([query["page"] for _, query in api.gets], ["1", "2"])

    def test_refuses_to_open_an_issue_without_seeing_every_page(self) -> None:
        full = [{"title": "Unrelated"}] * watchdog.ISSUE_PAGE_SIZE
        api = FakeApi({"issues": full})
        with self.assertRaises(ValueError):
            self._apply(_decision(alerts=(TODAY,)), api)
        self.assertEqual(api.posts, [])


class GitHubApiTests(unittest.TestCase):
    def test_requires_a_repository_and_token(self) -> None:
        for repository, token in (("", "t"), ("owner/name/extra", "t"), ("owner/name", "")):
            with self.subTest(repository=repository), self.assertRaises(ValueError):
                watchdog.GitHubApi(repository, token)


class WatchdogWorkflowTests(unittest.TestCase):
    def test_runs_hourly_and_manual_runs_default_to_dry_run(self) -> None:
        triggers = WORKFLOW.split("permissions:", 1)[0]
        self.assertIn('cron: "41 * * * *"', triggers)
        dry_run = triggers.split("      dry_run:\n", 1)[1]
        self.assertIn("        default: true", dry_run)
        self.assertIn("        type: boolean", dry_run)
        for automatic_trigger in ("push:", "pull_request:"):
            self.assertNotIn(automatic_trigger, triggers)

    def test_permissions_cover_only_dispatch_cancel_and_issues(self) -> None:
        permissions = WORKFLOW.split("permissions:\n", 1)[1].split("\n\n", 1)[0]
        self.assertEqual(sorted(line.strip() for line in permissions.splitlines()),
                         ["actions: write", "contents: read", "issues: write"])

    def test_uses_no_secrets_and_checks_out_main_without_credentials(self) -> None:
        self.assertNotIn("secrets.", WORKFLOW)
        self.assertIn("          ref: main\n          persist-credentials: false", WORKFLOW)
        self.assertIn("python3 -S briefing_watchdog.py $DRY_RUN", WORKFLOW)
        for action in re.findall(r"uses: (\S+)", WORKFLOW):
            self.assertRegex(action, r"@[0-9a-f]{40}$")

    def test_dispatched_mode_is_one_the_daily_workflow_accepts(self) -> None:
        self.assertIn("workflow_dispatch:", DAILY_WORKFLOW)
        self.assertIn("          - single-day", DAILY_WORKFLOW)
        self.assertTrue((REPOSITORY / ".github/workflows" / watchdog.WORKFLOW_FILE).is_file())


if __name__ == "__main__":
    unittest.main()
