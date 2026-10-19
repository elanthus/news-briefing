import json
import tempfile
import unittest
from pathlib import Path

from agent_runner.audit_adjudication import (
    adjudicated_outcomes,
    compare,
    export_adjudication,
    record_outcomes,
    validate_adjudication,
    verify_frozen_inputs,
)
from agent_runner.audit_packets import PROTOCOL_ID, RUBRIC


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


def labels_second_disputed_pair():
    """The second reviewer also disagrees on the pair's meaning label, which is material."""
    value = labels_second()
    value['items'][1]['labels']['meaning_preservation'] = 'added'
    return value


def exported(root, second_labels, *, seed=3):
    """Write both packets and label files, export the adjudication packet, return the paths."""
    first, second = packets()
    paths = {}
    for name, value in (('p1', first), ('l1', labels_first()), ('p2', second), ('l2', second_labels)):
        paths[name] = root / f'{name}.json'
        paths[name].write_text(json.dumps(value))
    export_adjudication(paths['p1'], paths['l1'], paths['p2'], paths['l2'],
                        root / 'adjudication', root / 'private' / 'key.json', seed=seed)
    paths['adj'] = root / 'adjudication' / 'adjudication-packet.json'
    paths['key'] = root / 'private' / 'key.json'
    return paths


def decide(packet, by_first_version_order):
    """A decisions file deciding every listed disagreement from a {(question, version): label} map."""
    items = []
    for item in packet['items']:
        chosen = [{'question': d['question'], 'version': d['version'],
                   'label': by_first_version_order[(d['question'], d['version'])]} for d in item['disagreements']]
        items.append({'adjudication_id': item['adjudication_id'], 'decisions': chosen, 'rationale': 'Decided.'})
    return {'schema_version': 1, 'adjudicator': 'adj', 'items': items}


def run_outcomes(paths, decisions, **kwargs):
    paths['dec'] = paths['dec'] if 'dec' in paths else paths['adj'].parent.parent / 'decisions.json'
    paths['dec'].write_text(json.dumps(decisions))
    return adjudicated_outcomes(paths['p1'], paths['l1'], paths['p2'], paths['l2'], paths['adj'], paths['dec'],
                                paths['key'], **kwargs)


def correspondence(root, sides=None):
    sides = ['repaired', 'original'] if sides is None else sides
    path = root / 'packet-correspondence.json'
    path.write_text(json.dumps({'schema_version': 1, 'protocol_id': PROTOCOL_ID, 'mapping': [
        {'packet_id': 'r1-00001', 'kind': 'pair', 'sides': sides},
        {'packet_id': 'r2-00002', 'kind': 'pair', 'sides': list(reversed(sides))},
    ]}))
    return path


class OutcomesTests(unittest.TestCase):
    def test_pairs_by_side_follow_first_packet_order_after_adjudication(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second_disputed_pair())
            decisions = decide(json.loads(paths['adj'].read_text()), {
                ('support', 0): 'unsupported', ('meaning_preservation', None): 'preserved',
                ('readability', 0): 'fragment_or_incomplete'})
            pooled = run_outcomes(paths, decisions)
            for sides in (['repaired', 'original'], ['original', 'repaired']):
                key = correspondence(root, sides)
                result = run_outcomes(paths, decisions, correspondence_path=key)
                split = result['outcomes']['pairs_by_side']
                self.assertEqual(result['outcomes']['pairs'], pooled['outcomes']['pairs'])
                self.assertEqual(result['outcomes']['controls'], pooled['outcomes']['controls'])
                self.assertEqual(result['unresolved_slots'], pooled['unresolved_slots'])
                self.assertEqual(result['inputs_sha256']['packet_correspondence'],
                                 hashlib.sha256(key.read_bytes()).hexdigest())
                for index, side in enumerate(sides):
                    self.assertEqual(split[side]['support'], {
                        'favorable': 'supported', 'count': int(index == 0), 'of': 1, 'unresolved': 0,
                        'labels': {'supported' if index == 0 else 'unsupported': 1}})
                    self.assertEqual(split[side]['readability']['count'], int(index == 1))
                    self.assertEqual(split[side]['citations'], {
                        'favorable': 'all_relevant', 'count': 1, 'of': 1, 'unresolved': 0,
                        'labels': {'all_relevant': 1}})
                    self.assertEqual(set(split[side]), {'support', 'citations', 'readability'})

    def test_packet_correspondence_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            decisions = decide(json.loads(paths['adj'].read_text()), {('support', 0): 'supported'})
            key = correspondence(root)
            valid = json.loads(key.read_text())
            cases = [None, {**valid, 'schema_version': 2}, {**valid, 'protocol_id': 'foreign'},
                     {**valid, 'mapping': {}}, {**valid, 'mapping': [None]},
                     {**valid, 'mapping': valid['mapping'][:1]},
                     {**valid, 'mapping': valid['mapping'] * 2}]
            for sides in (['original', 'original'], ['repaired', 'repaired'], [], ['original'],
                          ['original', 'repaired', 'original'], ['original', 'foreign'], 'original', [None, {}]):
                cases.append({**valid, 'mapping': [{**valid['mapping'][0], 'sides': sides}, valid['mapping'][1]]})
            cases.append({**valid, 'mapping': [{**valid['mapping'][0], 'kind': 'control'}, valid['mapping'][1]]})
            cases.append({**valid, 'mapping': [valid['mapping'][0],
                                               {**valid['mapping'][1], 'sides': ['repaired', 'original']}]})
            for index, value in enumerate(cases):
                key.write_text(json.dumps(value))
                with self.subTest(case=index), self.assertRaises(ValueError):
                    run_outcomes(paths, decisions, correspondence_path=key)

    def test_agreed_labels_stay_and_each_disputed_question_takes_the_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second_disputed_pair())
            packet = json.loads(paths['adj'].read_text())
            # Both shared items are adjudicated: the control on support, the pair on meaning and readability.
            self.assertEqual(len(packet['items']), 2)
            decisions = decide(packet, {('support', 0): 'unsupported', ('meaning_preservation', None): 'preserved',
                                        ('readability', 0): 'fragment_or_incomplete'})
            result = run_outcomes(paths, decisions, opened_at='2026-10-19T20:00:00Z')
            self.assertEqual(result['correspondence_opened_at'], '2026-10-19T20:00:00Z')
            self.assertEqual((result['shared_items'], result['shared_pairs'], result['shared_controls']), (2, 1, 1))
            self.assertEqual((result['adjudicated_items'], result['decisions_applied']), (2, 3))
            self.assertEqual(result['unresolved_slots'], 0)
            pairs, controls = result['outcomes']['pairs'], result['outcomes']['controls']
            # r1 labeled the pair's support [supported, unsupported] and r2 agreed after alignment.
            self.assertEqual((pairs['support']['count'], pairs['support']['of']), (1, 2))
            self.assertEqual(pairs['support']['labels'], {'supported': 1, 'unsupported': 1})
            self.assertEqual((pairs['meaning_preservation']['count'], pairs['meaning_preservation']['of']), (1, 1))
            self.assertEqual((pairs['citations']['count'], pairs['citations']['of']), (2, 2))
            self.assertEqual((pairs['readability']['count'], pairs['readability']['of']), (1, 2))
            self.assertEqual(pairs['readability']['labels'], {'fragment_or_incomplete': 1, 'readable': 1})
            self.assertEqual((controls['support']['count'], controls['support']['of']), (0, 1))
            self.assertEqual(controls['support']['labels'], {'unsupported': 1})
            self.assertNotIn('meaning_preservation', controls)
            self.assertEqual((controls['citations']['count'], controls['readability']['count']), (1, 1))
            self.assertEqual((result['flags'], result['outcomes_with_flags']), (0, None))
            # r1-00003 was labeled by the first reviewer only and is reported apart from the shared items.
            self.assertEqual(set(result['single_labeled']), {'r1'})
            single = result['single_labeled']['r1']
            self.assertEqual((single['items'], single['pairs'], single['controls']), (1, 0, 1))
            self.assertEqual(single['outcomes']['controls']['support'], {
                'favorable': 'supported', 'count': 1, 'of': 1, 'unresolved': 0, 'labels': {'supported': 1}})
            self.assertEqual(set(result['inputs_sha256']), {'packets', 'labels', 'adjudication_packet',
                                                            'decisions', 'key'})
            # Outlet enrichment of the adjudication packet does not break the content match.
            for item in packet['items']:
                for version in item['versions']:
                    for evidence in version['evidence']:
                        evidence.update({'source': 'Outlet', 'published': '2026-08-11'})
            paths['adj'].write_text(json.dumps(packet))
            self.assertEqual(run_outcomes(paths, decisions)['outcomes'], result['outcomes'])

    def test_flags_change_only_the_sensitivity_outcomes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            packet = json.loads(paths['adj'].read_text())
            decisions = decide(packet, {('support', 0): 'supported'})
            decisions['items'][0]['flags'] = [{'question': 'citations', 'version': 0, 'label': 'irrelevant_listed',
                                               'note': 'The listed source is about another event.'}]
            result = run_outcomes(paths, decisions)
            self.assertEqual((result['flags'], result['flag_counts']), (1, {'citations:irrelevant_listed': 1}))
            self.assertEqual(result['outcomes']['controls']['citations']['count'], 1)
            self.assertEqual(result['outcomes_with_flags']['controls']['citations']['count'], 0)
            self.assertEqual(result['outcomes_with_flags']['controls']['citations']['labels'],
                             {'irrelevant_listed': 1})
            self.assertEqual(result['outcomes_with_flags']['pairs'], result['outcomes']['pairs'])

    def test_non_material_disagreement_without_a_decision_is_counted_as_unresolved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # The pair differs only on readability, so it never reached the adjudicator.
            paths = exported(root, labels_second())
            result = run_outcomes(paths, decide(json.loads(paths['adj'].read_text()), {('support', 0): 'supported'}))
            self.assertEqual(result['unresolved_slots'], 1)
            readability = result['outcomes']['pairs']['readability']
            self.assertEqual((readability['count'], readability['of'], readability['unresolved']), (1, 2, 1))
            # Both reviewers agreed the pair's meaning changed, so nothing counts as preserved.
            self.assertEqual(result['outcomes']['pairs']['meaning_preservation'],
                             {'favorable': 'preserved', 'count': 0, 'of': 1, 'unresolved': 0,
                              'labels': {'changed': 1}})

    def test_outcomes_fail_closed_when_the_key_or_packet_does_not_match(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            packet = json.loads(paths['adj'].read_text())
            decisions = decide(packet, {('support', 0): 'supported'})
            key = json.loads(paths['key'].read_text())
            cases = []
            swapped = json.loads(json.dumps(key))
            swapped['mapping'][0]['reviewer_A'], swapped['mapping'][0]['reviewer_B'] = (
                key['mapping'][0]['reviewer_B'], key['mapping'][0]['reviewer_A'])
            cases.append((swapped, packet, 'are not reviewer'))
            foreign = json.loads(json.dumps(key))
            foreign['summary']['label_files_sha256']['r2'] = '0' * 64
            cases.append((foreign, packet, 'not built from these label files'))
            unmapped = json.loads(json.dumps(key))
            unmapped['mapping'][0]['adjudication_id'] = 'adj-99999'
            cases.append((unmapped, packet, 'exactly once'))
            misnamed = json.loads(json.dumps(key))
            misnamed['mapping'][0]['first_packet_id'] = 'r1-00003'
            cases.append((misnamed, packet, 'does not name a shared item'))
            edited = json.loads(json.dumps(packet))
            edited['items'][0]['versions'][0]['prose'] = 'Something else.'
            cases.append((key, edited, 'content differs'))
            for broken_key, broken_packet, message in cases:
                paths['key'].write_text(json.dumps(broken_key))
                paths['adj'].write_text(json.dumps(broken_packet))
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    run_outcomes(paths, decisions)

    def test_freeze_record_digests_are_checked_and_outcomes_recorded_once_opened(self):
        import hashlib
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            decisions = decide(json.loads(paths['adj'].read_text()), {('support', 0): 'supported'})
            paths['dec'] = root / 'decisions.json'
            paths['dec'].write_text(json.dumps(decisions))
            digest = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in paths.items()}
            record = {'schema_version': 1, 'correspondence_opened_at': None,
                      'label_files': {'r1': {'sha256': digest['l1']}, 'r2': {'sha256': digest['l2']}},
                      'packet_files': {'p1.json': digest['p1'], 'p2.json': digest['p2']},
                      'adjudication_decisions_returned': {'sha256': digest['dec'],
                                                          'validated_against_packet_sha256': digest['adj'],
                                                          'correspondence_opened_at': None}}
            check = dict(packets=[paths['p1'], paths['p2']], labels=[paths['l1'], paths['l2']],
                         adjudication_path=paths['adj'], decisions_path=paths['dec'])
            verify_frozen_inputs(record, **check)
            superseded = root / 'decisions-initial.json'
            superseded.write_text(json.dumps({**decisions, 'adjudicator': 'draft'}))
            with self.assertRaisesRegex(ValueError, 'decisions-initial.json: digest is not in the freeze record'):
                verify_frozen_inputs(record, **{**check, 'decisions_path': superseded})
            record_path = root / 'record.json'
            record_path.write_text(json.dumps(record))
            key = correspondence(root)
            result = run_outcomes(paths, decisions, opened_at='2026-10-19T20:00:00Z',
                                  correspondence_path=key, packet_opened_at='2026-10-19T21:00:00Z')
            written = record_outcomes(record_path, result)
            self.assertEqual(written['correspondence_opened_at'], '2026-10-19T20:00:00Z')
            self.assertEqual(written['adjudication_decisions_returned']['correspondence_opened_at'],
                             '2026-10-19T20:00:00Z')
            self.assertEqual(written['adjudicated_outcomes']['outcomes'], result['outcomes'])
            self.assertEqual(written['adjudicated_outcomes']['date'], '2026-10-19')
            self.assertEqual(written['packet_correspondence_opened_at'], '2026-10-19T21:00:00Z')
            self.assertEqual(written['adjudicated_outcomes']['packet_correspondence_opened_at'],
                             '2026-10-19T21:00:00Z')
            self.assertEqual(json.loads(record_path.read_text()), written)
            # A second run keeps the first opening time and refreshes the counts.
            later = run_outcomes(paths, decisions, opened_at='2026-10-20T09:00:00Z',
                                 correspondence_path=key, packet_opened_at='2026-10-20T10:00:00Z')
            again = record_outcomes(record_path, later)
            self.assertEqual(again['correspondence_opened_at'], '2026-10-19T20:00:00Z')
            self.assertEqual(later['correspondence_opened_at'], '2026-10-19T20:00:00Z')
            self.assertEqual(again['packet_correspondence_opened_at'], '2026-10-19T21:00:00Z')
            self.assertEqual(again['adjudicated_outcomes']['packet_correspondence_opened_at'],
                             '2026-10-19T21:00:00Z')
            self.assertEqual(later['packet_correspondence_opened_at'], '2026-10-19T21:00:00Z')

    def test_command_line_outcomes_writes_the_record(self):
        import subprocess
        import sys
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            decisions = decide(json.loads(paths['adj'].read_text()), {('support', 0): 'supported'})
            paths['dec'] = root / 'decisions.json'
            paths['dec'].write_text(json.dumps(decisions))
            key = correspondence(root)
            completed = subprocess.run(
                [sys.executable, '-S', '-m', 'agent_runner.audit_adjudication', 'outcomes',
                 str(paths['p1']), str(paths['l1']), str(paths['p2']), str(paths['l2']),
                 str(paths['adj']), str(paths['dec']), '--key', str(paths['key']), '--correspondence', str(key)],
                capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            printed = json.loads(completed.stdout)
            self.assertEqual(printed['outcomes']['controls']['support']['count'], 1)
            self.assertRegex(printed['correspondence_opened_at'], r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
            self.assertRegex(printed['packet_correspondence_opened_at'],
                             r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')
            self.assertEqual(printed['outcomes']['pairs_by_side']['original']['support']['count'], 0)

    def test_record_rerun_rejects_changed_or_omitted_keys_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = exported(root, labels_second())
            decisions = decide(json.loads(paths['adj'].read_text()), {('support', 0): 'supported'})
            result = run_outcomes(paths, decisions, correspondence_path=correspondence(root))
            record = root / 'record.json'
            record.write_text(json.dumps({'schema_version': 1}))
            record_outcomes(record, result)
            frozen = record.read_bytes()
            # Optional correspondence is valid initially, but cannot discard an existing split.
            pooled_only = run_outcomes(paths, decisions)
            with self.assertRaisesRegex(ValueError, 'packet_correspondence: recorded key digest'):
                record_outcomes(record, pooled_only)
            self.assertEqual(record.read_bytes(), frozen)
            for name in ('key', 'packet_correspondence'):
                for replacement in (None, '0' * 64):
                    changed = json.loads(json.dumps(result))
                    if replacement is None:
                        del changed['inputs_sha256'][name]
                    else:
                        changed['inputs_sha256'][name] = replacement
                    with self.subTest(key=name, replacement=replacement), self.assertRaisesRegex(
                            ValueError, f'{name}: recorded key digest'):
                        record_outcomes(record, changed)
                    self.assertEqual(record.read_bytes(), frozen)
            missing_time = json.loads(json.dumps(result))
            del missing_time['packet_correspondence_opened_at']
            with self.assertRaisesRegex(ValueError, 'rerun with --correspondence'):
                record_outcomes(record, missing_time)
            self.assertEqual(record.read_bytes(), frozen)


if __name__ == '__main__':
    unittest.main()
