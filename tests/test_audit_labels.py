import copy
import json
import tempfile
import unittest
from pathlib import Path

from agent_runner.audit_labels import validate_labels
from agent_runner.audit_packets import RUBRIC


def packet():
    version = {'headline': 'h', 'prose': 'p.', 'evidence': [{'title': 't', 'excerpt': 'e'}]}
    return {'schema_version': 1, 'rubric': RUBRIC, 'items': [
        {'packet_id': 'r1-00001', 'versions': [version, version]},
        {'packet_id': 'r1-00002', 'versions': [version]},
    ]}


def labels():
    return {'schema_version': 1, 'reviewer': 'r1', 'packet': 'reviewer-primary.json', 'items': [
        {'packet_id': 'r1-00001', 'labels': {'support': ['supported', 'unsupported'], 'meaning_preservation': 'lost',
                                             'citations': ['all_relevant', 'needed_source_absent'],
                                             'readability': ['readable', 'readable']},
         'rationale': 'Version 2 drops a figure its prose still uses.'},
        {'packet_id': 'r1-00002', 'labels': {'support': ['supported'], 'meaning_preservation': 'not_applicable',
                                             'citations': ['all_relevant'], 'readability': ['readable']},
         'rationale': 'All claims are in the excerpt.'},
    ]}


class AuditLabelTests(unittest.TestCase):
    def check(self, root, labels_value):
        (root / 'packet.json').write_text(json.dumps(packet()))
        (root / 'labels.json').write_text(json.dumps(labels_value))
        return validate_labels(root / 'packet.json', root / 'labels.json')

    def test_complete_labels_validate_and_count(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.check(Path(directory), labels())
            self.assertEqual((result['items'], result['pairs'], result['controls']), (2, 1, 1))
            self.assertEqual(result['label_counts']['support:unsupported'], 1)
            self.assertEqual(result['label_counts']['meaning_preservation:not_applicable'], 1)

    def test_coverage_shape_rubric_and_rationale_are_enforced(self):
        def broken(mutate):
            value = labels()
            mutate(value)
            return value

        def extra(value):
            value['items'][0]['labels']['extra'] = 1

        cases = {
            'missing item': (broken(lambda v: v['items'].pop(1)), 'coverage'),
            'duplicate': (broken(lambda v: v['items'].append(copy.deepcopy(v['items'][1]))), 'duplicated'),
            'per-version': (broken(lambda v: v['items'][0]['labels'].update(support=['supported'])), 'per version'),
            'rubric': (broken(lambda v: v['items'][0]['labels'].update(support=['fine', 'supported'])),
                       'not in the rubric'),
            'control meaning': (broken(lambda v: v['items'][1]['labels'].update(meaning_preservation='preserved')),
                                'not_applicable'),
            'pair meaning': (broken(lambda v: v['items'][0]['labels'].update(meaning_preservation='not_applicable')),
                             'not_applicable'),
            'rationale': (broken(lambda v: v['items'][0].update(rationale=' ')), 'rationale'),
            'reviewer': (broken(lambda v: v.pop('reviewer')), 'reviewer'),
            'extra question': (broken(extra), 'exactly'),
        }
        for name, (broken, message) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                with self.assertRaisesRegex(ValueError, message):
                    self.check(Path(directory), broken)


if __name__ == '__main__':
    unittest.main()
