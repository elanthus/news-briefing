"""Stable, bounded failure records shared by private diagnostics and publication."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

PROVIDER_CODES = frozenset({
    "provider_error", "rate_limited", "provider_unavailable", "invalid_request",
    "empty_response", "output_truncated",
})
FAILURE_CODES = PROVIDER_CODES | {
    "validation_failed", "correction_exhausted", "chain_exhausted", "chain_incomplete", "generation_failed",
}
# Frozen wire schema: evolve with an explicit version/migration, never dataclass introspection.
_V1_FIELDS = frozenset({
    "schema_version", "code", "status_code", "transient", "output_truncated",
    "checks", "stage", "corrections_used", "correction_limit",
})
_CHECK = re.compile(r"[a-z][a-z0-9_]{0,79}\Z")


@dataclass(frozen=True)
class FailureRecord:
    code: str
    status_code: int | None = None
    transient: bool = False
    output_truncated: bool = False
    checks: tuple[str, ...] = ()
    stage: str | None = None
    corrections_used: int | None = None
    correction_limit: int | None = None
    schema_version: int = 1

    def payload(self) -> dict[str, Any]:
        record = asdict(self)
        record["checks"] = list(self.checks)
        return record


def parse_failure(raw: Any) -> FailureRecord | None:
    """Read v1 and the original unversioned shape; reject unknown schemas/fields."""
    if not isinstance(raw, dict):
        return None
    # Records written before versioning retain their original strict field contract.
    if set(raw) == _V1_FIELDS - {"schema_version"}:
        raw = {**raw, "schema_version": 1}
    if (set(raw) != _V1_FIELDS or type(raw["schema_version"]) is not int
            or raw["schema_version"] != 1):
        return None
    if not isinstance(raw["code"], str) or raw["code"] not in FAILURE_CODES:
        return None
    if type(raw["transient"]) is not bool or type(raw["output_truncated"]) is not bool:
        return None
    status = raw["status_code"]
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return None
    if raw["stage"] not in (None, "selection", "prose"):
        return None
    for key in ("corrections_used", "correction_limit"):
        value = raw[key]
        if value is not None and (type(value) is not int or not 0 <= value <= 1000):
            return None
    checks = raw["checks"]
    if (not isinstance(checks, list) or len(checks) > 100
            or any(not isinstance(check, str) or not _CHECK.fullmatch(check) for check in checks)):
        return None
    if raw["output_truncated"] != (raw["code"] == "output_truncated"):
        return None
    if raw["code"] in PROVIDER_CODES and (checks or raw["stage"] is not None
            or raw["corrections_used"] is not None or raw["correction_limit"] is not None):
        return None
    if raw["code"] == "correction_exhausted" and (
        not checks or raw["stage"] is None or raw["corrections_used"] is None
        or raw["correction_limit"] is None or raw["correction_limit"] <= 0
        or raw["corrections_used"] < raw["correction_limit"]
    ):
        return None
    return FailureRecord(**{**raw, "checks": tuple(checks)})


def provider_failure(
    *, status_code: int | None, transient: bool,
    output_truncated: bool, empty_response: bool,
) -> FailureRecord:
    if output_truncated:
        code = "output_truncated"
    elif empty_response:
        code = "empty_response"
    elif status_code == 429:
        code = "rate_limited"
    elif status_code is not None and 500 <= status_code <= 599:
        code = "provider_unavailable"
    elif status_code is not None and 400 <= status_code <= 499:
        code = "invalid_request"
    elif transient:
        code = "provider_unavailable"
    else:
        code = "provider_error"
    return FailureRecord(code, status_code, transient, output_truncated)


def final_failure(final: dict[str, Any], manifest: dict[str, Any]) -> FailureRecord | None:
    if final.get("status") == "ready":
        return None
    findings = final.get("findings")
    checks = tuple(sorted({
        row["check"] for row in findings if isinstance(row, dict)
        and isinstance(row.get("check"), str) and _CHECK.fullmatch(row["check"])
        and row.get("level") == "ERROR"
    }))[:100] if isinstance(findings, list) else ()
    attempts = manifest.get("attempts")
    stage = None
    used = None
    # Damaged resume metadata must not make finalization itself fail or invent a budget.
    if (isinstance(attempts, list) and attempts
            and all(isinstance(row, dict) and isinstance(row.get("kind"), str) for row in attempts)):
        last_kind = attempts[-1]["kind"]
        if last_kind in {"selection", "selection_correction", "selection_repair", "selection_promotion"}:
            stage = "selection"
        elif last_kind in {"prose", "correction", "deterministic_repair"}:
            stage = "prose"
        if stage is not None:
            kind = "selection_correction" if stage == "selection" else "correction"
            count = sum(row["kind"] == kind for row in attempts)
            used = count if count <= 1000 else None
    identity = manifest.get("identity")
    maximum = identity.get("max_corrections") if isinstance(identity, dict) else None
    maximum = maximum if type(maximum) is int and 0 <= maximum <= 1000 else None
    exhausted = bool(checks) and maximum is not None and maximum > 0 and used is not None and used >= maximum
    return FailureRecord(
        "correction_exhausted" if exhausted else "validation_failed",
        checks=checks, stage=stage, corrections_used=used, correction_limit=maximum,
    )


def run_failure(manifest: dict[str, Any] | None, error: Any = None) -> FailureRecord:
    """Prefer the finalized disposition over earlier recoverable provider failures.

    The fallback chain acts on the completed checker result. Provider errors remain
    available to triage, but become the primary failure only without a valid final.
    """
    if manifest is not None:
        for key in ("final", "error", "correction_error"):
            source = manifest.get(key)
            record = parse_failure(source.get("failure")) if isinstance(source, dict) else None
            if record is not None:
                return record
    record = parse_failure(error.get("failure")) if isinstance(error, dict) else None
    return record or FailureRecord("generation_failed")
