import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent_runner.audit_metrics import aggregate
from agent_runner.jev_review import MAX_CALLS
from agent_runner.semantic_repairs import daily_semantic_review
from rerun_jev_partials import replay, tree_digest
from tests.test_semantic_repairs import Judge, make_run


class PartialReplayTests(unittest.TestCase):
    def baseline(self, root):
        source = root / 'original'
        source.mkdir()
        for day, partial in (('2026-08-11', True), ('2026-08-12', False), ('2026-08-13', True)):
            directory = source / day
            directory.mkdir()
            run, _ = make_run(directory, report_date=day)
            daily_semantic_review(run, directory / 'review', judge=Judge('never', missing_cost=partial))
        metrics = root / 'baseline.json'
        metrics.write_text(json.dumps(aggregate(source, start=date(2026, 8, 11), end=date(2026, 8, 13))))
        protocol = root / 'protocol.json'
        protocol.write_text(json.dumps({'protocol_id': 'synthetic-partial-replay',
                                        'review_call_limit': MAX_CALLS,
                                        'baseline_metrics_sha256': hashlib.sha256(metrics.read_bytes()).hexdigest(),
                                        'sample_dates': ['2026-08-11', '2026-08-13']}))
        return source, metrics, protocol

    def test_replay_uses_same_frozen_runs_and_keeps_nonconsecutive_cohort_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            original = tree_digest(source)
            result = replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                            judge=Judge('never'), code_commit='synthetic')
            self.assertEqual(tree_digest(source), original)
            self.assertEqual(result['generation_calls'], 0)
            self.assertTrue(result['original_artifacts_unchanged'])
            self.assertEqual(result['metrics']['status_counts'], {'complete': 2})
            self.assertEqual(result['metrics']['coverage']['trials'], 2)
            self.assertEqual(result['metrics']['coverage']['successes'], 2)
            self.assertEqual(result['metrics']['sampling_frame']['sample_dates'],
                             ['2026-08-11', '2026-08-13'])
            self.assertEqual({p.name for p in (root / 'supplement').iterdir()}, {'2026-08-11', '2026-08-13'})
            for item in result['replay_ledger']:
                day = item['report_date']
                self.assertEqual(tree_digest(source / day / 'run'),
                                 tree_digest(root / 'supplement' / day / 'run'))
            serialized = json.dumps(result)
            for forbidden in ('headline', 'excerpt', 'prose', str(source), 'OPENROUTER'):
                self.assertNotIn(forbidden, serialized)
            self.assertEqual(json.loads((root / 'result.json').read_text()), result)
            with self.assertRaisesRegex(ValueError, 'must be new'):
                replay(source, baseline, protocol, root / 'supplement', root / 'result.json', judge=Judge('never'))

    def test_changed_protocol_or_baseline_fails_before_creating_replay(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, baseline, protocol = self.baseline(root)
            valid = json.loads(protocol.read_text())
            for broken in ({**valid, 'review_call_limit': MAX_CALLS - 1},
                           {**valid, 'baseline_metrics_sha256': '0' * 64},
                           {**valid, 'sample_dates': ['2026-08-11']}):
                protocol.write_text(json.dumps(broken))
                with self.assertRaises(ValueError):
                    replay(source, baseline, protocol, root / 'supplement', root / 'result.json',
                           judge=Judge('never'))
                self.assertFalse((root / 'supplement').exists())
            protocol.write_text(json.dumps(valid))
            (source / '2026-08-11' / 'review' / 'audit.json').write_text('{}')
            with self.assertRaises(ValueError):
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
