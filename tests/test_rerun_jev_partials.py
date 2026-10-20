import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from agent_runner.audit_metrics import aggregate
from agent_runner.jev_review import MAX_CALLS
from agent_runner.semantic_repairs import daily_semantic_review
from rerun_jev_partials import DAY_LEDGER, clean_commit, main, replay, tree_digest
from tests.test_semantic_repairs import Judge, PatchProvider, make_run

# Every fixture judge and repair call reports this cost.
CALL_COST = 0.00001


class CountingProvider(PatchProvider):
    """Note the judge calls made before each repair; repair runs between the review phases."""

    def __init__(self, judge):
        super().__init__()
        self.judge = judge
        self.judged_before = []

    def generate(self, request):
        self.judged_before.append(len(self.judge.calls))
        return super().generate(request)


class PartialReplayTests(unittest.TestCase):
    def test_clean_revision_requires_successful_git_status_and_committed_head(self):
        for status, commit, allowed in ((SimpleNamespace(returncode=0, stdout=''), 'abc', True),
                                       (SimpleNamespace(returncode=0, stdout=' M runner.py'), 'abc', False),
                                       (SimpleNamespace(returncode=0, stdout='?? local.py'), 'abc', False),
                                       (SimpleNamespace(returncode=1, stdout=''), 'abc', False),
                                       (SimpleNamespace(returncode=0, stdout=''), None, False)):
            with self.subTest(status=status, commit=commit), patch(
                'rerun_jev_partials.subprocess.run', return_value=status
            ), patch('rerun_jev_partials._git_commit', return_value=commit):
                if allowed:
                    self.assertEqual(clean_commit(), commit)
                else:
                    with self.assertRaisesRegex(ValueError, 'clean committed worktree'):
                        clean_commit()

    def test_cli_refuses_dirty_code_before_replay_or_provider_calls(self):
        arguments = ['rerun_jev_partials.py', '--source-root', 'original', '--output-root', 'new',
                     '--output', 'new-result.json']
        with patch('sys.argv', arguments), patch.dict('os.environ', {'OPENROUTER_API_KEY': 'synthetic'}), patch(
            'rerun_jev_partials.clean_commit', side_effect=ValueError('replay requires a clean committed worktree')
        ), patch('rerun_jev_partials.replay') as paid_replay, redirect_stderr(io.StringIO()), self.assertRaises(
            SystemExit
        ) as caught:
            main()
        self.assertEqual(caught.exception.code, 2)
        paid_replay.assert_not_called()

    def baseline(self, root):
        source = root / 'original'
        source.mkdir()
        for day, partial in (('2026-08-11', True), ('2026-08-12', False), ('2026-08-13', True)):
            directory = source / day
            directory.mkdir()
            run, self.headline = make_run(directory, report_date=day)
            daily_semantic_review(run, directory / 'review', judge=Judge('never', missing_cost=partial))
        metrics = root / 'baseline.json'
        metrics.write_text(json.dumps(aggregate(source, start=date(2026, 8, 11), end=date(2026, 8, 13))))
        protocol = root / 'protocol.json'
        protocol.write_text(json.dumps({'protocol_id': 'synthetic-partial-replay',
                                        'review_call_limit': MAX_CALLS,
                                        'baseline_metrics_sha256': hashlib.sha256(metrics.read_bytes()).hexdigest(),
                                        'sample_dates': ['2026-08-11', '2026-08-13']}))
        return source, metrics, protocol

    def counted_review(self, judge, provider, calls, *, fail_on=None):
        """Record each day's judge calls and repair requests around the real review."""
        def review(run, destination, **options):
            if len(calls) + 1 == fail_on:
                raise RuntimeError('synthetic provider outage')
            seen, requested = len(judge.calls), len(provider.judged_before)
            audit = daily_semantic_review(run, destination, **options)
            calls.append((len(judge.calls) - seen, [n - seen for n in provider.judged_before[requested:]]))
            return audit
        return patch('rerun_jev_partials.daily_semantic_review', side_effect=review)

    def test_replay_uses_same_frozen_runs_and_keeps_nonconsecutive_cohort_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            original = tree_digest(source)
            judge = Judge(self.headline)
            provider, calls = CountingProvider(judge), []
            with self.counted_review(judge, provider, calls):
                result = replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                                judge=judge, repair_provider=provider, code_commit='synthetic')
            self.assertEqual(tree_digest(source), original)
            self.assertEqual(result['generation_calls'], 0)
            self.assertTrue(result['original_artifacts_unchanged'])
            self.assertEqual(result['metrics']['status_counts'], {'complete': 2})
            self.assertEqual(result['metrics']['coverage']['trials'], 2)
            self.assertEqual(result['metrics']['coverage']['successes'], 2)
            self.assertEqual(result['metrics']['sampling_frame']['sample_dates'],
                             ['2026-08-11', '2026-08-13'])
            self.assertEqual({p.name for p in (root / 'supplement').iterdir()}, {'2026-08-11', '2026-08-13'})
            self.assertEqual(len(calls), 2)
            self.assertAlmostEqual(result['reported_cost_usd'],
                                   (len(judge.calls) + len(provider.requests)) * CALL_COST, places=12)
            self.assertEqual(result['unknown_cost_calls'], 0)
            for item, (judged, repairs) in zip(result['replay_ledger'], calls, strict=True):
                day = item['report_date']
                self.assertEqual(tree_digest(source / day / 'run'),
                                 tree_digest(root / 'supplement' / day / 'run'))
                initial = repairs[0]
                self.assertEqual(set(repairs), {initial})
                followup = judged - initial
                self.assertGreater(followup, 0)
                planned = json.loads((source / day / 'review' / 'report.json').read_text())['planned_checks']
                self.assertEqual(item['review_counts'], {
                    phase: {'calls': count, 'planned_checks': planned, 'returned_checks': planned,
                            'stop_code': None, 'reached_call_limit': False}
                    for phase, count in (('initial', initial), ('followup', followup))})
                day_ledger = json.loads((root / 'supplement' / day / DAY_LEDGER).read_text())
                self.assertEqual(day_ledger, {**item, 'reported_cost_usd': day_ledger['reported_cost_usd'],
                                              'unknown_cost_calls': 0})
                self.assertAlmostEqual(day_ledger['reported_cost_usd'], (judged + len(repairs)) * CALL_COST,
                                       places=12)
            serialized = json.dumps(result)
            for forbidden in ('headline', 'excerpt', 'prose', str(source), 'OPENROUTER'):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(json.loads((root / 'result.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'must be new'):
                replay(source, baseline, protocol, root / 'supplement', root / 'result.json', judge=Judge('never'))

    def test_ledger_marks_reviews_that_reach_the_frozen_call_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            protocol.write_text(json.dumps({**json.loads(protocol.read_text()), 'review_call_limit': 1}))
            with patch('rerun_jev_partials.MAX_CALLS', 1):
                result = replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                                judge=Judge('never'), code_commit='synthetic')
            self.assertEqual(result['review_call_limit'], 1)
            for item in result['replay_ledger']:
                self.assertTrue(item['review_counts']['initial']['reached_call_limit'])

    def test_late_failure_keeps_completed_day_ledger_and_refuses_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            judge = Judge('never')
            provider, calls = CountingProvider(judge), []
            with self.counted_review(judge, provider, calls, fail_on=2), self.assertRaisesRegex(
                RuntimeError, 'synthetic provider outage'
            ):
                replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                       judge=judge, repair_provider=provider, code_commit='synthetic')
            day = root / 'supplement' / '2026-08-11'
            record = json.loads((day / DAY_LEDGER).read_text())
            (judged, repairs), = calls
            self.assertEqual(set(record), {'report_date', 'source_day_sha256', 'baseline_audit_sha256',
                                           'copied_run_sha256', 'replay_audit_sha256', 'review_tree_sha256',
                                           'review_counts', 'reported_cost_usd', 'unknown_cost_calls'})
            self.assertEqual(record['report_date'], '2026-08-11')
            self.assertEqual(record['source_day_sha256'], tree_digest(source / '2026-08-11'))
            self.assertEqual(record['replay_audit_sha256'],
                             hashlib.sha256((day / 'review' / 'audit.json').read_bytes()).hexdigest())
            self.assertEqual(record['review_tree_sha256'], tree_digest(day / 'review'))
            self.assertEqual(repairs, [])
            self.assertEqual(record['review_counts']['initial']['calls'], judged)
            self.assertAlmostEqual(record['reported_cost_usd'], judged * CALL_COST, places=12)
            self.assertEqual(record['unknown_cost_calls'], 0)
            self.assertFalse((root / 'supplement' / '2026-08-13' / DAY_LEDGER).exists())
            self.assertFalse((root / 'result.json').exists())
            with self.assertRaisesRegex(ValueError, 'must be new'):
                replay(source, baseline, protocol, root / 'supplement', root / 'result.json', judge=Judge('never'))

    def test_input_changed_during_replay_is_not_published(self):
        for target in ('baseline', 'protocol', 'source day'):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source, baseline, protocol = self.baseline(root)
                path = {'baseline': baseline, 'protocol': protocol,
                        'source day': source / '2026-08-11' / 'review' / 'audit.json'}[target]

                def review(run, destination, *, path=path, **options):
                    if not path.read_bytes().endswith(b' '):
                        path.write_bytes(path.read_bytes() + b' ')
                    return daily_semantic_review(run, destination, **options)

                with patch('rerun_jev_partials.daily_semantic_review', side_effect=review), \
                        self.assertRaisesRegex(ValueError, 'baseline changed during replay'):
                    replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                           judge=Judge('never'), code_commit='synthetic')
                self.assertFalse((root / 'result.json').exists())

    def test_overlapping_destinations_are_refused_before_creating_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            before = sorted(root.rglob('*'))
            for output_root, output_path in ((source / 'supplement', root / 'result.json'),
                                             (root, root / 'elsewhere' / 'result.json'),
                                             (root / 'supplement', source / 'result.json'),
                                             (root / 'supplement', root / 'supplement' / 'result.json')):
                with self.subTest(output_root=output_root, output_path=output_path), \
                        self.assertRaisesRegex(ValueError, 'must be separate'):
                    replay(source, baseline, protocol, output_root, output_path, judge=Judge('never'))
                self.assertEqual(sorted(root.rglob('*')), before)

    def test_changed_protocol_or_baseline_fails_before_creating_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            valid = json.loads(protocol.read_text())
            for broken, message in (({**valid, 'review_call_limit': MAX_CALLS - 1}, 'review call limit differs'),
                                    ({**valid, 'baseline_metrics_sha256': '0' * 64}, 'baseline digest'),
                                    ({**valid, 'sample_dates': ['2026-08-11']}, 'protocol cohort must equal')):
                protocol.write_text(json.dumps(broken))
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                           judge=Judge('never'))
                self.assertFalse((root / 'supplement').exists())
            # Source artifacts that still verify must also match the frozen metric rows.
            frozen = baseline.read_text()
            for field, value in (('audit_sha256', '0' * 64), ('status', 'missing_review')):
                metrics = json.loads(frozen)
                next(row for row in metrics['days'] if row['report_date'] == '2026-08-12')[field] = value
                baseline.write_text(json.dumps(metrics))
                protocol.write_text(json.dumps(
                    {**valid, 'baseline_metrics_sha256': hashlib.sha256(baseline.read_bytes()).hexdigest()}))
                with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'baseline artifacts differ'):
                    replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                           judge=Judge('never'))
                self.assertFalse((root / 'supplement').exists())
            baseline.write_text(frozen)
            protocol.write_text(json.dumps(valid))
            (source / '2026-08-11' / 'review' / 'audit.json').write_text('{}')
            with self.assertRaisesRegex(ValueError, 'daily audit verification'):
                replay(source, baseline, protocol, root / 'supplement', root / 'result.json', judge=Judge('never'))
            self.assertFalse((root / 'supplement').exists())

    def test_selected_frame_keeps_missing_selected_days_and_rejects_extra_days(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _, _ = self.baseline(root)
            options = {'start': date(2026, 8, 11), 'end': date(2026, 8, 14)}
            result = aggregate(source, **options,
                               sample_dates=['2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14'])
            self.assertEqual(result['coverage']['trials'], 4)
            self.assertEqual(result['status_counts']['missing_day'], 1)
            for dates in ([], ['2026-08-12', '2026-08-11'], ['2026-08-11'] * 2, ['2026-08-15'],
                          ['2026-08-11', '2026-08-13']):
                with self.subTest(dates=dates), self.assertRaises(ValueError):
                    aggregate(source, **options, sample_dates=dates)


if __name__ == '__main__':
    unittest.main()
