import copy
import json
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from agent_runner.audit_metrics import aggregate as aggregate_daily
from agent_runner.audit_metrics import observations, rate
from agent_runner.jev_review import load_topics
from agent_runner.models import ProviderError
from agent_runner.semantic_repairs import daily_semantic_review
from evaluator.metrics import wilson_interval
from tests.test_semantic_repairs import CitationJudge, Judge, PatchProvider, make_run, split_review


def aggregate(root):
    dates = sorted(p.name for p in root.iterdir())
    return aggregate_daily(root, start=date.fromisoformat(dates[0]), end=date.fromisoformat(dates[-1]))


class UnknownOnFlagJudge(Judge):
    def evaluate(self, *args, **kwargs):
        result = super().evaluate(*args, **kwargs)
        if any(p >= 0.8 for p in result['probabilities'].values()):
            result['cost_usd'] = None
        return result


class FailedJudge:
    def evaluate(self, *args, **kwargs):
        raise ProviderError('private provider response', transient=False)


class AuditMetricsTests(unittest.TestCase):
    def day(self, root, name='2026-08-11'):
        day = root / name
        day.mkdir()
        return day

    def test_complete_repair_metrics_and_public_projection_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            run, headline = make_run(day, report_date=day.name)
            audit = daily_semantic_review(run, day / 'review', judge=Judge(headline), repair_provider=PatchProvider())
            result = aggregate(root)
            self.assertEqual(result['coverage'], rate(1, 1))
            self.assertEqual(result['flag_rate_by_kind']['unsupported_claim']['successes'], 1)
            self.assertEqual(result['flag_rate_by_kind']['unsupported_claim']['trials'],
                             sum(c['check'] == 'unsupported_claim' for c in audit['checks']))
            self.assertEqual(result['confirmation_rates']['confirmed'], rate(1, 1))
            self.assertEqual(result['repair_round_rate'], rate(1, 1))
            self.assertEqual(result['per_slot_acceptance']['apply_repair'], rate(1, 1))
            self.assertEqual(result['days'][0]['publication_outcome'], 'repaired_applied')
            costs = result['days'][0]['costs']
            self.assertEqual({c['phase'] for c in costs}, {'initial_review', 'repair_generation', 'followup_review'})
            self.assertAlmostEqual(sum(c['reported_cost_usd'] for c in costs), audit['reported_cost_usd'])
            self.assertEqual(sum(c['unknown_cost_calls'] for c in costs), 0)
            serialized = json.dumps(result)
            self.assertNotIn(headline, serialized)
            for forbidden in ('typesafe', 'openrouter', 'excerpt', 'metadata', 'private provider response', str(day)):
                self.assertNotIn(forbidden, serialized)

    def test_partial_failed_and_generation_failure_stay_in_coverage_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, judge in [('2026-08-11', Judge('never', missing_cost=True)),
                                ('2026-08-12', FailedJudge()), ('2026-08-13', Judge('never'))]:
                day = self.day(root, name)
                run, _ = make_run(day, report_date=day.name)
                daily_semantic_review(run, day / 'review', judge=judge)
            day = self.day(root, '2026-08-14')
            (day / 'run').mkdir()
            (day / 'run/fallback-log.json').write_text(json.dumps({'status': 'failed', 'selected_run_dir': None}))
            result = aggregate(root)
            self.assertEqual(result['coverage'], rate(1, 4))
            self.assertEqual(result['status_counts'],
                             {'partial': 1, 'failed': 1, 'complete': 1, 'generation_failure': 1})
            self.assertEqual(result['repair_round_rate'], rate(0, 1))
            self.assertGreater(sum(c['unknown_cost_calls'] for c in result['days'][1]['costs']), 0)
            self.assertTrue(all(c['trials'] == 0 for c in result['confirmation_rates'].values()))

    def test_partial_followup_is_excluded_and_cost_uncertainty_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            run, headline = make_run(day, report_date=day.name)
            daily_semantic_review(run, day / 'review', judge=Judge(headline, fail_after_repair=True),
                                  repair_provider=PatchProvider())
            result = aggregate(root)
            self.assertEqual(result['coverage'], rate(0, 1))
            self.assertEqual(result['per_slot_acceptance']['withhold_repair'], rate(0, 0))
            self.assertGreater(sum(c['unknown_cost_calls'] for c in result['days'][0]['costs']), 0)

    def test_mixed_slots_disputed_flags_and_fixed_causes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            split_review(day, report_date=day.name)
            day2 = self.day(root, '2026-08-12')
            run, headline = make_run(day2, report_date=day2.name)
            daily_semantic_review(run, day2 / 'review', judge=Judge(headline, dispute=True))
            result = aggregate(root)
            self.assertEqual(result['per_slot_acceptance']['apply_repair'], rate(1, 2))
            self.assertEqual(result['per_slot_acceptance']['withhold_repair'], rate(1, 2))
            self.assertEqual(result['slot_causes']['withhold_repair']['followup_flag'], rate(1, 1))
            self.assertEqual(result['confirmation_rates']['disputed'], rate(1, 3))
            self.assertEqual(result['followup_blockers_by_kind']['unsupported_claim'], 1)
            self.assertEqual(result['publication_outcomes']['partial_applied'], rate(1, 2))

    def test_failed_assembly_and_skipped_last_source_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            with patch('agent_runner.semantic_repairs.assemble_accepted', side_effect=ValueError('private')):
                split_review(day, report_date=day.name)
            result = aggregate(root)
            self.assertEqual(result['per_slot_acceptance']['failed'], rate(2, 2))
            self.assertEqual(result['slot_causes']['failed']['invalid_candidate'], rate(2, 2))
            self.assertEqual(result['days'][0]['publication_outcome'], 'original_retained')

    def test_skipped_source_removal_and_unconfirmed_partial_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            run, headline = make_run(day, report_date=day.name)
            topic = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            daily_semantic_review(run, day / 'review', judge=CitationJudge(headline, topic['evidence'][0]['title']))
            day2 = self.day(root, '2026-08-12')
            run, headline = make_run(day2, report_date=day2.name)
            daily_semantic_review(run, day2 / 'review', judge=UnknownOnFlagJudge(headline))
            result = aggregate(root)
            self.assertEqual(result['per_slot_acceptance']['skipped'], rate(1, 1))
            self.assertEqual(result['slot_causes']['skipped']['all_sources_removed'], rate(1, 1))
            counts = result['days'][1]['counts']['confirmations']['unsupported_claim']
            self.assertEqual(counts['unconfirmed'], 1)
            self.assertEqual(result['confirmation_rates']['unconfirmed'], rate(0, 1))

    def test_estimated_budget_guard_is_visible_without_complete_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            day = self.day(root)
            run, _ = make_run(day, report_date=day.name)
            with patch('agent_runner.jev_review.MAX_CALLS', 0):
                daily_semantic_review(run, day / 'review', judge=Judge('never'))
            result = aggregate(root)
            self.assertEqual(result['coverage'], rate(0, 1))
            self.assertEqual(result['days_stopped_by_estimated_spending_guard'], 1)

    def test_two_position_followup_blocker_counts_both_slots_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, audit, first, second = split_review(root)
            envelope = json.loads((root / 'jev-review/audit.json').read_text())
            audit = copy.deepcopy(audit)
            audit['followup_checks'] = [{'check': 'duplicate', 'positions': [first['position'], second['position']],
                                         'probability': 0.9}]
            result = observations(audit, envelope['integrity'])
            self.assertEqual(result['followup_blockers'], {'duplicate': 1})
            self.assertEqual(result['blocked_slots']['duplicate'], 2)

    def test_tampered_envelope_or_escaped_selection_aborts(self):
        for mutation in ('metadata', 'score', 'integrity', 'run'):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                day = self.day(root)
                run, _ = make_run(day, report_date=day.name)
                daily_semantic_review(run, day / 'review', judge=Judge('never'))
                path = day / 'review/audit.json'
                value = json.loads(path.read_text())
                if mutation == 'metadata':
                    value['metadata']['schema_version'] = 2
                elif mutation == 'score':
                    value['audit']['checks'][0]['probability'] = 0.99
                elif mutation == 'integrity':
                    value['integrity']['costs'][0]['reported_cost_usd'] += 1
                else:
                    (run / 'fallback-log.json').write_text(json.dumps({'selected_run_dir': '../../escaped'}))
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    aggregate(root)

    def test_corpus_date_mismatch_and_duplicated_day_are_rejected(self):
        for duplicate in (False, True):
            with self.subTest(duplicate=duplicate), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                day = self.day(root)
                run, _ = make_run(day, report_date=day.name)
                daily_semantic_review(run, day / 'review', judge=Judge('never'))
                if duplicate:
                    shutil.copytree(day, root / '2026-08-12')
                else:
                    day.rename(root / '2026-08-12')
                with self.assertRaisesRegex(ValueError, 'duplicate daily audit|corpus date binding'):
                    aggregate(root)

    def test_explicit_frame_keeps_absent_dates_and_rejects_outside_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('2026-08-11', '2026-08-13'):
                day = self.day(root, name)
                run, _ = make_run(day, report_date=day.name)
                daily_semantic_review(run, day / 'review', judge=Judge('never'))
            result = aggregate_daily(root, start=date(2026, 8, 11), end=date(2026, 8, 14))
            self.assertEqual(result['coverage'], rate(2, 4))
            self.assertEqual(result['status_counts'], {'complete': 2, 'missing_day': 2})
            self.assertEqual(result['days'][1]['status'], 'missing_day')
            self.assertEqual(result['days'][-1]['status'], 'missing_day')
            self.assertEqual(result['repair_round_rate'], rate(0, 2))
            with self.assertRaisesRegex(ValueError, 'outside the declared'):
                aggregate_daily(root, start=date(2026, 8, 12), end=date(2026, 8, 14))
            with self.assertRaisesRegex(ValueError, 'invalid or excessive'):
                aggregate_daily(root, start=date(2026, 8, 14), end=date(2026, 8, 11))

    def test_wilson_matches_evaluator_including_empty_and_boundaries(self):
        for successes, trials in ((0, 0), (0, 14), (14, 14), (7, 10), (12, 220)):
            self.assertEqual(rate(successes, trials)['ci95_wilson'], wilson_interval(successes, trials))
