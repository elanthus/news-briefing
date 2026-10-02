from __future__ import annotations

import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import daily_publish


class FakeRunner:
    def __init__(self, restore_status: int) -> None:
        self.restore_status = restore_status
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        row = list(command)
        self.commands.append(row)
        if row[:2] == [sys.executable, "restore_private_corpora.py"]:
            return subprocess.CompletedProcess(row, self.restore_status, "", "")
        return subprocess.CompletedProcess(row, 0, "", "")


class RestoreCorpusTests(unittest.TestCase):
    def run_restore(self, runner: FakeRunner, root: Path) -> int:
        self.stdout = io.StringIO()
        with contextlib.redirect_stdout(self.stdout):
            return self._restore(runner, root)

    @staticmethod
    def _restore(runner: FakeRunner, root: Path) -> int:
        return daily_publish.restore_corpus(
            today=date(2026, 9, 3),
            event_name="schedule",
            manual_mode="",
            manual_report_date="",
            root=root,
            runner=runner,
        )

    def test_exit_zero_restores_then_prunes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(0)
            self.assertEqual(self.run_restore(runner, Path(directory)), 0)
            self.assertEqual(runner.commands[-1][1:3], ["private_archive.py", "prune-corpora"])


    def test_missing_archive_starts_fresh_without_public_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(4)
            self.assertEqual(self.run_restore(runner, Path(directory)), 0)
            self.assertIn("No unexpired private archive remains", self.stdout.getvalue())
            self.assertEqual([command[1] for command in runner.commands],
                             ["restore_private_corpora.py", "private_archive.py"])

    def test_exit_two_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(2)
            self.assertEqual(self.run_restore(runner, Path(directory)), 2)
            self.assertEqual(
                self.stdout.getvalue(), "::error::Private corpus restoration failed\n"
            )


class WindowTests(unittest.TestCase):
    def test_cli_honors_an_explicit_zero_snapshot_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            with contextlib.chdir(root):
                status = daily_publish.main([
                    "capture-window",
                    "--snapshot-end-epoch", "0",
                    "--github-env", str(github_env),
                ])
            self.assertEqual(status, 0)
            values = dict(
                line.split("=", 1)
                for line in github_env.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["SNAPSHOT_END_EPOCH"], "0")
            self.assertEqual(values["WINDOW_END"], "1970-01-01T00:00:00+00:00")

    def test_capture_window_writes_second_precision_utc_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            github_env = root / "github-env"
            snapshot = int(datetime(2026, 9, 3, 1, 0, tzinfo=UTC).timestamp())
            self.assertEqual(
                daily_publish.capture_window(
                    snapshot_end_epoch=snapshot,
                    github_env=github_env,
                    root=root,
                ),
                0,
            )
            values = dict(
                line.split("=", 1)
                for line in github_env.read_text(encoding="utf-8").splitlines()
            )
            self.assertRegex(values["WINDOW_START"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
            self.assertRegex(values["WINDOW_END"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+00:00$")
            start = datetime.fromisoformat(values["WINDOW_START"])
            end = datetime.fromisoformat(values["WINDOW_END"])
            self.assertEqual(end - start, timedelta(days=1))
            self.assertEqual(values["REPORT_TODAY"], "2026-09-02")
            report_today = date.fromisoformat(values["REPORT_TODAY"])
            self.assertEqual(
                daily_publish._report_dates(
                    event_name="workflow_dispatch",
                    manual_mode="backfill-7-days",
                    manual_report_date="",
                    today=report_today,
                ),
                [report_today - timedelta(days=days_ago) for days_ago in range(6, -1, -1)],
            )


class SingleDayValidationTests(unittest.TestCase):
    def assert_invalid_date(self, value: str, expected: str) -> None:
        runner = FakeRunner(0)
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(output):
            status = daily_publish.generate_reports(
                today=date(2026, 9, 3),
                window_start="2026-09-02T13:30:00+00:00",
                window_end="2026-09-03T13:30:00+00:00",
                event_name="workflow_dispatch",
                manual_mode="single-day",
                manual_report_date=value,
                root=Path(directory),
                runner=runner,
            )
        self.assertEqual(status, 1)
        self.assertEqual(runner.commands, [])
        self.assertIn(expected, output.getvalue())

    def test_malformed_single_day_is_rejected(self) -> None:
        self.assert_invalid_date(
            "2026-02-30",
            "::error::report_date must be a real calendar date in YYYY-MM-DD form",
        )

    def test_single_day_outside_retention_window_is_rejected(self) -> None:
        self.assert_invalid_date(
            "2026-08-20",
            "::error::report_date must be inside the retained private corpus window "
            "(2026-08-21 through 2026-09-03)",
        )


class GenerateReportsTests(unittest.TestCase):
    def test_backfill_report_date_loop_reuses_six_corpora_and_fetches_today(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "corpora").mkdir()
            (root / "runs").mkdir()
            (root / "reports").mkdir()
            (root / "briefing-history").mkdir()
            today = date(2026, 9, 3)
            for days_ago in range(1, 7):
                value = today - timedelta(days=days_ago)
                (root / "corpora" / f"{value.isoformat()}.json").write_text("{}")
            runner = FakeRunner(0)
            with contextlib.redirect_stdout(io.StringIO()):
                status = daily_publish.generate_reports(
                    today=today,
                    window_start="2026-09-02T13:30:00+00:00",
                    window_end="2026-09-03T13:30:00+00:00",
                    event_name="workflow_dispatch",
                    manual_mode="backfill-7-days",
                    manual_report_date="",
                    root=root,
                    runner=runner,
                )
            self.assertEqual(status, 0)
            scripts = [command[1] for command in runner.commands if command[0] == sys.executable]
            self.assertEqual(scripts.count("fetch_news.py"), 1)
            self.assertEqual(scripts.count("run_daily_briefing.py"), 7)
            for command in runner.commands:
                if command[:2] == [sys.executable, "run_daily_briefing.py"]:
                    self.assertEqual(command[command.index("--jev-repair-mode") + 1], "apply")
            self.assertEqual(scripts.count("prepare_publication.py"), 7)
            run_dates = [
                command[command.index("--date") + 1]
                for command in runner.commands
                if command[:2] == [sys.executable, "prepare_publication.py"]
            ]
            self.assertEqual(
                run_dates,
                [(today - timedelta(days=days_ago)).isoformat() for days_ago in range(6, -1, -1)],
            )
            fetch = next(
                command for command in runner.commands
                if command[:2] == [sys.executable, "fetch_news.py"]
            )
            self.assertEqual(fetch[fetch.index("--window-start") + 1], "2026-09-02T13:30:00+00:00")
            self.assertEqual(fetch[fetch.index("--window-end") + 1], "2026-09-03T13:30:00+00:00")
            self.assertEqual(fetch[fetch.index("--report-date") + 1], "2026-09-03")


class ScriptedRunner:
    """Returns a scripted exit status per script name and records every command."""

    def __init__(self, statuses: dict[str, int]) -> None:
        self.statuses = statuses
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        row = list(command)
        self.commands.append(row)
        return subprocess.CompletedProcess(row, self.statuses.get(row[1], 0), "", "")

    def scripts(self) -> list[str]:
        return [command[1] for command in self.commands]


class GenerateReportsFailureTests(unittest.TestCase):
    today = date(2026, 9, 3)

    def generate(
        self, root: Path, runner: ScriptedRunner, *, manual_mode: str = ""
    ) -> tuple[int, str]:
        for name in ("corpora", "runs", "reports", "briefing-history"):
            (root / name).mkdir(exist_ok=True)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            status = daily_publish.generate_reports(
                today=self.today,
                window_start="2026-09-02T13:30:00+00:00",
                window_end="2026-09-03T13:30:00+00:00",
                event_name="workflow_dispatch" if manual_mode else "schedule",
                manual_mode=manual_mode,
                manual_report_date="",
                root=root,
                runner=runner,
            )
        return status, stdout.getvalue()

    def test_failed_fetch_skips_generation_even_when_a_stale_corpus_exists(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = ScriptedRunner({"fetch_news.py": 1})
            (root / "corpora").mkdir()
            (root / "corpora" / "2026-09-03.json").write_text("{}", encoding="utf-8")
            status, stdout = self.generate(root, runner)
        self.assertEqual(status, 1)
        self.assertEqual(runner.scripts(), ["fetch_news.py", "prepare_publication.py"])
        self.assertIn("::warning::Corpus fetch failed for 2026-09-03", stdout)
        self.assertIn(
            "::warning::Skipping briefing generation for 2026-09-03: no corpus is available",
            stdout,
        )

    def test_backfill_uses_only_restored_private_corpora_for_prior_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = ScriptedRunner({})
            (root / "corpora").mkdir()
            (root / "corpora" / "2026-09-01.json").write_text("{}", encoding="utf-8")
            status, stdout = self.generate(root, runner, manual_mode="backfill-7-days")
        self.assertEqual(status, 1)
        corpora = [
            command[command.index("--corpus") + 1]
            for command in runner.commands
            if command[1] == "run_daily_briefing.py"
        ]
        self.assertEqual(
            [Path(value).name for value in corpora], ["2026-09-01.json", "2026-09-03.json"]
        )
        self.assertEqual(runner.scripts().count("fetch_news.py"), 1)
        self.assertEqual(stdout.count("no private stored corpus is available"), 5)

    def test_publication_preparation_failure_continues_with_remaining_dates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = ScriptedRunner({"prepare_publication.py": 1})
            (root / "corpora").mkdir()
            for day in ("2026-09-01", "2026-09-02"):
                (root / "corpora" / f"{day}.json").write_text("{}", encoding="utf-8")
            status, stdout = self.generate(root, runner, manual_mode="backfill-7-days")
        self.assertEqual(status, 1)
        self.assertEqual(runner.scripts().count("prepare_publication.py"), 3)
        self.assertEqual(stdout.count("::warning::Publication preparation failed"), 3)

    def test_generation_failure_warns_and_still_prepares_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = ScriptedRunner({"run_daily_briefing.py": 1})
            status, stdout = self.generate(root, runner)
        self.assertEqual(status, 1)
        self.assertEqual(
            runner.scripts(),
            ["fetch_news.py", "run_daily_briefing.py", "prepare_publication.py"],
        )
        self.assertIn("::warning::All briefing models failed for 2026-09-03", stdout)


class PublicationOutcomeTests(unittest.TestCase):
    def test_successful_deployment_does_not_hide_failed_generation(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(daily_publish.check_publication_outcome("failure", "success"), 1)
        self.assertIn("failure records or older briefings", output.getvalue())

    def test_generation_success_does_not_hide_stale_or_skipped_deployment(self) -> None:
        for outcome in ("failure", "skipped", "cancelled", ""):
            with self.subTest(outcome=outcome), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(daily_publish.check_publication_outcome("success", outcome), 1)
                self.assertIn("publication may be stale", output.getvalue())

    def test_both_steps_must_succeed(self) -> None:
        self.assertEqual(daily_publish.check_publication_outcome("success", "success"), 0)


class InterpreterTests(unittest.TestCase):
    def test_every_child_runs_on_the_parent_interpreter_without_flags(self) -> None:
        runner = FakeRunner(0)
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            root = Path(directory)
            daily_publish.restore_corpus(
                today=date(2026, 9, 3), event_name="schedule", manual_mode="",
                manual_report_date="", root=root, runner=runner,
            )
            daily_publish.generate_reports(
                today=date(2026, 9, 3),
                window_start="2026-09-02T13:30:00+00:00",
                window_end="2026-09-03T13:30:00+00:00",
                event_name="schedule", manual_mode="", manual_report_date="",
                root=root, runner=runner,
            )
        self.assertEqual(
            [command[:2] for command in runner.commands],
            [
                [sys.executable, "restore_private_corpora.py"],
                [sys.executable, "private_archive.py"],
                [sys.executable, "fetch_news.py"],
                [sys.executable, "run_daily_briefing.py"],
                [sys.executable, "prepare_publication.py"],
            ],
        )


class WorkflowIdentityTests(unittest.TestCase):
    def test_optional_valid_workflow_identity_is_forwarded_only_to_publication(self) -> None:
        for identity, expected in ((123456, True), (None, False), (0, False), (10**19, False), (True, False)):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as directory:
                runner = FakeRunner(0)
                with contextlib.redirect_stdout(io.StringIO()):
                    daily_publish.generate_reports(
                        today=date(2026, 10, 1), window_start="start", window_end="end",
                        event_name="schedule", manual_mode="", manual_report_date="",
                        root=Path(directory), runner=runner, workflow_run_id=identity)
                publication = next(c for c in runner.commands if c[1] == "prepare_publication.py")
                self.assertEqual("--workflow-run-id" in publication, expected)
                self.assertFalse(any("--workflow-run-id" in c for c in runner.commands if c != publication))


if __name__ == "__main__":
    unittest.main()
