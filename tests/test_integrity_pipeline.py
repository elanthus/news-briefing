"""Offline publication outcomes and evidence-binding regression cases."""
import copy
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from agent_runner.checkpoint import sha256_file
from agent_runner.integrity import corpus_health, degraded_health_records, generation_history, position_key
from agent_runner.jev_review import load_topics
from agent_runner.models import ProviderError
from agent_runner.semantic_repairs import daily_semantic_review, load_public_audit
from prepare_publication import prepare_publication
from publication_schema import parse_integrity
from tests.test_semantic_repairs import CitationJudge, Judge, PatchProvider, make_run, split_review


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
                answer.structured_output[key] = {"summary": self.topic["prose"]}
                if "headline" in request.output_schema["properties"][key]["properties"]:
                    answer.structured_output[key]["headline"] = self.topic["headline"]
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
    def run_review(self, root, *, grouping=False, judge=None, provider=None):
        run, headline = make_run(root, grouping=grouping)
        daily_semantic_review(run, root / "jev-review",
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
        degraded = {r["source_id"] for r in degraded_health_records(projected)}
        self.assertTrue({"undated", "zero-entries"} <= degraded)
        self.assertNotIn("filtered", degraded)
        self.assertNotIn("outside-window", degraded)
        rows[2]["error_type"] = "private unknown classification"
        self.assertEqual(corpus_schema.validate_corpus(corpus), [])
        self.assertIsNone(corpus_health(corpus))

    def test_public_health_uses_bound_run_corpus_and_marks_unavailable_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _ = make_run(root)
            (root / "input.json").write_text(json.dumps({"errors": [{"source_type": "rss",
                                                                      "source_id": "unrelated failure"}]}))
            record = prepare_publication(run, root / "input.json", root / "public", date(2026, 10, 1))
            expected = tuple(f'{r["source_type"]}:{r["source_id"]}'
                             for r in degraded_health_records(record.integrity["corpus_health"]))
            self.assertEqual(record.degraded_sources, expected)
            self.assertNotIn("unrelated failure", json.dumps(record.payload()))
            (run / "corpus.json").write_text("{}")
            record = prepare_publication(run, root / "input.json", root / "public", date(2026, 10, 1))
            self.assertIsNone(record.integrity["corpus_health"])
            self.assertEqual(record.degraded_sources, ("source health unavailable",))

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

    def test_retired_audit_versions_cannot_select_a_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, envelope = self.run_review(root)
            for version in (1, 2, True, 4):
                envelope["metadata"]["schema_version"] = version
                (root / "jev-review/audit.json").write_text(json.dumps(envelope))
                with self.subTest(version=version):
                    self.assertEqual(load_public_audit(run, root / "jev-review"), (None, run))

    def test_current_audit_mutation_is_rejected_at_reader_handoff(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, envelope = self.run_review(root)
            envelope["audit"]["reported_cost_usd"] += 0.1
            (root / "jev-review/audit.json").write_text(json.dumps(envelope))
            self.assertEqual(load_public_audit(run, root / "jev-review"), (None, run))

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

    def test_old_applied_audit_never_substitutes_for_verified_current_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, _, envelope = self.run_review(root)
            envelope.pop("integrity")
            envelope["metadata"]["schema_version"] = 1
            (root / "jev-review/audit.json").write_text(json.dumps(envelope))
            record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 1))
            self.assertIsNone(record.semantic_audit)
            self.assertEqual(record.integrity["decision"], "original_retained")
            self.assertEqual((root / "history/2026-10-01.md").read_bytes(), (run / "final.md").read_bytes())

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
            daily_semantic_review(run, root / "jev-review",
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

    def split_record(self, root, **options):
        run, audit, first, second = split_review(root, **options)
        (root / "fallback-log.json").write_text(json.dumps({"status": "ready", "selected_run_dir": "run"}))
        record = prepare_publication(root, root / "input.json", root / "history", date(2026, 10, 13))
        return run, record, first, second

    def test_partial_publication_records_per_story_outcomes_and_truthful_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, record, first, second = self.split_record(root)
            integrity = record.integrity
            self.assertEqual(integrity["decision"], "partial_applied")
            self.assertTrue(integrity["acceptance_verified"])
            self.assertEqual(set(integrity["reasons"]) - {"baseline_unavailable"}, {"followup_flag"})
            artifacts = {a["id"]: a["sha256"] for a in integrity["artifacts"]}
            self.assertEqual(artifacts["original"], sha256_file(run / "final.md"))
            self.assertEqual(artifacts["candidate"], sha256_file(root / "jev-review/repair/run/final.md"))
            self.assertEqual(artifacts["published"], sha256_file(root / "history/2026-10-13.md"))
            self.assertEqual(artifacts["published"], sha256_file(root / "jev-review/assembly/run/final.md"))
            self.assertEqual(len({artifacts["original"], artifacts["candidate"], artifacts["published"]}), 3)
            outcomes = {}
            for action in integrity["actions"]:
                if action["action"] in {"prose_repair", "remove_source", "apply_repair"} or (
                    action["action"] == "withhold_repair" and action["actor"] == "code"
                ):
                    key = position_key(action["positions"][0])
                    outcomes.setdefault(key, set()).add((action["action"], action["outcome"]))
            self.assertEqual(outcomes[position_key(first["position"])], {
                ("prose_repair", "applied"), ("remove_source", "applied"), ("apply_repair", "applied")})
            self.assertEqual(outcomes[position_key(second["position"])], {
                ("prose_repair", "rejected"), ("withhold_repair", "rejected")})
            withheld = next(a for a in integrity["actions"] if a["action"] == "withhold_repair"
                            and a["actor"] == "code")
            self.assertEqual((withheld["artifact"], withheld["reasons"]), ("original", ["followup_flag"]))
            blocker = record.semantic_audit["followup_checks"][withheld["check_refs"][0]["index"]]
            self.assertEqual((blocker["check"], blocker["positions"], blocker["probability"]),
                             ("unsupported_claim", [second["position"]], 0.86))
            phases = [p["phase"] for p in integrity["phases"]]
            self.assertLess(phases.index("followup_review"), phases.index("partial_assembly"))
            self.assertLess(phases.index("partial_assembly"), phases.index("publication_verification"))
            publication = next(a for a in integrity["actions"] if a["action"] == "publication")
            self.assertEqual(publication["outcome"], "applied")

    def test_all_reflagged_partial_round_retains_original_decision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run, record, *_ = self.split_record(root, after_score=0.65)
            self.assertEqual(record.integrity["decision"], "original_retained")
            self.assertFalse(record.integrity["acceptance_verified"])
            self.assertIn("followup_flag", record.integrity["reasons"])
            self.assertFalse(any(a["action"] == "apply_repair" for a in record.integrity["actions"]))
            self.assertEqual((root / "history/2026-10-13.md").read_bytes(), (run / "final.md").read_bytes())

    def test_schema_parses_partial_decision_and_rejects_unknown_or_inconsistent_records(self):
        with tempfile.TemporaryDirectory() as directory:
            _run, record, *_ = self.split_record(Path(directory))
            audit = record.semantic_audit
            self.assertEqual(parse_integrity(record.integrity, semantic_audit=audit, disposition="ready"),
                             record.integrity)
            withheld = next(a["id"] for a in record.integrity["actions"]
                            if a["action"] == "withhold_repair" and a["actor"] == "code")

            def drop_withheld(raw):
                raw["actions"] = [a for a in raw["actions"] if a["id"] != withheld]

            def apply_rejected(raw):
                next(a for a in raw["actions"] if a["id"] == withheld).update(
                    action="apply_repair", outcome="applied", artifact="candidate", reasons=[])

            for name, mutate in (
                ("unknown decision", lambda raw: raw.update(decision="mostly_applied")),
                ("full decision", lambda raw: raw.update(decision="repaired_applied")),
                ("unverified acceptance", lambda raw: raw.update(acceptance_verified=False)),
                ("missing candidate", lambda raw: raw.update(artifacts=[
                    a for a in raw["artifacts"] if a["id"] != "candidate"])),
                ("unrecorded slot", drop_withheld),
                ("rejected slot applied", apply_rejected),
            ):
                with self.subTest(name=name):
                    raw = copy.deepcopy(record.integrity)
                    mutate(raw)
                    with self.assertRaises(ValueError):
                        parse_integrity(raw, semantic_audit=audit, disposition="ready")


if __name__ == "__main__":
    unittest.main()
