#!/usr/bin/env python3
"""Validate a completed run and prepare its public archive input."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from agent_runner.outcomes import is_actionable_finding
from publication_failures import GenerationFailure, summarize_failed_chain
from publication_schema import (
    FINDING_FIELDS,
    ReviewContext,
    ReviewFinding,
    finding_has_fields,
    finding_level_is_valid,
    finding_payload,
    finding_strings_are_valid,
    parse_repair_actions,
)

FINAL_STATUSES = {"ready", "review_required", "rejected", "no_result"}
PUBLIC_ARTIFACTS = {
    "ready": ("final", "final.md"),
    "review_required": ("preview", "preview.md"),
}
STRUCTURED_PATH = re.compile(r"^(topics|excluded_topics)\.(.+?)\[(\d+)\]")
EXCLUDED_CONTEXT_PREFIX = "Excluded Topics: "
FALLBACK_LOG_NAME = "fallback-log.json"


@dataclass(frozen=True)
class PublicationRecord:
    date: str
    disposition: str
    findings_count: int
    findings: tuple[ReviewFinding, ...]
    degraded_sources: tuple[str, ...]
    repair_actions: tuple[dict[str, str], ...] = ()
    generation_failures: tuple[GenerationFailure, ...] = ()

    def payload(self) -> dict[str, object]:
        return {
            "date": self.date,
            "disposition": self.disposition,
            "findings_count": self.findings_count,
            "findings": [finding_payload(finding) for finding in self.findings],
            "degraded_sources": list(self.degraded_sources),
            "repair_actions": list(self.repair_actions),
            "generation_failures": [failure.payload() for failure in self.generation_failures],
        }


def _load_json(path: Path) -> object | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, UnicodeError):
        return None


def _selected_generation_run(run_dir: Path) -> Path | None:
    """Resolve a fallback-chain root to its selected successful child run."""
    fallback_path = run_dir / FALLBACK_LOG_NAME
    if not fallback_path.exists():
        return run_dir
    fallback = _load_json(fallback_path)
    if not isinstance(fallback, dict) or fallback.get("status") != "ready":
        return None
    selected = fallback.get("selected_run_dir")
    if not isinstance(selected, str) or not selected or Path(selected).name != selected:
        return None
    selected_path = run_dir / selected
    try:
        resolved_root = run_dir.resolve(strict=True)
        resolved_selected = selected_path.resolve(strict=True)
    except OSError:
        return None
    if selected_path.is_symlink() or resolved_selected.parent != resolved_root:
        return None
    manifest = _load_json(resolved_selected / "manifest.json")
    final = manifest.get("final") if isinstance(manifest, dict) else None
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "complete"
        or not isinstance(final, dict)
        or final.get("status") != "ready"
    ):
        return None
    return resolved_selected


def _review_findings(raw_findings: object) -> tuple[ReviewFinding, ...] | None:
    if not isinstance(raw_findings, list) or not raw_findings:
        return None
    findings: list[ReviewFinding] = []
    for raw in raw_findings:
        if not finding_has_fields(raw, {frozenset(FINDING_FIELDS)}):
            return None
        assert isinstance(raw, dict)
        if not finding_strings_are_valid(raw):
            return None
        if not finding_level_is_valid(raw):
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


def _actionable_findings(
    findings: tuple[ReviewFinding, ...],
) -> tuple[ReviewFinding, ...]:
    return tuple(finding for finding in findings if is_actionable_finding(finding))


def _actionable_finding_count(raw_findings: list[object]) -> int:
    return sum(1 for finding in raw_findings if is_actionable_finding(finding))


def _bound_json_artifact(
    run_dir: Path,
    manifest: dict[str, Any],
    artifact_name: str,
) -> object | None:
    artifacts = manifest.get("artifacts")
    expected_digest = artifacts.get(artifact_name) if isinstance(artifacts, dict) else None
    try:
        content = (run_dir / artifact_name).read_bytes()
    except OSError:
        return None
    if not isinstance(expected_digest, str) or hashlib.sha256(content).hexdigest() != expected_digest:
        return None
    try:
        return json.loads(content.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeError):
        return None


def _final_attempt(
    manifest: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, Any] | None:
    final_index = final.get("attempt")
    attempts = manifest.get("attempts")
    if (
        not isinstance(final_index, int)
        or isinstance(final_index, bool)
        or not isinstance(attempts, list)
    ):
        return None
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get("index") == final_index:
            return attempt
    return None


def _final_structured_output(
    run_dir: Path,
    manifest: dict[str, Any],
    final: dict[str, Any],
) -> dict[str, Any] | None:
    attempt = _final_attempt(manifest, final)
    if attempt is None:
        return None
    artifact_name = attempt.get("structured_artifact")
    if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
        return None
    payload = _bound_json_artifact(run_dir, manifest, artifact_name)
    return payload if isinstance(payload, dict) else None


def _extract_repair_actions(
    manifest: dict[str, Any],
    final: dict[str, Any],
) -> tuple[dict[str, str], ...]:
    attempt = _final_attempt(manifest, final)
    if attempt is None or attempt.get("kind") != "deterministic_repair":
        return ()
    return parse_repair_actions(attempt.get("repair_actions"))


def _review_context(
    finding: ReviewFinding,
    output: dict[str, Any],
) -> ReviewContext | None:
    path_match = STRUCTURED_PATH.match(finding.message)
    if path_match is not None:
        placement, section_name, raw_index = path_match.groups()
        structured_path = path_match.group(0)
        container_name = "sections" if placement == "topics" else "excluded_topics"
        container = output.get(container_name)
        section = container.get(section_name) if isinstance(container, dict) else None
        entries = section.get("topics") if placement == "topics" and isinstance(section, dict) else section
        index = int(raw_index)
        if isinstance(entries, list) and index < len(entries):
            entry = entries[index]
            headline = entry.get("headline") if isinstance(entry, dict) else None
            if isinstance(headline, str) and headline.strip():
                context_section = (
                    section_name
                    if placement == "topics"
                    else f"{EXCLUDED_CONTEXT_PREFIX}{section_name}"
                )
                return ReviewContext(
                    section=context_section,
                    headline=headline,
                    model_authored=json.dumps(
                        entry,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                    path=structured_path,
                )

    sections = output.get("sections")
    if not isinstance(sections, dict):
        return None
    for section_name, section in sections.items():
        if not isinstance(section_name, str) or not isinstance(section, dict):
            continue
        topics = section.get("topics")
        if not isinstance(topics, list):
            continue
        for entry in topics:
            if not isinstance(entry, dict):
                continue
            headline = entry.get("headline")
            if (
                isinstance(headline, str)
                and headline.strip()
                and finding.message.startswith(f"{section_name}: {headline!r}")
            ):
                return ReviewContext(
                    section=section_name,
                    headline=headline,
                    model_authored=json.dumps(
                        entry,
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    ),
                )
    return None


def _attach_review_context(
    findings: tuple[ReviewFinding, ...],
    output: dict[str, Any] | None,
) -> tuple[ReviewFinding, ...]:
    if output is None:
        return findings
    return tuple(
        ReviewFinding(
            level=finding.level,
            check=finding.check,
            domain=finding.domain,
            message=finding.message,
            section=context.section if context is not None else None,
            headline=context.headline if context is not None else None,
            model_authored=context.model_authored if context is not None else None,
            path=context.path if context is not None else None,
        )
        for finding in findings
        for context in [_review_context(finding, output)]
    )


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
    repair_actions: tuple[dict[str, str], ...] = ()
    public_content: bytes | None = None

    generation_run_dir = _selected_generation_run(run_dir)
    manifest = (
        _load_json(generation_run_dir / "manifest.json")
        if generation_run_dir is not None
        else None
    )
    if (
        generation_run_dir is not None
        and isinstance(manifest, dict)
        and manifest.get("status") == "complete"
    ):
        final = manifest.get("final")
        if isinstance(final, dict):
            status = final.get("status")
            raw_findings = final.get("findings")
            if isinstance(status, str) and status in FINAL_STATUSES and isinstance(raw_findings, list):
                disposition = status
                findings_count = _actionable_finding_count(raw_findings)
                if status == "review_required":
                    normalized = _review_findings(raw_findings)
                    if normalized is None:
                        disposition = "blocked"
                        findings_count = 0
                    else:
                        normalized = _actionable_findings(normalized)
                        findings_count = len(normalized)
                        findings = _attach_review_context(
                            normalized,
                            _final_structured_output(generation_run_dir, manifest, final),
                        )
                if disposition in PUBLIC_ARTIFACTS:
                    # Repair provenance is published only with a public artifact;
                    # non-public dispositions keep the minimal-metadata contract.
                    repair_actions = _extract_repair_actions(manifest, final)
                    public_content = _bound_artifact(
                        generation_run_dir, manifest, final, disposition
                    )
                    if public_content is None:
                        disposition = "blocked"
                        findings_count = 0
                        findings = ()
                        repair_actions = ()

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
        repair_actions=repair_actions,
        generation_failures=(
            summarize_failed_chain(_load_json(run_dir / FALLBACK_LOG_NAME))
            if disposition == "blocked" else ()
        ),
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
