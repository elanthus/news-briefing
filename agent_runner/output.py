"""Structured briefing contract, deterministic validation, and rendering."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, NamedTuple

import briefing_config
import corpus_schema
import eval_briefing
from agent_runner.outcomes import Outcome, classify_outcome, finding_domain


class OutputFinding(NamedTuple):
    level: str
    check: str
    message: str


@dataclass(frozen=True)
class Citation:
    ref: str
    item_ref: str
    category: str
    kind: str
    url: str


@dataclass(frozen=True)
class ModelCorpus:
    document: dict[str, Any]
    citations: dict[str, Citation]


DESTINATION_REDACTION = "[destination omitted; use citation refs]"
MODEL_EXCLUDED_ITEM_FIELDS = frozenset({"url", "discussion", "points", "comments"})


def _redact_text_destinations(value: str) -> str:
    decoded = value
    while True:
        decoded_once = html.unescape(decoded)
        if decoded_once == decoded:
            break
        decoded = decoded_once
    spellings = eval_briefing.url_spellings(decoded)
    if not spellings:
        return value

    redacted = decoded
    for start, _end, spelling, _absolute in reversed(spellings):
        redacted = redacted[:start] + DESTINATION_REDACTION + redacted[start + len(spelling):]
    return redacted


def redact_destinations(value: Any) -> Any:
    """Recursively remove every model-visible web destination spelling."""
    if isinstance(value, str):
        return _redact_text_destinations(value)
    if isinstance(value, list):
        return [redact_destinations(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and _redact_text_destinations(key) != key:
                raise ValueError("model input contains a destination-bearing dictionary key")
            redacted[key] = redact_destinations(item)
        return redacted
    return value


def redact_preview_value(value: Any) -> Any:
    """Redact destinations even in malformed dictionary keys for quarantine artifacts."""
    if isinstance(value, str):
        return _redact_text_destinations(value)
    if isinstance(value, list):
        return [redact_preview_value(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            rendered_key = _redact_text_destinations(key) if isinstance(key, str) else str(key)
            if rendered_key in redacted:
                rendered_key = f"{rendered_key} [duplicate key {index}]"
            redacted[rendered_key] = redact_preview_value(item)
        return redacted
    return value


def project_corpus(corpus: dict[str, Any]) -> ModelCorpus:
    """Replace mutable/source-owned fields with code-owned citation references.

    Hacker News engagement remains in the raw corpus for fetch admission and
    audit, but points and comment counts are mutable snapshots that should not
    become briefing claims.
    """
    projected_categories: dict[str, list[dict[str, Any]]] = {}
    citations: dict[str, Citation] = {}
    item_number = 0
    citation_number = 0
    for category, items in corpus["categories"].items():
        projected_items: list[dict[str, Any]] = []
        for item in items:
            item_number += 1
            item_ref = f"item_{item_number:04d}"
            projected = {
                key: redact_destinations(value)
                for key, value in item.items()
                if key not in MODEL_EXCLUDED_ITEM_FIELDS
            }
            projected["item_ref"] = item_ref
            projected_citations = []
            for key, kind in (("url", "article"), ("discussion", "discussion")):
                url = item.get(key)
                if not isinstance(url, str) or not url:
                    continue
                citation_number += 1
                ref = f"citation_{citation_number:04d}"
                citation = Citation(ref, item_ref, category, kind, url)
                citations[ref] = citation
                projected_citations.append({"ref": ref, "kind": kind})
            projected["citations"] = projected_citations
            projected_items.append(projected)
        projected_categories[category] = projected_items
    document = redact_destinations({
        "schema_version": corpus["schema_version"],
        "generated_at": corpus["generated_at"],
        "cutoff": corpus["cutoff"],
        "window_hours": corpus["window_hours"],
        "categories": projected_categories,
    })
    if not isinstance(document, dict):
        raise AssertionError("projected model corpus must be an object")
    return ModelCorpus(document=document, citations=citations)


def _text_property(max_length: int) -> dict[str, Any]:
    return {
        "type": "string",
        "description": f"A non-empty string of at most {max_length} characters.",
        "minLength": 1,
        "maxLength": max_length,
    }


def _citation_refs() -> dict[str, Any]:
    return {
        "type": "array",
        "description": (
            "One distinct, eligible citation reference per selected corpus item. "
            "The renderer adds code-owned companion destinations, including Hacker "
            "News discussion links."
        ),
        "items": {"type": "string"},
        "minItems": 1,
    }


def build_output_schema(config: briefing_config.BriefingConfig) -> dict[str, Any]:
    """Build a conservative Draft-07-compatible schema for all three providers."""
    section_properties: dict[str, Any] = {}
    excluded_properties: dict[str, Any] = {}
    accountable: list[str] = []
    for section in config.sections:
        topic = {
            "type": "object",
            "properties": {
                "headline": _text_property(300),
                "summary": _text_property(1_500),
                "citation_refs": _citation_refs(),
            },
            "required": ["headline", "summary", "citation_refs"],
            "additionalProperties": False,
        }
        section_properties[section.name] = {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "description": f"At most {section.target_stories} topics.",
                    "items": topic,
                    "minItems": 0,
                    "maxItems": section.target_stories,
                }
            },
            "required": ["topics"],
            "additionalProperties": False,
        }
        if section.excluded_stories:
            accountable.append(section.name)
            excluded = {
                "type": "object",
                "properties": {
                    "headline": _text_property(300),
                    "reason": _text_property(600),
                    "citation_refs": _citation_refs(),
                },
                "required": ["headline", "reason", "citation_refs"],
                "additionalProperties": False,
            }
            excluded_properties[section.name] = {
                "type": "array",
                "description": f"At most {section.excluded_stories} excluded topics.",
                "items": excluded,
                "minItems": 0,
                "maxItems": section.excluded_stories,
            }
    return {
        "type": "object",
        "properties": {
            "schema_version": {"type": "integer", "enum": [1]},
            "sections": {
                "type": "object",
                "properties": section_properties,
                "required": [section.name for section in config.sections],
                "additionalProperties": False,
            },
            "excluded_topics": {
                "type": "object",
                "properties": excluded_properties,
                "required": accountable,
                "additionalProperties": False,
            },
        },
        "required": ["schema_version", "sections", "excluded_topics"],
        "additionalProperties": False,
    }


def _object_fields(
    value: Any,
    *,
    required: set[str],
    where: str,
    findings: list[OutputFinding],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        findings.append(OutputFinding("ERROR", "structured_type", f"{where} must be an object"))
        return None
    unknown = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if unknown:
        findings.append(OutputFinding(
            "ERROR", "structured_unknown_field", f"{where} has unknown fields: {', '.join(unknown)}"
        ))
    if missing:
        findings.append(OutputFinding(
            "ERROR", "structured_missing_field", f"{where} is missing fields: {', '.join(missing)}"
        ))
    return value


def _text(
    value: Any,
    *,
    where: str,
    maximum: int,
    findings: list[OutputFinding],
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        findings.append(OutputFinding("ERROR", "structured_text", f"{where} must be a non-empty string"))
        return None
    if len(value) > maximum:
        findings.append(OutputFinding(
            "ERROR", "structured_text_length", f"{where} exceeds {maximum} characters"
        ))
    if eval_briefing.output_urls(value):
        findings.append(OutputFinding(
            "ERROR", "freeform_url", f"{where} contains a web destination; use citation_refs only"
        ))
    return value


def validate_output(
    output: Any,
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> list[OutputFinding]:
    """Independently validate the owned structured-output contract."""
    findings: list[OutputFinding] = []
    root = _object_fields(
        output,
        required={"schema_version", "sections", "excluded_topics"},
        where="output",
        findings=findings,
    )
    if root is None:
        return findings
    if root.get("schema_version") != 1:
        findings.append(OutputFinding("ERROR", "structured_schema_version", "schema_version must be 1"))
    sections = _object_fields(
        root.get("sections"),
        required={section.name for section in config.sections},
        where="sections",
        findings=findings,
    )
    accountable = {section.name for section in config.sections if section.excluded_stories}
    excluded_topics = _object_fields(
        root.get("excluded_topics"),
        required=accountable,
        where="excluded_topics",
        findings=findings,
    )
    if sections is None or excluded_topics is None:
        return findings

    used_items: dict[str, str] = {}

    def validate_entries(
        entries: Any,
        *,
        section: briefing_config.BriefingSection,
        maximum: int,
        excluded: bool,
    ) -> None:
        label = "excluded_topics" if excluded else "topics"
        where = f"{label}.{section.name}"
        if not isinstance(entries, list):
            findings.append(OutputFinding("ERROR", "structured_type", f"{where} must be an array"))
            return
        if len(entries) > maximum:
            findings.append(OutputFinding(
                "ERROR", "structured_item_limit", f"{where} has {len(entries)} entries; maximum is {maximum}"
            ))
        eligible_categories = set(section.corpus_categories)
        for index, entry in enumerate(entries):
            entry_where = f"{where}[{index}]"
            required = {"headline", "reason", "citation_refs"} if excluded else {
                "headline", "summary", "citation_refs"
            }
            parsed = _object_fields(entry, required=required, where=entry_where, findings=findings)
            if parsed is None:
                continue
            _text(parsed.get("headline"), where=f"{entry_where}.headline", maximum=300, findings=findings)
            prose_key = "reason" if excluded else "summary"
            _text(
                parsed.get(prose_key),
                where=f"{entry_where}.{prose_key}",
                maximum=600 if excluded else 1_500,
                findings=findings,
            )
            refs = parsed.get("citation_refs")
            if not isinstance(refs, list) or not refs:
                findings.append(OutputFinding(
                    "ERROR", "structured_citations", f"{entry_where}.citation_refs must be a non-empty array"
                ))
                continue
            seen_refs: set[str] = set()
            entry_items: set[str] = set()
            for ref in refs:
                if not isinstance(ref, str) or ref not in citations:
                    findings.append(OutputFinding(
                        "ERROR", "unknown_citation_ref", f"{entry_where} contains unknown citation ref {ref!r}"
                    ))
                    continue
                if ref in seen_refs:
                    findings.append(OutputFinding(
                        "ERROR", "duplicate_citation_ref", f"{entry_where} repeats citation ref {ref}"
                    ))
                seen_refs.add(ref)
                citation = citations[ref]
                if citation.category not in eligible_categories:
                    findings.append(OutputFinding(
                        "ERROR",
                        "category_ineligible_ref",
                        f"{entry_where} uses {ref} from ineligible category {citation.category}",
                    ))
                entry_items.add(citation.item_ref)
            for item_ref in entry_items:
                prior = used_items.get(item_ref)
                if prior is not None:
                    findings.append(OutputFinding(
                        "ERROR", "duplicate_item", f"{entry_where} repeats {item_ref}, already used by {prior}"
                    ))
                else:
                    used_items[item_ref] = entry_where

    for section in config.sections:
        section_value = sections.get(section.name)
        parsed_section = _object_fields(
            section_value,
            required={"topics"},
            where=f"sections.{section.name}",
            findings=findings,
        )
        if parsed_section is not None:
            validate_entries(
                parsed_section.get("topics"),
                section=section,
                maximum=section.target_stories,
                excluded=False,
            )
        if section.excluded_stories:
            validate_entries(
                excluded_topics.get(section.name),
                section=section,
                maximum=section.excluded_stories,
                excluded=True,
            )
    return findings


def _date_label(value: str) -> str:
    parsed = datetime.fromisoformat(value)
    return f"{parsed:%B} {parsed.day}, {parsed:%Y}"


def _complete_item_citations(
    refs: list[str],
    citations: dict[str, Citation],
) -> list[Citation]:
    """Expand selected items to every distinct code-owned destination.

    A model chooses evidence items, not presentation details. In particular, an
    HN article and its discussion page are a deterministic pair that the
    renderer owns. Canonical destination deduplication keeps self-posts and
    explicitly supplied companion refs from rendering twice.
    """
    item_order = list(dict.fromkeys(citations[ref].item_ref for ref in refs))
    completed: list[Citation] = []
    seen_destinations: set[str] = set()
    kind_order = {"article": 0, "discussion": 1}
    for item_ref in item_order:
        item_citations = sorted(
            (citation for citation in citations.values() if citation.item_ref == item_ref),
            key=lambda citation: kind_order.get(citation.kind, len(kind_order)),
        )
        for citation in item_citations:
            destination = corpus_schema.canonicalize_url(citation.url)
            if destination in seen_destinations:
                continue
            seen_destinations.add(destination)
            completed.append(citation)
    return completed


def _topic_lines(entry: dict[str, Any], citations: dict[str, Citation]) -> list[str]:
    refs = entry["citation_refs"]
    item_refs = {citations[ref].item_ref for ref in refs}
    consolidated = " *(consolidated)*" if len(item_refs) > 1 else ""
    lines = [f"**{entry['headline']}**{consolidated} — {entry['summary']}"]
    for citation in _complete_item_citations(refs, citations):
        prefix = "HN: " if citation.kind == "discussion" else ""
        lines.append(f"🔗 {prefix}{citation.url}")
    return lines + [""]


def render_briefing(
    output: dict[str, Any],
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
) -> str:
    """Render validated structured output into the existing Markdown contract."""
    lines = [
        f"# Daily Briefing — {_date_label(corpus['generated_at'])}",
        "",
        f"Corpus window: {corpus['cutoff']} → {corpus['generated_at']}",
        "",
    ]
    sections = output["sections"]
    index = 0
    while index < len(config.sections):
        section = config.sections[index]
        if section.group is None:
            lines.extend([f"## {section.name}", ""])
            for entry in sections[section.name]["topics"]:
                lines.extend(_topic_lines(entry, citations))
            index += 1
            continue
        group = section.group
        lines.extend([f"## {group}", ""])
        while index < len(config.sections) and config.sections[index].group == group:
            grouped = config.sections[index]
            lines.extend([f"**{grouped.name} ({grouped.target_stories} slots)**", ""])
            for entry in sections[grouped.name]["topics"]:
                lines.extend(_topic_lines(entry, citations))
            index += 1

    if any(section.excluded_stories for section in config.sections):
        lines.extend(["---", "", "### Excluded Topics (accountability log)", ""])
        excluded = output["excluded_topics"]
        for section in config.sections:
            if not section.excluded_stories:
                continue
            lines.append(f"**{section.name}**")
            for entry in excluded[section.name]:
                rendered_refs = []
                for citation in _complete_item_citations(
                    entry["citation_refs"], citations
                ):
                    prefix = "HN: " if citation.kind == "discussion" else ""
                    rendered_refs.append(f"🔗 {prefix}{citation.url}")
                lines.append(
                    f"- *{entry['headline']}* — {entry['reason']} " + " ".join(rendered_refs)
                )
            lines.append("")

    if corpus.get("errors"):
        failures = [
            {
                "source_type": error["source_type"],
                "source_id": error["source_id"],
                "status": error["status"],
            }
            for error in corpus["errors"]
        ]
        lines.extend([
            "---",
            "",
            "### Corpus health",
            "Coverage was degraded by the source failures or empty responses listed below.",
            "",
            "```json",
            json.dumps({"failed_sources": failures}, ensure_ascii=False, separators=(",", ":")),
            "```",
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _preview_text(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return str(redact_destinations(value.strip()))


def _finding_value(finding: Any, key: str) -> Any:
    if isinstance(finding, dict):
        return finding.get(key)
    return getattr(finding, key, None)


def _preview_entries(
    entries: Any,
    *,
    citations: dict[str, Citation],
    excluded: bool,
) -> list[str]:
    if not isinstance(entries, list):
        return ["_Candidate did not provide an entry list._", ""]
    lines: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            lines.extend(["_Malformed candidate entry omitted._", ""])
            continue
        headline = _preview_text(entry.get("headline"), "[missing headline]")
        prose_key = "reason" if excluded else "summary"
        prose = entry.get(prose_key)
        if excluded and not isinstance(prose, str) and isinstance(entry.get("summary"), str):
            prose = entry["summary"]
            prose_note = " _(candidate supplied this as `summary`, not `reason`)_"
        else:
            prose_note = ""
        rendered_prose = _preview_text(prose, "[missing candidate text]")
        marker = "- " if excluded else ""
        lines.append(f"{marker}**{headline}** — {rendered_prose}{prose_note}")
        refs = entry.get("citation_refs")
        if isinstance(refs, list):
            for ref in refs:
                citation = citations.get(ref) if isinstance(ref, str) else None
                if citation is None:
                    lines.append("  - Unknown citation reference omitted.")
                    continue
                prefix = "HN: " if citation.kind == "discussion" else ""
                lines.append(f"🔗 {prefix}{citation.url}")
        else:
            lines.append("  - No citation references supplied.")
        lines.append("")
    return lines


def render_candidate_preview(
    output: Any,
    corpus: dict[str, Any],
    config: briefing_config.BriefingConfig,
    citations: dict[str, Citation],
    findings: list[Any],
    outcome: Outcome,
) -> str:
    """Render a quarantined best-effort preview without creating a publishable artifact."""
    lines = [
        "# UNPUBLISHED BRIEFING CANDIDATE",
        "",
        "This candidate requires review and was not written to the configured output path.",
        "Unknown citations are omitted and model-authored web destinations are redacted.",
        "",
        f"Candidate date: {_date_label(corpus['generated_at'])}",
        f"Corpus window: {corpus['cutoff']} → {corpus['generated_at']}",
        "",
    ]
    root = output if isinstance(output, dict) else {}
    sections_value = root.get("sections")
    sections: dict[Any, Any] = sections_value if isinstance(sections_value, dict) else {}
    for section in config.sections:
        lines.extend([f"## {section.name}", ""])
        section_value = sections.get(section.name)
        topics = section_value.get("topics") if isinstance(section_value, dict) else None
        lines.extend(_preview_entries(topics, citations=citations, excluded=False))

    lines.extend(["---", "", "### Excluded Topics (candidate)", ""])
    excluded_value = root.get("excluded_topics")
    excluded_topics: dict[Any, Any] = (
        excluded_value if isinstance(excluded_value, dict) else {}
    )
    for section in config.sections:
        if not section.excluded_stories:
            continue
        lines.extend([f"**{section.name}**", ""])
        lines.extend(
            _preview_entries(
                excluded_topics.get(section.name), citations=citations, excluded=True
            )
        )

    return "\n".join(lines).rstrip() + "\n" + render_validation_status(
        findings, corpus, outcome=outcome
    )


def render_validation_status(
    findings: list[Any],
    corpus: dict[str, Any],
    *,
    outcome: Outcome | None = None,
) -> str:
    errors = [
        finding
        for finding in findings
        if _finding_value(finding, "level") == eval_briefing.ERROR
    ]
    warnings = [
        finding
        for finding in findings
        if _finding_value(finding, "level") == eval_briefing.WARN
    ]
    source_issues = corpus.get("errors", [])
    resolved = outcome or classify_outcome(findings, source_issues)
    disposition_label = resolved.disposition.replace("_", " ").upper()
    lines = [
        "",
        "### Run outcome",
        f"**Disposition: {disposition_label}**",
        "",
        f"- Protocol: `{resolved.protocol}`",
        f"- Contract: `{resolved.contract}`",
        f"- Evidence: `{resolved.evidence}`",
        f"- Coverage: `{resolved.coverage}`",
        "",
    ]
    for group, rows in (("Errors", errors), ("Warnings", warnings)):
        lines.append(f"**{group}**")
        if rows:
            lines.extend(
                f"- {_finding_value(row, 'level')} "
                f"[{finding_domain(str(_finding_value(row, 'check')))}/"
                f"{_finding_value(row, 'check')}] — Cause: "
                f"{_preview_text(_finding_value(row, 'message'), '[missing finding detail]')}"
                for row in rows
            )
        else:
            lines.append("None")
        lines.append("")
    lines.append("**Source issues**")
    if source_issues:
        for issue in source_issues:
            lines.append(
                "- "
                f"source_type={issue['source_type']}; source_id={issue['source_id']}; "
                f"status={issue['status']}; error_type={issue['error_type']}; "
                f"message={issue['message']}"
            )
    else:
        lines.append("None")
    return "\n".join(lines).rstrip() + "\n"
