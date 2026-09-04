from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import daily_publish


class FakeRunner:
    def __init__(self, restore_status: int, curl_statuses: list[str] | None = None) -> None:
        self.restore_status = restore_status
        self.curl_statuses = list(curl_statuses or [])
        self.commands: list[list[str]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[str]:
        row = list(command)
        self.commands.append(row)
        if row[:2] == ["python", "restore_private_corpora.py"]:
            return subprocess.CompletedProcess(row, self.restore_status, "", "")
        if row[0] == "curl":
            status = self.curl_statuses.pop(0)
            if status == "200":
                output = Path(row[row.index("--output") + 1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("{}", encoding="utf-8")
            return subprocess.CompletedProcess(row, 0, status, "")
        return subprocess.CompletedProcess(row, 0, "", "")


class RestoreCorpusTests(unittest.TestCase):
    def run_restore(self, runner: FakeRunner, root: Path) -> int:
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

    def test_exit_four_with_valid_marker_starts_fresh_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(4, ["200"])
            self.assertEqual(self.run_restore(runner, Path(directory)), 0)
            self.assertTrue(any("corpus_storage.py" in command for command in runner.commands))

    def test_exit_four_with_missing_marker_and_history_starts_first_window(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(4, ["404", "404"])
            self.assertEqual(self.run_restore(runner, Path(directory)), 0)
            self.assertEqual(sum(command[0] == "curl" for command in runner.commands), 2)

    def test_exit_four_with_marker_download_error_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(4, ["500"])
            self.assertEqual(self.run_restore(runner, Path(directory)), 1)
            self.assertFalse(any("prune-corpora" in command for command in runner.commands))

    def test_exit_two_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = FakeRunner(2)
            self.assertEqual(self.run_restore(runner, Path(directory)), 2)

    def test_legacy_migration_downloads_prunes_and_copies_all_thirteen(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner(4, ["404", "200", *(["200"] * 13)])
            self.assertEqual(self.run_restore(runner, root), 0)
            restored = sorted((root / "corpora").glob("*.json"))
            self.assertEqual(len(restored), 13)
            prune_commands = [
                command for command in runner.commands
                if command[:3] == ["python", "private_archive.py", "prune-corpora"]
            ]
            self.assertEqual(
                [Path(command[3]).name for command in prune_commands],
                ["legacy-corpora", "corpora"],
            )

    def test_incomplete_legacy_migration_without_target_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = FakeRunner(4, ["404", "200", *(["200"] * 5), *(["404"] * 8)])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = self.run_restore(runner, root)
            self.assertEqual(status, 1)
            self.assertIn("::error::Downloaded only 5 of 13", output.getvalue())
            self.assertFalse(any("prune-corpora" in command for command in runner.commands))

    def test_incomplete_legacy_migration_with_available_target_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            statuses = ["404"] * 13
            statuses[2] = "200"
            runner = FakeRunner(4, ["404", "200", *statuses])
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                status = daily_publish.restore_corpus(
                    today=date(2026, 9, 3),
                    event_name="workflow_dispatch",
                    manual_mode="single-day",
                    manual_report_date="2026-08-31",
                    root=root,
                    runner=runner,
                )
            self.assertEqual(status, 0)
            self.assertIn("::warning::Downloaded 1 of 13", output.getvalue())
            self.assertIn("continuing targeted repair for 2026-08-31", output.getvalue())
            self.assertTrue((root / "corpora" / "2026-08-31.json").is_file())


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
            self.assertEqual(
                daily_publish.generate_reports(
                    today=today,
                    window_start="2026-09-02T13:30:00+00:00",
                    window_end="2026-09-03T13:30:00+00:00",
                    event_name="workflow_dispatch",
                    manual_mode="backfill-7-days",
                    manual_report_date="",
                    root=root,
                    runner=runner,
                ),
                0,
            )
            scripts = [command[1] for command in runner.commands if command[0] == "python"]
            self.assertEqual(scripts.count("fetch_news.py"), 1)
            self.assertEqual(scripts.count("run_daily_briefing.py"), 7)
            self.assertEqual(scripts.count("prepare_publication.py"), 7)
            run_dates = [
                command[command.index("--date") + 1]
                for command in runner.commands
                if command[:2] == ["python", "prepare_publication.py"]
            ]
            self.assertEqual(
                run_dates,
                [(today - timedelta(days=days_ago)).isoformat() for days_ago in range(6, -1, -1)],
            )
            fetch = next(
                command for command in runner.commands
                if command[:2] == ["python", "fetch_news.py"]
            )
            self.assertEqual(fetch[fetch.index("--window-start") + 1], "2026-09-02T13:30:00+00:00")
            self.assertEqual(fetch[fetch.index("--window-end") + 1], "2026-09-03T13:30:00+00:00")
            self.assertEqual(fetch[fetch.index("--report-date") + 1], "2026-09-03")


if __name__ == "__main__":
    unittest.main()
