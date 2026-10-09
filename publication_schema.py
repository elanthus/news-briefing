"""Shared standard-library schema for publication review metadata."""

from __future__ import annotations

import math
import re
import urllib.parse
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

FINDING_FIELDS = {"level", "check", "domain", "message"}
FINDING_V3_FIELDS = FINDING_FIELDS | {"context"}
CONTEXT_FIELDS = {"section", "headline", "model_authored"}
CONTEXT_V4_FIELDS = CONTEXT_FIELDS | {"path"}
REPAIR_ACTION_FIELDS = {"action", "path", "reason"}
PROVENANCE_INT_FIELDS = (
    "attempt_index",
    "attempt_count",
    "selection_corrections",
    "prose_corrections",
    "repair_action_count",
)
PROVENANCE_FIELDS = {"provider", "model", "prompt_sha256", *PROVENANCE_INT_FIELDS}
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class ReviewContext:
    section: str
    headline: str
    model_authored: str
    path: str | None = None


@dataclass(frozen=True)
class ReviewFinding:
    level: str
    check: str
    domain: str
    message: str
    context: ReviewContext | None = None


def parse_review_context(raw: object) -> tuple[bool, ReviewContext | None]:
    if raw is None:
        return True, None
    if (
        not isinstance(raw, dict)
        or set(raw) not in (CONTEXT_FIELDS, CONTEXT_V4_FIELDS)
        or any(
            not isinstance(raw[field], str) or not raw[field].strip()
            for field in CONTEXT_FIELDS
        )
    ):
        return False, None
    raw_path = raw.get("path")
    if raw_path is not None and (not isinstance(raw_path, str) or not raw_path.strip()):
        return False, None
    path = raw_path
    return True, ReviewContext(
        section=raw["section"],
        headline=raw["headline"],
        model_authored=raw["model_authored"],
        path=path,
    )


def parse_finding(raw: object, require_context: bool) -> ReviewFinding | None:
    """Parse one review finding, or ``None`` when it is malformed.

    A runner manifest row carries exactly ``FINDING_FIELDS``. A publication
    sidecar row also carries ``context``, which is null or a review context.
    """
    fields = FINDING_V3_FIELDS if require_context else FINDING_FIELDS
    if (
        not isinstance(raw, dict)
        or set(raw) != fields
        or any(not isinstance(raw[field], str) or not raw[field].strip() for field in FINDING_FIELDS)
        or raw["level"] not in {"ERROR", "WARN"}
    ):
        return None
    valid_context, context = parse_review_context(raw.get("context"))
    if not valid_context:
        return None
    return ReviewFinding(raw["level"], raw["check"], raw["domain"], raw["message"], context)


def parse_repair_actions(raw: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    actions: list[dict[str, str]] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != REPAIR_ACTION_FIELDS
            or any(not isinstance(item[key], str) for key in REPAIR_ACTION_FIELDS)
        ):
            return ()
        actions.append(item)
    return tuple(actions)


@dataclass(frozen=True)
class Provenance:
    """Model identifiers and correction/repair counts for a published run.

    Fields only ever hold model identifiers and non-negative counts, plus the
    runner prompt's content hash already recorded in the checkpoint manifest.
    Never prompt text, corpus text, or a URL.
    """

    provider: str
    model: str
    attempt_index: int
    attempt_count: int
    selection_corrections: int
    prose_corrections: int
    repair_action_count: int
    prompt_sha256: str


def provenance_payload(provenance: Provenance) -> dict[str, object]:
    return asdict(provenance)


def parse_provenance(raw: object) -> Provenance | None:
    """Parse a provenance object; ``None`` for a missing value.

    ``build_site.py`` passes ``payload.get("provenance")``, so an absent
    field and an explicit ``null`` both return ``None``. Archives written before
    provenance was recorded omit the field, and ``prepare_publication.py``
    writes ``null`` for a disposition without generation provenance.

    A present-but-malformed value raises, matching the rest of this module's
    treatment of a field once it is declared: fail closed rather than publish
    a partially trusted record.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) != PROVENANCE_FIELDS:
        raise ValueError(f"provenance must contain exactly {sorted(PROVENANCE_FIELDS)}")
    provider = raw["provider"]
    model = raw["model"]
    prompt_sha256 = raw["prompt_sha256"]
    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("provenance provider must be a non-empty string")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("provenance model must be a non-empty string")
    if not isinstance(prompt_sha256, str) or not _SHA256_HEX.fullmatch(prompt_sha256):
        raise ValueError("provenance prompt_sha256 must be a lowercase sha256 hex digest")
    values: dict[str, int] = {}
    for field in PROVENANCE_INT_FIELDS:
        value = raw[field]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError(f"provenance {field} must be a non-negative integer")
        values[field] = value
    if not 1 <= values["attempt_index"] <= values["attempt_count"]:
        raise ValueError("provenance attempt_index must be between 1 and attempt_count")
    return Provenance(provider=provider, model=model, prompt_sha256=prompt_sha256, **values)


def finding_payload(finding: ReviewFinding) -> dict[str, object]:
    return {
        "level": finding.level,
        "check": finding.check,
        "domain": finding.domain,
        "message": finding.message,
        "context": asdict(finding.context) if finding.context is not None else None,
    }


SEMANTIC_CHECKS = {"duplicate", "unsafe_grouping", "unsupported_claim", "strengthened_claim", "reversed_claim",
                   "irrelevant_citation"}
SEMANTIC_AUDIT_FIELDS = {
    "status", "post_status", "model", "threshold", "planned_checks", "omitted_duplicate_pairs",
    "skipped_oversized_checks",
    "reported_cost_usd", "unknown_cost_calls", "topics", "checks",
}


def parse_semantic_audit(raw: object) -> dict[str, Any] | None:
    """Accept only bounded public prose and scores; never evidence or prompts."""
    import math

    if raw is None:
        return None
    if (not isinstance(raw, dict)
            or set(raw) not in tuple(SEMANTIC_AUDIT_FIELDS | extra for extra in (
                set(), {"citation_threshold"}, {"followup_checks"}, {"citation_threshold", "followup_checks"}))):
        raise ValueError("invalid semantic audit fields")

    def probability(value: object, *, optional: bool = False) -> None:
        if optional and value is None:
            return
        if (not isinstance(value, (int, float)) or isinstance(value, bool)
                or not 0 <= value <= 1 or not math.isfinite(value)):
            raise ValueError("invalid semantic audit probability")

    def position(value: object) -> str:
        if (not isinstance(value, dict) or set(value) != {"bucket", "section", "index"}
                or value["bucket"] not in ("sections", "excluded_topics")
                or not isinstance(value["section"], str) or not 1 <= len(value["section"]) <= 100
                or type(value["index"]) is not int or not 0 <= value["index"] < 64):
            raise ValueError("invalid semantic audit position")
        return repr((value["bucket"], value["section"], value["index"]))

    def prose(value: object) -> None:
        if (not isinstance(value, dict) or set(value) != {"headline", "prose"}
                or any(not isinstance(value[k], str) or not 1 <= len(value[k]) <= 2000 for k in value)):
            raise ValueError("invalid semantic audit prose")

    if raw["post_status"] not in (None, "complete", "partial", "failed"):
        raise ValueError("invalid semantic post-review status")
    if raw["status"] not in ("complete", "partial", "failed"):
        raise ValueError("invalid semantic audit status")
    if not isinstance(raw["model"], str) or not 1 <= len(raw["model"]) <= 100:
        raise ValueError("invalid semantic audit model")
    probability(raw["threshold"])
    probability(raw.get("citation_threshold", raw["threshold"]))
    if raw.get("citation_threshold", raw["threshold"]) == 0:
        raise ValueError("invalid citation threshold")
    if raw["threshold"] == 0:
        raise ValueError("invalid semantic audit threshold")
    for field in ("planned_checks", "omitted_duplicate_pairs", "skipped_oversized_checks", "unknown_cost_calls"):
        if type(raw[field]) is not int or not 0 <= raw[field] <= 5000:
            raise ValueError("invalid semantic audit count")
    cost = raw["reported_cost_usd"]
    if type(cost) not in (int, float) or not 0 <= cost <= 100 or not math.isfinite(cost):
        raise ValueError("invalid semantic audit cost")
    topics, checks = raw["topics"], raw["checks"]
    if not isinstance(topics, list) or len(topics) > 64 or not isinstance(checks, list) or len(checks) > 5000:
        raise ValueError("semantic audit exceeds scope bound")
    positions: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict) or set(topic) != {
            "position", "original", "changed", "repair_status", "removed_evidence_count",
        }:
            raise ValueError("invalid semantic audit topic fields")
        key = position(topic["position"])
        if key in positions:
            raise ValueError("duplicate semantic audit topic")
        positions.add(key)
        prose(topic["original"])
        if topic["changed"] is not None:
            prose(topic["changed"])
        if topic["repair_status"] not in (
            "unchanged", "candidate", "applied", "rejected", "failed", "skipped",
        ):
            raise ValueError("invalid semantic repair status")
        if (topic["repair_status"] in ("candidate", "applied", "rejected")) != (topic["changed"] is not None):
            raise ValueError("semantic repair prose does not match its status")
        if type(topic["removed_evidence_count"]) is not int or not 0 <= topic["removed_evidence_count"] <= 64:
            raise ValueError("invalid semantic removed-evidence count")
    seen: set[str] = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError("invalid semantic audit check")
        extra = {"evidence_index", "citation_urls"} if check.get("check") == "irrelevant_citation" else set()
        if set(check) != {
            "check", "positions", "probability", "confirmation_probability", "confirmation_label",
            "after_probability", "after_confirmation_probability", "after_confirmation_label", "after_basis",
        } | extra or not isinstance(check["check"], str) or check["check"] not in SEMANTIC_CHECKS:
            raise ValueError("invalid semantic audit check")
        refs = check["positions"]
        count = 2 if check["check"] == "duplicate" else 1
        if not isinstance(refs, list) or len(refs) != count:
            raise ValueError("invalid semantic check position count")
        keys = [position(ref) for ref in refs]
        if extra:
            if type(check["evidence_index"]) is not int or not 0 <= check["evidence_index"] < 5000:
                raise ValueError("invalid citation evidence index")
            urls = check["citation_urls"]
            if not isinstance(urls, list) or not 1 <= len(urls) <= 2:
                raise ValueError("invalid semantic citation destinations")
            for url in urls:
                if not isinstance(url, str) or not 1 <= len(url) <= 4096 or any(ord(c) < 32 for c in url):
                    raise ValueError("invalid semantic citation URL")
                parsed = urllib.parse.urlsplit(url)
                if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
                    raise ValueError("invalid semantic citation URL")
        key = repr((check["check"], sorted(keys), check.get("evidence_index")))
        if any(k not in positions for k in keys) or len(set(keys)) != count or key in seen:
            raise ValueError("invalid or duplicate semantic check reference")
        seen.add(key)
        if check["after_basis"] not in (None, "model", "single_evidence", "citation_removed"):
            raise ValueError("invalid semantic post-check basis")
        if (check["after_basis"] == "model") != (check["after_probability"] is not None):
            raise ValueError("semantic post-check basis does not match score")
        if check["after_basis"] == "single_evidence" and check["check"] != "unsafe_grouping":
            raise ValueError("single evidence proves only grouping scope")
        if check["after_basis"] == "citation_removed" and check["check"] != "irrelevant_citation":
            raise ValueError("citation removal proves only citation scope")
        cutoff = raw.get("citation_threshold", raw["threshold"]) if extra else raw["threshold"]
        probability(check["probability"])
        probability(check["confirmation_probability"], optional=True)
        probability(check["after_probability"], optional=True)
        probability(check["after_confirmation_probability"], optional=True)
        for prefix in ("", "after_"):
            p = check[prefix + "probability"]
            confirmation = check[prefix + "confirmation_probability"]
            label = check[prefix + "confirmation_label"]
            expected = (None if p is None else "not_flagged" if p < cutoff else
                        "unconfirmed" if confirmation is None else
                        "confirmed" if confirmation >= cutoff else "disputed")
            if label != expected or (confirmation is not None and (p is None or p < cutoff)):
                raise ValueError("semantic label does not match both scores")
    if len(checks) > raw["planned_checks"]:
        raise ValueError("semantic coverage exceeds planned checks")
    if raw["status"] == "complete" and (
        len(checks) != raw["planned_checks"] or raw["omitted_duplicate_pairs"]
        or raw["skipped_oversized_checks"] or any(c["confirmation_label"] == "unconfirmed" for c in checks)
    ):
        raise ValueError("complete semantic audit has incomplete coverage")
    if "followup_checks" in raw:
        followup = raw["followup_checks"]
        if raw["post_status"] is None or not isinstance(followup, list) or len(followup) > 5000:
            raise ValueError("invalid semantic follow-up checks")
        followup_audit = {k: v for k, v in raw.items() if k != "followup_checks"}
        followup_audit.update(checks=followup, status="partial", post_status=None, planned_checks=len(followup))
        parse_semantic_audit(followup_audit)
        for row in followup:
            if any(row[key] is not None for key in (
                "after_probability", "after_confirmation_probability", "after_confirmation_label", "after_basis",
            )):
                raise ValueError("follow-up check must not contain another follow-up")
        scores = {_integrity_check_key(row): row for row in followup}
        for row in checks:
            if row["after_basis"] == "model":
                after = scores.get(_integrity_check_key(row))
                if after is None or any(row["after_" + key] != after[key] for key in (
                    "probability", "confirmation_probability", "confirmation_label",
                )):
                    raise ValueError("semantic follow-up scores disagree")
    return raw


INTEGRITY_REASON_MESSAGES = {
    "incomplete_review": "Initial review or confirmation was incomplete.",
    "unknown_billing": "Billing was not reported for every call.",
    "target_limit": "The repair target limit was exceeded.",
    "all_sources_removed": "The proposed removal would remove every source item.",
    "invalid_candidate": "The repair candidate failed validation.",
    "provider_failure": "The provider call failed.",
    "incomplete_followup": "Follow-up review or confirmation was incomplete.",
    "followup_flag": "Follow-up review flagged an affected story.",
    "candidate_mode": "The cleared candidate was retained without applying it.",
    "no_targets": "Complete assessment found no eligible repair targets.",
    "confirmed_irrelevance": "Isolated confirmation found citation irrelevance.",
    "grouping_subset": "Grouping repair selected a smaller source subset.",
    "audit_unavailable": "Verified audit information was unavailable.",
    "verification_failed": "Audit artifact verification failed.",
    "baseline_unavailable": "The first complete prose baseline was unavailable.",
    "superseded_correction": "A later correction superseded this action.",
    "deterministic_correction": "Deterministic validation required correction.",
    "preserved_selection": "Code preserved the selected source items.",
    "promoted": "Generation bookkeeping promoted a selected story.",
    "review_required": "Deterministic findings require review before publication.",
    "rejected": "The publication candidate was rejected.",
    "no_result": "Generation did not produce a publishable result.",
}
INTEGRITY_FIELDS = {
    "version", "decision", "reasons", "acceptance_verified", "generation", "repair_generation",
    "artifacts", "phases", "actions", "initial_review", "followup_review", "costs", "workflow_run_id", "corpus_health",
}
INTEGRITY_PHASES = {
    "generation_preparation", "initial_validation", "initial_review", "confirmation", "code_removal",
    "selection_repair", "prose_repair", "candidate_validation", "followup_review", "publication_verification",
}
INTEGRITY_ACTIONS = {
    "selection_correction", "prose_correction", "remove_source", "selection_repair", "prose_repair",
    "promotion", "publication", "withhold_repair",
}
INTEGRITY_DECISIONS = {
    "original_retained", "repaired_applied", "candidate_retained", "unpublished", "historical_unknown",
}


def _integrity_object(value: object, fields: set[str], name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError(f"invalid integrity {name} fields")
    return value


def _integrity_list(value: object, limit: int, name: str) -> list[Any]:
    if not isinstance(value, list) or len(value) > limit:
        raise ValueError(f"invalid integrity {name} list")
    return value


def _integrity_int(value: object, limit: int = 5000) -> int:
    if type(value) is not int or not 0 <= value <= limit:
        raise ValueError("invalid integrity count")
    return value


def _integrity_enum(value: object, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise ValueError("invalid integrity enum")
    return value


def _integrity_string(value: object, limit: int) -> str:
    if (not isinstance(value, str) or not value.strip() or len(value) > limit
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError("invalid integrity string")
    return value


def _integrity_reasons(value: object) -> list[str]:
    reasons = _integrity_list(value, 24, "reasons")
    for reason in reasons:
        _integrity_enum(reason, set(INTEGRITY_REASON_MESSAGES))
    if len(set(reasons)) != len(reasons):
        raise ValueError("duplicate integrity reason")
    return reasons


def _integrity_position(value: object) -> tuple[str, str, int]:
    row = _integrity_object(value, {"bucket", "section", "index"}, "position")
    bucket = _integrity_enum(row["bucket"], {"sections", "excluded_topics"})
    section = _integrity_string(row["section"], 100)
    index = _integrity_int(row["index"], 63)
    return bucket, section, index


def _integrity_check_key(row: dict[str, Any]) -> tuple[str, tuple[tuple[str, str, int], ...], int | None]:
    return row["check"], tuple(sorted(_integrity_position(p) for p in row["positions"])), row.get("evidence_index")


def _integrity_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    text = _integrity_string(value, 32)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("invalid integrity timestamp") from exc
    if "T" not in text or parsed.utcoffset() != timedelta(0):
        raise ValueError("integrity timestamp must be UTC")
    return parsed


def _integrity_coverage(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    fields = {"status", "planned", "returned", "confirmation_required", "confirmation_returned",
              "omitted_pairs", "oversized", "unavailable_results", "stop_reason"}
    row = _integrity_object(value, fields, "coverage")
    _integrity_enum(row["status"], {"complete", "partial", "failed"})
    for field in fields - {"status", "stop_reason"}:
        _integrity_int(row[field])
    if row["stop_reason"] is not None:
        _integrity_enum(row["stop_reason"], {
            "deadline", "budget", "unknown_billing", "provider_failure", "invalid_result", "unavailable",
        })
    if (row["returned"] > row["planned"]
            or row["unavailable_results"] != row["planned"] - row["returned"] - row["oversized"]
            or not row["confirmation_returned"] <= row["confirmation_required"] <= row["returned"]):
        raise ValueError("inconsistent integrity coverage")
    if row["status"] == "complete" and (
        row["returned"] != row["planned"] or row["confirmation_returned"] != row["confirmation_required"]
        or row["omitted_pairs"] or row["oversized"] or row["unavailable_results"]
    ):
        raise ValueError("complete integrity review has missing coverage")
    return row


def _integrity_cost(value: object) -> None:
    if value is not None and (
        not isinstance(value, (int, float)) or isinstance(value, bool)
        or not math.isfinite(value) or not 0 <= value <= 100
    ):
        raise ValueError("invalid integrity cost")


def _integrity_review_matches(
    coverage: dict[str, Any] | None, rows: list[Any], audit: dict[str, Any], *, followup: bool,
) -> None:
    if coverage is None:
        return
    if coverage["status"] != audit["post_status" if followup else "status"]:
        raise ValueError("integrity review status disagrees with audit")
    required = sum(row["confirmation_label"] != "not_flagged" for row in rows)
    returned = sum(row["confirmation_probability"] is not None for row in rows)
    if (coverage["returned"] != len(rows) or coverage["confirmation_required"] != required
            or coverage["confirmation_returned"] != returned):
        raise ValueError("integrity review coverage disagrees with scores")
    if not followup and any(coverage[k] != audit[a] for k, a in (
        ("planned", "planned_checks"), ("omitted_pairs", "omitted_duplicate_pairs"),
        ("oversized", "skipped_oversized_checks"),
    )):
        raise ValueError("integrity initial coverage disagrees with audit")


def parse_integrity(
    raw: object, *, semantic_audit: object = None, disposition: str | None = None,
) -> dict[str, Any] | None:
    """Validate bounded public operational metadata; artifact binding belongs to the pipeline.

    Missing history is explicitly unavailable. This parser never authorizes
    publication or proves that a claimed hash, reason, or score was recorded.
    The producer must independently reconstruct metadata from verified artifacts.
    """
    if raw is None:
        return None
    record = _integrity_object(raw, INTEGRITY_FIELDS, "record")
    if type(record["version"]) is not int or record["version"] != 1:
        raise ValueError("invalid integrity version")
    decision = _integrity_enum(record["decision"], INTEGRITY_DECISIONS)
    reasons = _integrity_reasons(record["reasons"])
    accepted = record["acceptance_verified"]
    if type(accepted) is not bool or accepted != (decision in {"repaired_applied", "candidate_retained"}):
        raise ValueError("integrity acceptance disagrees with decision")
    if disposition is not None:
        _integrity_enum(disposition, {"ready", "review_required", "rejected", "no_result", "blocked"})
        if decision != "historical_unknown" and (
            (decision == "unpublished") != (disposition in {"rejected", "no_result", "blocked"})
        ):
            raise ValueError("integrity decision disagrees with disposition")
    for field in ("generation", "repair_generation"):
        identity = parse_provenance(record[field])
        if identity is not None:
            _integrity_string(identity.provider, 100)
            _integrity_string(identity.model, 200)
            for name in PROVENANCE_INT_FIELDS:
                _integrity_int(getattr(identity, name), 64 if name in {"attempt_index", "attempt_count"} else 5000)
    artifacts: dict[str, str] = {}
    for value in _integrity_list(record["artifacts"], 4, "artifacts"):
        artifact = _integrity_object(value, {"id", "sha256"}, "artifact")
        key = _integrity_enum(artifact["id"], {"initial_prose", "original", "candidate", "published"})
        digest = artifact["sha256"]
        if key in artifacts or not isinstance(digest, str) or not _SHA256_HEX.fullmatch(digest):
            raise ValueError("invalid or duplicate integrity artifact")
        artifacts[key] = digest
    if decision in {"original_retained", "candidate_retained", "repaired_applied"}:
        selected = "candidate" if decision == "repaired_applied" else "original"
        if selected not in artifacts or artifacts.get("published") != artifacts[selected]:
            raise ValueError("integrity published artifact disagrees with decision")
    if accepted and "candidate" not in artifacts:
        raise ValueError("accepted integrity candidate has no artifact")
    phases: dict[int, dict[str, Any]] = {}
    previous = -1
    for value in _integrity_list(record["phases"], 128, "phases"):
        phase = _integrity_object(value, {
            "sequence", "phase", "status", "started_at", "completed_at", "reasons",
        }, "phase")
        sequence = _integrity_int(phase["sequence"])
        if sequence <= previous:
            raise ValueError("integrity phases are not in execution order")
        previous = sequence
        _integrity_enum(phase["phase"], INTEGRITY_PHASES)
        _integrity_enum(phase["status"], {"complete", "partial", "failed", "skipped", "unavailable"})
        _integrity_reasons(phase["reasons"])
        start, end = _integrity_timestamp(phase["started_at"]), _integrity_timestamp(phase["completed_at"])
        if start is not None and end is not None and end < start:
            raise ValueError("integrity phase ends before it starts")
        phases[sequence] = phase
    initial = _integrity_coverage(record["initial_review"])
    followup = _integrity_coverage(record["followup_review"])
    audit = parse_semantic_audit(semantic_audit)
    if audit is None and (initial is not None or followup is not None):
        raise ValueError("integrity coverage has no canonical semantic audit")
    initial_rows = audit["checks"] if audit is not None else []
    followup_rows = audit.get("followup_checks", []) if audit is not None else []
    if audit is not None:
        _integrity_review_matches(initial, initial_rows, audit, followup=False)
        _integrity_review_matches(followup, followup_rows, audit, followup=True)
        if followup is not None and "followup_checks" not in audit:
            raise ValueError("integrity follow-up coverage has no canonical checks")
    positions = {_integrity_position(t["position"]) for t in audit["topics"]} if audit is not None else set()
    ids: set[str] = set()
    previous = -1
    actions = _integrity_list(record["actions"], 512, "actions")
    affected: set[tuple[str, str, int]] = set()
    removals: dict[tuple[str, str, int], set[int]] = {}
    for value in actions:
        action = _integrity_object(value, {
            "id", "sequence", "phase_sequence", "actor", "artifact", "positions", "source_indexes",
            "check_refs", "action", "outcome", "reasons",
        }, "action")
        action_id = action["id"]
        if not isinstance(action_id, str) or not re.fullmatch(r"action_[0-9]{1,4}", action_id) or action_id in ids:
            raise ValueError("invalid integrity action identifier")
        ids.add(action_id)
        sequence = _integrity_int(action["sequence"])
        if sequence <= previous:
            raise ValueError("integrity actions are not in execution order")
        previous = sequence
        if _integrity_int(action["phase_sequence"]) not in phases:
            raise ValueError("integrity action references missing phase")
        _integrity_enum(action["actor"], {"code", "model", "judge"})
        _integrity_enum(action["action"], INTEGRITY_ACTIONS)
        outcome = _integrity_enum(action["outcome"], {
            "applied", "candidate", "rejected", "failed", "skipped", "superseded", "recorded",
        })
        action_reasons = _integrity_reasons(action["reasons"])
        scope = action["artifact"]
        if scope is not None and _integrity_enum(scope, set(artifacts)) not in artifacts:
            raise ValueError("integrity action references missing artifact")
        subjects = [_integrity_position(p) for p in _integrity_list(action["positions"], 2, "positions")]
        if len(set(subjects)) != len(subjects) or any(p not in positions for p in subjects):
            raise ValueError("integrity action references missing or duplicate topic")
        source_indexes = [_integrity_int(i, 4999) for i in _integrity_list(action["source_indexes"], 64, "sources")]
        if len(set(source_indexes)) != len(source_indexes) or (source_indexes and len(subjects) != 1):
            raise ValueError("invalid integrity action source reference")
        references = _integrity_list(action["check_refs"], 128, "check references")
        if (subjects or source_indexes or references) and scope not in {"original", "candidate"}:
            raise ValueError("integrity subjects require original or candidate artifact alignment")
        ref_ids: set[tuple[str, int]] = set()
        for value in references:
            ref = _integrity_object(value, {"stage", "index"}, "check reference")
            stage = _integrity_enum(ref["stage"], {"initial", "followup"})
            index = _integrity_int(ref["index"], 4999)
            rows = initial_rows if stage == "initial" else followup_rows
            if index >= len(rows) or (stage, index) in ref_ids:
                raise ValueError("integrity check reference is missing or duplicated")
            ref_ids.add((stage, index))
            if not set(subjects).intersection(_integrity_position(p) for p in rows[index]["positions"]):
                raise ValueError("integrity check reference disagrees with action subject")
        if action["action"] == "remove_source":
            if (not subjects or not source_indexes or scope != "original"
                    or not set(action_reasons) & {"confirmed_irrelevance", "grouping_subset"}):
                raise ValueError("integrity removal requires original sources and a cause")
            _integrity_removal_matches(action, initial_rows)
            if outcome in {"applied", "candidate"}:
                previous_sources = removals.setdefault(subjects[0], set())
                if previous_sources.intersection(source_indexes):
                    raise ValueError("integrity repeats the same source removal")
                previous_sources.update(source_indexes)
        if action["action"] == "promotion" and outcome != "recorded":
            raise ValueError("integrity promotion is bookkeeping")
        semantic_phase = phases[action["phase_sequence"]]["phase"] in {
            "code_removal", "selection_repair", "prose_repair",
        }
        if (semantic_phase and outcome == "applied"
                and action["action"] in {"selection_repair", "prose_repair"} and scope != "candidate"):
            raise ValueError("applied semantic repair requires candidate artifact")
        if semantic_phase and decision != "repaired_applied" and outcome == "applied" and action["action"] in {
            "remove_source", "selection_repair", "prose_repair",
        }:
            raise ValueError("retained original cannot contain applied semantic repairs")
        if semantic_phase and action["action"] in {"remove_source", "selection_repair", "prose_repair"} and outcome in {
            "applied", "candidate",
        }:
            affected.update(subjects)
    costs: dict[str, dict[str, Any]] = {}
    for value in _integrity_list(record["costs"], 3, "costs"):
        cost = _integrity_object(value, {"phase", "reported_cost_usd", "unknown_cost_calls"}, "cost")
        key = _integrity_enum(cost["phase"], {"initial_review", "repair_generation", "followup_review"})
        if key in costs:
            raise ValueError("duplicate integrity cost phase")
        costs[key] = cost
        _integrity_cost(cost["reported_cost_usd"])
        if cost["unknown_cost_calls"] is not None:
            _integrity_int(cost["unknown_cost_calls"])
    if audit is not None and costs and all(
        c["reported_cost_usd"] is not None and c["unknown_cost_calls"] is not None for c in costs.values()
    ):
        if (not math.isclose(sum(c["reported_cost_usd"] for c in costs.values()),
                             audit["reported_cost_usd"], rel_tol=0, abs_tol=1e-8)
                or sum(c["unknown_cost_calls"] for c in costs.values()) != audit["unknown_cost_calls"]):
            raise ValueError("integrity costs disagree with semantic aggregate")
    eligible = {_integrity_position(p) for row in initial_rows
                if row["confirmation_label"] == "confirmed" and row["check"] != "duplicate"
                for p in row["positions"] if p["bucket"] == "sections"}
    if "no_targets" in reasons and (initial is None or initial["status"] != "complete" or eligible):
        raise ValueError("no-targets conclusion requires complete applicable assessment")
    if audit is not None:
        applied_topics = {_integrity_position(t["position"]) for t in audit["topics"]
                          if t["repair_status"] == "applied"}
        if applied_topics and decision != "repaired_applied":
            raise ValueError("integrity retained decision disagrees with applied semantic topics")
        if accepted:
            expected_status = "applied" if decision == "repaired_applied" else "candidate"
            accepted_topics = {_integrity_position(t["position"]) for t in audit["topics"]
                               if t["repair_status"] == expected_status}
            if affected != accepted_topics:
                raise ValueError("integrity accepted actions disagree with semantic repair topics")
            for topic in audit["topics"]:
                position = _integrity_position(topic["position"])
                if position in affected and topic["removed_evidence_count"] != len(removals.get(position, set())):
                    raise ValueError("integrity source removals disagree with semantic repair count")
    if accepted:
        _integrity_acceptance(record, initial, followup, phases, costs, affected, eligible, followup_rows)
    run_id = record["workflow_run_id"]
    if run_id is not None and not 1 <= _integrity_int(run_id, 10**18):
        raise ValueError("invalid workflow run identifier")
    _integrity_health(record["corpus_health"])
    if (decision == "unpublished" or disposition in {"rejected", "no_result", "blocked"}) and (
        record["generation"] is not None or record["repair_generation"] is not None or artifacts or actions
        or audit is not None or initial is not None or followup is not None or costs
        or any(p["phase"] != "publication_verification" for p in phases.values())
    ):
        raise ValueError("unpublished integrity must preserve status-only privacy")
    return record


def _integrity_removal_matches(action: dict[str, Any], rows: list[Any]) -> None:
    """Match removal causes to initial scores; pipeline also proves source identity."""
    refs = [rows[ref["index"]] for ref in action["check_refs"] if ref["stage"] == "initial"]
    if "confirmed_irrelevance" in action["reasons"]:
        confirmed = {row["evidence_index"] for row in refs
                     if row["check"] == "irrelevant_citation" and row["confirmation_label"] == "confirmed"}
        if not set(action["source_indexes"]) <= confirmed:
            raise ValueError("integrity citation removal lacks isolated confirmation")
    if "grouping_subset" in action["reasons"] and not any(
        row["check"] == "unsafe_grouping" and row["confirmation_label"] == "confirmed" for row in refs
    ):
        raise ValueError("integrity grouping removal lacks confirmed grouping target")


def _integrity_acceptance(
    record: dict[str, Any], initial: dict[str, Any] | None, followup: dict[str, Any] | None,
    phases: dict[int, dict[str, Any]], costs: dict[str, dict[str, Any]],
    affected: set[tuple[str, str, int]], eligible: set[tuple[str, str, int]], followup_rows: list[Any],
) -> None:
    if initial is None or followup is None or initial["status"] != "complete" or followup["status"] != "complete":
        raise ValueError("accepted integrity repair requires complete initial and follow-up review")
    if not affected or not affected <= eligible:
        raise ValueError("accepted integrity repair is outside confirmed targets")
    if set(costs) != {"initial_review", "repair_generation", "followup_review"} or any(
        cost["reported_cost_usd"] is None or cost["unknown_cost_calls"] != 0 for cost in costs.values()
    ):
        raise ValueError("accepted integrity repair requires known billing in each phase")
    complete = {p["phase"] for p in phases.values() if p["status"] == "complete"}
    if not {"candidate_validation", "publication_verification"} <= complete:
        raise ValueError("accepted integrity repair requires verified acceptance phases")
    if record["decision"] == "repaired_applied" and not any(
        action["outcome"] == "applied" for action in record["actions"]
    ):
        raise ValueError("applied integrity publication has no applied actions")
    if any(row["confirmation_label"] != "not_flagged" and affected.intersection(
        _integrity_position(p) for p in row["positions"]
    ) for row in followup_rows):
        raise ValueError("accepted integrity repair has a follow-up flag in an affected position")


def _integrity_health(value: object) -> None:
    if value is None:
        return
    seen: set[tuple[str, str]] = set()
    for item in _integrity_list(value, 256, "corpus health"):
        row = _integrity_object(item, {
            "source_type", "source_id", "category", "status", "reason", "parsed_entries",
            "dated_entries", "retained_entries",
        }, "source health")
        source_type = _integrity_enum(row["source_type"], {"rss", "hacker_news", "reddit"})
        source_id = _integrity_string(row["source_id"], 256)
        _integrity_string(row["category"], 100)
        if (source_type, source_id) in seen:
            raise ValueError("duplicate integrity source health")
        seen.add((source_type, source_id))
        status = _integrity_enum(row["status"], {"ok", "quiet", "empty", "error"})
        for key in ("parsed_entries", "dated_entries", "retained_entries"):
            _integrity_int(row[key], 10**7)
        if not row["retained_entries"] <= row["dated_entries"] <= row["parsed_entries"]:
            raise ValueError("inconsistent integrity source counts")
        reason = row["reason"]
        allowed = {"ok": {"undated_entries"}, "quiet": {"no_window_entries", "entries_filtered"},
                   "empty": {"empty_source", "no_dated_entries"}, "error": {"fetch_error"}}
        if reason is None:
            if status != "ok" or row["parsed_entries"] != row["dated_entries"]:
                raise ValueError("integrity source health hides degradation")
        else:
            _integrity_enum(reason, allowed[status])
            if reason == "undated_entries" and row["parsed_entries"] <= row["dated_entries"]:
                raise ValueError("integrity undated-source cause contradicts counts")
