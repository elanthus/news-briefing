#!/usr/bin/env python3
"""Build a dependency-free static archive from publication-gated briefings."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import html
import importlib
import json
import re
import shutil
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import corpus_schema
import eval_briefing
from agent_runner.outcomes import ADVISORY_QUALITY_CHECKS, QUALITY_CHECKS
from audit_manifest import build_audit_manifest
from publication_failures import (
    FAILURE_MESSAGES,
    MODEL_LABELS,
    GenerationFailure,
    parse_generation_failures,
)
from publication_schema import (
    CONTEXT_FIELDS,
    FINDING_V3_FIELDS,
    INTEGRITY_REASON_MESSAGES,
    Provenance,
    ReviewFinding,
    finding_has_fields,
    finding_level_is_valid,
    finding_strings_are_valid,
    parse_integrity,
    parse_provenance,
    parse_repair_actions,
    parse_review_context,
    parse_semantic_audit,
    provenance_payload,
)

SIDECAR_FIELDS = {
    "date", "disposition", "findings_count", "degraded_sources", "findings",
    "repair_actions", "generation_failures", "advisory_findings", "provenance",
}
LEGACY_SIDECAR_FIELDS = SIDECAR_FIELDS.copy()
SIDECAR_FIELDS |= {"semantic_audit"}
AUDIT_SIDECAR_FIELDS = SIDECAR_FIELDS.copy()
SIDECAR_FIELDS |= {"integrity"}
HISTORY_FIELDS = SIDECAR_FIELDS | {"markdown"}
LEGACY_HISTORY_FIELDS = LEGACY_SIDECAR_FIELDS | {"markdown"}
STORY_ANCHOR = re.compile(r"^<!-- story: ((?:topics|excluded_topics)\..+?\[\d+\]) -->$")
# Wrap the code-owned citation URL after each "🔗" marker in a commonmark
# autolink so it renders as a link even with linkify disabled (see
# _render_markdown). Citations sit on their own line in the body but inline in
# the exclusion log ("- *Title* — reason. 🔗 url"), so match the marker
# anywhere, mirroring the checker's _LINK grammar; prose without the marker
# stays inert.
_CITATION_AUTOLINK = re.compile(r"(🔗\s*(?:HN:\s*)?)(https?://\S+)")
DISPOSITIONS = {
    "blocked",
    "degraded",
    "no_result",
    "ready",
    "rejected",
    "review_required",
}
PAGE_DISPOSITIONS = {"ready", "review_required"}
PUBLICATION_RANK = {"ready": 2, "review_required": 1}
PROJECT_URL = "https://github.com/elanthus/news-briefing"
PROJECT_DESCRIPTION = (
    "A daily LLM-generated news briefing with deterministic corpus and citation checks."
)
FAVICON_SOURCE_DIR = Path(__file__).resolve().parent / "docs" / "images"
FAVICON_FILENAMES = ("favicon-light.png", "favicon-dark.png")


STYLE = """
:root { color-scheme: light dark;
  --bg: #faf9f6; --surface: #ffffff; --text: #1c1c1e; --muted: #5c5c63; --rule: #dcd9d2;
  --strong-rule: #1c1c1e; --accent: #1d5fb0; --chip-bg: #efede8; --chip-text: #38383d;
  --hn: #b4480a; --hn-bg: #fbeee4; --ok: #1b6e38; --ok-bg: #e5f3e9; --warn: #7d5200;
  --warn-bg: #fbf0d6; --bad: #9e2428; --bad-bg: #fbe6e6;
  --serif: Charter, "Bitstream Charter", "Iowan Old Style", "Sitka Text", Cambria, Georgia, serif;
  --sans: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", sans-serif;
  font-family: var(--sans); line-height: 1.5; }
@media (prefers-color-scheme: dark) { :root {
  --bg: #131315; --surface: #1c1c1f; --text: #e9e7e2; --muted: #a4a4aa; --rule: #34343a;
  --strong-rule: #8a8a90; --accent: #8fb9f3; --chip-bg: #27272c; --chip-text: #d6d4ce;
  --hn: #f3a56c; --hn-bg: #3a2416; --ok: #82d29f; --ok-bg: #173020; --warn: #f1c86f;
  --warn-bg: #362b12; --bad: #f29c9c; --bad-bg: #3b1b1c; } }
body { background: var(--bg); color: var(--text); overflow-wrap: anywhere; margin: 0 auto;
  max-width: 76rem; padding: 2rem 1.25rem 4rem; }
body:not(.integrity-report) { max-width: 46rem; }
a { color: inherit; text-underline-offset: .18em; }
a:hover { color: var(--accent); }
ul { list-style: none; padding: 0; }
article { padding: 1rem 0; }
.site-header { border-bottom: 1px solid var(--rule); margin-bottom: 1.25rem; padding-bottom: .9rem; }
.site-header p { color: var(--muted); font-size: .9rem; margin: .2rem 0 0; }
.site-name { font-size: 1.05rem; font-weight: 750; letter-spacing: -.01em; text-decoration: none; }
.site-footer { border-top: 1px solid var(--rule); color: var(--muted); font-size: .85rem;
  margin-top: 3rem; padding-top: 1rem; }
.history-nav { margin-bottom: 1.25rem; }
.history-label { color: var(--muted); font-size: .72rem; font-weight: 650; letter-spacing: .08em;
  margin: 0 0 .4rem; text-transform: uppercase; }
.history-nav ul { display: flex; gap: .4rem; margin: 0; overflow-x: auto; padding-bottom: .2rem; }
.history-nav li a, .history-nav li strong { align-items: center; border: 1px solid var(--rule);
  border-radius: .55rem; display: flex; flex-direction: column; font-weight: 600; font-size: .82rem;
  line-height: 1.25; min-width: 3.6rem; padding: .35rem .55rem; text-decoration: none; }
.history-nav li a:hover { border-color: var(--accent); }
.history-nav time { display: contents; }
.weekday { color: var(--muted); font-size: .66rem; letter-spacing: .07em; text-transform: uppercase; }
.history-nav [aria-current] { background: var(--text); border-color: var(--text); color: var(--bg); }
.history-nav [aria-current] .weekday { color: inherit; opacity: .75; }
.verdict { font-weight: 700; }
.muted { color: var(--muted); }
.briefing-content h1, .briefing-content h2, .briefing-content h3 { line-height: 1.2; }
.briefing-content h1 { font-family: var(--serif); font-size: clamp(2rem, 7vw, 2.85rem);
  letter-spacing: -.015em; margin: .15rem 0 .9rem; }
.briefing-content h1 + hr { border-top: 3px double var(--strong-rule); }
.briefing-content hr { border: 0; border-top: 1px solid var(--rule); margin: .9rem 0; }
.briefing-content ul { list-style: disc; padding-left: 1.25rem; }
.briefing-content li { margin: .35rem 0; }
.briefing-content pre { background: var(--chip-bg); border: 1px solid var(--rule); overflow-x: auto; padding: 1rem; }
.briefing-content code { background: var(--chip-bg); border-radius: .2rem; padding: .1rem .25rem; }
.briefing-content pre code { background: none; padding: 0; }
.briefing-content blockquote { border-left: .25rem solid var(--rule); margin-left: 0; padding-left: 1rem; }
.dateline { color: var(--muted); font-size: .75rem; font-weight: 650; letter-spacing: .09em;
  margin: 0; text-transform: uppercase; }
.status-chip { font-size: .88rem; }
.status-chip a { text-decoration: underline; }
p.status-chip { border-radius: 999px; display: inline-block; font-weight: 600; margin: .1rem 0 .4rem;
  padding: .2rem .75rem; }
.status-ok { background: var(--ok-bg); color: var(--ok); }
.status-warn { background: var(--warn-bg); color: var(--warn); }
.status-bad { background: var(--bad-bg); color: var(--bad); }
.status-chip a:hover { color: inherit; }
.corpus-window { color: var(--muted); font-size: .88rem; margin: 0; }
.section-nav { background: var(--bg); border-bottom: 1px solid var(--rule); margin: 0 0 .5rem;
  position: sticky; top: 0; z-index: 1; }
.briefing-content .section-nav ul { display: flex; gap: .3rem 1.1rem; list-style: none; margin: 0;
  overflow-x: auto; padding: .6rem 0; white-space: nowrap; }
.briefing-content .section-nav li { margin: 0; }
.section-nav a { font-size: .88rem; font-weight: 600; text-decoration: none; }
.section-nav .count { color: var(--muted); font-size: .75rem; font-weight: 500; margin-left: .3rem; }
.topic, .run-notes { scroll-margin-top: 3.5rem; }
.topic > h2 { border-top: 2px solid var(--strong-rule); font-family: var(--serif);
  font-size: 1.75rem; margin: 2.25rem 0 .25rem; padding-top: .55rem; }
.group-label { color: var(--muted); font-size: .76rem; letter-spacing: .09em;
  margin: 1.4rem 0 .6rem; text-transform: uppercase; }
.briefing-content .story { background: var(--surface); border: 1px solid var(--rule);
  border-radius: .65rem; font-family: var(--serif); font-size: 1.05rem; line-height: 1.6;
  margin: 0 0 .9rem; padding: .95rem 1.15rem 1rem; }
.story-title { font-family: var(--sans); font-size: 1.06rem; line-height: 1.35; }
.story-dash { display: block; font-size: 0; height: .3rem; }
.story-tag { border: 1px solid var(--rule); border-radius: 999px; color: var(--muted);
  font-family: var(--sans); font-size: .66rem; font-weight: 600; letter-spacing: .06em;
  margin-left: .35rem; padding: .05rem .45rem; text-transform: uppercase; vertical-align: .18em;
  white-space: nowrap; }
.sources { display: flex; flex-wrap: wrap; gap: .35rem; margin-top: .65rem; }
.source-chip { background: var(--chip-bg); border-radius: 999px; color: var(--chip-text);
  font-family: var(--sans); font-size: .76rem; line-height: 1.5; max-width: 100%; overflow: hidden;
  padding: .12rem .62rem; text-decoration: none; text-overflow: ellipsis; white-space: nowrap; }
.source-chip:hover { background: var(--accent); color: var(--bg); }
.source-hn { background: var(--hn-bg); color: var(--hn); }
.run-notes { background: var(--surface); border: 1px solid var(--rule); border-radius: .75rem;
  font-size: .92rem; margin-top: 3rem; padding: .4rem 1.3rem 1rem; }
.run-notes > h2 { font-size: 1.2rem; margin: .9rem 0 .2rem; }
.run-notes h3 { color: var(--muted); font-size: .8rem; letter-spacing: .08em; margin-top: 1.6rem;
  text-transform: uppercase; }
.run-notes .sources { margin-top: .35rem; }
.integrity-summary { border: 2px solid var(--rule); padding: .8rem 1rem; border-radius: .4rem; }
.integrity-summary p { margin: .35rem 0; }
.integrity-report .site-header { margin-bottom: .5rem; padding-bottom: .5rem; }
.integrity-report .site-header p { display: none; }
.integrity-report h1 { font-size: 1.65rem; margin: .5rem 0; }
.integrity-report .verdict { margin: .4rem 0; }
.integrity-report .integrity-summary h2 { font-size: 1.1rem; margin: 0 0 .3rem; }
.action-ledger li, .story-change { border-bottom: 1px solid var(--rule); padding: .8rem 0; }
.semantic-audit, .action-ledger, .corpus-health { overflow-wrap: anywhere; }
table { border-collapse: collapse; width: 100%; margin: 1rem 0; font-variant-numeric: tabular-nums; }
caption { text-align: left; font-weight: 700; padding-bottom: .4rem; }
th, td { text-align: left; vertical-align: top; border-bottom: 1px solid var(--rule); padding: .5rem .7rem; }
.table-scroll { overflow-x: auto; }
.table-scroll table { min-width: 45rem; }
details { margin: .7rem 0; } summary { cursor: pointer; }
ins { text-decoration: underline; background: #298a2930; } del { background: #bd393930; }
@media (max-width: 40rem) { body { padding: 1rem .75rem 3rem; } th, td { padding: .4rem; }
  .integrity-summary { padding: .6rem; }
  .briefing-content .story { font-size: 1rem; padding: .8rem .85rem .85rem; }
  .run-notes { padding: .3rem .9rem .8rem; } }
""".strip()


@dataclass(frozen=True)
class BriefingEntry:
    day: date
    disposition: str
    findings_count: int
    findings: tuple[ReviewFinding, ...]
    degraded_sources: tuple[str, ...]
    markdown: str | None
    repair_actions: tuple[dict[str, str], ...] = ()
    generation_failures: tuple[GenerationFailure, ...] = ()
    advisory_findings: tuple[ReviewFinding, ...] = ()
    provenance: Provenance | None = None
    semantic_audit: dict[str, Any] | None = None
    integrity: dict[str, Any] | None = None

    @property
    def slug(self) -> str:
        return self.day.isoformat()


def _entry_from_sidecar(path: Path) -> BriefingEntry:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    entry = _entry_from_payload(payload, source=f"sidecar {path}", expected_slug=path.stem)
    markdown_path = path.with_suffix(".md")
    if entry.disposition in PAGE_DISPOSITIONS and not markdown_path.is_file():
        raise ValueError(
            f"{entry.disposition} sidecar {path} requires matching Markdown {markdown_path.name}"
        )
    markdown_bytes = markdown_path.read_bytes() if entry.disposition in PAGE_DISPOSITIONS else None
    _verify_published_artifact(entry, markdown_bytes, source=f"sidecar {path}")
    markdown = markdown_bytes.decode("utf-8") if markdown_bytes is not None else None
    return BriefingEntry(
        day=entry.day,
        disposition=entry.disposition,
        findings_count=entry.findings_count,
        findings=entry.findings,
        degraded_sources=entry.degraded_sources,
        markdown=markdown,
        repair_actions=entry.repair_actions,
        generation_failures=entry.generation_failures,
        advisory_findings=entry.advisory_findings,
        provenance=entry.provenance,
        semantic_audit=entry.semantic_audit,
        integrity=entry.integrity,
    )


def _verify_published_artifact(entry: BriefingEntry, markdown: bytes | None, *, source: str) -> None:
    """Bind a new public report's published-artifact claim to its exact UTF-8 bytes."""
    if entry.integrity is None:
        return
    published = next((a for a in entry.integrity["artifacts"] if a["id"] == "published"), None)
    if published is not None and (
        markdown is None or hashlib.sha256(markdown).hexdigest() != published["sha256"]
    ):
        raise ValueError(f"{source} integrity published artifact does not match Markdown")


def _parse_finding_entries(
    raw_findings: list[object],
    *,
    label: str,
) -> list[ReviewFinding]:
    """Parse one finding-shaped list, shared by ``findings`` and ``advisory_findings``."""
    findings: list[ReviewFinding] = []
    for index, raw_finding in enumerate(raw_findings):
        finding_source = f"{label} {index}"
        allowed_finding_fields = {frozenset(FINDING_V3_FIELDS)}
        if not finding_has_fields(raw_finding, allowed_finding_fields):
            expected = sorted(FINDING_V3_FIELDS)
            raise ValueError(f"{finding_source} must contain exactly {expected}")
        assert isinstance(raw_finding, dict)
        if not finding_strings_are_valid(raw_finding):
            raise ValueError(f"{finding_source} fields must be non-empty strings")
        if not finding_level_is_valid(raw_finding):
            raise ValueError(f"{finding_source} level must be ERROR or WARN")
        raw_context = raw_finding.get("context")
        valid_context, context = parse_review_context(raw_context)
        if not valid_context:
            raise ValueError(
                f"{finding_source} context must be null or contain exactly "
                f"{sorted(CONTEXT_FIELDS)} as non-empty strings"
            )
        findings.append(
            ReviewFinding(
                level=raw_finding["level"],
                check=raw_finding["check"],
                domain=raw_finding["domain"],
                message=raw_finding["message"],
                section=context.section if context is not None else None,
                headline=context.headline if context is not None else None,
                model_authored=context.model_authored if context is not None else None,
                path=context.path if context is not None else None,
            )
        )
    return findings


def _entry_from_payload(
    payload: object,
    *,
    source: str,
    expected_slug: str | None = None,
) -> BriefingEntry:
    if not isinstance(payload, dict) or set(payload) not in (
        SIDECAR_FIELDS, AUDIT_SIDECAR_FIELDS, LEGACY_SIDECAR_FIELDS,
    ):
        raise ValueError(f"{source} must contain exactly {sorted(SIDECAR_FIELDS)}")

    raw_date = payload["date"]
    disposition = payload["disposition"]
    findings_count = payload["findings_count"]
    raw_findings = payload["findings"]
    degraded_sources = payload["degraded_sources"]
    if not isinstance(raw_date, str):
        raise ValueError(f"{source} date must be an ISO date string")
    try:
        parsed_date = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"{source} date must be an ISO date string") from exc
    if raw_date != parsed_date.isoformat() or (
        expected_slug is not None and expected_slug != raw_date
    ):
        raise ValueError(f"{source} date must match its filename")
    if disposition not in DISPOSITIONS:
        raise ValueError(f"{source} has an invalid disposition")
    if (
        not isinstance(findings_count, int)
        or isinstance(findings_count, bool)
        or findings_count < 0
    ):
        raise ValueError(f"{source} findings_count must be a non-negative integer")
    if (
        not isinstance(degraded_sources, list)
        or any(not isinstance(source, str) or not source.strip() for source in degraded_sources)
        or len(set(degraded_sources)) != len(degraded_sources)
    ):
        raise ValueError(f"{source} degraded_sources must be unique non-empty strings")
    if not isinstance(raw_findings, list):
        raise ValueError(f"{source} findings must be an array")
    findings = _parse_finding_entries(
        raw_findings,
        label=f"{source} finding",
    )
    if disposition == "review_required" and len(findings) != findings_count:
        raise ValueError(f"{source} must include every review-required finding")
    if disposition != "review_required" and findings:
        raise ValueError(f"{source} findings details are allowed only for review_required entries")
    raw_advisory_findings = payload.get("advisory_findings", [])
    if not isinstance(raw_advisory_findings, list):
        raise ValueError(f"{source} advisory findings must be an array")
    advisory_findings = _parse_finding_entries(
        raw_advisory_findings,
        label=f"{source} advisory finding",
    )
    if advisory_findings and disposition not in PAGE_DISPOSITIONS:
        raise ValueError(f"{source} advisory findings require a published disposition")
    generation_failures = parse_generation_failures(payload.get("generation_failures", []))
    if generation_failures and disposition != "blocked":
        raise ValueError(f"{source} generation failures require a blocked disposition")
    repair_actions = parse_repair_actions(payload.get("repair_actions"))
    semantic_audit = parse_semantic_audit(payload.get("semantic_audit"))
    integrity = parse_integrity(payload.get("integrity"), semantic_audit=semantic_audit,
                                disposition=disposition)
    if semantic_audit is not None and disposition not in PAGE_DISPOSITIONS:
        raise ValueError(f"{source} semantic audit requires a public artifact")
    provenance = parse_provenance(payload.get("provenance"))
    if provenance is not None and disposition not in PAGE_DISPOSITIONS:
        raise ValueError(f"{source} provenance requires a public artifact")
    return BriefingEntry(
        day=parsed_date,
        disposition=disposition,
        findings_count=findings_count,
        findings=tuple(findings),
        degraded_sources=tuple(degraded_sources),
        markdown=None,
        repair_actions=repair_actions,
        generation_failures=generation_failures,
        advisory_findings=tuple(advisory_findings),
        provenance=provenance,
        semantic_audit=semantic_audit,
        integrity=integrity,
    )


def _load_history(path: Path) -> list[BriefingEntry]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "entries"}
        or type(payload.get("schema_version")) is not int
        or payload["schema_version"] not in (7, 8, 9)
        or not isinstance(payload.get("entries"), list)
    ):
        raise ValueError(f"history {path} must use schema_version 7, 8 or 9 with an entries array")
    entries: list[BriefingEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(payload["entries"]):
        source = f"history {path} entry {index}"
        fields = {7: LEGACY_HISTORY_FIELDS, 8: AUDIT_SIDECAR_FIELDS | {"markdown"},
                  9: HISTORY_FIELDS}[payload["schema_version"]]
        if not isinstance(raw_entry, dict) or set(raw_entry) != fields:
            raise ValueError(f"{source} must contain exactly {sorted(HISTORY_FIELDS)}")
        metadata = {key: raw_entry[key] for key in SIDECAR_FIELDS if key in raw_entry}
        entry = _entry_from_payload(
            metadata,
            source=source,
        )
        markdown = raw_entry["markdown"]
        if (entry.disposition in PAGE_DISPOSITIONS and not isinstance(markdown, str)) or (
            entry.disposition not in PAGE_DISPOSITIONS and markdown is not None
        ):
            raise ValueError(f"{source} markdown does not match its disposition")
        _verify_published_artifact(
            entry, markdown.encode("utf-8") if markdown is not None else None, source=source,
        )
        if entry.slug in seen:
            raise ValueError(f"history {path} contains duplicate date {entry.slug}")
        seen.add(entry.slug)
        entries.append(
            BriefingEntry(
                day=entry.day,
                disposition=entry.disposition,
                findings_count=entry.findings_count,
                findings=entry.findings,
                degraded_sources=entry.degraded_sources,
                markdown=markdown,
                repair_actions=entry.repair_actions,
                generation_failures=entry.generation_failures,
                advisory_findings=entry.advisory_findings,
                provenance=entry.provenance,
                semantic_audit=entry.semantic_audit,
                integrity=entry.integrity,
            )
        )
    return entries


def _finding_history_payload(finding: ReviewFinding) -> dict[str, object]:
    return {
        "level": finding.level,
        "check": finding.check,
        "domain": finding.domain,
        "message": finding.message,
        "context": (
            {
                "section": finding.section,
                "headline": finding.headline,
                "model_authored": finding.model_authored,
                **({"path": finding.path} if finding.path is not None else {}),
            }
            if finding.section is not None
            and finding.headline is not None
            and finding.model_authored is not None
            else None
        ),
    }


def _history_payload(entries: list[BriefingEntry]) -> dict[str, object]:
    return {
        "schema_version": 9,
        "entries": [
            {
                "semantic_audit": entry.semantic_audit,
                "integrity": entry.integrity,
                "date": entry.slug,
                "disposition": entry.disposition,
                "findings_count": entry.findings_count,
                "findings": [_finding_history_payload(finding) for finding in entry.findings],
                "degraded_sources": list(entry.degraded_sources),
                "repair_actions": [dict(action) for action in entry.repair_actions],
                "generation_failures": [failure.payload() for failure in entry.generation_failures],
                "advisory_findings": [
                    _finding_history_payload(finding) for finding in entry.advisory_findings
                ],
                "provenance": (
                    provenance_payload(entry.provenance) if entry.provenance is not None else None
                ),
                "markdown": entry.markdown,
            }
            for entry in entries
        ],
    }


def _document(title: str, body: str, *, asset_prefix: str = "") -> str:
    escaped_title = html.escape(title)
    escaped_description = html.escape(PROJECT_DESCRIPTION)
    escaped_asset_prefix = html.escape(asset_prefix, quote=True)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f'<meta name="description" content="{escaped_description}">\n'
        f'<meta property="og:title" content="{escaped_title}">\n'
        f'<meta property="og:description" content="{escaped_description}">\n'
        '<meta property="og:type" content="website">\n'
        '<meta name="twitter:card" content="summary">\n'
        f'<link rel="icon" href="{escaped_asset_prefix}favicon-light.png" '
        'type="image/png" sizes="512x512">\n'
        f'<link rel="icon" href="{escaped_asset_prefix}favicon-light.png" '
        'type="image/png" sizes="512x512" media="(prefers-color-scheme: light)">\n'
        f'<link rel="icon" href="{escaped_asset_prefix}favicon-dark.png" '
        'type="image/png" sizes="512x512" media="(prefers-color-scheme: dark)">\n'
        f"<title>{escaped_title}</title>\n<style>{STYLE}</style>\n"
        "</head>\n"
        + ('<body class="integrity-report">\n' if title.startswith("Integrity report") else "<body>\n")
        +
        '<header class="site-header">'
        f'<a class="site-name" href="{PROJECT_URL}">news-briefing</a>'
        f"<p>{escaped_description}</p>"
        "</header>\n"
        f"{body}\n"
        '<footer class="site-footer">Generated by '
        f'<a href="{PROJECT_URL}">news-briefing</a>. '
        "Deterministic checks cover corpus and citation contracts. When available, semantic review "
        "assesses frozen excerpts; its judgments do not prove correctness.</footer>\n"
        "</body>\n</html>\n"
    )


def _verdict(entry: BriefingEntry) -> str:
    noun = "finding" if entry.findings_count == 1 else "findings"
    return f"{entry.disposition.replace('_', ' ').upper()} · {entry.findings_count} {noun}"


def _corpus_health(entry: BriefingEntry) -> str:
    if not entry.degraded_sources:
        return "No source problems reported"
    sources = ", ".join(html.escape(source) for source in entry.degraded_sources)
    return f"No results today from some sources: {sources}"


_CORPUS_HEALTH_HEADING = "### Corpus health"
_CORPUS_HEALTH_EXPLANATIONS = {
    "Coverage was degraded by the source failures or empty responses listed below.",
    "Coverage was degraded by the source failures, empty responses, or undated drops listed below.",
}
_TYPE_LABELS = {
    "rss": ("RSS feed", "RSS feeds"),
    "hacker_news": ("Hacker News search", "Hacker News searches"),
    "reddit": ("subreddit", "subreddits"),
}
_STATUS_SENTENCES = {
    "empty": "returned no items in this day's window",
    "error": "fetch failed",
}
_STATUS_BULLET_LABELS = {
    "empty": "no items in this day's window",
    "error": "fetch failed",
}


def _corpus_health_records(
    fence_body: str,
) -> tuple[list[tuple[str, str, str]], list[tuple[str, str, int]]] | None:
    """Strictly parse the machine block; ``None`` on any shape mismatch."""
    try:
        payload = json.loads(fence_body)
    except ValueError:
        return None
    if (not isinstance(payload, dict)
            or set(payload) not in ({"failed_sources"}, {"failed_sources", "undated_sources"})):
        return None
    sources = payload["failed_sources"]
    if not isinstance(sources, list):
        return None
    keys = ("source_type", "source_id", "status")
    failures: list[tuple[str, str, str]] = []
    for item in sources:
        if not isinstance(item, dict) or set(item) != set(keys):
            return None
        for key in keys:
            value = item[key]
            if not isinstance(value, str) or not value:
                return None
            # JSON \n/\r/\t escapes fit in a single-line fence; splicing them
            # into the page would promote fenced text to active Markdown
            # (injected headings, story anchors, fence desyncs). Anything
            # unrenderable stays fenced.
            if any(char < " " or char == "\x7f" for char in value):
                return None
        failures.append((item["source_type"], item["source_id"], item["status"]))
    undated: list[tuple[str, str, int]] = []
    undated_value = payload.get("undated_sources", [])
    if not isinstance(undated_value, list):
        return None
    seen_undated: set[tuple[str, str]] = set()
    for item in undated_value:
        if (not isinstance(item, dict)
                or set(item) != {"source_type", "source_id", "count"}
                or not isinstance(item.get("source_type"), str)
                or not item["source_type"]
                or not isinstance(item.get("source_id"), str)
                or not item["source_id"]
                or not isinstance(item.get("count"), int)
                or isinstance(item.get("count"), bool)
                or item["count"] <= 0):
            return None
        if any(any(char < " " or char == "\x7f" for char in item[key])
               for key in ("source_type", "source_id")):
            return None
        identity = (item["source_type"], item["source_id"])
        if identity in seen_undated:
            return None
        seen_undated.add(identity)
        undated.append((item["source_type"], item["source_id"], item["count"]))
    if not failures and not undated:
        return None
    return failures, undated


def _join_phrases(phrases: list[str]) -> str:
    if len(phrases) == 1:
        return phrases[0]
    if len(phrases) == 2:
        return f"{phrases[0]} and {phrases[1]}"
    return ", ".join(phrases[:-1]) + f", and {phrases[-1]}"


def _corpus_health_prose(failures: list[tuple[str, str, str]],
                         undated: list[tuple[str, str, int]]) -> list[str]:
    """Grouped Markdown prose for the failure list, in first-appearance order."""
    groups: dict[tuple[str, str], list[str]] = {}
    for source_type, source_id, status in failures:
        groups.setdefault((source_type, status), []).append(source_id)

    def count_phrase(source_type: str, count: int) -> str:
        singular, plural = _TYPE_LABELS.get(source_type, (source_type, source_type))
        return f"{count} {singular if count == 1 else plural}"

    sentences: list[str] = []
    ordered_statuses = list(dict.fromkeys(status for _, status in groups))
    if "empty" in ordered_statuses:
        ordered_statuses.remove("empty")
        ordered_statuses.insert(0, "empty")
    for status in ordered_statuses:
        phrases = [
            count_phrase(source_type, len(ids))
            for (source_type, group_status), ids in groups.items()
            if group_status == status
        ]
        verb = _STATUS_SENTENCES.get(status, status)
        sentences.append(f"{_join_phrases(phrases)} {verb}.")
    if undated:
        item_count = sum(count for _source_type, _source_id, count in undated)
        source_count = len(undated)
        sentences.append(
            f"{source_count} source{'s' if source_count != 1 else ''} dropped "
            f"{item_count} item{'s' if item_count != 1 else ''} without parseable dates.")

    lines = [f"⚠ {' '.join(sentences)}", ""]
    for (source_type, status), ids in groups.items():
        _, plural = _TYPE_LABELS.get(source_type, (source_type, source_type))
        label = plural[0].upper() + plural[1:]
        bullet_status = _STATUS_BULLET_LABELS.get(status, status)
        rendered_ids = ", ".join(
            f'"{source_id}"' if source_type == "hacker_news" else source_id
            for source_id in ids
        )
        lines.append(f"- **{label} — {bullet_status}:** {rendered_ids}")
    for source_type, source_id, count in undated:
        singular, _plural = _TYPE_LABELS.get(source_type, (source_type, source_type))
        rendered_id = f'"{source_id}"' if source_type == "hacker_news" else source_id
        lines.append(
            f"- **{singular[0].upper() + singular[1:]} — undated items dropped:** "
            f"{rendered_id} ({count})")
    return lines


def _humanize_corpus_health(markdown: str) -> str:
    """Replace the machine-readable corpus health block with grouped prose.

    Page rendering only — history.json keeps the raw machine block. The parse
    is strict and fail-closed: any deviation from the exact renderer-emitted
    shape (heading, fixed explanation line, blank line, single-line ```json
    fence) leaves the markdown untouched, so unexpected content is escaped by
    the Markdown parser rather than interpreted.
    """
    lines = markdown.split("\n")
    # Approximate (CommonMark-lite) fence tracking — any ```-prefixed line
    # toggles; every divergence fails safe (the block stays verbatim).
    in_fence = False
    for index, line in enumerate(lines):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or line != _CORPUS_HEALTH_HEADING:
            continue
        block = lines[index + 1 : index + 6]
        if len(block) < 5 or block[4] != "```":
            return markdown
        if block[0] not in _CORPUS_HEALTH_EXPLANATIONS or block[1:3] != ["", "```json"]:
            return markdown
        records = _corpus_health_records(block[3])
        if records is None:
            return markdown
        prose = _corpus_health_prose(*records)
        return "\n".join(lines[: index + 1] + [""] + prose + lines[index + 6 :])
    return markdown


def _markdown_structure_mask(lines: list[str]) -> list[bool]:
    """Identify lines that can safely act as Markdown structure."""
    outside: list[bool] = []
    fence_char: str | None = None
    fence_length = 0
    fence = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
    for line in lines:
        match = fence.match(line)
        if fence_char is None:
            outside.append(True)
            if match is not None:
                marker, info = match.groups()
                if marker[0] != "`" or "`" not in info:
                    fence_char = marker[0]
                    fence_length = len(marker)
            continue
        outside.append(False)
        if (
            match is not None
            and match.group(1)[0] == fence_char
            and len(match.group(1)) >= fence_length
            and not match.group(2).strip()
        ):
            fence_char = None
            fence_length = 0
    return outside


# Excerpt-bounded quality heuristics stay in run artifacts and manifests; the
# reader page omits their rows from the Run outcome warning list.
_REPORT_ONLY_QUALITY_CHECKS = frozenset(QUALITY_CHECKS - ADVISORY_QUALITY_CHECKS)
_RUN_OUTCOME_HEADING = "### Run outcome"
_WARNING_ROW = re.compile(r"^- WARN \[[a-z_]+/([a-z_]+)\] — ")


def _omit_report_only_warnings(markdown: str) -> str:
    """Drop report-only heuristic rows from the Run outcome warning list.

    A list left empty reads "None", matching the renderer's empty-list form.
    """
    lines = markdown.split("\n")
    structural = _markdown_structure_mask(lines)
    output: list[str] = []
    in_run_outcome = False
    warnings_start: int | None = None
    dropped = False

    def close_warnings() -> None:
        nonlocal warnings_start, dropped
        if warnings_start is not None and dropped and not any(
            line.startswith("- ") for line in output[warnings_start:]
        ):
            output.insert(warnings_start, "None")
        warnings_start = None
        dropped = False

    for index, line in enumerate(lines):
        if structural[index] and (line.startswith("### ") or (in_run_outcome and line.startswith("**"))):
            close_warnings()
            if line.startswith("### "):
                in_run_outcome = line == _RUN_OUTCOME_HEADING
            elif line == "**Warnings**":
                warnings_start = len(output) + 1
        elif warnings_start is not None and structural[index]:
            match = _WARNING_ROW.match(line)
            if match is not None and match.group(1) in _REPORT_ONLY_QUALITY_CHECKS:
                dropped = True
                continue
        output.append(line)
    close_warnings()
    return "\n".join(output)


def _autolink_citation(match: re.Match[str]) -> str:
    """Wrap one 🔗 citation URL, keeping sentence punctuation out of the href.

    The checker grounds citation URLs after stripping trailing punctuation
    (eval_briefing._clean_link_url), so the href must stop at the same
    boundary or a grounded "🔗 url." would link to a dead destination.
    """
    url = match.group(2)
    cleaned = eval_briefing._clean_link_url(url)
    return f"{match.group(1)}<{cleaned}>{url[len(cleaned):]}"


def _is_web_link(url: str) -> bool:
    """Restrict rendered links to the http(s) forms the checker detects.

    Commonmark autolinks accept any scheme (<ftp://…>, <mailto:…>) and
    markdown-it's default validateLink blocks only a small script blocklist;
    eval_briefing's destination grammar detects http(s), protocol-relative,
    and www. spellings, so everything else must render inert to keep the
    renderer and the checker in lockstep.
    """
    return url.lower().startswith(("http://", "https://"))


def _render_markdown(markdown: str) -> str:
    """Render untrusted Markdown with only code-owned citation links live."""
    markdown_it = importlib.import_module("markdown_it")
    # linkify is deliberately OFF: it would turn any bare domain a model wrote
    # into prose (e.g. "attacker.com") into a live link the corpus never
    # grounded. Only the code-owned citation URLs become links, by wrapping
    # each "🔗 …" destination in a commonmark autolink below; arbitrary prose
    # renders inert, and validateLink refuses the non-http(s) schemes the
    # checker's destination grammar does not detect.
    parser = markdown_it.MarkdownIt("commonmark", {"html": False})
    parser.validateLink = _is_web_link
    public_markdown = markdown or ""
    public_markdown = _omit_report_only_warnings(public_markdown)
    public_markdown = _humanize_corpus_health(public_markdown)
    public_markdown = _CITATION_AUTOLINK.sub(_autolink_citation, public_markdown)
    # With html disabled, story anchor comments would otherwise render as text.
    lines = [
        line for line in public_markdown.splitlines() if STORY_ANCHOR.match(line) is None
    ]
    return str(parser.render("\n".join(lines)))


# Page decoration below runs on markdown-it output, after untrusted text has
# been escaped and only code-owned citation autolinks are live. Each pattern
# matches the exact shape the renderer emits; anything else is left as plain
# rendered Markdown, so a mismatch costs styling, never content or links.
_CITATION_ANCHOR = re.compile(r'🔗\s*(HN:\s*)?<a href="([^"<>]*)">[^<]*</a>')
_CITATION_RUN = re.compile(r'(?:🔗\s*(?:HN:\s*)?<a href="[^"<>]*">[^<]*</a>\s*)+')
_STORY_OPENING = re.compile(
    r"<p><strong>([^<]*)</strong>(?: <em>\(([^<()]*)\)</em>)?((?: \[[^\]<]*\])*) — "
)
_STORY_LITERAL_TAG = re.compile(r" \[([^\]<]*)\]")
_GROUP_LABEL = re.compile(r"<p><strong>([^<]*)</strong></p>")
_SLOT_COUNT = re.compile(r" \(\d+ slots?\)$")
_TOPIC_HEADING = re.compile(r"<h2>([^<]*)</h2>")
_RUN_NOTES_START = re.compile(
    r"(?:<hr ?/?>\s*)?<h3>(?:Excluded Topics|Corpus health|Run outcome)\b"
)
_LEADING_RULE = re.compile(r"^<hr ?/?>\s*")
_CORPUS_WINDOW = re.compile(r"<p>Corpus window: (\S+) → (\S+)</p>")
_SOURCE_HINT_LIMIT = 32
# Fixed English names: strftime's %a/%b/%A follow LC_TIME, and pages declare lang="en".
_WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _source_parts(href: str) -> tuple[str, str]:
    """Return the display host and last path segment of an escaped href."""
    try:
        parts = urlsplit(html.unescape(href))
        host = parts.hostname or ""
    except ValueError:
        return "", ""
    segments = [segment for segment in parts.path.split("/") if segment]
    hint = unquote(segments[-1]) if segments else ""
    hint = re.sub(r"\.(?:s?html?|php|aspx?)$", "", hint)
    hint = "".join(char for char in hint if char.isprintable())
    if len(hint) > _SOURCE_HINT_LIMIT:
        hint = hint[: _SOURCE_HINT_LIMIT - 1] + "…"
    return host.removeprefix("www."), hint


def _source_chips(run: re.Match[str]) -> str:
    """Replace one run of 🔗 citation links with labelled source chips.

    The href is markdown-it's escaped attribute value, reused unchanged; only
    the visible text changes from the full URL to its host. Hosts cited more
    than once in the same run also show their last path segment so readers can
    tell the links apart.
    """
    citations = [
        (match.group(1) is not None, match.group(2), *_source_parts(match.group(2)))
        for match in _CITATION_ANCHOR.finditer(run.group(0))
    ]
    article_hosts = [host for is_hn, _, host, _ in citations if not is_hn]
    chips = []
    for is_hn, href, host, hint in citations:
        if is_hn:
            label, css = "HN discussion", "source-chip source-hn"
        else:
            label, css = host or "source", "source-chip"
            if host and hint and article_hosts.count(host) > 1:
                label = f"{host} · {hint}"
        chips.append(f'<a class="{css}" href="{href}" title="{href}">{html.escape(label)}</a>')
    return f'<span class="sources">{"".join(chips)}</span>'


def _story_opening(match: re.Match[str]) -> str:
    tags = [match.group(2)] if match.group(2) else []
    tags += _STORY_LITERAL_TAG.findall(match.group(3))
    rendered_tags = "".join(f' <span class="story-tag">{tag}</span>' for tag in tags)
    return (
        f'<p class="story"><strong class="story-title">{match.group(1)}</strong>'
        f'{rendered_tags}<span class="story-dash"> — </span>'
    )


def _group_label(match: re.Match[str]) -> str:
    return f'<p class="group-label"><strong>{_SLOT_COUNT.sub("", match.group(1))}</strong></p>'


def _clock_label(moment: datetime) -> str:
    hour = moment.hour % 12 or 12
    meridiem = "AM" if moment.hour < 12 else "PM"
    return f"{_MONTHS[moment.month - 1]} {moment.day}, {hour}:{moment.minute:02d} {meridiem}"


def _corpus_window(match: re.Match[str]) -> str:
    try:
        start = datetime.fromisoformat(html.unescape(match.group(1)))
        end = datetime.fromisoformat(html.unescape(match.group(2)))
    except ValueError:
        return match.group(0)
    if start.tzinfo is None or end.tzinfo is None:
        return match.group(0)
    start, end = start.astimezone(timezone.utc), end.astimezone(timezone.utc)
    return (
        '<p class="corpus-window">Covers news from '
        f'<time datetime="{match.group(1)}">{_clock_label(start)}</time> to '
        f'<time datetime="{match.group(2)}">{_clock_label(end)}</time> UTC</p>'
    )


def _anchor_id(label: str, used: set[str]) -> str:
    base = "topic-" + (re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "section")
    anchor, suffix = base, 2
    while anchor in used:
        anchor, suffix = f"{base}-{suffix}", suffix + 1
    used.add(anchor)
    return anchor


def _decorate_briefing(rendered: str, entry: BriefingEntry) -> str:
    """Add story cards, source chips, a section bar, and a run-notes panel."""
    rendered = _CITATION_RUN.sub(_source_chips, rendered)
    rendered = _CORPUS_WINDOW.sub(_corpus_window, rendered, count=1)
    notes_start = _RUN_NOTES_START.search(rendered)
    news = rendered[: notes_start.start()] if notes_start else rendered
    notes = rendered[notes_start.start():] if notes_start else ""
    news = _STORY_OPENING.sub(_story_opening, news)
    news = _GROUP_LABEL.sub(_group_label, news)

    pieces = re.split(r"(?=<h2>)", news)
    masthead, sections, nav_links = pieces[0], [], []
    used: set[str] = set()
    for piece in pieces[1:]:
        heading = _TOPIC_HEADING.match(piece)
        if heading is None:
            sections.append(piece)
            continue
        anchor = _anchor_id(html.unescape(heading.group(1)), used)
        count = piece.count('<p class="story">')
        nav_links.append(
            f'<li><a href="#{anchor}">{heading.group(1)}'
            f'<span class="count">{count}</span></a></li>'
        )
        sections.append(f'<section class="topic" id="{anchor}">{piece}</section>\n')
    if notes:
        nav_links.append('<li><a href="#run-notes">Behind this briefing</a></li>')
        notes = (
            '<section class="run-notes" id="run-notes" aria-labelledby="run-notes-title">'
            '<h2 id="run-notes-title">Behind this briefing</h2>'
            f"{_LEADING_RULE.sub('', notes, count=1)}"
            "</section>\n"
        )
    story_count = news.count('<p class="story">')
    dateline = f"{_WEEKDAYS[entry.day.weekday()]} edition"
    if story_count:
        dateline += f" · {_count_label(story_count, 'story')}"
    nav = (
        f'<nav class="section-nav" aria-label="Sections"><ul>{"".join(nav_links)}</ul></nav>\n'
        if len(nav_links) > 1 else ""
    )
    return f'<p class="dateline">{dateline}</p>\n{masthead}{nav}{"".join(sections)}{notes}'


def _history_nav(entries: list[BriefingEntry], current: BriefingEntry) -> str:
    links = []
    newest = entries[0]
    for entry in entries:
        escaped_date = html.escape(entry.slug)
        weekday = _WEEKDAYS[entry.day.weekday()][:3]
        day = (
            f'<time datetime="{escaped_date}"><span class="weekday">{weekday}</span>'
            f'<span class="monthday">{_MONTHS[entry.day.month - 1]} {entry.day.day}</span></time>'
        )
        if entry.slug == current.slug:
            label = f'<strong aria-current="date">{day}</strong>'
        elif entry.slug == newest.slug:
            label = f'<a href="index.html">{day}</a>'
        else:
            label = f'<a href="{escaped_date}.html">{day}</a>'
        links.append(f"<li>{label}</li>")
    return (
        '<nav class="history-nav" aria-label="The seven most recent briefings">'
        '<p class="history-label">Latest 7 briefings</p>'
        f"<ul>{''.join(links)}</ul>"
        "</nav>"
    )


def _status_chip(entry: BriefingEntry) -> str:
    tone = "status-ok"
    if entry.disposition == "ready" and entry.repair_actions:
        n = len(entry.repair_actions)
        label = f"⚠ Published after automated repair ({n} {'action' if n == 1 else 'actions'})"
        tone = "status-warn"
    elif entry.disposition == "ready":
        label = "✓ Contract checks passed"
    elif entry.disposition == "review_required":
        label = "🔍 Review required"
        tone = "status-bad"
    else:
        label = "✖ Not published"
        tone = "status-bad"
    suffixes = []
    if entry.advisory_findings:
        n = len(entry.advisory_findings)
        suffixes.append(f"{n} advisory {'note' if n == 1 else 'notes'}")
    if entry.degraded_sources:
        suffixes.append("no results today from some sources")
    if suffixes:
        label += " · " + " · ".join(suffixes)
        if tone == "status-ok":
            tone = "status-warn"
    report_href = f"reports/{html.escape(entry.slug)}.html"
    return f'<p class="status-chip {tone}"><a href="{report_href}">{label}</a></p>'


def _briefing_layout(rendered_markdown: str, status_chip: str) -> str:
    """Place publication metadata between the title and the news sections."""
    title_end = rendered_markdown.find("</h1>")
    if title_end < 0:
        return f"{status_chip}{rendered_markdown}"
    title_end += len("</h1>")
    first_section = rendered_markdown.find("<h2", title_end)
    if first_section < 0:
        return (
            f"{rendered_markdown[:title_end]}\n<hr>\n{status_chip}"
            f"{rendered_markdown[title_end:]}<hr>\n"
        )
    return (
        f"{rendered_markdown[:title_end]}\n<hr>\n{status_chip}"
        f"{rendered_markdown[title_end:first_section]}<hr>\n"
        f"{rendered_markdown[first_section:]}"
    )


def _generation_failure_notice(entry: BriefingEntry) -> str:
    if not entry.generation_failures:
        return '<p class="muted">No briefing prose is available for this run.</p>'
    items = "".join(
        f"<li><strong>{html.escape(MODEL_LABELS[failure.model])}:</strong> "
        f"{html.escape(FAILURE_MESSAGES[failure.reason])}</li>"
        for failure in entry.generation_failures
    )
    return (
        '<section class="generation-failure">'
        '<h2>Briefing unavailable</h2>'
        '<p>Every model in the fallback chain failed to produce a briefing that passed '
        'publication checks. No briefing was published for this run.</p>'
        f'<ul>{items}</ul>'
        '<p>A future retry may recover this briefing.</p></section>'
    )


def _entry_body(entry: BriefingEntry) -> str:
    status_chip = _status_chip(entry)
    if entry.disposition == "review_required":
        report_href = f"reports/{html.escape(entry.slug)}.html"
        briefing = (
            f"<h1>Daily briefing — {html.escape(entry.slug)}</h1>"
            f"<hr>{status_chip}<hr>"
            "<p>This day’s briefing did not pass automated checks and is withheld.</p>"
            f'<p>See the <a href="{report_href}">integrity report</a> for details.</p>'
        )
    elif entry.markdown is not None:
        rendered_markdown = _render_markdown(entry.markdown)
        briefing = _decorate_briefing(_briefing_layout(rendered_markdown, status_chip), entry)
    else:
        briefing = (
            f"<h1>Daily briefing — {html.escape(entry.slug)}</h1>"
            f"<hr>{status_chip}<hr>"
            f"{_generation_failure_notice(entry)}"
        )
    return f'<article class="briefing-content">{briefing}</article>'


def _render_index(entries: list[BriefingEntry]) -> str:
    if not entries:
        return _document(
            "Daily news briefing",
            "<h1>Daily news briefing</h1><p class=\"muted\">No runs are available yet.</p>",
        )
    newest = entries[0]
    body = _history_nav(entries, newest) + _entry_body(newest)
    return _document(f"Daily briefing — {newest.slug}", body)


def _render_briefing(entry: BriefingEntry, entries: list[BriefingEntry]) -> str:
    return _document(
        f"Daily briefing — {entry.slug}",
        _history_nav(entries, entry) + _entry_body(entry),
    )


def _count_label(n: int, noun: str) -> str:
    if n == 1:
        return f"{n} {noun}"
    if noun.endswith("y") and noun[-2:-1] not in ("a", "e", "i", "o", "u"):
        return f"{n} {noun[:-1]}ies"
    return f"{n} {noun}s"


def _provenance_line(provenance: Provenance) -> str:
    """Compact per-run generation summary for the integrity report only.

    Model identifiers and counts only — the same public-facing content the
    provenance object carries, never prompt text, corpus text, or a URL. The
    reader-facing page never calls this.
    """
    segments = [f"Generated by {provenance.provider} / {provenance.model}"]
    if provenance.attempt_count > 1:
        segments.append(f"fallback position {provenance.attempt_index} of {provenance.attempt_count}")
    segments.append(_count_label(provenance.selection_corrections, "selection correction"))
    segments.append(_count_label(provenance.prose_corrections, "prose correction"))
    segments.append(_count_label(provenance.repair_action_count, "total repair action"))
    return (f'<p class="muted provenance">{html.escape(", ".join(segments))}.</p>'
            f'<p class="muted provenance">Prompt SHA-256: {html.escape(provenance.prompt_sha256)}</p>')


def _render_report(
    entry: BriefingEntry,
    entries: list[BriefingEntry],
    manifest_dates: frozenset[str] = frozenset(),
) -> str:
    newest = entries[0]
    briefing_href = "index.html" if entry.slug == newest.slug else f"{entry.slug}.html"
    parts = [
        f'<p><a href="../{briefing_href}">← Back to briefing</a></p>',
        f"<h1>Integrity report — {html.escape(entry.slug)}</h1>",
        f'<p class="verdict">{html.escape(_verdict(entry))}</p>',
    ]
    parts.append(_render_integrity(entry))
    if entry.provenance is not None and entry.integrity is None:
        parts.append(_provenance_line(entry.provenance))
    if entry.slug in manifest_dates:
        parts.append(
            '<p class="audit-artifact"><a href="../manifests/'
            f'{html.escape(entry.slug)}.json">Download audit manifest (JSON)</a> — '
            "corpus membership, canonical destinations, provenance, and content hashes; "
            "source-owned titles and excerpts are not included.</p>"
        )
    if entry.generation_failures:
        parts.append(_generation_failure_notice(entry))
    all_clear_note: str | None = None
    if entry.disposition == "ready":
        if entry.findings_count == 0:
            if entry.advisory_findings:
                n = len(entry.advisory_findings)
                all_clear_note = (
                    f"The publication gate passed with {n} advisory {'note' if n == 1 else 'notes'}."
                )
            else:
                all_clear_note = "All deterministic contract checks passed."
            if entry.semantic_audit is None:
                all_clear_note += " Semantic faithfulness was not assessed."
        else:
            # Reachable only if a future actionable check stays WARN-level
            # outside review_required.
            count = entry.findings_count
            all_clear_note = (
                f"{count} actionable {'finding was' if count == 1 else 'findings were'} "
                "recorded."
            )
    if all_clear_note is not None:
        parts.append(f'<p class="muted">{html.escape(all_clear_note)}</p>')
    elif not entry.findings:
        parts.append('<p class="muted">No findings details are available for this disposition. '
                     'A zero count does not mean the checker accepted a candidate.</p>')
    if entry.findings or entry.advisory_findings:
        counts: dict[tuple[str, str], int] = {}
        for label, findings in (("Actionable", entry.findings), ("Advisory", entry.advisory_findings)):
            for finding in findings:
                key = (label, finding.check)
                counts[key] = counts.get(key, 0) + 1
        parts.append('<table><caption>Recorded deterministic findings</caption><thead><tr>'
                     '<th>Category</th><th>Check</th><th>Count</th></tr></thead><tbody>')
        for (label, check), count in counts.items():
            parts.append(f'<tr><td>{label}</td><td>{html.escape(check.replace("_", " "))}</td>'
                         f'<td>{count}</td></tr>')
        parts.append('</tbody></table>')
        parts.append('<ul>')
        for finding in (*entry.findings, *entry.advisory_findings):
            subject = ' — '.join(x for x in (finding.section, finding.headline) if x)
            parts.append(f'<li><strong>{html.escape(finding.check.replace("_", " "))}</strong>: '
                         f'{html.escape(finding.message)} '
                         f'{html.escape(subject)}</li>')
        parts.append('</ul>')
    if entry.semantic_audit is not None:
        parts.append(_render_semantic_audit(entry.semantic_audit, entry.integrity))
    parts.append(_render_integrity_details(entry))
    if entry.repair_actions and entry.disposition in PAGE_DISPOSITIONS:
        items = []
        for action in entry.repair_actions:
            escaped_action = html.escape(action.get("action", ""))
            escaped_path = html.escape(action.get("path", ""))
            escaped_reason = html.escape(action.get("reason", ""))
            items.append(f"<li><strong>{escaped_action}</strong> {escaped_path} — {escaped_reason}</li>")
        parts.append(
            '<section class="repair-log">'
            f"<h2>Automated repair actions ({len(entry.repair_actions)})</h2>"
            f"<ol>{''.join(items)}</ol>"
            "</section>"
        )
    parts.append(
        '<p><a href="../history.json">Full public history and check data (JSON)</a></p>'
    )
    parts.append(
        '<section class="corpus-health">'
        "<h2>Corpus health</h2>"
        f"<p>{_corpus_health(entry)}</p>"
        "</section>"
    )
    return _document(
        f"Integrity report — {entry.slug}",
        "\n".join(parts),
        asset_prefix="../",
    )


def _position_key(position: dict[str, Any]) -> str:
    return json.dumps(position, sort_keys=True)


def _story_anchor(position: dict[str, Any]) -> str:
    return "story-" + hashlib.sha256(_position_key(position).encode()).hexdigest()[:12]


def _reasons(codes: list[str]) -> str:
    return "; ".join(INTEGRITY_REASON_MESSAGES[c] for c in codes)


def _word_diff(old: str, new: str) -> tuple[str, str]:
    left, right = [], []
    before, after = re.findall(r"\S+\s*", old), re.findall(r"\S+\s*", new)
    for operation, i, j, k, end in difflib.SequenceMatcher(
        None, before, after, autojunk=False
    ).get_opcodes():
        x, y = html.escape("".join(before[i:j])), html.escape("".join(after[k:end]))
        left.append(x if operation == "equal" else f"<del>{x}</del>" if x else "")
        right.append(y if operation == "equal" else f"<ins>{y}</ins>" if y else "")
    return "".join(left), "".join(right)


def _score_text(check: dict[str, Any], prefix: str = "") -> str:
    probability = check[prefix + "probability"]
    confirmation = check[prefix + "confirmation_probability"]
    second = "—" if confirmation is None else f"{confirmation:.2f}"
    label = check[prefix + "confirmation_label"] or "unavailable"
    return f"{probability:.2f} / {second} ({html.escape(label.replace('_', ' '))})"


def _render_integrity(entry: BriefingEntry) -> str:
    record = entry.integrity
    if record is None:
        return (
            '<p class="muted">Historical publication decision and phase records unavailable. '
            "Legacy scores and repair statuses do not establish a verified publication decision.</p>"
        )
    decisions = {
        "original_retained": "Original briefing published",
        "repaired_applied": "Accepted repair published",
        "candidate_retained": "Original published; clear candidate retained without application",
        "unpublished": "No briefing published",
        "historical_unknown": "Historical decision unknown",
    }
    parts = [
        '<section class="integrity-summary"><h2>Publication decision</h2>',
        f"<p><strong>{decisions[record['decision']]}</strong></p>",
        f"<p>{html.escape(_reasons(record['reasons']))}</p>",
    ]
    public = entry.disposition in PAGE_DISPOSITIONS
    if public:
        audit = entry.semantic_audit
        if audit:
            outcomes: dict[str, int] = {}
            for topic in audit["topics"]:
                status = topic["repair_status"]
                if status != "unchanged":
                    outcomes[status] = outcomes.get(status, 0) + 1
            if outcomes:
                parts.append('<p><strong>Repair outcomes by story:</strong> ' + '; '.join(
                    f'{count} {status}' for status, count in outcomes.items()) + '.</p>')
            applied = [t for t in audit["topics"] if t["repair_status"] == "applied"]
            changed = sum(t["changed"] != t["original"] or t["removed_evidence_count"] > 0 for t in applied)
            removed = sum(t["removed_evidence_count"] for t in applied)
            repair_published = record["decision"] == "repaired_applied"
            remaining = audit.get("followup_checks", []) if repair_published else audit["checks"]
            flags = sum(c["confirmation_label"] != "not_flagged" for c in remaining)
            parts.append('<p>Published since semantic-review baseline: <strong>'
                         + _count_label(changed, "story") + ' changed; '
                         + _count_label(removed, "source item") + ' removed</strong>.</p>')
            scope = "follow-up assessment" if repair_published else "initial assessment"
            parts.append(f'<p>Published briefing: <strong>{_count_label(flags, "semantic flag")}</strong> '
                         f'({scope}).</p>')
            if not repair_published and "followup_checks" in audit:
                candidate_flags = sum(c["confirmation_label"] != "not_flagged"
                                      for c in audit["followup_checks"])
                parts.append(f'<p>Unpublished candidate: {_count_label(candidate_flags, "semantic flag")} '
                             'in follow-up assessment.</p>')
        gate = "passed" if entry.disposition == "ready" else "review required"
        parts.append(f'<p>Deterministic gate: <strong>{gate}</strong>; '
                     f'{_count_label(entry.findings_count, "actionable finding")} on published artifact.</p>')
        followup_label = "Follow-up review" if record["decision"] == "repaired_applied" else "Candidate follow-up"
        for field, label in (("initial_review", "Initial review"), ("followup_review", followup_label)):
            coverage = record[field]
            if coverage is None:
                parts.append(f'<p>{label}: unavailable / not attempted.</p>')
            else:
                parts.append(f'<p>{label}: <strong>{coverage["status"]}</strong>; '
                             f'questions {coverage["returned"]}/{coverage["planned"]}; '
                             f'confirmations {coverage["confirmation_returned"]}/'
                             f'{coverage["confirmation_required"]}.</p>')
        if any(record[field] is not None and record[field]["status"] != "complete"
               for field in ("initial_review", "followup_review")):
            parts.append('<p><strong>Unassessed questions / missing confirmations unresolved.</strong></p>')
    parts.append("</section>")
    if record["actions"] and public:
        topics = {
            _position_key(t["position"]): t for t in (entry.semantic_audit or {}).get("topics", [])
        }
        parts.append('<section class="action-ledger"><h2>Action ledger</h2><ol>')
        for action in record["actions"]:
            subjects = []
            for position in action["positions"]:
                topic = topics.get(_position_key(position))
                if topic:
                    subjects.append(
                        f'<a href="#{_story_anchor(position)}">{html.escape(topic["original"]["headline"])}</a>'
                    )
            parts.append(
                f'<li id="{action["id"]}"><strong>{html.escape(action["actor"])}: '
                f"{html.escape(action['action'].replace('_', ' '))} — {action['outcome']}</strong> "
                + " · ".join(subjects)
                + f"<p>{html.escape(_reasons(action['reasons']))}</p></li>"
            )
        parts.append("</ol></section>")
    return "\n".join(parts)


def _render_integrity_details(entry: BriefingEntry) -> str:
    record = entry.integrity
    if record is None:
        return ""
    parts: list[str] = []
    if entry.disposition in PAGE_DISPOSITIONS:
        artifacts = {a["id"] for a in record["artifacts"]}
        parts.append('<p>First complete initial prose baseline: '
                     + ('recorded by hash; earlier corrections appear in the ledger.' if 'initial_prose' in artifacts
                        else 'unavailable; change counts do not reconstruct all generation corrections.') + '</p>')
        parts.append('<p>Change counts compare the first ready semantic-review briefing with the published result. '
                     'Actions include code bookkeeping and validation; they are not paid model requests.</p>')
        outcomes: dict[str, int] = {}
        for action in record["actions"]:
            status = action["outcome"]
            outcomes[status] = outcomes.get(status, 0) + 1
        if outcomes:
            parts.append('<p>Recorded action outcomes: ' + '; '.join(
                f'{count} {status}' for status, count in outcomes.items()) + '.</p>')
        for field, label in (
            ("generation", "Original generation"),
            ("repair_generation", "Repair generation"),
        ):
            if record[field] is not None:
                provenance = parse_provenance(record[field])
                if provenance is not None:
                    parts.append(f"<h3>{label}</h3>" + _provenance_line(provenance))
        parts.append("<h2>Recorded phases and costs</h2><ol>")
        for phase in record["phases"]:
            times = " — ".join(x for x in (phase["started_at"], phase["completed_at"]) if x)
            parts.append(
                f"<li>{phase['sequence']}: {phase['phase'].replace('_', ' ')} — "
                f"<strong>{phase['status']}</strong> {html.escape(times)} "
                f"{html.escape(_reasons(phase['reasons']))}</li>"
            )
        parts.append(
            "</ol><p>Phase order records execution; it does not imply measured durations.</p>"
        )
        for cost in record["costs"]:
            spend = (
                "unavailable"
                if cost["reported_cost_usd"] is None
                else f"${cost['reported_cost_usd']:.4f}"
            )
            unknown = (
                "unavailable"
                if cost["unknown_cost_calls"] is None
                else str(cost["unknown_cost_calls"])
            )
            parts.append(
                f"<p>{cost['phase'].replace('_', ' ')}: reported {spend}; unknown billing calls: {unknown}.</p>"
            )
    if record["workflow_run_id"] is not None:
        parts.append(
            f'<p><a href="{PROJECT_URL}/actions/runs/{record["workflow_run_id"]}">Originating workflow</a></p>'
        )
    if record["corpus_health"] is None:
        parts.append("<p>Detailed historical source health unavailable.</p>")
    else:
        parts.append('<h2>Verified source health</h2>')
        messages = {
            "fetch_error": "fetch failed",
            "empty_source": "empty source",
            "no_dated_entries": "no dated entries",
            "no_window_entries": "no entries in date window",
            "entries_filtered": "entries filtered",
            "undated_entries": "some entries undated",
        }
        degraded = [r for r in record["corpus_health"] if r["status"] != "ok" or r["reason"] is not None]
        groups: dict[str, list[str]] = {}
        for row in degraded:
            label = messages.get(row["reason"], row["status"])
            groups.setdefault(label, []).append(f'{row["source_type"]}: {row["source_id"]}')
        if groups:
            parts.append('<ul>')
            for label, sources in groups.items():
                parts.append(f'<li><strong>{html.escape(label)}</strong>: '
                             f'{html.escape("; ".join(sources))}</li>')
            parts.append('</ul>')
        else:
            parts.append('<p>No source problems recorded.</p>')
        parts.append('<details><summary>All verified source counts</summary><ul>')
        for row in record["corpus_health"]:
            parts.append(
                f"<li>{html.escape(row['source_id'])} ({html.escape(row['category'])}): "
                f"{row['status']}; {messages.get(row['reason'], 'healthy')}; "
                f"{row['parsed_entries']} parsed / {row['dated_entries']} dated / "
                f"{row['retained_entries']} retained.</li>"
            )
        parts.append("</ul></details>")
    return "\n".join(parts)


def _render_semantic_audit(audit: dict[str, Any], integrity: dict[str, Any] | None = None) -> str:
    topics = {_position_key(t["position"]): t for t in audit["topics"]}
    checks = audit["checks"]
    categories = (
        ("irrelevant_citation", "Citation irrelevance probability"),
        ("unsafe_grouping", "Story grouping"),
        ("unsupported_claim", "Unsupported claims"),
        ("strengthened_claim", "Overstated claims"),
        ("reversed_claim", "Reversed meaning"),
        ("duplicate", "Duplicates"),
    )
    names = dict(categories)
    action_subjects = {
        _position_key(position)
        for action in (integrity or {}).get("actions", [])
        for position in action["positions"]
    }
    parts = [
        '<section class="semantic-audit"><h2>Jev automated checks and repairs</h2>',
        "<p>Preliminary judgments against frozen excerpts; agreement does not prove correctness.</p>",
    ]
    for key, topic in topics.items():
        related = [
            c
            for c in checks
            if any(_position_key(p) == key for p in c["positions"])
            and c["confirmation_label"] != "not_flagged"
        ]
        new, old = topic["changed"], topic["original"]
        if not related and topic["repair_status"] == "unchanged" and key not in action_subjects:
            continue
        position = topic["position"]
        location = f"{position['section']}, slot {position['index'] + 1}"
        parts.append(
            f'<section class="story-change" id="{_story_anchor(position)}"><h3>{html.escape(location)} — '
            f"{html.escape(old['headline'])}</h3><p><strong>{topic['repair_status']}</strong></p>"
        )
        for check in related:
            parts.append(f"<p>{names[check['check']]}: {_score_text(check)}.</p>")
            if check["check"] == "irrelevant_citation" and check["after_basis"] != "citation_removed":
                links = []
                for url in check["citation_urls"]:
                    host = urlsplit(url).hostname or "source"
                    label = "HN discussion" if host == "news.ycombinator.com" else f"Article · {host}"
                    links.append(f'<a href="{html.escape(url, quote=True)}">{html.escape(label)}</a>')
                parts.append(f'<p>Flagged source item {check["evidence_index"] + 1} (original slot alignment): '
                             + " · ".join(links) + '. No removal established by this finding.</p>')
            if check["after_basis"] == "single_evidence":
                parts.append(
                    "<p>Follow-up grouping not in scope: one source item remains; no model score.</p>"
                )
            elif check["after_probability"] is not None and "followup_checks" not in audit:
                parts.append(f"<p>Recorded legacy follow-up: {_score_text(check, 'after_')}.</p>")
        followup = [
            c
            for c in audit.get("followup_checks", [])
            if any(_position_key(p) == key for p in c["positions"])
        ]
        for check in followup:
            if check["confirmation_label"] != "not_flagged":
                parts.append(
                    f"<p><strong>Follow-up blocker</strong> — {names[check['check']]}: {_score_text(check)}.</p>"
                )
        removed = [
            c
            for c in checks
            if c["after_basis"] == "citation_removed" and _position_key(c["positions"][0]) == key
        ]
        for check in removed:
            outcome = (
                "Removed"
                if topic["repair_status"] == "applied"
                else f"Proposed removal — {topic['repair_status']}"
            )
            causes = []
            if integrity:
                for action in integrity["actions"]:
                    if (
                        action["action"] == "remove_source"
                        and position in action["positions"]
                        and check["evidence_index"] in action["source_indexes"]
                    ):
                        causes.extend(action["reasons"])
            cause = (
                _reasons(list(dict.fromkeys(causes)))
                if causes
                else "Legacy removal cause unavailable"
            )
            links = []
            for url in check["citation_urls"]:
                host = urlsplit(url).hostname or "source"
                label = "HN discussion" if host == "news.ycombinator.com" else f"Article · {host}"
                links.append(
                    f'<del><a href="{html.escape(url, quote=True)}">{html.escape(label)}</a></del>'
                )
            parts.append(
                f"<p>{outcome}: source item {check['evidence_index'] + 1}; "
                + " · ".join(links)
                + f" — {html.escape(cause)}.</p>"
            )
        if new is not None and (new["headline"] != old["headline"] or new["prose"] != old["prose"]):
            old_head, new_head = _word_diff(old["headline"], new["headline"])
            old_prose, new_prose = _word_diff(old["prose"], new["prose"])
            label = "published" if topic["repair_status"] == "applied" else "proposed"
            parts.append(
                "<details><summary>Prose differences — "
                + label
                + "</summary><h4>Original prose</h4>"
                f"<p><strong>{old_head}</strong></p><p>{old_prose}</p>"
                f"<h4>Changed prose — {topic['repair_status']} ({label})</h4>"
                f"<p><strong>{new_head}</strong></p><p>{new_prose}</p></details>"
            )
        parts.append("</section>")
    for stage, rows in (
        ("Initial review", checks),
        ("Follow-up review", audit.get("followup_checks")),
    ):
        if rows is None:
            parts.append("<p>Follow-up statistics unavailable in this historical record.</p>")
            continue
        display_stage = ("Unpublished candidate follow-up" if stage == "Follow-up review"
                         and integrity is not None and integrity["decision"] != "repaired_applied" else stage)
        parts.append(
            f'<h3>{display_stage} statistics</h3><div class="table-scroll" tabindex="0" '
            f'role="region" aria-label="{display_stage} statistics"><table>'
            f'<caption>{display_stage} question scope</caption>'
            '<thead><tr><th scope="col">Check category</th><th scope="col">Threshold</th>'
            '<th scope="col">Total checks</th>'
            '<th scope="col">Below threshold</th><th scope="col">Confirmed</th><th scope="col">Disputed</th>'
            '<th scope="col">Unconfirmed</th></tr></thead><tbody>'
        )
        for kind, label in categories:
            group = [c for c in rows if c["check"] == kind]
            threshold = (
                audit.get("citation_threshold", audit["threshold"])
                if kind == "irrelevant_citation"
                else audit["threshold"]
            )
            if not group:
                field = "initial_review" if stage == "Initial review" else "followup_review"
                coverage = integrity.get(field) if integrity is not None else None
                status = coverage["status"] if coverage is not None else (
                    audit["status"] if stage == "Initial review" else audit["post_status"])
                scope = ("Not in scope — no questions in complete assessment" if status == "complete"
                         else "No results returned — scope unresolved")
                parts.append(f'<tr><th scope="row">{label}</th><td>{threshold:.2f}</td>'
                             f'<td colspan="5">{scope}</td></tr>')
                continue
            counts = "".join(
                f"<td>{sum(c['confirmation_label'] == state for c in group)}</td>"
                for state in ("not_flagged", "confirmed", "disputed", "unconfirmed")
            )
            parts.append(
                f'<tr><th scope="row">{label}</th><td>{threshold:.2f}</td><td>{len(group)}</td>{counts}</tr>'
            )
        parts.append("</tbody></table></div>")
    parts.append(
        "<p>Initial coverage: "
        + html.escape(audit["status"])
        + f"; {len(checks)} of {audit['planned_checks']} questions. "
        f"Omitted pairs: {audit['omitted_duplicate_pairs']}; oversized checks: {audit['skipped_oversized_checks']}. "
        f"Judge: {html.escape(audit['model'])}. Aggregate reported cost: ${audit['reported_cost_usd']:.4f}; "
        f"unknown-cost calls: {audit['unknown_cost_calls']}.</p>"
    )
    parts.append(
        "<details><summary>Definitions and review limits</summary><p>"
        "Higher probabilities indicate more likely problems; "
        "scores at or above the category threshold are flagged. Confirmed means an isolated question also crossed "
        "the threshold; disputed means it did not; unconfirmed means confirmation is unavailable. Partial review "
        "leaves scope unresolved. Complete review covers all declared questions and required confirmations. "
        "Checks assess frozen excerpts only. Applied changes reached publication; candidates and rejected changes "
        "are proposals. Removed evidence and singleton grouping have no invented follow-up score."
        "</p></details></section>"
    )
    return "\n".join(parts)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _publish_audit_manifests(
    corpora_dir: Path | None,
    output_dir: Path,
    newest_entry_date: date | None,
) -> frozenset[str]:
    """Publish text-free manifests derived from validated private corpora.

    Raw corpora never enter the site output. Each input must be a schema-valid
    corpus whose report date matches its canonical filename. Dates outside the 14-day window ending at
    the newest history entry are pruned.
    """
    raw_corpora_out = output_dir / "corpora"
    if raw_corpora_out.exists() or raw_corpora_out.is_symlink():
        _remove_path(raw_corpora_out)
    manifests_out = output_dir / "manifests"
    if manifests_out.exists() or manifests_out.is_symlink():
        _remove_path(manifests_out)
    if newest_entry_date is None or corpora_dir is None:
        return frozenset()

    oldest_kept = newest_entry_date - timedelta(days=13)
    survivors: dict[str, dict[str, Any]] = {}
    for corpus in sorted(corpora_dir.glob("*.json")):
        if not corpus.is_file():
            continue
        try:
            day = date.fromisoformat(corpus.stem)
        except ValueError:
            continue
        slug = day.isoformat()
        if (
            corpus.stem != slug
            or day < oldest_kept
            or day > newest_entry_date
        ):
            continue
        raw = corpus.read_bytes()
        try:
            payload = json.loads(raw)
        except ValueError:
            continue
        if (
            corpus_schema.validate_corpus(payload)
            or payload.get("report_date") != slug
        ):
            continue
        try:
            survivors[slug] = build_audit_manifest(payload, raw)
        except (AssertionError, KeyError, TypeError, ValueError):
            # A historical corpus can satisfy its storage schema yet be
            # unusable by a newer projection. Isolate that date so one bad
            # retained input cannot suppress the rest of the Pages build.
            continue
    if not survivors:
        return frozenset()
    manifests_out.mkdir(parents=True, exist_ok=True)
    for slug, manifest in survivors.items():
        (manifests_out / f"{slug}.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    return frozenset(survivors)


def build_site(
    briefings_dir: Path,
    output_dir: Path,
    prior_history: Path | None = None,
    replace_existing: bool = False,
    corpora_dir: Path | None = None,
    *,
    allow_empty_history: bool = False,
) -> None:
    """Render ready briefings and review-required previews from validated inputs."""
    # The never-silently-truncate-the-archive invariant lives here, on the
    # function that owns archive construction, so every caller (not only the
    # CLI) must opt in before building without prior history.
    if prior_history is None and not allow_empty_history:
        raise ValueError(
            "no prior_history was given; pass allow_empty_history=True to build "
            "without one intentionally, otherwise this silently truncates the "
            "published archive")
    if not briefings_dir.is_dir():
        raise ValueError(f"briefings directory does not exist: {briefings_dir}")
    by_date: dict[str, BriefingEntry] = {}
    for entry in _load_history(prior_history) if prior_history is not None else []:
        prior = by_date.get(entry.slug)
        prior_rank = PUBLICATION_RANK.get(prior.disposition, 0) if prior is not None else -1
        if PUBLICATION_RANK.get(entry.disposition, 0) >= prior_rank:
            by_date[entry.slug] = entry
    for sidecar in briefings_dir.glob("*.json"):
        entry = _entry_from_sidecar(sidecar)
        prior = by_date.get(entry.slug)
        prior_rank = PUBLICATION_RANK.get(prior.disposition, 0) if prior is not None else -1
        entry_rank = PUBLICATION_RANK.get(entry.disposition, 0)
        replace_page = replace_existing and entry.disposition in PAGE_DISPOSITIONS
        if replace_page or entry_rank >= prior_rank:
            # A legacy sidecar can re-present the same published artifact during
            # a rebuild. Retain its verified report metadata only when both the
            # artifact and all non-report publication metadata are unchanged.
            if (prior is not None and entry.integrity is None and prior.integrity is not None
                    and entry.disposition in PAGE_DISPOSITIONS
                    and entry.markdown == prior.markdown
                    and entry.disposition == prior.disposition
                    and entry.findings == prior.findings
                    and entry.findings_count == prior.findings_count
                    and entry.advisory_findings == prior.advisory_findings
                    and entry.provenance == prior.provenance
                    and entry.repair_actions == prior.repair_actions
                    and entry.degraded_sources == prior.degraded_sources
                    and entry.semantic_audit in (None, prior.semantic_audit)):
                entry = replace(entry, integrity=prior.integrity, semantic_audit=prior.semantic_audit)
            by_date[entry.slug] = entry
    entries = sorted(by_date.values(), key=lambda entry: entry.day, reverse=True)
    entries = entries[:7]

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_dates = _publish_audit_manifests(
        corpora_dir,
        output_dir,
        entries[0].day if entries else None,
    )
    for favicon_filename in FAVICON_FILENAMES:
        source = FAVICON_SOURCE_DIR / favicon_filename
        (output_dir / favicon_filename).write_bytes(source.read_bytes())
    for stale_page in output_dir.glob("*.html"):
        stale_page.unlink()
    reports_dir = output_dir / "reports"
    if reports_dir.is_dir():
        for stale_report in reports_dir.glob("*.html"):
            stale_report.unlink()
    reports_dir.mkdir(exist_ok=True)
    (output_dir / "index.html").write_text(_render_index(entries), encoding="utf-8")
    (output_dir / "history.json").write_text(
        json.dumps(_history_payload(entries), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for entry in entries[1:]:
        (output_dir / f"{entry.slug}.html").write_text(
            _render_briefing(entry, entries),
            encoding="utf-8",
        )
    for entry in entries:
        (reports_dir / f"{entry.slug}.html").write_text(
            _render_report(entry, entries, manifest_dates),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("briefings_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    history_source = parser.add_mutually_exclusive_group(required=True)
    history_source.add_argument(
        "--prior-history",
        type=Path,
        help="validated history.json downloaded from the previously deployed site",
    )
    history_source.add_argument(
        "--allow-empty-history",
        action="store_true",
        help=(
            "build without --prior-history intentionally; required when no "
            "history is passed, so a failed history download (which also leaves "
            "--prior-history unset) cannot silently publish a truncated archive"
        ),
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="replace prior-history pages for publishable dates present in briefings_dir",
    )
    parser.add_argument(
        "--corpora-dir",
        type=Path,
        help=(
            "private directory of per-day corpus JSON files from which to publish "
            "text-free manifests under site/manifests"
        ),
    )
    args = parser.parse_args()
    try:
        build_site(
            args.briefings_dir,
            args.output_dir,
            args.prior_history,
            args.replace_existing,
            args.corpora_dir,
            allow_empty_history=args.allow_empty_history,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
