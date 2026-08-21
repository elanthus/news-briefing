#!/usr/bin/env python3
"""Build a dependency-free static archive from publication-gated briefings."""

from __future__ import annotations

import argparse
import hashlib
import html
import importlib
import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

LEGACY_FIELDS = {"date", "disposition", "findings_count", "degraded_sources"}
SIDECAR_FIELDS = LEGACY_FIELDS | {"findings"}
SIDECAR_V4_FIELDS = SIDECAR_FIELDS | {"repair_actions"}
HISTORY_FIELDS = SIDECAR_FIELDS | {"markdown"}
LEGACY_HISTORY_FIELDS = LEGACY_FIELDS | {"markdown"}
FINDING_FIELDS = {"level", "check", "domain", "message"}
FINDING_V3_FIELDS = FINDING_FIELDS | {"context"}
CONTEXT_FIELDS = {"section", "headline", "model_authored"}
CONTEXT_V4_FIELDS = CONTEXT_FIELDS | {"path"}
STORY_ANCHOR = re.compile(r"^<!-- story: ((?:topics|excluded_topics)\..+?\[\d+\]) -->$")
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

STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }
body { margin: 0 auto; max-width: 76rem; padding: 2rem 1.25rem 4rem; }
a { color: inherit; }
ul { list-style: none; padding: 0; }
article { border-top: 1px solid #8886; padding: 1rem 0; }
.history-nav { border-bottom: 1px solid #8886; margin-bottom: 1.5rem; padding-bottom: 1rem; }
.history-nav ul { display: flex; flex-wrap: wrap; gap: .5rem 1rem; margin: .5rem 0 0; }
.verdict { font-weight: 700; }
.muted { color: #777; }
.review-panel { background: #f5a62318; border: 1px solid #d98200; border-radius: .4rem;
  font-size: .88rem; margin: .6rem 0 1rem; padding: .5rem .7rem; }
.review-story { background: #f5a62318; border: 1px solid #d98200; border-radius: .4rem;
  margin: .6rem 0 1rem; padding: .55rem .7rem; }
.review-story-heading { font-size: .95rem; margin: 0 0 .35rem; }
.review-story > p { margin: .1rem 0; }
.review-story .inline-review { background: none; border: 0; border-radius: 0;
  border-top: 2px solid #d9820099; margin: .55rem 0 0; padding: .45rem 0 0; }
.review-panel h2 { font-size: 1rem; margin: 0 0 .15rem; }
.review-panel h3 { font-size: .95rem; margin: 0 0 .15rem; }
.review-panel ol { margin: .2rem 0 0; padding-left: 1.25rem; }
.review-panel li { margin: .2rem 0; padding-left: .1rem; }
.finding-label { font-weight: 700; }
.review-action::before { content: " — "; }
.review-panel details { border-top: 1px solid #d9820066; margin-top: .4rem; padding-top: .3rem; }
.review-panel summary { cursor: pointer; font-weight: 650; }
.briefing-content .review-panel pre { background: #fff8; border: 1px solid #8884; font-size: .8rem;
  margin: .35rem 0 0; max-height: 16rem; overflow: auto; padding: .5rem; white-space: pre-wrap; }
.briefing-content { max-width: 76ch; overflow-wrap: anywhere; }
.briefing-content h1, .briefing-content h2, .briefing-content h3 { line-height: 1.2; }
.briefing-content ul { list-style: disc; padding-left: 1.25rem; }
.briefing-content li { margin: .35rem 0; }
.briefing-content pre { background: #8881; border: 1px solid #8884; overflow-x: auto; padding: 1rem; }
.briefing-content code { background: #8881; border-radius: .2rem; padding: .1rem .25rem; }
.briefing-content pre code { background: none; padding: 0; }
.briefing-content blockquote { border-left: .25rem solid #8886; margin-left: 0; padding-left: 1rem; }
.status-chip { font-size: .88rem; }
.status-chip a { text-decoration: none; }
""".strip()


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


@dataclass(frozen=True)
class BriefingEntry:
    day: date
    disposition: str
    findings_count: int
    findings: tuple[ReviewFinding, ...]
    degraded_sources: tuple[str, ...]
    markdown: str | None
    repair_actions: tuple[dict[str, str], ...] = ()

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
    markdown = (
        markdown_path.read_text(encoding="utf-8")
        if entry.disposition in PAGE_DISPOSITIONS
        else None
    )
    return BriefingEntry(
        day=entry.day,
        disposition=entry.disposition,
        findings_count=entry.findings_count,
        findings=entry.findings,
        degraded_sources=entry.degraded_sources,
        markdown=markdown,
        repair_actions=entry.repair_actions,
    )


REPAIR_ACTION_FIELDS = {"action", "path", "reason"}


def _parse_repair_actions(raw: object) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    actions: list[dict[str, str]] = []
    for item in raw:
        if (
            not isinstance(item, dict)
            or set(item) != REPAIR_ACTION_FIELDS
            or any(not isinstance(item[k], str) for k in REPAIR_ACTION_FIELDS)
        ):
            return ()
        actions.append(item)
    return tuple(actions)


def _entry_from_payload(
    payload: object,
    *,
    source: str,
    expected_slug: str | None = None,
    schema_version: int = 3,
    flexible_findings: bool = True,
) -> BriefingEntry:
    expected_fields = SIDECAR_FIELDS if schema_version >= 2 else LEGACY_FIELDS
    allowed_fields = (
        (expected_fields, SIDECAR_V4_FIELDS)
        if schema_version >= 2
        else (expected_fields,)
    )
    if not isinstance(payload, dict) or set(payload) not in allowed_fields:
        raise ValueError(f"{source} must contain exactly {sorted(expected_fields)}")

    raw_date = payload["date"]
    disposition = payload["disposition"]
    findings_count = payload["findings_count"]
    raw_findings = payload["findings"] if schema_version >= 2 else []
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
    findings: list[ReviewFinding] = []
    if not isinstance(raw_findings, list):
        raise ValueError(f"{source} findings must be an array")
    for index, raw_finding in enumerate(raw_findings):
        finding_source = f"{source} finding {index}"
        allowed_finding_fields = (
            {frozenset(FINDING_FIELDS), frozenset(FINDING_V3_FIELDS)}
            if schema_version >= 3 and flexible_findings
            else {frozenset(FINDING_V3_FIELDS if schema_version >= 3 else FINDING_FIELDS)}
        )
        if not isinstance(raw_finding, dict) or frozenset(raw_finding) not in allowed_finding_fields:
            expected = sorted(FINDING_V3_FIELDS if schema_version >= 3 else FINDING_FIELDS)
            raise ValueError(f"{finding_source} must contain exactly {expected}")
        if any(
            not isinstance(raw_finding[field], str) or not raw_finding[field].strip()
            for field in FINDING_FIELDS
        ):
            raise ValueError(f"{finding_source} fields must be non-empty strings")
        if raw_finding["level"] not in {"ERROR", "WARN"}:
            raise ValueError(f"{finding_source} level must be ERROR or WARN")
        raw_context = raw_finding.get("context")
        if raw_context is not None and (
            not isinstance(raw_context, dict)
            or set(raw_context) not in (CONTEXT_FIELDS, CONTEXT_V4_FIELDS)
            or any(
                not isinstance(raw_context[field], str) or not raw_context[field].strip()
                for field in CONTEXT_FIELDS
            )
        ):
            raise ValueError(
                f"{finding_source} context must be null or contain exactly "
                f"{sorted(CONTEXT_FIELDS)} as non-empty strings"
            )
        raw_path = raw_context.get("path") if isinstance(raw_context, dict) else None
        if raw_path is not None and (not isinstance(raw_path, str) or not raw_path.strip()):
            raw_path = None
        findings.append(
            ReviewFinding(
                level=raw_finding["level"],
                check=raw_finding["check"],
                domain=raw_finding["domain"],
                message=raw_finding["message"],
                section=raw_context["section"] if isinstance(raw_context, dict) else None,
                headline=raw_context["headline"] if isinstance(raw_context, dict) else None,
                model_authored=(
                    raw_context["model_authored"] if isinstance(raw_context, dict) else None
                ),
                path=raw_path,
            )
        )
    if schema_version >= 2 and disposition == "review_required" and len(findings) != findings_count:
        raise ValueError(f"{source} must include every review-required finding")
    if disposition != "review_required" and findings:
        raise ValueError(f"{source} findings details are allowed only for review_required entries")
    repair_actions = _parse_repair_actions(payload.get("repair_actions"))
    return BriefingEntry(
        day=parsed_date,
        disposition=disposition,
        findings_count=findings_count,
        findings=tuple(findings),
        degraded_sources=tuple(degraded_sources),
        markdown=None,
        repair_actions=repair_actions,
    )


def _load_history(path: Path) -> list[BriefingEntry]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "entries"}
        or payload.get("schema_version") not in {1, 2, 3, 4}
        or not isinstance(payload.get("entries"), list)
    ):
        raise ValueError(f"history {path} must use schema_version 1 through 4 with an entries array")
    schema_version = payload["schema_version"]
    entries: list[BriefingEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(payload["entries"]):
        source = f"history {path} entry {index}"
        expected_fields = HISTORY_FIELDS if schema_version >= 2 else LEGACY_HISTORY_FIELDS
        if schema_version >= 4:
            expected_fields = expected_fields | {"repair_actions"}
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_fields:
            raise ValueError(f"{source} must contain exactly {sorted(expected_fields)}")
        if schema_version >= 4:
            metadata_fields = SIDECAR_V4_FIELDS
        elif schema_version >= 2:
            metadata_fields = SIDECAR_FIELDS
        else:
            metadata_fields = LEGACY_FIELDS
        metadata = {key: raw_entry[key] for key in metadata_fields}
        entry = _entry_from_payload(
            metadata,
            source=source,
            schema_version=schema_version,
            flexible_findings=False,
        )
        markdown = raw_entry["markdown"]
        page_dispositions = PAGE_DISPOSITIONS if schema_version >= 2 else {"ready"}
        if (entry.disposition in page_dispositions and not isinstance(markdown, str)) or (
            entry.disposition not in page_dispositions and markdown is not None
        ):
            raise ValueError(f"{source} markdown does not match its disposition")
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
            )
        )
    return entries


def _history_payload(entries: list[BriefingEntry]) -> dict[str, object]:
    return {
        "schema_version": 4,
        "entries": [
            {
                "date": entry.slug,
                "disposition": entry.disposition,
                "findings_count": entry.findings_count,
                "findings": [
                    {
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
                    for finding in entry.findings
                ],
                "degraded_sources": list(entry.degraded_sources),
                "repair_actions": [dict(action) for action in entry.repair_actions],
                "markdown": _strip_legacy_preview_banner(entry.markdown),
            }
            for entry in entries
        ],
    }


def _document(title: str, body: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{html.escape(title)}</title>\n<style>{STYLE}</style>\n"
        f"</head>\n<body>\n{body}\n</body>\n</html>\n"
    )


def _verdict(entry: BriefingEntry) -> str:
    noun = "finding" if entry.findings_count == 1 else "findings"
    return f"{entry.disposition.replace('_', ' ').upper()} · {entry.findings_count} {noun}"


def _corpus_health(entry: BriefingEntry) -> str:
    if not entry.degraded_sources:
        return "Healthy — no degraded sources reported"
    sources = ", ".join(html.escape(source) for source in entry.degraded_sources)
    return f"Degraded sources: {sources}"


LEGACY_PREVIEW_BANNER = (
    "# UNPUBLISHED BRIEFING CANDIDATE\n\n"
    "This candidate requires review and was not written to the configured output path.\n"
    "Unknown citations are omitted and model-authored web destinations are redacted.\n\n"
)
DESTINATION_REDACTION = "[destination omitted; use citation refs]"
EXCLUDED_CONTEXT_PREFIX = "Excluded Topics: "


def _strip_legacy_preview_banner(markdown: str | None) -> str | None:
    if markdown is None:
        return None
    normalized = markdown.replace("\r\n", "\n")
    if normalized.startswith(LEGACY_PREVIEW_BANNER):
        normalized = normalized.removeprefix(LEGACY_PREVIEW_BANNER)
    outcome_marker = "\n### Run outcome\n"
    if outcome_marker in normalized:
        normalized = normalized.split(outcome_marker, 1)[0].rstrip() + "\n"
    return normalized


def _topic_headline(line: str) -> str | None:
    candidate = line[2:] if line.startswith("- ") else line
    if not candidate.startswith("**"):
        return None
    closing = candidate.find("** — ", 2)
    return candidate[2:closing] if closing >= 2 else None


def _section_subheading(line: str) -> str | None:
    """Legacy section attribution for findings that predate story anchors."""
    if not line.startswith("**") or not line.endswith("**") or " — " in line:
        return None
    label = line[2:-2].strip()
    slots = re.fullmatch(r"(.+) \(\d+ slots\)", label)
    return slots.group(1) if slots is not None else label


def _finding_matches(
    finding: ReviewFinding,
    section: str,
    headline: str,
    current_path: str | None = None,
) -> bool:
    if finding.path is not None and current_path is not None:
        return finding.path == current_path
    if finding.section is not None or finding.headline is not None:
        return finding.section == section and finding.headline == headline
    return finding.message.startswith(f"{section}: {headline!r}")


def _render_markdown(
    markdown: str,
    findings: tuple[ReviewFinding, ...] = (),
) -> tuple[str, frozenset[int]]:
    """Render untrusted Markdown and place story-specific findings beside the story."""
    markdown_it = importlib.import_module("markdown_it")
    parser = markdown_it.MarkdownIt("commonmark", {"html": False, "linkify": True})
    parser.enable("linkify")
    public_markdown = _strip_legacy_preview_banner(markdown) or ""
    lines: list[str] = []
    replacements: dict[str, str] = {}
    matched: set[int] = set()
    section = ""
    excluded_section = False
    current_path: str | None = None
    pending_end_marker: str | None = None
    digest = hashlib.sha256(public_markdown.encode("utf-8")).hexdigest()[:16]
    for line in public_markdown.splitlines():
        anchor_match = STORY_ANCHOR.match(line)
        if anchor_match is not None:
            current_path = anchor_match.group(1)
            continue
        headline = _topic_headline(line)
        if pending_end_marker is not None and (
            line.startswith("## ") or headline is not None
        ):
            lines.extend(["", pending_end_marker, ""])
            pending_end_marker = None
        # Section/subheading tracking only serves findings without a structured
        # path (pre-v4 sidecars and histories); anchored findings match by path.
        if line.startswith("### Excluded Topics"):
            excluded_section = True
        elif line.startswith("## "):
            excluded_section = False
            section = line.removeprefix("## ").strip()
        subheading = _section_subheading(line)
        if subheading is not None:
            section = (
                f"{EXCLUDED_CONTEXT_PREFIX}{subheading}"
                if excluded_section
                else subheading
            )
        if headline is None:
            lines.append(line)
            current_path = None
            continue
        indices = [
            index
            for index, finding in enumerate(findings)
            if index not in matched
            and _finding_matches(finding, section, headline, current_path)
        ]
        current_path = None
        if not indices:
            lines.append(line)
            continue
        marker_id = len(replacements)
        start_marker = f"INLINE_REVIEW_START_{digest}_{marker_id}"
        end_marker = f"INLINE_REVIEW_END_{digest}_{marker_id}"
        while start_marker in public_markdown or end_marker in public_markdown:
            start_marker += "_"
            end_marker += "_"
        original = next(
            (
                findings[index].model_authored
                for index in indices
                if findings[index].model_authored is not None
            ),
            None,
        )
        if DESTINATION_REDACTION not in line:
            original = None
        count = len(indices)
        box_heading = f"Review required · {count} {'finding' if count == 1 else 'findings'}"
        replacements[start_marker] = (
            '<section class="review-story">\n'
            f'<h3 class="review-story-heading">{html.escape(box_heading)}</h3>\n'
        )
        replacements[end_marker] = _render_review_panel(
            tuple(findings[index] for index in indices),
            inline=True,
            original=original,
            show_heading=False,
        ) + "</section>\n"
        matched.update(indices)
        lines.extend([start_marker, "", line])
        pending_end_marker = end_marker
    if pending_end_marker is not None:
        lines.extend(["", pending_end_marker])
    rendered = str(parser.render("\n".join(lines)))
    for marker, panel in replacements.items():
        rendered = rendered.replace(f"<p>{marker}</p>\n", panel, 1)
    return rendered, frozenset(matched)


def _history_nav(entries: list[BriefingEntry], current: BriefingEntry) -> str:
    links = []
    newest = entries[0]
    for entry in entries:
        escaped_date = html.escape(entry.slug)
        if entry.slug == current.slug:
            label = f'<strong aria-current="date">{escaped_date}</strong>'
        elif entry.slug == newest.slug:
            label = f'<a href="index.html">{escaped_date}</a>'
        else:
            label = f'<a href="{escaped_date}.html">{escaped_date}</a>'
        links.append(f"<li>{label}</li>")
    return (
        '<nav class="history-nav" aria-label="Briefings from the past seven days">'
        "<strong>Past 7 days</strong>"
        f"<ul>{''.join(links)}</ul>"
        "</nav>"
    )


def _status_chip(entry: BriefingEntry) -> str:
    if entry.disposition == "ready" and entry.repair_actions:
        n = len(entry.repair_actions)
        label = f"⚠ Published after automated repair ({n} {'action' if n == 1 else 'actions'})"
    elif entry.disposition == "ready":
        label = "✓ Verified"
    elif entry.disposition == "review_required":
        label = "🔍 Review required"
    else:
        label = "✖ Not published"
    suffixes = []
    if entry.degraded_sources:
        suffixes.append("sources degraded")
    if suffixes:
        label += " · " + " · ".join(suffixes)
    report_href = f"reports/{html.escape(entry.slug)}.html"
    return f'<p class="status-chip"><a href="{report_href}">{label}</a></p>'


def _entry_body(entry: BriefingEntry) -> str:
    review_panel = ""
    if entry.markdown is not None:
        rendered_markdown, matched = _render_markdown(entry.markdown, entry.findings)
        unmatched = tuple(
            finding for index, finding in enumerate(entry.findings) if index not in matched
        )
        if entry.disposition == "review_required" and unmatched:
            review_panel = _render_review_panel(unmatched)
        briefing = f'{review_panel}<article class="briefing-content">{rendered_markdown}</article>'
    else:
        briefing = '<p class="muted">No briefing prose is available for this run.</p>'
    return (
        f"<h1>Briefing for {html.escape(entry.slug)}</h1>"
        f"{_status_chip(entry)}"
        f"{briefing}"
    )


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


def _review_action(finding: ReviewFinding) -> str:
    actions = {
        "unsupported_figure": (
            "Verify the figure against the cited source; correct or remove it if the source does not support it."
        ),
        "unsupported_quotation": (
            "Verify the quotation against the cited source; correct or remove it if it is not supported."
        ),
        "claim_exceeds_evidence": (
            "Compare the summary with the cited excerpt and shorten or remove claims the excerpt does not support."
        ),
    }
    if finding.check in actions:
        return actions[finding.check]
    if finding.domain == "evidence":
        return "Compare the claim with its cited evidence and correct or remove any unsupported detail."
    if finding.domain == "coverage":
        return "Confirm the missing or degraded coverage and decide whether the briefing is complete enough to use."
    return "Resolve the checker message below and rerun the briefing before treating this preview as approved."


def _render_review_panel(
    findings: tuple[ReviewFinding, ...],
    *,
    inline: bool = False,
    original: str | None = None,
    show_heading: bool = True,
) -> str:
    items = []
    for finding in findings:
        label = " · ".join(
            [finding.level, finding.domain, finding.check.replace("_", " ")]
        )
        items.append(
            "<li>"
            f'<span class="finding-label">{html.escape(label)}:</span> '
            f"{html.escape(finding.message)} "
            f'<span class="review-action"><strong>Action:</strong> '
            f"{html.escape(_review_action(finding))}</span>"
            "</li>"
        )
    count = len(findings)
    heading = f"Review required · {count} {'finding' if count == 1 else 'findings'}"
    disclosure = (
        "<details>"
        "<summary>Click to see redacted information</summary>"
        f'<pre class="model-authored">{html.escape(original)}</pre>'
        "</details>"
        if original is not None
        else ""
    )
    tag = "aside" if inline else "section"
    heading_tag = "h3" if inline else "h2"
    classes = "review-panel inline-review" if inline else "review-panel"
    heading_html = (
        f"<{heading_tag}>{html.escape(heading)}</{heading_tag}>" if show_heading else ""
    )
    aria_label = ' aria-label="Review findings"' if not show_heading else ""
    return (
        f'<{tag} class="{classes}"{aria_label}>'
        f"{heading_html}"
        f"<ol>{''.join(items)}</ol>"
        f"{disclosure}"
        f"</{tag}>\n"
    )


def _render_report(entry: BriefingEntry, entries: list[BriefingEntry]) -> str:
    newest = entries[0]
    briefing_href = "index.html" if entry.slug == newest.slug else f"{entry.slug}.html"
    parts = [
        f'<p><a href="../{briefing_href}">← Back to briefing</a></p>',
        f"<h1>Integrity report — {html.escape(entry.slug)}</h1>",
        f'<p class="verdict">{html.escape(_verdict(entry))}</p>',
    ]
    if entry.findings:
        parts.append(_render_review_panel(entry.findings))
    elif entry.findings_count == 0:
        parts.append('<p class="muted">All checks passed.</p>')
    if entry.repair_actions:
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
    if entry.degraded_sources:
        sources = "".join(
            f"<li>{html.escape(source)}</li>" for source in entry.degraded_sources
        )
        parts.append(
            '<section class="degraded-sources">'
            "<h2>Degraded sources</h2>"
            f"<ul>{sources}</ul>"
            "</section>"
        )
    return _document(f"Integrity report — {entry.slug}", "\n".join(parts))


def build_site(
    briefings_dir: Path,
    output_dir: Path,
    prior_history: Path | None = None,
    bootstrap_dir: Path | None = None,
    replace_existing: bool = False,
) -> None:
    """Render ready briefings and review-required previews from validated inputs."""
    if not briefings_dir.is_dir():
        raise ValueError(f"briefings directory does not exist: {briefings_dir}")
    by_date: dict[str, BriefingEntry] = {}
    if bootstrap_dir is not None:
        if not bootstrap_dir.is_dir():
            raise ValueError(f"bootstrap directory does not exist: {bootstrap_dir}")
        for sidecar in bootstrap_dir.glob("*.json"):
            entry = _entry_from_sidecar(sidecar)
            by_date[entry.slug] = entry
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
            by_date[entry.slug] = entry
    entries = sorted(by_date.values(), key=lambda entry: entry.day, reverse=True)
    entries = entries[:7]

    output_dir.mkdir(parents=True, exist_ok=True)
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
            _render_report(entry, entries),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("briefings_dir", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument(
        "--prior-history",
        type=Path,
        help="validated history.json downloaded from the previously deployed site",
    )
    parser.add_argument(
        "--bootstrap-dir",
        type=Path,
        help="validated sidecars and Markdown used to seed an initially empty deployment",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="replace prior-history pages for publishable dates present in briefings_dir",
    )
    args = parser.parse_args()
    try:
        build_site(
            args.briefings_dir,
            args.output_dir,
            args.prior_history,
            args.bootstrap_dir,
            args.replace_existing,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
