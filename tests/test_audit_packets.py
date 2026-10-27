import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from agent_runner.audit_metrics import flagged
from agent_runner.audit_packets import control_order
from agent_runner.audit_packets import export_packets as export_daily_packets
from agent_runner.jev_review import load_verified_run
from agent_runner.positions import StoryPosition
from agent_runner.semantic_repairs import daily_semantic_review
from tests.test_briefing_output import fixture_contract
from tests.test_semantic_repairs import Judge, PatchProvider, make_run, split_review


def export_packets(root, output, key, *, seed=None):
    dates = sorted(p.name for p in root.iterdir())
    return export_daily_packets(root, output, key, start=date.fromisoformat(dates[0]),
                                end=date.fromisoformat(dates[-1]), seed=seed)


def dated_fixture(day):
    corpus, config, projected, output = fixture_contract()
    corpus['report_date'] = day
    return corpus, config, projected, output


class AuditPacketTests(unittest.TestCase):
    def make_day(self, root, day='2026-08-11', *, split=False):
        path = root / day
        path.mkdir()
        with patch('tests.test_semantic_repairs.fixture_contract', side_effect=lambda: dated_fixture(day)):
            if split:
                run, audit, *_ = split_review(path)
            else:
                run, headline = make_run(path)
                audit = daily_semantic_review(run, path / 'review', judge=Judge(headline),
                                              repair_provider=PatchProvider())
        return path, run, audit

    def test_all_applied_and_withheld_pairs_and_exact_controls_are_blinded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            frame.mkdir()
            day, run, audit = self.make_day(frame, split=True)
            result = export_packets(frame, root / 'packets', root / 'private/key.json', seed=11)
            self.assertEqual(result, {'pairs': 2, 'controls': 3, 'primary_items': 5, 'double_items': 3})
            key = json.loads((root / 'private/key.json').read_text())
            self.assertEqual((root / 'private/key.json').stat().st_mode & 0o777, 0o600)
            mapping = {m['packet_id']: m for m in key['mapping']}
            primary_pairs, secondary_pairs = {}, {}
            for file, pair_map in [('reviewer-primary.json', primary_pairs), ('reviewer-double.json', secondary_pairs)]:
                packet = json.loads((root / 'packets' / file).read_text())
                self.assertEqual(set(packet), {'schema_version', 'rubric', 'items'})
                self.assertEqual(set(packet['rubric']['labels']),
                                 {'support', 'meaning_preservation', 'citations', 'readability'})
                for item in packet['items']:
                    self.assertEqual(set(item), {'packet_id', 'versions'})
                    private = mapping[item['packet_id']]
                    self.assertEqual(len(item['versions']), len(private['sides']))
                    if private['kind'] == 'pair':
                        pair_map[StoryPosition.from_payload(private['position'])] = private['sides']
                    for side, version in zip(private['sides'], item['versions'], strict=True):
                        self.assertEqual(set(version), {'headline', 'prose', 'evidence'})
                        for evidence in version['evidence']:
                            self.assertEqual(set(evidence), {'title', 'excerpt', 'source', 'published'})
                        source = run if side == 'original' else day / 'jev-review/repair/run'
                        topic = next(t for t in load_verified_run(source).topics
                                     if t['position'] == private['position'])
                        self.assertEqual(version['headline'], topic['headline'])
                        self.assertEqual(version['prose'], topic['prose'])
                        expected_evidence = [{'title': e['title'], 'excerpt': e.get('summary'),
                                              'source': e.get('source'), 'published': e.get('published')}
                                             for e in topic['evidence']]
                        self.assertEqual(version['evidence'], expected_evidence)
                text = json.dumps(packet)
                for forbidden in ('typesafe/', 'openrouter', 'repair_status', 'probability', 'confirmation_label',
                                  'applied', 'rejected', str(root), 'original', 'repaired', 'protocol_id', 'seed'):
                    self.assertNotIn(forbidden, text)
            self.assertEqual(set(primary_pairs), set(secondary_pairs))
            self.assertNotEqual(primary_pairs, secondary_pairs)
            self.assertEqual({m['repair_status'] for m in mapping.values() if m['kind'] == 'pair'},
                             {'applied', 'rejected'})
            flagged_positions = {StoryPosition.from_payload(p) for c in audit['checks'] if flagged(c, audit)
                                 for p in c['positions']}
            eligible = [t for t in load_verified_run(run).topics if t['included'] and
                        StoryPosition.from_payload(t['position']) not in flagged_positions]
            ordered = sorted(eligible, key=lambda t: control_order(day.name, t['position']))[:3]
            first_controls = [m for m in mapping.values()
                              if m['packet_id'].startswith('r1-') and m['kind'] == 'control']
            self.assertEqual({StoryPosition.from_payload(m['position']) for m in first_controls},
                             {StoryPosition.from_payload(t['position']) for t in ordered})
            second_controls = [m for m in mapping.values()
                               if m['packet_id'].startswith('r2-') and m['kind'] == 'control']
            self.assertEqual([m['position'] for m in second_controls], [ordered[0]['position']])
            self.assertEqual({p.name for p in (root / 'packets').iterdir()},
                             {'reviewer-primary.json', 'reviewer-double.json'})

    def test_partial_days_do_not_supply_unflagged_controls_and_tampering_aborts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            frame.mkdir()
            day = frame / '2026-08-11'
            day.mkdir()
            with patch('tests.test_semantic_repairs.fixture_contract', side_effect=lambda: dated_fixture(day.name)):
                run, _ = make_run(day)
            daily_semantic_review(run, day / 'review', judge=Judge('never', missing_cost=True))
            result = export_packets(frame, root / 'packets', root / 'key.json', seed=1)
            self.assertEqual(result['controls'], 0)
            audit_path = day / 'review/audit.json'
            value = json.loads(audit_path.read_text())
            value['metadata']['schema_version'] = 2
            audit_path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                export_packets(frame, root / 'invalid-packets', root / 'invalid-key.json')
            self.assertFalse((root / 'invalid-packets').exists())
            self.assertFalse((root / 'invalid-key.json').exists())

    def test_absent_sampled_day_is_recorded_in_private_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            frame.mkdir()
            self.make_day(frame)
            export_daily_packets(frame, root / 'packets', root / 'key.json',
                                 start=date(2026, 8, 10), end=date(2026, 8, 12))
            key = json.loads((root / 'key.json').read_text())
            self.assertEqual([r['status'] for r in key['coverage']], ['missing_day', 'complete', 'missing_day'])

    def test_private_key_cannot_leak_into_packets_or_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            frame.mkdir()
            self.make_day(frame)
            for output, key in [(root / 'packets', root / 'packets/key.json'),
                                (frame / 'packets', root / 'key.json'), (root, root / 'key.json')]:
                with self.subTest(output=output, key=key), self.assertRaises(ValueError):
                    export_packets(frame, output, key)
            (root / 'existing-key.json').write_text('keep private key')
            with self.assertRaises(ValueError):
                export_packets(frame, root / 'packets', root / 'existing-key.json')
            self.assertEqual((root / 'existing-key.json').read_text(), 'keep private key')
            self.assertFalse((root / 'packets').exists())

    def test_date_identity_and_missing_review_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            frame.mkdir()
            day, _, _ = self.make_day(frame)
            (frame / '2026-08-12').mkdir()
            export_packets(frame, root / 'packets', root / 'key.json')
            key = json.loads((root / 'key.json').read_text())
            self.assertEqual(key['coverage'][1]['status'], 'missing_review')
            day.rename(frame / '2026-08-13')
            with self.assertRaisesRegex(ValueError, 'corpus date binding'):
                export_packets(frame, root / 'other-packets', root / 'other-key.json')
