"""Offline publication outcomes and evidence-binding regression cases."""
import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from agent_runner.checkpoint import sha256_file
from agent_runner.integrity import corpus_health, generation_history
from agent_runner.jev_review import load_topics
from agent_runner.models import ProviderError
from agent_runner.semantic_repairs import daily_semantic_review, load_public_audit
from prepare_publication import prepare_publication
from tests.test_semantic_repairs import CitationJudge, Judge, PatchProvider, make_run


class FailingPatch(PatchProvider):
    def generate(self, request):
        raise ProviderError("private exception body", transient=False)


class UnknownFlagJudge(Judge):
    def evaluate(self, state, questions, *, timeout=30):
        return {"probabilities": {key: 0.9 for key in questions}, "model": "test",
                "cost_usd": None, "latency_ms": 1}


class MixedJudge(CitationJudge):
    def evaluate(self, state, questions, *, timeout=30):
        result = super().evaluate(state, questions, timeout=timeout)
        for index, (key, question) in enumerate(questions.items()):
            if ("combine distinct" in question["instructions"] and
                    state["checks"][index].get("topic", {}).get("headline") == self.headline):
                result["probabilities"][key] = 0.9
        return result


class PreservingPatch(PatchProvider):
    def __init__(self, topic):
        super().__init__()
        self.topic = topic

    def generate(self, request):
        answer = super().generate(request)
        for key in answer.structured_output:
            if isinstance(answer.structured_output[key], dict):
                answer.structured_output[key] = {"headline": self.topic["headline"], "summary": self.topic["prose"]}
        return answer


class MultipleSkipJudge(Judge):
    def __init__(self, topics):
        super().__init__(topics[0]["headline"])
        self.headlines = {t["headline"] for t in topics[:5]}
        self.bad_title = topics[0]["evidence"][0]["title"]

    def evaluate(self, state, questions, *, timeout=30):
        probabilities = {}
        for index, (key, question) in enumerate(questions.items()):
            row = state["checks"][index]
            topic = row.get("topic", {})
            flag = (topic.get("headline") in self.headlines and "material fact unsupported" in
                    question["instructions"] or row.get("citation_evidence", {}).get("title") == self.bad_title)
            probabilities[key] = 0.9 if flag else 0.1
        return {"probabilities": probabilities, "model": "test", "cost_usd": 0.00001, "latency_ms": 1}


class IntegrityPipelineTests(unittest.TestCase):
    def run_review(self, root, *, grouping=False, judge=None, provider=None, apply=True):
        run, headline = make_run(root, grouping=grouping)
        daily_semantic_review(run, root / "jev-review", apply_repairs=apply,
                              judge=judge(headline, run) if judge else Judge(headline),
                              repair_provider=provider or PatchProvider())
        # A single run's review sits beside it; daily chains store it under their root.
        (root / "fallback-log.json").write_text(json.dumps({"status": "ready", "selected_run_dir": "run"}))
        record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1))
        return run, record, json.loads((root / "jev-review/audit.json").read_text())

    def test_corpus_health_distinguishes_undated_empty_and_filtered_quiet_sources(self):
        import corpus_schema
        from news_fetch.collect import error_record
        from tests.test_briefing_output import fixture_contract

        corpus, *_ = fixture_contract()
        rows = []
        for identifier, status, parsed, dated, error_type in (
            ("undated", "empty", 3, 0, "NoDatedEntries"),
            ("zero-entries", "empty", 0, 0, "EmptySource"),
            ("filtered", "quiet", 3, 3, "EntriesFiltered"),
            ("outside-window", "quiet", 3, 3, "NoWindowEntries"),
        ):
            row = {**corpus["sources"][0], "source_id": identifier, "status": status,
                   "parsed_entries": parsed, "dated_entries": dated, "retained_entries": 0,
                   "retained_bytes": 0, "estimated_tokens": 0, "error_type": error_type,
                   "message": "private raw upstream detail"}
            rows.append(row)
        corpus["processing"][rows[0]["category"]]["undated_dropped"] += 3
        corpus["sources"].extend(rows)
        corpus["errors"].extend(error_record(r) for r in rows if r["status"] == "empty")
        self.assertEqual(corpus_schema.validate_corpus(corpus), [])
        projected = corpus_health(corpus)
        reasons = {r["source_id"]: r["reason"] for r in projected}
        self.assertEqual(reasons["undated"], "no_dated_entries")
        self.assertEqual(reasons["zero-entries"], "empty_source")
        self.assertEqual(reasons["filtered"], "entries_filtered")
        self.assertEqual(reasons["outside-window"], "no_window_entries")
        self.assertNotIn("private raw upstream detail", json.dumps(projected))
        self.assertNotIn("EntriesFiltered", json.dumps(projected))
        rows[2]["error_type"] = "private unknown classification"
        self.assertEqual(corpus_schema.validate_corpus(corpus), [])
        self.assertIsNone(corpus_health(corpus))

    def test_complete_no_targets_establishes_no_repair_needed(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), judge=lambda h, r: Judge("never flagged"))
            self.assertEqual(record.integrity["decision"], "original_retained")
            self.assertIn("no_targets", record.integrity["reasons"])
            self.assertEqual(record.integrity["initial_review"]["status"], "complete")
            self.assertIsNone(record.integrity["followup_review"])

    def test_failed_provider_reports_phase_subject_unknown_billing_and_original(self):
        with tempfile.TemporaryDirectory() as directory:
            run, record, _ = self.run_review(Path(directory), provider=FailingPatch())
            integrity = record.integrity
            self.assertEqual(integrity["decision"], "original_retained")
            self.assertEqual(set(integrity["reasons"]) - {"baseline_unavailable"},
                             {"provider_failure", "unknown_billing"})
            failure = next(a for a in integrity["actions"] if a["action"] == "prose_repair" and
                           a["outcome"] == "failed")
            self.assertTrue(failure["positions"])
            self.assertTrue(failure["check_refs"])
            self.assertNotIn("private exception body", json.dumps(record.payload()))
            self.assertEqual(load_public_audit(run, Path(directory) / "jev-review")[1], run)

    def test_clear_candidate_mode_retains_original_and_separates_paid_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, record, _ = self.run_review(root, apply=False)
            integrity = record.integrity
            self.assertEqual(integrity["decision"], "candidate_retained")
            self.assertTrue(integrity["acceptance_verified"])
            self.assertIn("candidate_mode", integrity["reasons"])
            calls = json.loads((root / "jev-review/repair/repair-calls.json").read_text())
            self.assertEqual(calls[0]["status"], "code_preserved")
            self.assertEqual(sum(c["status"] != "code_preserved" for c in calls), 1)
            self.assertEqual((root / "history/2026-10-01.md").read_bytes(), (run / "final.md").read_bytes())

    def test_grouping_removal_cause_is_grouping_and_singleton_has_no_invented_score(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), grouping=True,
                                           judge=lambda h, r: Judge(h, grouping=True))
            removals = [a for a in record.integrity["actions"] if a["action"] == "remove_source"]
            self.assertEqual(len(removals), 1)
            self.assertEqual(removals[0]["reasons"], ["grouping_subset"])
            self.assertEqual(removals[0]["source_indexes"], [1])
            pos = removals[0]["positions"]
            initial = next(c for c in record.semantic_audit["checks"] if c["check"] == "unsafe_grouping"
                           and c["positions"] == pos)
            self.assertEqual(initial["after_basis"], "single_evidence")
            self.assertIsNone(initial["after_probability"])
            self.assertFalse(any(c["check"] == "unsafe_grouping" and c["positions"] == pos
                                 for c in record.semantic_audit["followup_checks"]))

    def test_new_followup_blocker_below_initial_threshold_is_identified(self):
        def judge(headline, run):
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            return CitationJudge(headline, topic["evidence"][0]["title"], after_score=0.65)
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), grouping=True, judge=judge)
            self.assertIn("followup_flag", record.integrity["reasons"])
            blocker = next(a for a in record.integrity["actions"] if a["check_refs"] and
                           a["check_refs"][0]["stage"] == "followup")
            row = record.semantic_audit["followup_checks"][blocker["check_refs"][0]["index"]]
            initial = next(c for c in record.semantic_audit["checks"] if c["check"] == row["check"]
                           and c["positions"] == row["positions"]
                           and c.get("evidence_index") == row.get("evidence_index"))
            self.assertLess(initial["probability"], 0.6)
            self.assertEqual(row["probability"], 0.65)
            self.assertEqual(row["evidence_index"], 1)

    def test_partial_confirmation_scope_with_unknown_billing_has_multiple_causes(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), judge=lambda h, r: UnknownFlagJudge(h))
            self.assertTrue({"incomplete_review", "unknown_billing"} <= set(record.integrity["reasons"]))
            coverage = record.integrity["initial_review"]
            self.assertGreater(coverage["confirmation_required"], coverage["confirmation_returned"])
            self.assertNotIn("no_targets", record.integrity["reasons"])

    def test_followup_failure_preserves_candidate_and_separate_unknown_cost(self):
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), judge=lambda h, r: Judge(h, fail_after_repair=True))
            self.assertEqual(record.integrity["decision"], "original_retained")
            self.assertTrue({"incomplete_followup", "unknown_billing"} <= set(record.integrity["reasons"]))
            costs = {c["phase"]: c for c in record.integrity["costs"]}
            self.assertEqual(costs["initial_review"]["unknown_cost_calls"], 0)
            self.assertEqual(costs["repair_generation"]["unknown_cost_calls"], 0)
            self.assertGreater(costs["followup_review"]["unknown_cost_calls"], 0)

    def test_original_fallback_identity_survives_different_repair_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, _ = self.run_review(root)
            (root / "fallback-log.json").write_text(json.dumps({"status": "ready", "selected_run_dir": "run",
                "model_chain": ["other", "fake"], "attempts": [{"run_dir": "run", "index": 2}]}))
            record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1),
                                         workflow_run_id=123456)
            self.assertEqual(record.provenance.attempt_index, 2)
            self.assertEqual(record.integrity["generation"]["attempt_index"], 2)
            self.assertEqual(record.integrity["generation"]["model"], "deterministic")
            self.assertEqual(record.integrity["repair_generation"]["model"], "patch")
            self.assertEqual(record.integrity["workflow_run_id"], 123456)
            self.assertNotEqual(record.integrity["generation"]["prompt_sha256"],
                                record.integrity["repair_generation"]["prompt_sha256"])
            self.assertNotEqual((run / "final.md").read_bytes(), (root / "history/2026-10-01.md").read_bytes())

    def test_audit_metadata_and_valid_looking_public_claims_do_not_authorize_application(self):
        for field in ("cost", "removed", "grouping", "singleton", "integrity", "followup", "unaffected"):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                run, _, envelope = self.run_review(root, grouping=True,
                                                   judge=lambda h, r: Judge(h, grouping=True))
                if field == "cost":
                    envelope["audit"]["reported_cost_usd"] = 0
                elif field == "removed":
                    next(t for t in envelope["audit"]["topics"] if t["changed"])["removed_evidence_count"] = 0
                elif field == "grouping":
                    envelope["metadata"]["grouping"] = []
                elif field == "singleton":
                    row = next(c for c in envelope["audit"]["checks"] if c["after_basis"] == "single_evidence")
                    row["after_basis"] = None
                elif field == "followup":
                    envelope["audit"]["followup_checks"][0]["probability"] = 0.2
                elif field == "unaffected":
                    topic = next(t for t in envelope["audit"]["topics"] if t["repair_status"] == "unchanged")
                    topic.update(repair_status="failed", removed_evidence_count=4)
                else:
                    envelope["integrity"]["generation"] = None
                    envelope["integrity"]["reasons"] = ["candidate_mode"]
                (root / "jev-review/audit.json").write_text(json.dumps(envelope))
                self.assertEqual(load_public_audit(run, root / "jev-review"), (None, run))
                record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1))
                self.assertIsNone(record.semantic_audit)
                self.assertIn("verification_failed", record.integrity["reasons"])
                self.assertEqual((root / "history/2026-10-01.md").read_bytes(), (run / "final.md").read_bytes())

    def test_superseded_generation_selection_repair_has_private_subject(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, envelope = self.run_review(root, judge=lambda h, r: Judge("never flagged"))
            manifest = json.loads((run / "manifest.json").read_text())
            final = manifest["attempts"][-1]
            history = [copy.deepcopy(final), copy.deepcopy(final)]
            history[0].update(index=1, kind="selection_repair", repair_actions=[{"action": "drop_entry", "path":
                "topics.AI News[0]", "reason": "private explanation"}])
            history[1].update(index=2, kind="selection_correction")
            manifest["attempts"] = history + [{**final, "index": 3}]
            manifest["final"]["attempt"] = 3
            (run / "manifest.json").write_text(json.dumps(manifest))
            record = generation_history(run, envelope["integrity"])
            action = next(a for a in record["actions"] if a["action"] == "selection_repair")
            self.assertEqual(action["outcome"], "superseded")
            self.assertEqual(action["positions"], [])
            self.assertNotIn("private explanation", json.dumps(record))
            self.assertEqual(next(a["sha256"] for a in record["artifacts"] if a["id"] == "initial_prose"),
                             sha256_file(run / final["briefing_artifact"]))

    def test_legacy_applied_audit_keeps_content_and_original_provenance_with_unknown_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, envelope = self.run_review(root)
            envelope.pop("integrity")
            envelope["metadata"]["schema_version"] = 1
            for field in ("apply_repairs", "repair_calls_sha256", "repair_manifest_sha256"):
                envelope["metadata"].pop(field)
            envelope["audit"].pop("followup_checks")
            (root / "jev-review/audit.json").write_text(json.dumps(envelope))
            record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1))
            self.assertIsNone(record.integrity)
            self.assertEqual(record.semantic_audit, envelope["audit"])
            self.assertEqual((root / "history/2026-10-01.md").read_bytes(),
                             (root / "jev-review/repair/run/final.md").read_bytes())
            self.assertEqual(record.provenance.model, "deterministic")
            self.assertNotEqual(sha256_file(run / "final.md"), sha256_file(root / "jev-review/repair/run/final.md"))

    def test_target_limit_and_all_sources_removal_both_withhold_the_same_round(self):
        from agent_runner.integrity import repair_scope
        positions = [{"bucket": "sections", "section": "News", "index": i} for i in range(5)]
        report = {"status": "complete", "calls": [], "topics": [
            {"position": p, "evidence": [{}]} for p in positions], "results": [
            {"check": "irrelevant_citation", "positions": [p], "evidence_index": 0,
             "confirmation_label": "confirmed"} for p in positions]}
        self.assertEqual(set(repair_scope(report)[3]), {"target_limit", "all_sources_removed"})

    def test_multiple_skip_causes_are_saved_for_each_affected_story(self):
        def judge(headline, run):
            return MultipleSkipJudge([t for t in load_topics(run)[0] if t["included"]])
        with tempfile.TemporaryDirectory() as directory:
            provider = PatchProvider()
            _, record, _ = self.run_review(Path(directory), judge=judge, provider=provider)
            self.assertFalse(provider.requests)
            self.assertTrue({"target_limit", "all_sources_removed"} <= set(record.integrity["reasons"]))
            actions = [a for a in record.integrity["actions"] if a["action"] == "withhold_repair"]
            self.assertEqual(len(actions), 5)
            self.assertTrue(all({"target_limit", "all_sources_removed"} <= set(a["reasons"]) for a in actions))

    def test_partial_question_scope_never_claims_no_repair_required(self):
        from agent_runner.jev_review import review_run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("agent_runner.semantic_repairs.review_run", side_effect=lambda *args, **kwargs:
                       review_run(*args, **kwargs, max_pairs=0)):
                _, record, _ = self.run_review(root, judge=lambda h, r: Judge("never flagged"))
            self.assertIn("incomplete_review", record.integrity["reasons"])
            self.assertNotIn("no_targets", record.integrity["reasons"])
            self.assertGreater(record.integrity["initial_review"]["omitted_pairs"], 0)

    def test_citation_removal_can_preserve_all_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, headline = make_run(root, grouping=True)
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            # This fixture begins with complete prose so preserving it satisfies the existing repair policy.
            manifest = json.loads((run / "manifest.json").read_text())
            attempt = next(a for a in manifest["attempts"] if a["index"] == manifest["final"]["attempt"])
            candidate = json.loads((run / attempt["structured_artifact"]).read_text())
            pos = topic["position"]
            complete = "The Supreme Court will hear arguments on Nov. 3."
            candidate["sections"][pos["section"]]["topics"][pos["index"]]["summary"] = complete
            (run / attempt["structured_artifact"]).write_text(json.dumps(candidate))
            manifest["artifacts"][attempt["structured_artifact"]] = sha256_file(run / attempt["structured_artifact"])
            for name in ("final.md", attempt["briefing_artifact"]):
                (run / name).write_text((run / name).read_text().replace(topic["prose"], complete))
                manifest["artifacts"][name] = sha256_file(run / name)
            manifest["final"]["output_sha256"] = sha256_file(run / "final.md")
            (run / "manifest.json").write_text(json.dumps(manifest))
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            daily_semantic_review(run, root / "jev-review", apply_repairs=True,
                                  judge=CitationJudge(headline, topic["evidence"][0]["title"]),
                                  repair_provider=PreservingPatch(topic))
            (root / "fallback-log.json").write_text(json.dumps({"status": "ready", "selected_run_dir": "run"}))
            record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1))
            changed = next(t for t in record.semantic_audit["topics"] if t["changed"])
            self.assertEqual(changed["original"], changed["changed"])
            self.assertEqual(changed["removed_evidence_count"], 1)
            self.assertEqual(record.integrity["decision"], "repaired_applied")

    def test_failed_citation_repair_keeps_source_trigger_and_failed_removal(self):
        def judge(headline, run):
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            return CitationJudge(headline, topic["evidence"][0]["title"])
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), grouping=True, judge=judge, provider=FailingPatch())
            removal = next(a for a in record.integrity["actions"] if a["action"] == "remove_source")
            self.assertEqual(removal["outcome"], "failed")
            self.assertEqual(removal["source_indexes"], [0])
            self.assertIn("confirmed_irrelevance", removal["reasons"])
            self.assertIn("provider_failure", removal["reasons"])

    def test_multiple_triggers_share_one_story_and_keep_both_removal_causes(self):
        def judge(headline, run):
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            return MixedJudge(headline, topic["evidence"][0]["title"])
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), grouping=True, judge=judge)
            self.assertEqual(len([t for t in record.semantic_audit["topics"] if t["changed"]]), 1)
            removal = next(a for a in record.integrity["actions"] if a["action"] == "remove_source")
            self.assertEqual(set(removal["reasons"]), {"confirmed_irrelevance", "grouping_subset"})
            self.assertEqual(len(removal["check_refs"]), 2)

    def test_citation_removal_preserves_original_index_and_changes_prose_once(self):
        def judge(headline, run):
            topic = next(t for t in load_topics(run)[0] if t["headline"] == headline)
            return CitationJudge(headline, topic["evidence"][0]["title"])
        with tempfile.TemporaryDirectory() as directory:
            _, record, _ = self.run_review(Path(directory), grouping=True, judge=judge)
            removal = next(a for a in record.integrity["actions"] if a["action"] == "remove_source")
            self.assertEqual(removal["reasons"], ["confirmed_irrelevance"])
            self.assertEqual(removal["source_indexes"], [0])
            changed = [t for t in record.semantic_audit["topics"] if t["changed"]]
            self.assertEqual(len(changed), 1)
            self.assertNotEqual(changed[0]["original"], changed[0]["changed"])


if __name__ == "__main__":
    unittest.main()
