#!/usr/bin/env python3
"""Build a dependency-free static archive from publication-gated briefings."""

from __future__ import annotations

import argparse
import html
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SIDECAR_FIELDS = {"date", "disposition", "findings_count", "degraded_sources"}
HISTORY_FIELDS = SIDECAR_FIELDS | {"markdown"}
DISPOSITIONS = {
    "blocked",
    "degraded",
    "no_result",
    "ready",
    "rejected",
    "review_required",
}

STYLE = """
:root { color-scheme: light dark; font-family: system-ui, sans-serif; line-height: 1.5; }
body { margin: 0 auto; max-width: 76rem; padding: 2rem 1.25rem 4rem; }
a { color: inherit; }
ul { list-style: none; padding: 0; }
article { border-top: 1px solid #8886; padding: 1rem 0; }
.verdict { font-weight: 700; }
.muted { color: #777; }
pre { background: #8881; border: 1px solid #8884; overflow-wrap: anywhere; padding: 1rem; white-space: pre-wrap; }
""".strip()


@dataclass(frozen=True)
class BriefingEntry:
    day: date
    disposition: str
    findings_count: int
    degraded_sources: tuple[str, ...]
    markdown: str | None

    @property
    def slug(self) -> str:
        return self.day.isoformat()


def _entry_from_sidecar(path: Path) -> BriefingEntry:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    entry = _entry_from_payload(payload, source=f"sidecar {path}", expected_slug=path.stem)
    markdown_path = path.with_suffix(".md")
    if entry.disposition == "ready" and not markdown_path.is_file():
        raise ValueError(f"ready sidecar {path} requires matching Markdown {markdown_path.name}")
    markdown = markdown_path.read_text(encoding="utf-8") if entry.disposition == "ready" else None
    return BriefingEntry(
        day=entry.day,
        disposition=entry.disposition,
        findings_count=entry.findings_count,
        degraded_sources=entry.degraded_sources,
        markdown=markdown,
    )


def _entry_from_payload(
    payload: object,
    *,
    source: str,
    expected_slug: str | None = None,
) -> BriefingEntry:
    if not isinstance(payload, dict) or set(payload) != SIDECAR_FIELDS:
        raise ValueError(f"{source} must contain exactly {sorted(SIDECAR_FIELDS)}")

    raw_date = payload["date"]
    disposition = payload["disposition"]
    findings_count = payload["findings_count"]
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
    return BriefingEntry(
        day=parsed_date,
        disposition=disposition,
        findings_count=findings_count,
        degraded_sources=tuple(degraded_sources),
        markdown=None,
    )


def _load_history(path: Path) -> list[BriefingEntry]:
    payload: Any = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "entries"}
        or payload.get("schema_version") != 1
        or not isinstance(payload.get("entries"), list)
    ):
        raise ValueError(f"history {path} must use schema_version 1 with an entries array")
    entries: list[BriefingEntry] = []
    seen: set[str] = set()
    for index, raw_entry in enumerate(payload["entries"]):
        source = f"history {path} entry {index}"
        if not isinstance(raw_entry, dict) or set(raw_entry) != HISTORY_FIELDS:
            raise ValueError(f"{source} must contain exactly {sorted(HISTORY_FIELDS)}")
        metadata = {key: raw_entry[key] for key in SIDECAR_FIELDS}
        entry = _entry_from_payload(metadata, source=source)
        markdown = raw_entry["markdown"]
        if (entry.disposition == "ready" and not isinstance(markdown, str)) or (
            entry.disposition != "ready" and markdown is not None
        ):
            raise ValueError(f"{source} markdown must exist only for ready entries")
        if entry.slug in seen:
            raise ValueError(f"history {path} contains duplicate date {entry.slug}")
        seen.add(entry.slug)
        entries.append(
            BriefingEntry(
                day=entry.day,
                disposition=entry.disposition,
                findings_count=entry.findings_count,
                degraded_sources=entry.degraded_sources,
                markdown=markdown,
            )
        )
    return entries


def _history_payload(entries: list[BriefingEntry]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "entries": [
            {
                "date": entry.slug,
                "disposition": entry.disposition,
                "findings_count": entry.findings_count,
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


def _render_index(entries: list[BriefingEntry]) -> str:
    items: list[str] = []
    for entry in entries:
        escaped_date = html.escape(entry.slug)
        heading = (
            f'<a href="{escaped_date}.html">{escaped_date}</a>'
            if entry.disposition == "ready"
            else escaped_date
        )
        items.append(
            "<li><article>"
            f"<h2>{heading}</h2>"
            f'<p class="verdict">Checker verdict: {html.escape(_verdict(entry))}</p>'
            f"<p>Corpus health: {_corpus_health(entry)}</p>"
            "</article></li>"
        )
    archive = "\n".join(items) if items else '<li class="muted">No runs published yet.</li>'
    body = (
        "<h1>Daily news briefing archive</h1>"
        '<p class="muted">Only publication-ready briefings are linked. '
        "Degraded or blocked runs remain visible as status entries.</p>"
        f"<ul>{archive}</ul>"
    )
    return _document("Daily news briefing archive", body)


def _render_briefing(entry: BriefingEntry) -> str:
    if entry.markdown is None:
        raise ValueError("only ready entries may be rendered")
    body = (
        '<nav><a href="index.html">← Archive</a></nav>'
        f"<h1>Briefing for {html.escape(entry.slug)}</h1>"
        f'<p class="verdict">Checker verdict: {html.escape(_verdict(entry))}</p>'
        f"<p>Corpus health: {_corpus_health(entry)}</p>"
        f"<pre>{html.escape(entry.markdown)}</pre>"
    )
    return _document(f"Daily briefing — {entry.slug}", body)


def build_site(
    briefings_dir: Path,
    output_dir: Path,
    prior_history: Path | None = None,
) -> None:
    """Render an archive index and pages for publication-ready Markdown only."""
    if not briefings_dir.is_dir():
        raise ValueError(f"briefings directory does not exist: {briefings_dir}")
    by_date = {
        entry.slug: entry
        for entry in (_load_history(prior_history) if prior_history is not None else [])
    }
    for sidecar in briefings_dir.glob("*.json"):
        entry = _entry_from_sidecar(sidecar)
        by_date[entry.slug] = entry
    entries = sorted(by_date.values(), key=lambda entry: entry.day, reverse=True)

    output_dir.mkdir(parents=True, exist_ok=True)
    for stale_page in output_dir.glob("*.html"):
        stale_page.unlink()
    (output_dir / "index.html").write_text(_render_index(entries), encoding="utf-8")
    (output_dir / "history.json").write_text(
        json.dumps(_history_payload(entries), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for entry in entries:
        if entry.disposition == "ready":
            (output_dir / f"{entry.slug}.html").write_text(
                _render_briefing(entry),
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
    args = parser.parse_args()
    try:
        build_site(args.briefings_dir, args.output_dir, args.prior_history)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
