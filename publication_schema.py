"""Shared standard-library schema for publication review metadata."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
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
    section: str | None = None
    headline: str | None = None
    model_authored: str | None = None
    path: str | None = None

    @property
    def context(self) -> ReviewContext | None:
        if self.section is None or self.headline is None or self.model_authored is None:
            return None
        return ReviewContext(
            self.section,
            self.headline,
            self.model_authored,
            self.path,
        )


def finding_has_fields(raw: object, allowed: set[frozenset[str]]) -> bool:
    return isinstance(raw, dict) and frozenset(raw) in allowed


def finding_strings_are_valid(raw: dict[str, Any]) -> bool:
    return all(
        isinstance(raw[field], str) and bool(raw[field].strip())
        for field in FINDING_FIELDS
    )


def finding_level_is_valid(raw: dict[str, Any]) -> bool:
    return raw["level"] in {"ERROR", "WARN"}


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


SEMANTIC_CHECKS = {"duplicate", "unsafe_grouping", "unsupported_claim", "strengthened_claim", "reversed_claim"}
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
    if not isinstance(raw, dict) or set(raw) != SEMANTIC_AUDIT_FIELDS:
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
    if raw["threshold"] == 0:
        raise ValueError("invalid semantic audit threshold")
    for field in ("planned_checks", "omitted_duplicate_pairs", "skipped_oversized_checks", "unknown_cost_calls"):
        if type(raw[field]) is not int or not 0 <= raw[field] <= 5000:
            raise ValueError("invalid semantic audit count")
    cost = raw["reported_cost_usd"]
    if type(cost) not in (int, float) or not 0 <= cost <= 100 or not math.isfinite(cost):
        raise ValueError("invalid semantic audit cost")
    topics, checks = raw["topics"], raw["checks"]
    if not isinstance(topics, list) or len(topics) > 64 or not isinstance(checks, list) or len(checks) > 2300:
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
        if not isinstance(check, dict) or set(check) != {
            "check", "positions", "probability", "confirmation_probability", "confirmation_label",
            "after_probability", "after_confirmation_probability", "after_confirmation_label", "after_basis",
        } or not isinstance(check["check"], str) or check["check"] not in SEMANTIC_CHECKS:
            raise ValueError("invalid semantic audit check")
        refs = check["positions"]
        count = 2 if check["check"] == "duplicate" else 1
        if not isinstance(refs, list) or len(refs) != count:
            raise ValueError("invalid semantic check position count")
        keys = [position(ref) for ref in refs]
        key = repr((check["check"], sorted(keys)))
        if any(k not in positions for k in keys) or len(set(keys)) != count or key in seen:
            raise ValueError("invalid or duplicate semantic check reference")
        seen.add(key)
        if check["after_basis"] not in (None, "model", "single_evidence"):
            raise ValueError("invalid semantic post-check basis")
        if (check["after_basis"] == "model") != (check["after_probability"] is not None):
            raise ValueError("semantic post-check basis does not match score")
        if check["after_basis"] == "single_evidence" and check["check"] != "unsafe_grouping":
            raise ValueError("single evidence proves only grouping scope")
        probability(check["probability"])
        probability(check["confirmation_probability"], optional=True)
        probability(check["after_probability"], optional=True)
        probability(check["after_confirmation_probability"], optional=True)
        for prefix in ("", "after_"):
            p = check[prefix + "probability"]
            confirmation = check[prefix + "confirmation_probability"]
            label = check[prefix + "confirmation_label"]
            expected = (None if p is None else "not_flagged" if p < raw["threshold"] else
                        "unconfirmed" if confirmation is None else
                        "confirmed" if confirmation >= raw["threshold"] else "disputed")
            if label != expected or (confirmation is not None and (p is None or p < raw["threshold"])):
                raise ValueError("semantic label does not match both scores")
    if len(checks) > raw["planned_checks"]:
        raise ValueError("semantic coverage exceeds planned checks")
    if raw["status"] == "complete" and (
        len(checks) != raw["planned_checks"] or raw["omitted_duplicate_pairs"]
        or raw["skipped_oversized_checks"] or any(c["confirmation_label"] == "unconfirmed" for c in checks)
    ):
        raise ValueError("complete semantic audit has incomplete coverage")
    return raw
