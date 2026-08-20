#!/usr/bin/env python3
"""Build a dependency-free static archive from publication-gated briefings."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

LEGACY_FIELDS = {"date", "disposition", "findings_count", "degraded_sources"}
SIDECAR_FIELDS = LEGACY_FIELDS | {"findings"}
HISTORY_FIELDS = SIDECAR_FIELDS | {"markdown"}
LEGACY_HISTORY_FIELDS = LEGACY_FIELDS | {"markdown"}
FINDING_FIELDS = {"level", "check", "domain", "message"}
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
.review-panel { background: #f5a62322; border: 2px solid #d98200; border-radius: .5rem;
  margin: 1.5rem 0; padding: 1rem 1.25rem; }
.review-panel h2 { margin-top: 0; }
.review-panel li { margin: 1rem 0; }
.finding-label { font-weight: 700; }
pre { background: #8881; border: 1px solid #8884; overflow-wrap: anywhere; padding: 1rem; white-space: pre-wrap; }
""".strip()


@dataclass(frozen=True)
class ReviewFinding:
    level: str
    check: str
    domain: str
    message: str


@dataclass(frozen=True)
class BriefingEntry:
    day: date
    disposition: str
    findings_count: int
    findings: tuple[ReviewFinding, ...]
    degraded_sources: tuple[str, ...]
    markdown: str | None

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
    )


def _entry_from_payload(
    payload: object,
    *,
    source: str,
    expected_slug: str | None = None,
    schema_version: int = 2,
) -> BriefingEntry:
    expected_fields = SIDECAR_FIELDS if schema_version == 2 else LEGACY_FIELDS
    if not isinstance(payload, dict) or set(payload) != expected_fields:
        raise ValueError(f"{source} must contain exactly {sorted(expected_fields)}")

    raw_date = payload["date"]
    disposition = payload["disposition"]
    findings_count = payload["findings_count"]
    raw_findings = payload["findings"] if schema_version == 2 else []
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
        if not isinstance(raw_finding, dict) or set(raw_finding) != FINDING_FIELDS:
            raise ValueError(f"{finding_source} must contain exactly {sorted(FINDING_FIELDS)}")
        if any(
            not isinstance(raw_finding[field], str) or not raw_finding[field].strip()
            for field in FINDING_FIELDS
        ):
            raise ValueError(f"{finding_source} fields must be non-empty strings")
        if raw_finding["level"] not in {"ERROR", "WARN"}:
            raise ValueError(f"{finding_source} level must be ERROR or WARN")
        findings.append(
            ReviewFinding(
                level=raw_finding["level"],
                check=raw_finding["check"],
                domain=raw_finding["domain"],
                message=raw_finding["message"],
            )
        )
    if schema_version == 2 and disposition == "review_required" and len(findings) != findings_count:
        raise ValueError(f"{source} must include every review-required finding")
    if disposition != "review_required" and findings:
        raise ValueError(f"{source} findings details are allowed only for review_required entries")
    return BriefingEntry(
        day=parsed_date,
        disposition=disposition,
        findings_count=findings_count,
        findings=tuple(findings),
        degraded_sources=tuple(degraded_sources),
        markdown=None,
    )


def _load_history(path: Path) -> list[BriefingEntry]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "entries"}
        or payload.get("schema_version") not in {1, 2}
        or not isinstance(payload.get("entries"), list)
    ):
        raise ValueError(f"history {path} must use schema_version 1 or 2 with an entries array")
    schema_version = payload["schema_version"]
    entries: list[BriefingEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(payload["entries"]):
        source = f"history {path} entry {index}"
        expected_fields = HISTORY_FIELDS if schema_version == 2 else LEGACY_HISTORY_FIELDS
        if not isinstance(raw_entry, dict) or set(raw_entry) != expected_fields:
            raise ValueError(f"{source} must contain exactly {sorted(expected_fields)}")
        metadata_fields = SIDECAR_FIELDS if schema_version == 2 else LEGACY_FIELDS
        metadata = {key: raw_entry[key] for key in metadata_fields}
        entry = _entry_from_payload(metadata, source=source, schema_version=schema_version)
        markdown = raw_entry["markdown"]
        page_dispositions = PAGE_DISPOSITIONS if schema_version == 2 else {"ready"}
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
            )
        )
    return entries


def _history_payload(entries: list[BriefingEntry]) -> dict[str, object]:
    return {
        "schema_version": 2,
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
                    }
                    for finding in entry.findings
                ],
                "degraded_sources": list(entry.degraded_sources),
                "markdown": entry.markdown,
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


def _entry_body(entry: BriefingEntry) -> str:
    review_panel = _render_review_panel(entry) if entry.disposition == "review_required" else ""
    briefing = (
        f"{review_panel}<pre>{html.escape(entry.markdown)}</pre>"
        if entry.markdown is not None
        else '<p class="muted">No briefing prose is available for this run.</p>'
    )
    return (
        f"<h1>Briefing for {html.escape(entry.slug)}</h1>"
        f'<p class="verdict">Checker verdict: {html.escape(_verdict(entry))}</p>'
        f"<p>Corpus health: {_corpus_health(entry)}</p>"
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


def _render_review_panel(entry: BriefingEntry) -> str:
    items = []
    for finding in entry.findings:
        label = " · ".join(
            [finding.level, finding.domain, finding.check.replace("_", " ")]
        )
        items.append(
            "<li>"
            f'<p class="finding-label">{html.escape(label)}</p>'
            f"<p>{html.escape(finding.message)}</p>"
            f"<p><strong>Review action:</strong> {html.escape(_review_action(finding))}</p>"
            "</li>"
        )
    return (
        '<section class="review-panel" aria-labelledby="review-findings">'
        '<h2 id="review-findings">Review required before relying on this briefing</h2>'
        "<p>This is a checker-generated preview, not an approved briefing. A person must address "
        "each finding below and rerun the checker before treating it as publication-ready.</p>"
        f"<ol>{''.join(items)}</ol>"
        "</section>"
    )


def build_site(
    briefings_dir: Path,
    output_dir: Path,
    prior_history: Path | None = None,
    bootstrap_dir: Path | None = None,
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
        if entry_rank >= prior_rank:
            by_date[entry.slug] = entry
    entries = sorted(by_date.values(), key=lambda entry: entry.day, reverse=True)
    if entries:
        cutoff = entries[0].day - timedelta(days=6)
        entries = [entry for entry in entries if entry.day >= cutoff]

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_page in output_dir.glob("*.html"):
        stale_page.unlink()
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
    args = parser.parse_args()
    try:
        build_site(args.briefings_dir, args.output_dir, args.prior_history, args.bootstrap_dir)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
