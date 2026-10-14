import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from agent_runner.integrity import position_key
from agent_runner.jev_review import load_topics, load_verified_run
from agent_runner.models import ModelResponse, ProviderError
from agent_runner.output import validate_output
from agent_runner.runner import RunnerSettings, run_workflow
from agent_runner.semantic_repairs import daily_semantic_review, load_public_audit, verify_repair
from prepare_publication import prepare_publication
from publication_schema import parse_semantic_audit
from tests.test_briefing_output import ROOT, fixture_contract, used_item_refs
from tests.test_run_briefing import FakeProvider

REPAIRED_PROSE = 'A supported complete sentence.'


def repaired(topic):
    """Code keeps headlines on prose-only repairs, so fakes recognize repaired slots by prose."""
    return topic.get('prose', '').startswith('A supported complete sentence')


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
        if self.fail_after_repair and any(repaired(c.get('topic', {})) for c in state['checks']):
            raise ProviderError('remote private body', transient=False, ambiguous_completion=True)
        probabilities = {}
        for i, (key, question) in enumerate(questions.items()):
            topic = state['checks'][i].get('topic', {})
            instructions = question['instructions']
            relevant = ('combine distinct' if self.grouping else 'material fact unsupported') in instructions
            flagged = relevant and topic.get('headline') == self.headline and not repaired(topic)
            if repaired(topic) and self.reject_after:
                flagged = relevant
            # Confirmation requests contain exactly one question.
            probabilities[key] = 0.2 if flagged and len(questions) == 1 and self.dispute else 0.9 if flagged else 0.1
        return {'probabilities': probabilities, 'model': 'typesafe/jev-test',
                'cost_usd': None if self.missing_cost else 0.00001, 'latency_ms': 1}


class CitationJudge(Judge):
    def __init__(self, headline, bad_title, *, score=0.7, confirmation=0.7, after_score=0.1):
        super().__init__(headline)
        self.bad_title = bad_title
        self.score = score
        self.confirmation = confirmation
        self.after_score = after_score

    def evaluate(self, state, questions, *, timeout=30):
        self.calls.append((copy.deepcopy(state), copy.deepcopy(questions)))
        probabilities = {}
        for i, (key, question) in enumerate(questions.items()):
            check = state['checks'][i]
            relevant = ('citation_evidence' in question['instructions']
                        and check['topic']['headline'] == self.headline
                        and check['citation_evidence']['title'] == self.bad_title)
            after = 'citation_evidence' in question['instructions'] and repaired(check['topic'])
            probabilities[key] = (self.confirmation if len(questions) == 1 else self.score) if relevant else (
                self.after_score if after else 0.1)
        return {'probabilities': probabilities, 'model': 'typesafe/jev-test', 'cost_usd': 0.00001, 'latency_ms': 1}


class PatchProvider:
    name = 'fake'
    model = 'patch'

    def __init__(self, *, bad_subset=False, incomplete=False, force_headline=False):
        self.requests = []
        self.bad_subset = bad_subset
        self.incomplete = incomplete
        self.force_headline = force_headline

    def info(self):
        return {'provider': self.name, 'model': self.model}

    def generate(self, request):
        self.requests.append(request)
        output = {}
        for key, schema in request.output_schema['properties'].items():
            if schema['type'] == 'array':
                output[key] = [10000] if self.bad_subset else [0]
            else:
                # Distinct prose per slot keeps multi-target candidates free of duplicate summaries.
                summary = REPAIRED_PROSE if key == 'repair_0' else f'A supported complete sentence {key}.'
                output[key] = {'summary': summary if not self.incomplete else 'An unfinished…'}
                if 'headline' in schema['properties'] or self.force_headline:
                    output[key]['headline'] = 'Repaired headline'

        return ModelResponse('', output, 1, cost_usd=0.00001)


class SplitJudge(CitationJudge):
    """Confirms a citation target and a prose target; the prose target stays flagged after repair."""

    def __init__(self, headline, bad_title, second, *, after_score=0.1):
        super().__init__(headline, bad_title, after_score=after_score)
        self.second = second

    def evaluate(self, state, questions, *, timeout=30):
        result = super().evaluate(state, questions, timeout=timeout)
        for i, (key, question) in enumerate(questions.items()):
            if ('material fact unsupported' in question['instructions']
                    and state['checks'][i].get('topic', {}).get('headline') == self.second):
                result['probabilities'][key] = 0.86
        return result


def split_review(root, *, after_score=0.1):
    """Two confirmed targets: a citation removal in US Politics and a prose flag in AI Dev Tools."""
    run, headline = make_run(root, grouping=True)
    topics = load_topics(run)[0]
    first = next(t for t in topics if t['headline'] == headline)
    second = next(t for t in topics if t['position'] == {'bucket': 'sections', 'section': 'AI Dev Tools',
                                                         'index': 0})
    judge = SplitJudge(headline, first['evidence'][0]['title'], second['headline'], after_score=after_score)
    audit = daily_semantic_review(run, root / 'jev-review', judge=judge, repair_provider=PatchProvider())
    return run, audit, first, second


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
                              prompt_path=ROOT / 'briefing-runner-prompt.md',
                              output_path=root / 'briefing.md', corpus_path=root / 'input.json')
    result = run_workflow(FakeProvider([output]), settings, root / 'run')
    assert result.status == 'ready'
    return root / 'run', target['headline']


class SemanticRepairTests(unittest.TestCase):
    def test_citation_removal_at_lower_threshold_and_followup_indexes_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            topic = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            provider = PatchProvider()
            judge = CitationJudge(headline, topic['evidence'][0]['title'])
            audit = daily_semantic_review(run, root / 'review',
                                          judge=judge, repair_provider=provider)
            rows = [c for c in audit['checks'] if c['check'] == 'irrelevant_citation'
                    and c['positions'] == [topic['position']]]
            self.assertEqual(audit['citation_threshold'], 0.6)
            self.assertEqual(audit['threshold'], 0.8)
            self.assertEqual(rows[0]['confirmation_label'], 'confirmed')
            self.assertEqual(rows[0]['after_basis'], 'citation_removed')
            self.assertIsNone(rows[0]['after_probability'])
            self.assertEqual(rows[1]['after_probability'], 0.1)
            changed = next(t for t in audit['topics'] if t['changed'])
            self.assertEqual(changed['repair_status'], 'applied')
            self.assertEqual(changed['removed_evidence_count'], 1)
            self.assertEqual(len(provider.requests), 1)
            self.assertNotIn(topic['evidence'][0]['title'], provider.requests[0].prompt)
            isolated = [state['checks'][0] for state, q in judge.calls if len(q) == 1]
            self.assertEqual(isolated[0]['citation_evidence'], topic['evidence'][0])
            self.assertEqual(set(isolated[0]), {'topic', 'citation_evidence'})
            self.assertNotRegex(json.dumps(judge.calls), r'https?://|\b(?:citation|item)_\d+\b')
            self.assertEqual(load_public_audit(run, root / 'review')[0], audit)
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            (root / 'review').rename(root / 'jev-review')
            record = prepare_publication(root, root / 'input.json', root / 'published', date(2026, 10, 1))
            self.assertEqual(record.disposition, 'ready')
            self.assertEqual(record.semantic_audit, audit)
            markdown = (root / 'published/2026-10-01.md').read_text()
            self.assertNotIn(rows[0]['citation_urls'][0], markdown)
            for field, value in (('citation_urls', ['https://example.com/wrong']), ('evidence_index', 1)):
                envelope = json.loads((root / 'jev-review/audit.json').read_text())
                next(c for c in envelope['audit']['checks'] if c['check'] == 'irrelevant_citation'
                     and c['confirmation_label'] == 'confirmed')[field] = value
                (root / 'jev-review/audit.json').write_text(json.dumps(envelope))
                self.assertEqual(load_public_audit(run, root / 'jev-review'), (None, run))

    def test_followup_citation_above_its_cutoff_prevents_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            topic = next(t for t in load_topics(run)[0] if t['headline'] == headline)
            judge = CitationJudge(headline, topic['evidence'][0]['title'], after_score=0.65)
            audit = daily_semantic_review(run, root / 'review',
                                          judge=judge,
                                          repair_provider=PatchProvider())
            self.assertIn('rejected', [t['repair_status'] for t in audit['topics']])
            self.assertEqual(load_public_audit(run, root / 'review')[1], run)
            self.assertTrue(any(c['after_probability'] == 0.65 for c in audit['checks']))

    def test_citation_schema_rejects_executable_urls_and_wrong_index_types(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', judge=Judge('never flagged'))
            for field, value in (('citation_urls', ['javascript:alert(1)']), ('evidence_index', True),
                                 ('citation_urls', ['https://user:password@example.com'])):
                bad = copy.deepcopy(audit)
                next(c for c in bad['checks'] if c['check'] == 'irrelevant_citation')[field] = value
                with self.assertRaises(ValueError):
                    parse_semantic_audit(bad)

    def test_disputed_citation_and_last_source_are_preserved(self):
        for grouping, confirmation in ((True, 0.59), (False, 0.7)):
            with self.subTest(grouping=grouping), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, headline = make_run(root, grouping=grouping)
                topic = next(t for t in load_topics(run)[0] if t['headline'] == headline)
                provider = PatchProvider()
                judge = CitationJudge(headline, topic['evidence'][0]['title'], confirmation=confirmation)
                audit = daily_semantic_review(run, root / 'review',
                                              judge=judge, repair_provider=provider)
                self.assertFalse(provider.requests)
                row = next(c for c in audit['checks'] if c['check'] == 'irrelevant_citation'
                           and c['positions'] == [topic['position']] and c['evidence_index'] == 0)
                self.assertEqual(row['confirmation_label'], 'disputed' if grouping else 'confirmed')
                self.assertEqual(load_public_audit(run, root / 'review')[1], run)

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
            audit = daily_semantic_review(run, run / '../jev-review',
                                          judge=Judge(headline), repair_provider=provider)
            changed = next(t for t in audit['topics'] if t['changed'])
            self.assertEqual(changed['repair_status'], 'applied')
            self.assertEqual(changed['removed_evidence_count'], 0)
            self.assertEqual(len(provider.requests), 1)
            self.assertEqual(before, {p.name: p.read_bytes() for p in run.iterdir() if p.is_file()})
            self.assertNotRegex(json.dumps(provider.requests[0].prompt), r'https?://|\b(?:citation|item)_\d+\b')
            # Prose-only repairs never ask for a headline; code keeps the original one.
            self.assertEqual(set(provider.requests[0].output_schema['properties']['repair_0']['properties']),
                             {'summary'})
            self.assertEqual(changed['changed'], {'headline': headline, 'prose': REPAIRED_PROSE})
            bound, selected = load_public_audit(run, root / 'jev-review')
            self.assertEqual(bound, audit)
            self.assertNotEqual(selected, run)
            # Daily fallback chains keep the original selected child immutable.
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'history', date(2026, 9, 30))
            self.assertEqual(record.semantic_audit, audit)
            self.assertIn(f'**{headline}** — {REPAIRED_PROSE}', (root / 'history/2026-09-30.md').read_text())

    def test_headline_change_without_source_removal_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review', judge=Judge(headline),
                                          repair_provider=PatchProvider(force_headline=True))
            self.assertIn('failed', [t['repair_status'] for t in audit['topics']])
            self.assertFalse(any(t['changed'] for t in audit['topics']))
            self.assertEqual(load_public_audit(run, root / 'review')[1], run)
            # The verifier enforces the same rule on any persisted candidate, independent of the provider.
            corpus, config, projected, output = fixture_contract()
            section = next(iter(output['sections']))
            output['sections'][section]['topics'][0]['headline'] = 'Changed headline'
            settings = RunnerSettings(config_path=ROOT / 'fixtures/briefing-config-2026-08-11.json',
                                      prompt_path=ROOT / 'briefing-runner-prompt.md',
                                      output_path=root / 'other.md', corpus_path=root / 'input.json')
            self.assertEqual(run_workflow(FakeProvider([output]), settings, root / 'other').status, 'ready')
            key = position_key({'bucket': 'sections', 'section': section, 'index': 0})
            with self.assertRaisesRegex(ValueError, 'headline without removing a source'):
                verify_repair(run, root / 'other', {key}, set(), preserve_headlines=True)
            verify_repair(run, root / 'other', {key}, set())

    def test_cleared_slot_is_applied_while_reflagged_slot_keeps_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, audit, first, second = split_review(root)
            statuses = {position_key(t['position']): t['repair_status'] for t in audit['topics']}
            first_key, second_key = position_key(first['position']), position_key(second['position'])
            self.assertEqual(statuses.pop(first_key), 'applied')
            self.assertEqual(statuses.pop(second_key), 'rejected')
            self.assertEqual(set(statuses.values()), {'unchanged'})
            # The re-flagged slot kept its headline even though its prose was rewritten.
            rejected = next(t for t in audit['topics'] if position_key(t['position']) == second_key)
            self.assertEqual(rejected['changed']['headline'], second['headline'])
            bound, selected = load_public_audit(run, root / 'jev-review')
            self.assertEqual(bound, audit)
            self.assertEqual(selected, root / 'jev-review/assembly/run')
            original = load_verified_run(run)
            repaired = load_verified_run(root / 'jev-review/repair/run')
            assembled = load_verified_run(selected)
            for section, data in original.candidate['sections'].items():
                for index in range(len(data['topics'])):
                    key = position_key({'bucket': 'sections', 'section': section, 'index': index})
                    expected = repaired.candidate if key == first_key else original.candidate
                    self.assertEqual(assembled.candidate['sections'][section]['topics'][index],
                                     expected['sections'][section]['topics'][index])
            self.assertEqual(assembled.candidate['excluded_topics'], original.candidate['excluded_topics'])
            metadata = json.loads((root / 'jev-review/audit.json').read_text())['metadata']
            self.assertEqual(metadata['schema_version'], 3)
            self.assertEqual(metadata['accepted_targets'], [first_key])
            self.assertFalse(metadata['post_review_clear'])
            self.assertTrue(metadata['applied'])
            (root / 'fallback-log.json').write_text(json.dumps({'status': 'ready', 'selected_run_dir': 'run'}))
            record = prepare_publication(root, root / 'input.json', root / 'history', date(2026, 10, 13))
            markdown = (root / 'history/2026-10-13.md').read_text()
            self.assertEqual(markdown.encode(), (selected / 'final.md').read_bytes())
            self.assertIn('**Repaired headline** — A supported complete sentence repair_1.', markdown)
            self.assertNotIn(REPAIRED_PROSE, markdown)
            self.assertIn(f"**{second['headline']}** — {second['prose']}", markdown)
            removed = next(c for c in audit['checks'] if c['after_basis'] == 'citation_removed')
            self.assertNotIn(removed['citation_urls'][0], markdown)
            self.assertEqual(record.integrity['decision'], 'partial_applied')

    def test_tampered_assembly_falls_back_to_original(self):
        for tamper in ('markdown', 'accepted'):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, _audit, _first, second = split_review(root)
                if tamper == 'markdown':
                    (root / 'jev-review/assembly/run/final.md').write_text('Tampered.')
                else:
                    path = root / 'jev-review/audit.json'
                    envelope = json.loads(path.read_text())
                    envelope['metadata']['accepted_targets'].append(position_key(second['position']))
                    envelope['metadata']['accepted_targets'].sort()
                    path.write_text(json.dumps(envelope))
                self.assertEqual(load_public_audit(run, root / 'jev-review'), (None, run))

    def test_all_reflagged_targets_retain_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, audit, _first, _second = split_review(root, after_score=0.65)
            self.assertEqual({t['repair_status'] for t in audit['topics'] if t['changed']}, {'rejected'})
            self.assertFalse((root / 'jev-review/assembly').exists())
            self.assertEqual(load_public_audit(run, root / 'jev-review')[1], run)
            metadata = json.loads((root / 'jev-review/audit.json').read_text())['metadata']
            self.assertEqual((metadata['accepted_targets'], metadata['applied']), ([], False))

    def test_grouping_repair_uses_subset_then_only_refrozen_evidence_for_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            provider = PatchProvider()
            audit = daily_semantic_review(run, root / 'review',
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
                audit = daily_semantic_review(run, root / 'review',
                                              judge=Judge(headline, grouping=True),
                                              repair_provider=PatchProvider(**options))
                self.assertIn('failed', [t['repair_status'] for t in audit['topics']])
                self.assertEqual(load_public_audit(run, root / 'review')[1], run)

    def test_high_post_score_rejects_repair_and_preserves_changed_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            audit = daily_semantic_review(run, root / 'review',
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
            audit = daily_semantic_review(run, root / 'review',
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
            daily_semantic_review(run, root / 'review',
                                  judge=Judge(headline), repair_provider=PatchProvider())
            path = root / 'review/audit.json'
            envelope = json.loads(path.read_text())
            next(t for t in envelope['audit']['topics'] if t['changed'])['changed']['prose'] = 'Tampered.'
            path.write_text(json.dumps(envelope))
            self.assertEqual(load_public_audit(run, root / 'review'), (None, run))

    def test_valid_looking_score_tampering_and_changed_review_artifact_are_rejected(self):
        for tamper in ('audit-score', 'post-report'):
            with self.subTest(tamper=tamper), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, headline = make_run(root)
                daily_semantic_review(run, root / 'review',
                                      judge=Judge(headline), repair_provider=PatchProvider())
                path = root / ('review/audit.json' if tamper == 'audit-score' else 'review/post-review/report.json')
                data = json.loads(path.read_text())
                if tamper == 'audit-score':
                    row = next(c for c in data['audit']['checks'] if c['confirmation_label'] == 'confirmed')
                    row['probability'] = 0.85
                else:
                    data['results'][0]['probability'] = 0.05
                path.write_text(json.dumps(data))
                self.assertEqual(load_public_audit(run, root / 'review'), (None, run))

    def test_damaged_repaired_markdown_falls_back_to_original_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root)
            daily_semantic_review(run, root / 'jev-review',
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
