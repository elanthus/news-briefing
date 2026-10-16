import json
import tempfile
import unittest
from pathlib import Path

import run_jev_stage
from run_jev_stage import DAY_RECORD, DayFailure, load_sample, run_sample, write_ledger


class FakeCommands:
    """Simulate the restore and daily commands by writing the files they would write."""

    def __init__(self, *, obtainable=None, failing_days=(), review_crash_days=(), run_exit_days=()):
        self.obtainable = obtainable
        self.failing_days = set(failing_days)
        self.review_crash_days = set(review_crash_days)
        self.run_exit_days = set(run_exit_days)
        self.calls = []

    def __call__(self, command):
        self.calls.append(command)
        script = Path(command[2]).name
        options = dict(zip(command[3::2], command[4::2], strict=True))
        if script == 'restore_private_corpora.py':
            day = options['--newest']
            if self.obtainable is not None and day not in self.obtainable:
                return 4
            out = Path(options['--output-dir'])
            out.mkdir(parents=True)
            (out / f'{day}.json').write_text(json.dumps({'report_date': day}))
            return 0
        assert script == 'run_daily_briefing.py'
        run = Path(options['--run-dir'])
        day = run.parent.name
        run.mkdir(parents=True)
        if day in self.run_exit_days:
            (run / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'x'}))
            return 1
        if day in self.failing_days:
            (run / 'fallback-log.json').write_text(json.dumps(
                {'status': 'failed', 'selected_run_dir': None, 'generation_budget': {'known_spend_usd': 0.02}}))
            return 1
        (run / 'hy3').mkdir()
        (run / 'hy3' / 'manifest.json').write_text('{"status": "complete"}')
        (run / 'fallback-log.json').write_text(json.dumps(
            {'status': 'ready', 'selected_run_dir': 'hy3', 'generation_budget': {'known_spend_usd': 0.05,
                                                                                 'unknown_reserved_usd': 0.0}}))
        Path(options['--output']).write_text('# briefing\n')
        if day not in self.review_crash_days:
            review = Path(options['--jev-review-dir'])
            review.mkdir(parents=True)
            (review / 'audit.json').write_text(json.dumps(
                {'metadata': {}, 'audit': {'reported_cost_usd': 0.1, 'unknown_cost_calls': 0}, 'integrity': {}}))
        return 0


class RunJevStageTests(unittest.TestCase):
    def test_sample_file_is_well_formed_and_runner_commands_match_the_plan(self):
        dates = load_sample(run_jev_stage.DEFAULT_SAMPLE)
        self.assertEqual(len(dates), 28)
        self.assertEqual(dates, sorted(dates))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            restore = run_jev_stage.restore_command('2026-10-15', root / 'wrappers', root / 'd' / 'corpora')
            self.assertEqual(restore[1], '-S')
            self.assertIn('--artifact-dir', restore)
            run = run_jev_stage.run_command('2026-10-15', root / '2026-10-15', 1.0)
            self.assertEqual(run[1], '-S')
            self.assertEqual(run[run.index('--jev-review-dir') + 1], str(root / '2026-10-15' / 'review'))
            self.assertEqual(run[run.index('--corpus') + 1], str(root / '2026-10-15' / 'corpora' / '2026-10-15.json'))

    def test_runs_in_order_records_digests_and_skips_complete_days(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'stage'
            commands = FakeCommands(failing_days={'2026-10-02'})
            messages = []
            dates = ['2026-10-01', '2026-10-02', '2026-10-03']
            records = run_sample(dates, root, Path(directory) / 'wrappers', generation_usd=1.0,
                                 execute=commands, commit='abc', report=messages.append)
            self.assertEqual([r['status'] for r in records], ['reviewed', 'generation_failure', 'reviewed'])
            record = json.loads((root / '2026-10-01' / DAY_RECORD).read_text())
            self.assertEqual(set(record['artifact_sha256']), {'run/fallback-log.json', 'corpora/2026-10-01.json',
                                                              'run/hy3/manifest.json', 'briefing.md',
                                                              'review/audit.json'})
            self.assertEqual(record['code_commit'], 'abc')
            self.assertAlmostEqual(record['costs']['review_reported_cost_usd'], 0.1)
            failed = json.loads((root / '2026-10-02' / DAY_RECORD).read_text())
            self.assertEqual(set(failed['artifact_sha256']), {'run/fallback-log.json', 'corpora/2026-10-02.json'})
            self.assertIn('cumulative reported cost USD 0.3200', messages[-1])
            # A second pass makes no further calls for completed days.
            count = len(commands.calls)
            again = run_sample(dates, root, Path(directory) / 'wrappers', generation_usd=1.0,
                               execute=commands, report=messages.append)
            self.assertEqual(len(commands.calls), count)
            self.assertEqual([r['status'] for r in again], [r['status'] for r in records])
            ledger = Path(directory) / 'ledger.json'
            write_ledger(records, ledger)
            text = ledger.read_text()
            self.assertNotIn(str(root), text)
            self.assertEqual(len(json.loads(text)['days']), 3)

    def test_stops_on_non_generation_failures(self):
        cases = {
            'unobtainable date': FakeCommands(obtainable={'2026-10-01'}),
            'review stage did not complete': FakeCommands(review_crash_days={'2026-10-02'}),
            'nonzero exit with ready chain': FakeCommands(run_exit_days={'2026-10-02'}),
        }
        for label, commands in cases.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                root = Path(directory) / 'stage'
                with self.assertRaises(DayFailure):
                    run_sample(['2026-10-01', '2026-10-02', '2026-10-03'], root, Path(directory) / 'w',
                               generation_usd=1.0, execute=commands, report=lambda _: None)
                self.assertTrue((root / '2026-10-01' / DAY_RECORD).exists())
                self.assertFalse((root / '2026-10-02' / DAY_RECORD).exists())
                self.assertFalse((root / '2026-10-03').exists())

    def test_sample_validation_rejects_disorder_and_duplicates(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.json'
            for dates in (['2026-10-02', '2026-10-01'], ['2026-10-01', '2026-10-01'], []):
                path.write_text(json.dumps({'sample_dates': dates}))
                with self.subTest(dates=dates), self.assertRaises(ValueError):
                    load_sample(path)


if __name__ == '__main__':
    unittest.main()
