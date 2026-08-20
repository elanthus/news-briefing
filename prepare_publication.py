#!/usr/bin/env python3
"""Validate a completed run and prepare its public archive input."""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

FINAL_STATUSES = {"ready", "review_required", "rejected", "no_result"}
PUBLIC_ARTIFACTS = {
    "ready": ("final", "final.md"),
    "review_required": ("preview", "preview.md"),
}
FINDING_FIELDS = {"level", "check", "domain", "message"}


@dataclass(frozen=True)
class ReviewFinding:
    level: str
    check: str
    domain: str
    message: str


@dataclass(frozen=True)
class PublicationRecord:
    date: str
    disposition: str
    findings_count: int
    findings: tuple[ReviewFinding, ...]
    degraded_sources: tuple[str, ...]

    def payload(self) -> dict[str, object]:
        return {
            "date": self.date,
            "disposition": self.disposition,
            "findings_count": self.findings_count,
            "findings": [asdict(finding) for finding in self.findings],
            "degraded_sources": list(self.degraded_sources),
        }


def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None


def _review_findings(raw_findings: object) -> tuple[ReviewFinding, ...] | None:
    if not isinstance(raw_findings, list) or not raw_findings:
        return None
    findings: list[ReviewFinding] = []
    for raw in raw_findings:
        if not isinstance(raw, dict) or set(raw) != FINDING_FIELDS:
            return None
        if any(not isinstance(raw[field], str) or not raw[field].strip() for field in FINDING_FIELDS):
            return None
        if raw["level"] not in {"ERROR", "WARN"}:
            return None
        findings.append(
            ReviewFinding(
                level=raw["level"],
                check=raw["check"],
                domain=raw["domain"],
                message=raw["message"],
            )
        )
    return tuple(findings)


def _bound_artifact(run_dir: Path, manifest: dict[str, Any], final: dict[str, Any], status: str) -> bytes | None:
    artifact_type, artifact_name = PUBLIC_ARTIFACTS[status]
    artifacts = manifest.get("artifacts")
    expected_digest = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    artifact_path = run_dir / artifact_name
    try:
        content = artifact_path.read_bytes()
        content.decode("utf-8")
    except (OSError, UnicodeError):
        return None
    actual_digest = hashlib.sha256(content).hexdigest()
    if (
        final.get("artifact_type") != artifact_type
        or final.get("run_artifact") != artifact_name
        or not isinstance(expected_digest, str)
        or actual_digest != expected_digest
    ):
        return None
    return content


def _degraded_sources(corpus_path: Path) -> tuple[str, ...]:
    corpus = _load_json(corpus_path)
    errors = corpus.get("errors", []) if isinstance(corpus, dict) else []
    if not isinstance(errors, list):
        return ()
    labels: list[str] = []
    for error in errors:
        if isinstance(error, dict):
            source_type = error.get("source_type", "source")
            source_id = error.get("source_id", "unknown")
            label = f"{source_type}:{source_id}"
        elif isinstance(error, str):
            label = error
        else:
            label = "source:unknown"
        if label not in labels:
            labels.append(label)
    return tuple(labels)


def prepare_publication(
    run_dir: Path,
    corpus_path: Path,
    history_dir: Path,
    day: date,
) -> PublicationRecord:
    """Write a fail-closed sidecar and any hash-bound public briefing artifact."""
    disposition = "blocked"
    findings_count = 0
    findings: tuple[ReviewFinding, ...] = ()
    public_content: bytes | None = None

    manifest = _load_json(run_dir / "manifest.json")
    if isinstance(manifest, dict) and manifest.get("status") == "complete":
        final = manifest.get("final")
        if isinstance(final, dict):
            status = final.get("status")
            raw_findings = final.get("findings")
            if isinstance(status, str) and status in FINAL_STATUSES and isinstance(raw_findings, list):
                disposition = status
                findings_count = len(raw_findings)
                if status == "review_required":
                    normalized = _review_findings(raw_findings)
                    if normalized is None:
                        disposition = "blocked"
                        findings_count = 0
                    else:
                        findings = normalized
                if disposition in PUBLIC_ARTIFACTS:
                    public_content = _bound_artifact(run_dir, manifest, final, disposition)
                    if public_content is None:
                        disposition = "blocked"
                        findings_count = 0
                        findings = ()

    history_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = history_dir / f"{day.isoformat()}.md"
    if public_content is None:
        markdown_path.unlink(missing_ok=True)
    else:
        markdown_path.write_bytes(public_content)

    record = PublicationRecord(
        date=day.isoformat(),
        disposition=disposition,
        findings_count=findings_count,
        findings=findings,
        degraded_sources=_degraded_sources(corpus_path),
    )
    (history_dir / f"{record.date}.json").write_text(
        json.dumps(record.payload(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, default=Path("run"))
    parser.add_argument("--corpus", type=Path, default=Path("corpus.json"))
    parser.add_argument("--history-dir", type=Path, default=Path("briefing-history"))
    parser.add_argument("--date", type=date.fromisoformat, default=None, dest="day")
    args = parser.parse_args()
    day = args.day or datetime.now(UTC).date()
    record = prepare_publication(args.run_dir, args.corpus, args.history_dir, day)
    print(json.dumps(record.payload(), indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
