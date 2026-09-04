"""Shared standard-library schema for publication review metadata."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

FINDING_FIELDS = {"level", "check", "domain", "message"}
FINDING_V3_FIELDS = FINDING_FIELDS | {"context"}
CONTEXT_FIELDS = {"section", "headline", "model_authored"}
CONTEXT_V4_FIELDS = CONTEXT_FIELDS | {"path"}
REPAIR_ACTION_FIELDS = {"action", "path", "reason"}


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
    path = raw_path if isinstance(raw_path, str) and raw_path.strip() else None
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


def finding_payload(finding: ReviewFinding) -> dict[str, object]:
    return {
        "level": finding.level,
        "check": finding.check,
        "domain": finding.domain,
        "message": finding.message,
        "context": asdict(finding.context) if finding.context is not None else None,
    }
