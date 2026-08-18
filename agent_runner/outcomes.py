"""Multi-axis run outcomes derived from deterministic findings."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

EVIDENCE_VIOLATION_CHECKS = {
    "altered_link",
    "excluded_topic_without_link",
    "freeform_url",
    "structured_citations",
    "topic_without_link",
    "ungrounded_link",
    "unknown_citation_ref",
}
EVIDENCE_REVIEW_CHECKS = {
    "claim_exceeds_evidence",
    "unsupported_figure",
    "unsupported_quotation",
}
ASSESSMENT_BLOCKED_CHECKS = {
    "structured_missing_field",
    "structured_schema_version",
    "structured_type",
    "structured_unknown_field",
}
COVERAGE_CHECKS = {
    "config_category_missing",
    "corpus_health_missing",
    "corpus_health_not_machine_readable",
    "duplicate_failed_source",
    "failed_source_status_mismatch",
    "failed_source_unnamed",
    "unexpected_failed_source",
}


@dataclass(frozen=True)
class Outcome:
    disposition: str
    protocol: str
    contract: str
    evidence: str
    coverage: str

    def record(self) -> dict[str, str]:
        return asdict(self)


def _value(finding: Any, key: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(key)
    return getattr(finding, key, None)


def finding_domain(check: str) -> str:
    if check in EVIDENCE_VIOLATION_CHECKS | EVIDENCE_REVIEW_CHECKS:
        return "evidence"
    if check in COVERAGE_CHECKS:
        return "coverage"
    if check.startswith("structured_"):
        return "schema"
    if check in {"slots_underfilled", "exclusion_log_missing", "exclusion_log_short"}:
        return "quality"
    return "editorial"


def classify_outcome(
    findings: Sequence[Any],
    source_issues: Sequence[Any],
    *,
    protocol_completed: bool = True,
) -> Outcome:
    """Classify usefulness without weakening the publication boundary."""
    coverage = "degraded" if source_issues else "full"
    if not protocol_completed:
        return Outcome(
            disposition="no_result",
            protocol="no_result",
            contract="not_evaluated",
            evidence="unassessed",
            coverage=coverage,
        )

    errors = [finding for finding in findings if _value(finding, "level") == "ERROR"]
    checks = {
        check
        for finding in findings
        if isinstance((check := _value(finding, "check")), str)
    }
    evidence_violated = bool(checks & EVIDENCE_VIOLATION_CHECKS)
    evidence_review = bool(checks & EVIDENCE_REVIEW_CHECKS)
    assessment_blocked = bool(checks & ASSESSMENT_BLOCKED_CHECKS)

    if evidence_violated:
        disposition = "rejected"
        contract = "rejected"
        evidence = "violated"
    elif errors:
        disposition = "review_required"
        contract = "review_required"
        evidence = "unassessed" if assessment_blocked else "corpus_bound"
    elif evidence_review:
        disposition = "review_required"
        contract = "accepted"
        evidence = "review_required"
    else:
        disposition = "ready"
        contract = "accepted"
        evidence = "corpus_bound"

    return Outcome(
        disposition=disposition,
        protocol="completed",
        contract=contract,
        evidence=evidence,
        coverage=coverage,
    )
