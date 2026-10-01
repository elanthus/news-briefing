import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent_runner.jev_review import load_topics
from agent_runner.models import ModelResponse, ProviderError
from agent_runner.output import validate_output
from agent_runner.runner import RunnerSettings, run_workflow
from agent_runner.semantic_repairs import daily_semantic_review, load_public_audit
from prepare_publication import prepare_publication
from publication_schema import parse_semantic_audit
from tests.test_briefing_output import ROOT, fixture_contract, used_item_refs
from tests.test_run_briefing import FakeProvider


class Judge:
    def __init__(self, headline, *, grouping=False, dispute=False, reject_after=False,
                 missing_cost=False, fail_after_repair=False):
        self.headline = headline
        self.grouping = grouping
        self.dispute = dispute
        self.reject_after = reject_after
        self.missing_cost = missing_cost
        self.fail_after_repair = fail_after_repair
        self.calls = []

    def evaluate(self, state, questions, *, timeout=30):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        if self.fail_after_repair and any(c.get('topic', {}).get('headline') == 'Repaired headline'
                                          for c in state['checks']):
            raise ProviderError('remote private body', transient=False, ambiguous_completion=True)
        probabilities = {}
        for i, (key, question) in enumerate(questions.items()):
            topic = state['checks'][i].get('topic', {})
            instructions = question['instructions']
            relevant = ('combine distinct' if self.grouping else 'material fact unsupported') in instructions
            flagged = relevant and topic.get('headline') == self.headline
            if topic.get('headline') == 'Repaired headline' and self.reject_after:
                flagged = relevant
            # Confirmation requests contain exactly one question.
            probabilities[key] = 0.2 if flagged and len(questions) == 1 and self.dispute else 0.9 if flagged else 0.1
        return {'probabilities': probabilities, 'model': 'typesafe/jev-test',
                'cost_usd': None if self.missing_cost else 0.00001, 'latency_ms': 1}


class PatchProvider:
    name = 'fake'
    model = 'patch'

    def __init__(self, *, bad_subset=False, incomplete=False):
        self.requests = []
        self.bad_subset = bad_subset
        self.incomplete = incomplete

    def info(self):
        return {'provider': self.name, 'model': self.model}

    def generate(self, request):
        self.requests.append(request)
        output = {}
        for key, schema in request.output_schema['properties'].items():
            if schema['type'] == 'array':
                output[key] = [10000] if self.bad_subset else [0]
            else:
                output[key] = {'headline': 'Repaired headline',
                               'summary': 'A supported complete sentence.' if not self.incomplete else 'An unfinished…'}
        return ModelResponse('', output, 1, cost_usd=0.00001)


def make_run(root, *, grouping=False):
    corpus, config, projected, output = fixture_contract()
    section = next(iter(output['sections']))
    target = output['sections'][section]['topics'][0]
    if grouping:
        used = used_item_refs(projected, output)
        allowed = next(s.corpus_categories for s in config.sections if s.name == section)
        extra = next(ref for ref, c in projected.citations.items() if c.item_ref not in used and c.category in allowed)
        target['citation_refs'].append(extra)
    assert not any(f.level == 'ERROR' for f in validate_output(output, config, projected.citations))
    (root / 'input.json').write_text(json.dumps(corpus))
    settings = RunnerSettings(config_path=ROOT / 'fixtures/briefing-config-2026-08-11.json',
                              sources_path=ROOT / 'sources.json', prompt_path=ROOT / 'briefing-runner-prompt.md',
                              output_path=root / 'briefing.md', corpus_path=root / 'input.json')
    result = run_workflow(FakeProvider([output]), settings, root / 'run')
    assert result.status == 'ready'
    return root / 'run', target['headline']


class SemanticRepairTests(unittest.TestCase):
    def test_confirmation_is_exact_and_disputed_score_prevents_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            judge = Judge(headline, dispute=True)
            provider = PatchProvider()
            audit = daily_semantic_review(run, root / 'review', judge=judge, repair_provider=provider)
            row = next(c for c in audit['checks'] if c['confirmation_label'] == 'disputed')
            self.assertEqual((row['probability'], row['confirmation_probability']), (0.9, 0.2))
            self.assertFalse(provider.requests)
            isolated = [s['checks'][0] for s, q in judge.calls if len(q) == 1]
            self.assertTrue(isolated)
            original = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            self.assertEqual(isolated[0]['topic'], original)

    def test_prose_repair_is_applied_and_publication_changes_without_mutating_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            before = {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()}
            provider = PatchProvider()
            audit = daily_semantic_review(run, run / '../jev-review', apply_repairs=True,
                                          judge=Judge(headline), repair_provider=provider)
            changed = next(t for t in audit['topics'] if t['changed'])
            self.assertEqual(changed['repair_status'], 'applied')
            self.assertEqual(changed['removed_evidence_count'], 0)
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()})
            self.assertNotRegex(json.dumps(provider.requests[0].prompt), r'https?://|\b(?:citation|item)_\d+\b')
            bound, selected = load_public_audit(run, root / 'jev-review')
            self.assertEqual(bound, audit)
            self.assertNotEqual(selected, run)
            # Daily fallback chains keep the original selected child immutable.
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'history', date(2026, 9, 30))
            self.assertEqual(record.semantic_audit, audit)
            self.assertIn('Repaired headline', (root / 'history/2026-09-30.md').read_text())

    def test_grouping_repair_uses_subset_then_only_refrozen_evidence_for_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            provider = PatchProvider()
            audit = daily_semantic_review(run, root / 'review', apply_repairs=True,
                                          judge=Judge(headline, grouping=True), repair_provider=provider)
            changed = next(t for t in audit['topics'] if t['changed'])
            self.assertEqual(changed['repair_status'], 'applied')
            self.assertEqual(changed['removed_evidence_count'], 1)
            self.assertEqual(len(provider.requests), 2)
            original = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            self.assertNotIn(original['evidence'][1]['title'], provider.requests[1].prompt)

    def test_failed_subset_and_incomplete_prose_leave_original_published(self):
        for options in ({'bad_subset': True}, {'incomplete': True}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, headline = make_run(root, grouping=True)
                audit = daily_semantic_review(run, root / 'review', apply_repairs=True,
                                              judge=Judge(headline, grouping=True),
                                              repair_provider=PatchProvider(**options))
                self.assertIn('failed', [t['repair_status'] for t in audit['topics']])
                self.assertEqual(load_public_audit(run, root / 'review')[1], run)

    def test_high_post_score_rejects_repair_and_preserves_changed_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', apply_repairs=True,
                                          judge=Judge(headline, reject_after=True), repair_provider=PatchProvider())
            self.assertIn('rejected', [t['repair_status'] for t in audit['topics']])
            self.assertEqual(load_public_audit(run, root / 'review')[1], run)
            self.assertTrue(any(c['after_probability'] == 0.9 for c in audit['checks']))

    def test_missing_confirmation_cost_prevents_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            provider = PatchProvider()
            audit = daily_semantic_review(run, root / 'review', judge=Judge(headline, missing_cost=True),
                                          repair_provider=provider)
            self.assertFalse(provider.requests)
            self.assertGreater(audit['unknown_cost_calls'], 0)

    def test_partial_post_review_preserves_candidate_but_never_applies_it(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', apply_repairs=True,
                                          judge=Judge(headline, fail_after_repair=True),
                                          repair_provider=PatchProvider())
            self.assertIn(audit['post_status'], ('failed', 'partial'))
            self.assertIn('rejected', [t['repair_status'] for t in audit['topics']])
            self.assertGreater(audit['unknown_cost_calls'], 0)
            self.assertEqual(load_public_audit(run, root / 'review')[1], run)

    def test_audit_prose_tampering_never_replaces_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            daily_semantic_review(run, root / 'review', apply_repairs=True,
                                  judge=Judge(headline), repair_provider=PatchProvider())
            path = root / 'review/audit.json'
            envelope = json.loads(path.read_text())
            next(t for t in envelope['audit']['topics'] if t['changed'])['changed']['prose'] = 'Tampered.'
            path.write_text(json.dumps(envelope))
            self.assertEqual(load_public_audit(run, root / 'review'), (None, run))

    def test_damaged_repaired_markdown_falls_back_to_original_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            daily_semantic_review(run, root / 'jev-review', apply_repairs=True,
                                  judge=Judge(headline), repair_provider=PatchProvider())
            (root / 'jev-review/repair/run/final.md').write_text('Tampered.')
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'history', date(2026, 9, 30))
            self.assertEqual(record.disposition, 'ready')
            self.assertIsNone(record.semantic_audit)
            self.assertEqual((root / 'history/2026-09-30.md').read_bytes(), (run / 'final.md').read_bytes())

    def test_schema_rejects_evidence_and_false_confirmation_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', judge=Judge(headline, dispute=True))
            bad = copy.deepcopy(audit)
            bad['topics'][0]['evidence'] = [{'title': 'private'}]
            with self.assertRaises(ValueError):
                parse_semantic_audit(bad)
            bad = copy.deepcopy(audit)
            next(c for c in bad['checks'] if c['confirmation_label'] == 'disputed')['confirmation_label'] = 'confirmed'
            with self.assertRaises(ValueError):
                parse_semantic_audit(bad)
