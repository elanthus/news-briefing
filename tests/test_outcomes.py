import unittest

from agent_runner.outcomes import classify_outcome, finding_domain, is_actionable_finding
from agent_runner.output import OutputFinding


class OutcomeTests(unittest.TestCase):
    def test_source_failures_degrade_coverage_without_rejecting_candidate(self):
        outcome = classify_outcome([], [{"status": "error"}])
        self.assertEqual(outcome.disposition, "ready")
        self.assertEqual(outcome.coverage, "degraded")
        self.assertEqual(outcome.evidence, "corpus_bound")

    def test_evidence_warning_requires_review_without_becoming_violation(self):
        outcome = classify_outcome([
            OutputFinding("WARN", "unsupported_quotation", "quotation not found")
        ], [])
        self.assertEqual(outcome.disposition, "review_required")
        self.assertEqual(outcome.contract, "accepted")
        self.assertEqual(outcome.evidence, "review_required")

    def test_editorial_error_does_not_hide_evidence_review(self):
        outcome = classify_outcome([
            OutputFinding("ERROR", "category_ineligible_ref", "wrong section"),
            OutputFinding("WARN", "unsupported_quotation", "quotation not found"),
        ], [])
        self.assertEqual(outcome.disposition, "review_required")
        self.assertEqual(outcome.contract, "review_required")
        self.assertEqual(outcome.evidence, "review_required")

    def test_figure_supported_elsewhere_is_a_nonblocking_quality_note(self):
        finding = OutputFinding(
            "WARN", "figure_supported_elsewhere", "figure in matching corpus item")
        outcome = classify_outcome([finding], [])
        self.assertEqual(finding_domain(finding.check), "quality")
        self.assertEqual(outcome.disposition, "ready")
        self.assertEqual(outcome.contract, "accepted")
        self.assertEqual(outcome.evidence, "corpus_bound")

    def test_figure_absent_from_excerpt_is_a_nonblocking_quality_note(self):
        finding = OutputFinding("WARN", "unsupported_figure", "figure not found")
        outcome = classify_outcome([finding], [])
        self.assertEqual(finding_domain(finding.check), "quality")
        self.assertEqual(outcome.disposition, "ready")
        self.assertEqual(outcome.contract, "accepted")
        self.assertEqual(outcome.evidence, "corpus_bound")

    def test_editorial_error_is_reviewable_but_unknown_citation_is_rejected(self):
        reviewable = classify_outcome([
            OutputFinding("ERROR", "category_ineligible_ref", "wrong section")
        ], [])
        rejected = classify_outcome([
            OutputFinding("ERROR", "unknown_citation_ref", "not in corpus")
        ], [])
        self.assertEqual(reviewable.disposition, "review_required")
        self.assertEqual(reviewable.evidence, "corpus_bound")
        self.assertEqual(rejected.disposition, "rejected")
        self.assertEqual(rejected.evidence, "violated")

    def test_schema_failure_keeps_evidence_unassessed(self):
        outcome = classify_outcome([
            OutputFinding("ERROR", "structured_missing_field", "reason missing")
        ], [])
        self.assertEqual(outcome.disposition, "review_required")
        self.assertEqual(outcome.evidence, "unassessed")

    def test_no_result_is_separate_from_candidate_quality(self):
        outcome = classify_outcome([], [], protocol_completed=False)
        self.assertEqual(outcome.disposition, "no_result")
        self.assertEqual(outcome.contract, "not_evaluated")
        self.assertEqual(outcome.evidence, "unassessed")

    def test_finding_domains_are_explicit(self):
        self.assertEqual(finding_domain("unknown_citation_ref"), "evidence")
        self.assertEqual(finding_domain("structured_missing_field"), "schema")
        self.assertEqual(finding_domain("slots_underfilled"), "quality")
        self.assertEqual(finding_domain("unsupported_figure"), "quality")
        self.assertEqual(finding_domain("failed_source_unnamed"), "coverage")
        self.assertEqual(finding_domain("category_ineligible_ref"), "editorial")

    def test_quality_findings_are_not_actionable(self):
        self.assertFalse(is_actionable_finding({"domain": "quality"}))
        self.assertTrue(is_actionable_finding({"domain": "evidence"}))
        self.assertTrue(is_actionable_finding({}))


if __name__ == "__main__":
    unittest.main()
