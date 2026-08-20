#!/usr/bin/env python3
"""Seed the public archive from selected, verifiable dogfood runs."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from agent_runner.outcomes import finding_domain


@dataclass(frozen=True)
class DogfoodRun:
    day: date
    directory: str
    artifact: str
    corpus: str
    allow_legacy_findings: bool = False


DOGFOOD_RUNS = (
    DogfoodRun(
        day=date(2026, 8, 17),
        directory="docs/runs/2026-08-17",
        artifact="final.md",
        corpus="corpus-2026-08-17.json",
        allow_legacy_findings=True,
    ),
    DogfoodRun(
        day=date(2026, 8, 18),
        directory="docs/runs/2026-08-18/replay-deepseek-v4-flash",
        artifact="preview.md",
        corpus="corpus.json",
    ),
)
SUPPORTED_DISPOSITIONS = {"ready", "review_required", "rejected", "no_result"}
PAGE_DISPOSITIONS = {"ready", "review_required"}
PUBLIC_ARTIFACTS = {
    "ready": ("final", "final.md"),
    "review_required": ("preview", "preview.md"),
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read dogfood artifact {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"dogfood artifact {path} must contain an object")
    return payload


def _findings(raw_findings: object, *, allow_legacy: bool) -> list[dict[str, str]]:
    if not isinstance(raw_findings, list) or not raw_findings:
        raise ValueError("dogfood review-required run must contain findings")
    normalized: list[dict[str, str]] = []
    for raw in raw_findings:
        expected = {"level", "check", "message"} if allow_legacy else {"level", "check", "domain", "message"}
        if not isinstance(raw, dict) or set(raw) != expected:
            raise ValueError("dogfood finding has an unexpected schema")
        level = raw["level"]
        check = raw["check"]
        message = raw["message"]
        if any(not isinstance(value, str) or not value.strip() for value in (level, check, message)):
            raise ValueError("dogfood finding fields must be non-empty strings")
        domain = finding_domain(check) if allow_legacy else raw.get("domain")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError("dogfood finding fields must be non-empty strings")
        if level not in {"ERROR", "WARN"}:
            raise ValueError("dogfood finding level must be ERROR or WARN")
        normalized.append(
            {"level": level, "check": check, "domain": domain, "message": message}
        )
    return normalized


def _disposition(final: dict[str, Any], *, allow_legacy: bool) -> str:
    status = final.get("status")
    if allow_legacy and status == "WARN":
        return "review_required"
    if isinstance(status, str) and status in SUPPORTED_DISPOSITIONS:
        return status
    raise ValueError(f"dogfood run has unsupported final status {status!r}")


def _degraded_sources(corpus: dict[str, Any]) -> list[str]:
    errors = corpus.get("errors", [])
    if not isinstance(errors, list):
        raise ValueError("dogfood corpus errors must be an array")
    labels: list[str] = []
    for error in errors:
        if isinstance(error, dict):
            label = f"{error.get('source_type', 'source')}:{error.get('source_id', 'unknown')}"
        elif isinstance(error, str):
            label = error
        else:
            label = "source:unknown"
        if label not in labels:
            labels.append(label)
    return labels


def bootstrap_history(repository_root: Path, history_dir: Path) -> None:
    """Write curated dogfood records after validating their manifest hashes."""
    history_dir.mkdir(parents=True, exist_ok=True)
    for run in DOGFOOD_RUNS:
        run_dir = repository_root / run.directory
        manifest = _load_object(run_dir / "manifest.json")
        final = manifest.get("final")
        artifacts = manifest.get("artifacts")
        if manifest.get("status") != "complete" or not isinstance(final, dict) or not isinstance(artifacts, dict):
            raise ValueError(f"dogfood run {run.directory} is not complete")
        if final.get("run_artifact") != run.artifact:
            raise ValueError(f"dogfood run {run.directory} names a different final artifact")
        disposition = _disposition(final, allow_legacy=run.allow_legacy_findings)
        if disposition in PUBLIC_ARTIFACTS and not run.allow_legacy_findings:
            expected_type, expected_name = PUBLIC_ARTIFACTS[disposition]
            if final.get("artifact_type") != expected_type or run.artifact != expected_name:
                raise ValueError(
                    f"dogfood run {run.directory} artifact does not match {disposition}"
                )
        artifact_path = run_dir / run.artifact
        content = artifact_path.read_bytes()
        content.decode("utf-8")
        digest = hashlib.sha256(content).hexdigest()
        if artifacts.get(run.artifact) != digest:
            raise ValueError(f"dogfood run {run.directory} artifact hash differs from its manifest")
        raw_findings = final.get("findings")
        if not isinstance(raw_findings, list):
            raise ValueError(f"dogfood run {run.directory} findings must be an array")
        findings = (
            _findings(raw_findings, allow_legacy=run.allow_legacy_findings)
            if disposition == "review_required"
            else []
        )
        corpus = _load_object(run_dir / run.corpus)
        payload = {
            "date": run.day.isoformat(),
            "disposition": disposition,
            "findings_count": len(raw_findings),
            "findings": findings,
            "degraded_sources": _degraded_sources(corpus),
        }
        markdown_path = history_dir / f"{run.day.isoformat()}.md"
        if disposition in PAGE_DISPOSITIONS:
            markdown_path.write_bytes(content)
        else:
            markdown_path.unlink(missing_ok=True)
        (history_dir / f"{run.day.isoformat()}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("history_dir", type=Path)
    parser.add_argument("--repository-root", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        bootstrap_history(args.repository_root, args.history_dir)
    except (OSError, UnicodeError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
