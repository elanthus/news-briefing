"""Offline public-boundary and cross-stage integrity contract tests."""

import copy
import unittest

from publication_schema import parse_integrity, parse_semantic_audit

POSITION = {"bucket": "sections", "section": "Tech", "index": 0}


def audit_fixture():
    return {
        "status": "complete", "post_status": "complete", "model": "judge/model", "threshold": 0.8,
        "citation_threshold": 0.8, "planned_checks": 1, "omitted_duplicate_pairs": 0,
        "skipped_oversized_checks": 0, "reported_cost_usd": 0.03, "unknown_cost_calls": 0,
        "topics": [{"position": dict(POSITION), "original": {"headline": "Original", "prose": "Summary"},
                    "changed": {"headline": "Revised", "prose": "Revised summary"},
                    "repair_status": "applied", "removed_evidence_count": 1}],
        "checks": [{"check": "irrelevant_citation", "positions": [dict(POSITION)], "probability": 0.9,
                    "confirmation_probability": 0.95, "confirmation_label": "confirmed",
                    "after_probability": None, "after_confirmation_probability": None,
                    "after_confirmation_label": None, "after_basis": "citation_removed",
                    "evidence_index": 1, "citation_urls": ["https://example.com/story"]}],
        "followup_checks": [],
    }


def coverage(planned=0, confirmed=0):
    return {"status": "complete", "planned": planned, "returned": planned, "confirmation_required": confirmed,
            "confirmation_returned": confirmed, "omitted_pairs": 0, "oversized": 0,
            "unavailable_results": 0, "stop_reason": None}


def phase(sequence, name):
    return {"sequence": sequence, "phase": name, "status": "complete", "started_at": None,
            "completed_at": None, "reasons": []}


def integrity_fixture():
    return {
        "version": 1, "decision": "repaired_applied", "reasons": [], "acceptance_verified": True,
        "generation": None, "repair_generation": None,
        "artifacts": [{"id": "original", "sha256": "a" * 64},
                      {"id": "candidate", "sha256": "b" * 64}, {"id": "published", "sha256": "b" * 64}],
        "phases": [phase(0, "code_removal"), phase(1, "candidate_validation"),
                   phase(2, "publication_verification")],
        "actions": [{"id": "action_0", "sequence": 0, "phase_sequence": 0, "actor": "code",
                     "artifact": "original", "positions": [dict(POSITION)], "source_indexes": [1],
                     "check_refs": [{"stage": "initial", "index": 0}], "action": "remove_source",
                     "outcome": "applied", "reasons": ["confirmed_irrelevance"]}],
        "initial_review": coverage(1, 1), "followup_review": coverage(),
        "costs": [{"phase": name, "reported_cost_usd": 0.01, "unknown_cost_calls": 0}
                  for name in ("initial_review", "repair_generation", "followup_review")],
        "workflow_run_id": None, "corpus_health": None,
    }


class IntegritySchemaTests(unittest.TestCase):
    def test_missing_historical_metadata_remains_unavailable(self):
        self.assertIsNone(parse_integrity(None))

    def test_verified_removal_accepts_observed_zero_followup_without_invented_score(self):
        raw, audit = integrity_fixture(), audit_fixture()
        self.assertEqual(parse_integrity(raw, semantic_audit=audit, disposition="ready"), raw)
        self.assertIsNone(audit["checks"][0]["after_probability"])

    def test_original_and_candidate_publication_hashes_are_distinct_decisions(self):
        raw, audit = integrity_fixture(), audit_fixture()
        raw.update(decision="candidate_retained", reasons=["candidate_mode"])
        raw["artifacts"][2]["sha256"] = "a" * 64
        raw["actions"][0]["outcome"] = "candidate"
        audit["topics"][0]["repair_status"] = "candidate"
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit))
        raw["artifacts"][2]["sha256"] = "b" * 64
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)

    def test_every_bound_and_unknown_field_fails_closed(self):
        changes = [
            ("version", True), ("version", 2), ("decision", "published"), ("reasons", ["remote error body"]),
            ("reasons", ["provider_failure"] * 2), ("workflow_run_id", True), ("workflow_run_id", 0),
            ("workflow_run_id", 10**18 + 1), ("actions", [integrity_fixture()["actions"][0]] * 513),
            ("phases", [phase(0, "initial_review")] * 129),
        ]
        for key, value in changes:
            raw = integrity_fixture()
            raw[key] = value
            with self.subTest(key=key, value=str(value)[:80]), self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture())
        raw = integrity_fixture()
        raw["private_path"] = "/private/run"
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit_fixture())

    def test_subject_and_check_references_cannot_escape_verified_artifact_scope(self):
        changes = [
            ("artifact", None), ("artifact", "initial_prose"),
            ("phase_sequence", 5000), ("positions", [{**POSITION, "index": 1}]),
            ("source_indexes", [True]), ("source_indexes", [1, 1]), ("source_indexes", [5000]),
            ("check_refs", [{"stage": "initial", "index": 1}]),
            ("check_refs", [{"stage": "initial", "index": 0}] * 2), ("id", "https://example.com"),
        ]
        for field, value in changes:
            raw = integrity_fixture()
            raw["actions"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture())

    def test_grouping_removal_must_reference_grouping_instead_of_citation_score(self):
        raw = integrity_fixture()
        raw["actions"][0]["reasons"] = ["grouping_subset"]
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit_fixture())
        audit = audit_fixture()
        row = audit["checks"][0]
        row.update(check="unsafe_grouping", after_basis="single_evidence")
        del row["evidence_index"], row["citation_urls"]
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit))

    def test_unconfirmed_citation_cannot_justify_source_removal(self):
        audit = audit_fixture()
        audit.update(status="partial")
        audit["checks"][0].update(confirmation_probability=None, confirmation_label="unconfirmed")
        raw = integrity_fixture()
        raw["initial_review"].update(status="partial", confirmation_returned=0)
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)

    def test_complete_and_no_targets_require_actual_complete_coverage(self):
        for field, value in (("returned", 0), ("confirmation_returned", 0), ("omitted_pairs", 1),
                             ("oversized", 1), ("planned", True), ("planned", 5001)):
            raw = integrity_fixture()
            raw["initial_review"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture())
        raw = integrity_fixture()
        raw["reasons"] = ["no_targets"]
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit_fixture())

    def test_acceptance_cannot_hide_incomplete_review_unknown_cost_or_unverified_phase(self):
        changes = [lambda r: r.update(initial_review=None), lambda r: r.update(followup_review=None),
                   lambda r: r["costs"][1].update(unknown_cost_calls=1),
                   lambda r: r["costs"][1].update(reported_cost_usd=None),
                   lambda r: r["phases"][1].update(status="failed"), lambda r: r.update(actions=[]),
                   lambda r: r.update(acceptance_verified=False)]
        for change in changes:
            raw = integrity_fixture()
            change(raw)
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture())

    def test_new_followup_blocker_rejects_candidate_even_when_old_trigger_cleared(self):
        raw, audit = integrity_fixture(), audit_fixture()
        row = copy.deepcopy(audit["checks"][0])
        row.update(check="unsupported_claim", after_basis=None)
        del row["evidence_index"], row["citation_urls"]
        audit["followup_checks"] = [row]
        raw["followup_review"] = coverage(1, 1)
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)
        raw.update(decision="original_retained", acceptance_verified=False, reasons=["followup_flag"])
        raw["artifacts"][2]["sha256"] = "a" * 64
        raw["actions"][0]["outcome"] = "rejected"
        audit["topics"][0]["repair_status"] = "rejected"
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit))

    def test_stage_costs_cannot_double_count_or_omit_reported_spend(self):
        raw = integrity_fixture()
        raw["costs"][2]["reported_cost_usd"] = 0.02
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit_fixture())
        raw = integrity_fixture()
        raw["costs"][2]["phase"] = "initial_review"
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit_fixture())

    def test_execution_order_and_timestamp_bounds(self):
        for change in (lambda r: r["phases"][1].update(sequence=0),
                       lambda r: r["phases"][0].update(started_at="2026-10-01T00:00:00"),
                       lambda r: r["phases"][0].update(started_at="2026-10-02T00:00:00Z",
                                                       completed_at="2026-10-01T00:00:00Z")):
            raw = integrity_fixture()
            change(raw)
            with self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture())

    def test_status_only_privacy_also_applies_to_historical_unknown(self):
        for decision in ("unpublished", "historical_unknown"):
            raw = integrity_fixture()
            raw.update(decision=decision, acceptance_verified=False)
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                parse_integrity(raw, semantic_audit=audit_fixture(), disposition="rejected")
        raw.update(generation=None, repair_generation=None, artifacts=[], phases=[], actions=[],
                   initial_review=None, followup_review=None, costs=[])
        self.assertIsNotNone(parse_integrity(raw, disposition="no_result"))

    def test_one_story_action_can_reference_its_full_bounded_question_set(self):
        raw, audit = integrity_fixture(), audit_fixture()
        audit["checks"] = []
        for index in range(129):
            row = copy.deepcopy(audit_fixture()["checks"][0])
            row.update(evidence_index=index, after_basis=None)
            audit["checks"].append(row)
        audit["planned_checks"] = 129
        raw["initial_review"] = coverage(129, 129)
        raw["actions"][0].update(source_indexes=[0], check_refs=[
            {"stage": "initial", "index": index} for index in range(128)
        ])
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit))
        raw["actions"][0]["check_refs"].append({"stage": "initial", "index": 128})
        with self.assertRaisesRegex(ValueError, "check references list"):
            parse_integrity(raw, semantic_audit=audit)

    def test_generation_bookkeeping_is_separate_from_semantic_repair_acceptance(self):
        raw, audit = integrity_fixture(), audit_fixture()
        raw.update(decision="original_retained", acceptance_verified=False, initial_review=None,
                   followup_review=None, costs=[])
        raw["artifacts"] = [raw["artifacts"][0], {"id": "published", "sha256": "a" * 64}]
        raw["phases"] = [phase(0, "initial_validation")]
        action = raw["actions"][0]
        action.update(action="prose_repair", artifact=None, positions=[], source_indexes=[], check_refs=[],
                      reasons=["deterministic_correction"])
        audit["topics"][0].update(changed=None, repair_status="unchanged", removed_evidence_count=0)
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit))

    def test_applied_topic_and_removed_source_counts_cannot_disagree_with_ledger(self):
        raw, audit = integrity_fixture(), audit_fixture()
        audit["topics"][0]["removed_evidence_count"] = 2
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)
        audit = audit_fixture()
        duplicate = copy.deepcopy(raw["actions"][0])
        duplicate.update(id="action_1", sequence=1)
        raw["actions"].append(duplicate)
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)

    def test_partial_review_retains_missing_counts_and_multiple_causes(self):
        raw, audit = integrity_fixture(), audit_fixture()
        raw.update(decision="original_retained", acceptance_verified=False,
                   reasons=["incomplete_review", "target_limit", "all_sources_removed"], followup_review=None)
        raw["artifacts"][2]["sha256"] = "a" * 64
        raw["actions"][0].update(outcome="skipped")
        audit.update(status="partial", post_status=None, planned_checks=3)
        del audit["followup_checks"]
        audit["checks"][0]["after_basis"] = None
        audit["topics"][0].update(changed=None, repair_status="skipped", removed_evidence_count=0)
        raw["initial_review"].update(status="partial", planned=3, unavailable_results=2,
                                     stop_reason="budget")
        self.assertEqual(parse_integrity(raw, semantic_audit=audit)["initial_review"]["unavailable_results"], 2)
        raw["initial_review"]["unavailable_results"] = 0
        with self.assertRaises(ValueError):
            parse_integrity(raw, semantic_audit=audit)

    def test_undated_source_degradation_is_not_hidden_by_ok_status(self):
        raw = integrity_fixture()
        source = {"source_type": "rss", "source_id": "Example", "category": "Tech", "status": "ok",
                  "reason": "undated_entries", "parsed_entries": 10, "dated_entries": 8, "retained_entries": 4}
        raw["corpus_health"] = [source]
        self.assertIsNotNone(parse_integrity(raw, semantic_audit=audit_fixture()))
        for field, value in (("reason", None), ("reason", "remote error text"), ("retained_entries", 9),
                             ("source_id", "bad\nlabel"), ("parsed_entries", 10**7 + 1)):
            mutated = copy.deepcopy(raw)
            mutated["corpus_health"][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                parse_integrity(mutated, semantic_audit=audit_fixture())


class SemanticFollowupSchemaTests(unittest.TestCase):
    def test_known_legacy_exact_shapes_remain_accepted(self):
        audit = audit_fixture()
        del audit["followup_checks"]
        self.assertIsNotNone(parse_semantic_audit(audit))
        del audit["citation_threshold"]
        self.assertIsNotNone(parse_semantic_audit(audit))
        audit["unknown_extension"] = []
        with self.assertRaises(ValueError):
            parse_semantic_audit(audit)

    def test_followup_labels_and_recursive_after_scores_remain_fail_closed(self):
        for change in (lambda row: row.update(confirmation_label="disputed"),
                       lambda row: row.update(after_probability=0.2, after_confirmation_label="not_flagged",
                                               after_basis="model")):
            audit = audit_fixture()
            row = copy.deepcopy(audit["checks"][0])
            row["after_basis"] = None
            change(row)
            audit["followup_checks"] = [row]
            with self.assertRaises(ValueError):
                parse_semantic_audit(audit)

    def test_overlapping_legacy_after_score_must_match_actual_followup(self):
        audit = audit_fixture()
        row = audit["checks"][0]
        row.update(after_basis="model", after_probability=0.1, after_confirmation_label="not_flagged")
        followup = copy.deepcopy(row)
        followup.update(probability=0.2, confirmation_probability=None, confirmation_label="not_flagged",
                        after_probability=None, after_confirmation_probability=None,
                        after_confirmation_label=None, after_basis=None)
        audit["followup_checks"] = [followup]
        with self.assertRaises(ValueError):
            parse_semantic_audit(audit)
        followup["probability"] = 0.1
        self.assertIsNotNone(parse_semantic_audit(audit))


if __name__ == "__main__":
    unittest.main()
