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
    """Parse a provenance object; ``None`` only for an absent (pre-#174) field.

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
