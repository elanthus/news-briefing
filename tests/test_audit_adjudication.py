import json
import tempfile
import unittest
from pathlib import Path

from agent_runner.audit_adjudication import compare, export_adjudication, validate_adjudication
from agent_runner.audit_packets import RUBRIC


def version(tag, evidence=1):
    return {'headline': f'Headline {tag}', 'prose': f'Prose {tag}.',
            'evidence': [{'title': f'Source {tag}{i}', 'excerpt': f'Excerpt {tag}{i}'} for i in range(evidence)]}


PAIR_A, PAIR_B, CONTROL, R1_ONLY = version('a'), version('b'), version('c'), version('d')


def packets():
    first = {'schema_version': 1, 'rubric': RUBRIC, 'items': [
        {'packet_id': 'r1-00001', 'versions': [PAIR_A, PAIR_B]},
        {'packet_id': 'r1-00002', 'versions': [CONTROL]},
        {'packet_id': 'r1-00003', 'versions': [R1_ONLY]},
    ]}
    # The second reviewer saw the pair in the opposite order.
    second = {'schema_version': 1, 'rubric': RUBRIC, 'items': [
        {'packet_id': 'r2-00001', 'versions': [CONTROL]},
        {'packet_id': 'r2-00002', 'versions': [PAIR_B, PAIR_A]},
    ]}
    return first, second


def labels_first():
    return {'schema_version': 1, 'reviewer': 'r1', 'packet': 'reviewer-primary.json', 'items': [
        {'packet_id': 'r1-00001', 'labels': {'support': ['supported', 'unsupported'], 'meaning_preservation': 'changed',
                                             'citations': ['all_relevant', 'all_relevant'],
                                             'readability': ['readable', 'readable']},
         'rationale': 'B drops a figure.'},
        {'packet_id': 'r1-00002', 'labels': {'support': ['supported'], 'meaning_preservation': 'not_applicable',
                                             'citations': ['all_relevant'], 'readability': ['readable']},
         'rationale': 'Matches the excerpt.'},
        {'packet_id': 'r1-00003', 'labels': {'support': ['supported'], 'meaning_preservation': 'not_applicable',
                                             'citations': ['all_relevant'], 'readability': ['readable']},
         'rationale': 'Fine.'},
    ]}


def labels_second(control_support='unsupported'):
    return {'schema_version': 1, 'reviewer': 'r2', 'packet': 'reviewer-double.json', 'items': [
        {'packet_id': 'r2-00001', 'labels': {'support': [control_support], 'meaning_preservation': 'not_applicable',
                                             'citations': ['all_relevant'], 'readability': ['readable']},
         'rationale': 'The number is not in the excerpt.'},
        # Order is [PAIR_B, PAIR_A]: this agrees with r1 after alignment except readability of PAIR_A.
        {'packet_id': 'r2-00002', 'labels': {'support': ['unsupported', 'supported'], 'meaning_preservation': 'changed',
                                             'citations': ['all_relevant', 'all_relevant'],
                                             'readability': ['readable', 'fragment_or_incomplete']},
         'rationale': 'Same reading, A ends mid-sentence.'},
    ]}


class AdjudicationTests(unittest.TestCase):
    def test_compare_aligns_version_order_and_counts_agreement(self):
        first, second = packets()
        result = compare(first, labels_first(), second, labels_second())
        self.assertEqual((result['shared_items'], result['shared_pairs'], result['shared_controls']), (2, 1, 1))
        self.assertEqual(result['raw_agreement']['support'], {'agreements': 2, 'compared': 3})
        self.assertEqual(result['raw_agreement']['readability'], {'agreements': 2, 'compared': 3})
        self.assertEqual(result['raw_agreement']['meaning_preservation'], {'agreements': 1, 'compared': 1})
        self.assertEqual(result['items_with_any_disagreement'], 2)
        # The pair differs only on readability, which is not material; the control differs on support.
        self.assertEqual(result['items_with_material_disagreement'], 1)

    def test_export_is_blinded_random_per_item_and_validates_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = packets()
            paths = {}
            for name, value in (('p1', first), ('l1', labels_first()), ('p2', second), ('l2', labels_second())):
                paths[name] = root / f'{name}.json'
                paths[name].write_text(json.dumps(value))
            summary = export_adjudication(paths['p1'], paths['l1'], paths['p2'], paths['l2'],
                                          root / 'adjudication', root / 'private' / 'key.json', seed=3)
            self.assertEqual(summary['items_with_material_disagreement'], 1)
            self.assertEqual(set(summary['label_files_sha256']), {'r1', 'r2'})
            packet = json.loads((root / 'adjudication' / 'adjudication-packet.json').read_text())
            self.assertEqual(packet['kind'], 'adjudication')
            self.assertEqual(len(packet['items']), 1)
            item = packet['items'][0]
            self.assertEqual(set(item), {'adjudication_id', 'versions', 'labels', 'rationales', 'disagreements'})
            self.assertEqual(item['disagreements'], [{'question': 'support', 'version': 0}])
            self.assertEqual({item['labels']['A']['support'][0], item['labels']['B']['support'][0]},
                             {'supported', 'unsupported'})
            text = json.dumps(packet)
            for forbidden in ('r1-', 'r2-', 'packet_id', 'reviewer_A', '"r1"', '"r2"', str(root)):
                self.assertNotIn(forbidden, text)
            key = json.loads((root / 'private' / 'key.json').read_text())
            self.assertEqual((root / 'private' / 'key.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual({key['mapping'][0]['reviewer_A'], key['mapping'][0]['reviewer_B']}, {'r1', 'r2'})
            self.assertEqual(key['mapping'][0]['first_packet_id'], 'r1-00002')
            decisions = {'schema_version': 1, 'adjudicator': 'adj', 'items': [
                {'adjudication_id': item['adjudication_id'],
                 'decisions': [{'question': 'support', 'version': 0, 'label': 'unsupported'}],
                 'rationale': 'The figure is absent from the excerpt.'}]}
            decisions_path = root / 'decisions.json'
            decisions_path.write_text(json.dumps(decisions))
            result = validate_adjudication(root / 'adjudication' / 'adjudication-packet.json', decisions_path)
            self.assertEqual((result['items'], result['decisions']), (1, 1))
            self.assertEqual(result['label_counts'], {'support:unsupported': 1})
            for broken, message in (
                ({'decisions': []}, 'undecided'),
                ({'decisions': [{'question': 'readability', 'version': 0, 'label': 'readable'}]}, 'not a listed'),
                ({'decisions': [{'question': 'support', 'version': 0, 'label': 'fine'}]}, 'not in the rubric'),
                ({'rationale': ''}, 'rationale'),
            ):
                value = json.loads(json.dumps(decisions))
                value['items'][0].update(broken)
                decisions_path.write_text(json.dumps(value))
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    validate_adjudication(root / 'adjudication' / 'adjudication-packet.json', decisions_path)
            with self.assertRaises(ValueError):
                export_adjudication(paths['p1'], paths['l1'], paths['p2'], paths['l2'],
                                    root / 'adjudication', root / 'private' / 'key2.json')

    def test_flags_on_agreed_labels_are_validated_and_counted_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first, second = packets()
            paths = {}
            for name, value in (('p1', first), ('l1', labels_first()), ('p2', second), ('l2', labels_second())):
                paths[name] = root / f'{name}.json'
                paths[name].write_text(json.dumps(value))
            export_adjudication(paths['p1'], paths['l1'], paths['p2'], paths['l2'],
                                root / 'adjudication', root / 'key.json', seed=3)
            packet_path = root / 'adjudication' / 'adjudication-packet.json'
            item = json.loads(packet_path.read_text())['items'][0]
            base = {'schema_version': 1, 'adjudicator': 'adj', 'items': [
                {'adjudication_id': item['adjudication_id'],
                 'decisions': [{'question': 'support', 'version': 0, 'label': 'unsupported'}],
                 'rationale': 'No basis in the excerpt.',
                 'flags': [{'question': 'citations', 'version': 0, 'label': 'irrelevant_listed',
                            'note': 'The listed source is about something else.'}]}]}
            decisions_path = root / 'decisions.json'
            decisions_path.write_text(json.dumps(base))
            result = validate_adjudication(packet_path, decisions_path)
            self.assertEqual((result['flags'], result['flagged_items']), (1, 1))
            self.assertEqual(result['flag_counts'], {'citations:irrelevant_listed': 1})
            self.assertEqual(result['label_counts'], {'support:unsupported': 1})
            for broken, message in (
                ({'question': 'support', 'version': 0, 'label': 'supported', 'note': 'x'}, 'not an agreed'),
                ({'question': 'citations', 'version': 0, 'label': 'all_relevant', 'note': 'x'}, 'repeats the agreed'),
                ({'question': 'citations', 'version': 0, 'label': 'bogus', 'note': 'x'}, 'not in the rubric'),
                ({'question': 'citations', 'version': 0, 'label': 'irrelevant_listed', 'note': ' '}, 'needs a note'),
            ):
                value = json.loads(json.dumps(base))
                value['items'][0]['flags'] = [broken]
                decisions_path.write_text(json.dumps(value))
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    validate_adjudication(packet_path, decisions_path)


class EnrichTests(unittest.TestCase):
    def test_enrich_adds_outlets_by_content_and_fails_closed_on_unknown_evidence(self):
        from datetime import date
        from unittest.mock import patch

        from agent_runner.audit_adjudication import enrich_evidence
        from agent_runner.audit_packets import export_packets
        from agent_runner.semantic_repairs import daily_semantic_review
        from tests.test_audit_packets import dated_fixture
        from tests.test_semantic_repairs import Judge, PatchProvider, make_run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / 'days'
            day = frame / '2026-08-11'
            day.mkdir(parents=True)
            with patch('tests.test_semantic_repairs.fixture_contract', side_effect=lambda: dated_fixture(day.name)):
                run, headline = make_run(day)
                daily_semantic_review(run, day / 'review', judge=Judge(headline), repair_provider=PatchProvider())
            # Two reviewer packets over the same day, then labels that disagree on every pair's support.
            for reviewer in ('r1', 'r2'):
                export_packets(frame, root / f'packets-{reviewer}', root / f'key-{reviewer}.json',
                               start=date(2026, 8, 11), end=date(2026, 8, 11), seed=1 if reviewer == 'r1' else 2)
            packets, labels = {}, {}
            for reviewer in ('r1', 'r2'):
                packet = json.loads((root / f'packets-{reviewer}' / 'reviewer-primary.json').read_text())
                packets[reviewer] = packet
                labels[reviewer] = {'schema_version': 1, 'reviewer': reviewer, 'packet': 'x', 'items': [
                    {'packet_id': it['packet_id'],
                     'labels': {'support': ['supported' if reviewer == 'r1' else 'unsupported'] * len(it['versions']),
                                'meaning_preservation': 'changed' if len(it['versions']) == 2 else 'not_applicable',
                                'citations': ['all_relevant'] * len(it['versions']),
                                'readability': ['readable'] * len(it['versions'])}, 'rationale': 'r'}
                    for it in packet['items']]}
                (root / f'p-{reviewer}.json').write_text(json.dumps(packet))
                (root / f'l-{reviewer}.json').write_text(json.dumps(labels[reviewer]))
            export_adjudication(root / 'p-r1.json', root / 'l-r1.json', root / 'p-r2.json', root / 'l-r2.json',
                                root / 'adjudication', root / 'adj-key.json', seed=4)
            packet_path = root / 'adjudication' / 'adjudication-packet.json'
            before = json.loads(packet_path.read_text())
            self.assertTrue(before['items'])
            result = enrich_evidence(packet_path, frame, start=date(2026, 8, 11), end=date(2026, 8, 11))
            after = json.loads(packet_path.read_text())
            self.assertEqual([i['adjudication_id'] for i in after['items']],
                             [i['adjudication_id'] for i in before['items']])
            self.assertEqual(after['items'][0]['labels'], before['items'][0]['labels'])
            self.assertGreater(result['evidence_enriched'], 0)
            self.assertTrue(all('source' in e and 'published' in e
                                for it in after['items'] for v in it['versions'] for e in v['evidence']))
            after['items'][0]['versions'][0]['evidence'][0]['title'] = 'not in any run'
            packet_path.write_text(json.dumps(after))
            with self.assertRaisesRegex(ValueError, 'resolves to 0'):
                enrich_evidence(packet_path, frame, start=date(2026, 8, 11), end=date(2026, 8, 11))


if __name__ == '__main__':
    unittest.main()
