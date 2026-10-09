#!/usr/bin/env python3
"""Code-enforced news corpus fetcher for the daily briefing.

Pulls RSS feeds, the Hacker News Algolia API, and Reddit (anonymous RSS,
falling back to the Arctic Shift archive and then, when
``SCRAPECREATORS_API_KEY`` is set, the ScrapeCreators API), drops everything
older than the cutoff (default 24h) IN CODE, and emits a JSON corpus grouped
by category. The LLM only ranks and summarizes what this script outputs — it
never decides what counts as "recent."

Usage:
    python3 fetch_news.py                 # JSON to stdout, 24h window
    python3 fetch_news.py --hours 12
    python3 fetch_news.py --window-end 2026-08-20T04:00:00+00:00
    python3 fetch_news.py --window-start 2026-08-19T04:00:00+00:00 \
        --window-end 2026-08-20T04:00:00+00:00 --report-date 2026-08-19
    python3 fetch_news.py --markdown      # human-readable digest instead
    python3 fetch_news.py -o corpus.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import corpus_schema
from agent_runner.checkpoint import write_text_atomic
from news_fetch.collect import fetch_all_sources, new_corpus, record_source_health
from news_fetch.config import Sources, load_sources
from news_fetch.curation import (
    DEFAULT_CATEGORY_CAP,
    DEFAULT_SOURCE_CAP,
    context_budget_report,
    curate_categories,
)

DEFAULT_SOURCES_PATH = Path(__file__).with_name("sources.json")
DEFAULT_WINDOW_HOURS = 24


def positive_int(value: str) -> int:
    """argparse type for strictly positive integer options."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def utc_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp with an explicit offset and normalize it to UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def iso_date(value: str) -> str:
    """Parse and canonicalize a calendar date for report presentation."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO 8601 date") from exc
    if value != parsed.isoformat():
        raise argparse.ArgumentTypeError("must be an ISO 8601 date")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH,
                        help=f"source configuration JSON (default: {DEFAULT_SOURCES_PATH})")
    window = parser.add_mutually_exclusive_group()
    window.add_argument(
        "--hours",
        type=positive_int,
        help="hard cutoff applied to every source: drop anything older "
             f"(default {DEFAULT_WINDOW_HOURS})",
    )
    window.add_argument(
        "--window-start",
        type=utc_timestamp,
        help="fixed ISO 8601 start timestamp for a calendar-aligned historical window",
    )
    parser.add_argument(
        "--window-end",
        type=utc_timestamp,
        help="fixed ISO 8601 end timestamp for replayable historical windows "
             "(default: current time)",
    )
    parser.add_argument(
        "--report-date",
        type=iso_date,
        help="calendar date rendered in the briefing title; requires --window-start",
    )
    parser.add_argument("--source-cap", type=positive_int, default=DEFAULT_SOURCE_CAP,
                        help=f"maximum items retained per source (default {DEFAULT_SOURCE_CAP})")
    parser.add_argument("--category-cap", type=positive_int, default=DEFAULT_CATEGORY_CAP,
                        help=f"maximum items retained per category (default {DEFAULT_CATEGORY_CAP})")
    parser.add_argument("--markdown", action="store_true",
                        help="emit a markdown digest instead of JSON")
    parser.add_argument("-o", "--output", help="write to file instead of stdout")
    return parser


def load_configured_sources(parser: argparse.ArgumentParser, args: argparse.Namespace) -> Sources:
    try:
        sources = load_sources(args.sources)
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load sources from {args.sources}: {exc}")

    if args.window_start is not None and args.window_end is None:
        parser.error("--window-start requires --window-end")
    if args.report_date is not None and args.window_start is None:
        parser.error("--report-date requires --window-start")
    return sources


def resolve_window(parser: argparse.ArgumentParser,
                   args: argparse.Namespace) -> tuple[datetime, datetime, int]:
    """Return the window's cutoff, end, and length in whole hours."""
    window_end = args.window_end or datetime.now(timezone.utc)
    if args.window_start is not None:
        cutoff = args.window_start
        window_seconds = (window_end - cutoff).total_seconds()
        if window_seconds <= 0 or window_seconds % 3600:
            parser.error("fixed window boundaries must span a positive whole number of hours")
        window_hours = int(window_seconds // 3600)
    else:
        window_hours = args.hours or DEFAULT_WINDOW_HOURS
        cutoff = window_end - timedelta(hours=window_hours)
    return cutoff, window_end, window_hours


def render_markdown(corpus: dict[str, Any], window_hours: int, window_end: datetime) -> str:
    """A Markdown digest of every category followed by any fetch errors."""
    lines = [f"# News corpus — last {window_hours}h "
             f"(generated {window_end:%Y-%m-%d %H:%M} UTC)\n"]
    for category, items in corpus["categories"].items():
        lines.append(f"\n## {category} ({len(items)} items)\n")
        for item in items:
            lines.append(f"- **{item['title']}** ({item['source']}, {item['published'][:16]})\n"
                         f"  {item['url']}")
    if corpus["errors"]:
        lines.append("\n## Fetch errors\n")
        lines.extend(
            f"- {e['source_type']}:{e['source_id']}: "
            f"{e['error_type']}: {e['message']}"
            for e in corpus["errors"])
    return "\n".join(lines)


def emit(corpus: dict[str, Any], text: str, output: str | None) -> int:
    """Validate, write or print the corpus, and return the process exit code."""
    total = sum(len(v) for v in corpus["categories"].values())
    # Validate before reporting success: a contract violation identifies a
    # producer bug, and downstream consumers require this exact shape.
    schema_problems = corpus_schema.validate_corpus(corpus)

    # A schema-invalid or zero-item corpus is still valid JSON that downstream
    # publication and backfill cannot distinguish from a good one, so a failed
    # fetch must not leave an output file behind. Determine failure first, then
    # write --output only on success (atomically, so a crash mid-write cannot
    # leave a truncated file either). When no file is written the value still
    # goes to stdout so the failure context stays visible.
    failed = bool(schema_problems) or total == 0
    wrote_file = False
    if output and not failed:
        write_text_atomic(Path(output), text)
        print(f"Wrote {total} items ({len(corpus['errors'])} fetch errors) "
              f"to {output}")
        wrote_file = True
    elif output:
        # A pre-existing file at the output path (a prior run's corpus) is
        # indistinguishable downstream from this run's output, so a failed
        # fetch must remove it, not merely decline to overwrite it.
        Path(output).unlink(missing_ok=True)
    if not wrote_file:
        print(text)

    # The serialized value is emitted before the diagnostic either way because
    # it contains more useful failure context than the violation list alone.
    if schema_problems:
        print(f"error: corpus violates schema v{corpus_schema.SCHEMA_VERSION}:",
              file=sys.stderr)
        for problem in schema_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if total == 0:
        print("error: no usable items fetched from any source", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    sources = load_configured_sources(parser, args)
    cutoff, window_end, window_hours = resolve_window(parser, args)

    corpus = new_corpus(sources, window_end, cutoff, window_hours,
                        args.source_cap, args.category_cap, args.report_date)
    undated = fetch_all_sources(corpus, sources, cutoff, window_end, window_hours)
    used_bytes, estimated_tokens = curate_categories(
        corpus, undated, args.source_cap, args.category_cap)
    record_source_health(corpus)
    corpus["context_budget"] = context_budget_report(
        corpus["processing"], used_bytes, estimated_tokens)

    text = (render_markdown(corpus, window_hours, window_end) if args.markdown
            else json.dumps(corpus, indent=1))
    return emit(corpus, text, args.output)


if __name__ == "__main__":
    sys.exit(main())
