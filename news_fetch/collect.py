"""Fetch every configured source into a new corpus, with per-source health."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

import corpus_schema
from news_fetch.config import Sources
from news_fetch.curation import item_context_usage
from news_fetch.model import FetchResult, Item, SourceDataError, SourceStatus, TimedFetchResult
from news_fetch.sources.hn import fetch_hn
from news_fetch.sources.reddit import REDDIT_PAUSE_SECONDS, fetch_reddit
from news_fetch.sources.rss import fetch_rss

FETCH_WORKERS = 8


def timed_fetch(fetcher: Callable[..., FetchResult], *args: Any) -> TimedFetchResult:
    """Run one source fetch and retain its outcome and wall-clock latency."""
    started = time.perf_counter()
    error_type: str | None
    message: str | None
    try:
        result = fetcher(*args)
    except Exception as exc:
        error_type = (exc.error_type if isinstance(exc, SourceDataError)
                      else exc.__class__.__name__)
        message = str(exc) or error_type
        result = None
        http_success = isinstance(exc, SourceDataError)
    else:
        error_type = None
        message = None
        http_success = True
    duration_ms = round((time.perf_counter() - started) * 1000)
    return TimedFetchResult(result, error_type, message, duration_ms, http_success)


def source_status(source_type: str, source_id: str, category: str,
                  outcome: TimedFetchResult) -> SourceStatus:
    """Convert a fetch outcome into the stable, machine-readable health record."""
    result = outcome.result
    parsed = result.parsed_entries if result else 0
    dated = result.dated_entries if result else 0
    status: SourceStatus = {
        "source_type": source_type,
        "source_id": source_id,
        "category": category,
        "status": "ok",
        "requested": True,
        "http_success": outcome.http_success,
        "parsed_entries": parsed,
        "dated_entries": dated,
        "retained_entries": 0,
        "retained_bytes": 0,
        "estimated_tokens": 0,
        "duration_ms": outcome.duration_ms,
    }
    if result is None:
        status["status"] = "error"
        status["error_type"] = outcome.error_type or "FetchError"
        status["message"] = outcome.message or "unknown fetch error"
    elif parsed == 0:
        status["status"] = "empty"
        status["error_type"] = "EmptySource"
        status["message"] = "response contained zero recognized entries"
    elif dated == 0:
        status["status"] = "empty"
        status["error_type"] = "NoDatedEntries"
        status["message"] = "response contained zero entries with parseable dates"
    elif not result.items:
        # Valid, dated entries exist; there are simply none in the window (or
        # all of them were filtered). That is a quiet source, not a broken
        # one, so it is kept out of `errors` and the failed-source contract
        # unless too many pile up in one category (see corpus_schema).
        status["status"] = "quiet"
        if result.filtered_entries:
            status["error_type"] = "EntriesFiltered"
            status["message"] = (
                "zero usable entries in the requested window "
                f"({result.filtered_entries} filtered as removed or low-score)"
            )
        else:
            status["error_type"] = "NoWindowEntries"
            status["message"] = "response contained zero usable entries in the requested window"
    return status


def error_record(status: SourceStatus) -> dict[str, Any]:
    """Project a non-healthy source outcome into the compact errors list."""
    return {
        "source_type": status["source_type"],
        "source_id": status["source_id"],
        "status": status["status"],
        "error_type": status.get("error_type", "FetchError"),
        "message": status.get("message", "unknown fetch error"),
        "duration_ms": status["duration_ms"],
    }


def _item_belongs_to_source(item: Item, status: SourceStatus) -> bool:
    source_type = status["source_type"]
    source_id = status["source_id"]
    if source_type == "rss":
        return item.get("source") == source_id
    if source_type == "hacker_news":
        return item.get("source") == "Hacker News" and item.get("query") == source_id
    return item.get("source") == f"r/{source_id}"


def record_source_health(corpus: dict[str, Any]) -> None:
    """Fill each source's retained usage and derive the compact errors list."""
    for status in corpus["sources"]:
        retained = [
            item for item in corpus["categories"][status["category"]]
            if _item_belongs_to_source(item, status)
        ]
        retained_usage = [item_context_usage(item) for item in retained]
        status["retained_entries"] = len(retained)
        status["retained_bytes"] = sum(size for size, _tokens in retained_usage)
        status["estimated_tokens"] = sum(tokens for _size, tokens in retained_usage)
    corpus["errors"] = [error_record(status) for status in corpus["sources"]
                        if status["status"] not in ("ok", "quiet")]


def _record_outcome(corpus: dict[str, Any], undated: dict[str, int], source_type: str,
                    source_id: str, category: str, outcome: TimedFetchResult) -> None:
    """Add one source's items, undated count, and health record to the corpus."""
    if outcome.result is not None:
        corpus["categories"][category].extend(outcome.result.items)
        undated[category] += outcome.result.undated
    corpus["sources"].append(source_status(source_type, source_id, category, outcome))


def new_corpus(sources: Sources, window_end: datetime, cutoff: datetime,
               window_hours: int, source_cap: int, category_cap: int,
               report_date: str | None) -> dict[str, Any]:
    """An empty corpus for one fixed window, before any source is fetched."""
    corpus: dict[str, Any] = {
        "schema_version": corpus_schema.SCHEMA_VERSION,
        "generated_at": window_end.isoformat(),
        "cutoff": cutoff.isoformat(),
        "window_hours": window_hours,
        "limits": {"source_cap": source_cap, "category_cap": category_cap},
        "categories": {name: [] for name in sources.categories},
        "processing": {},
        "errors": [],
        "sources": [],
    }
    if report_date is not None:
        corpus["report_date"] = report_date
    return corpus


def fetch_concurrent_sources(corpus: dict[str, Any], sources: Sources,
                             cutoff: datetime, window_end: datetime,
                             undated: dict[str, int]) -> None:
    """Fetch RSS feeds and Hacker News queries in parallel."""
    jobs: list[tuple[Future[TimedFetchResult], str, str, str]] = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for category, feeds in sources.rss_feeds.items():
            for name, url in feeds:
                jobs.append((
                    pool.submit(timed_fetch, fetch_rss, name, url, cutoff, window_end),
                    category,
                    "rss",
                    name,
                ))
        for query in sources.hn_queries:
            jobs.append((
                pool.submit(timed_fetch, fetch_hn, query, cutoff, window_end),
                sources.hn_category,
                "hacker_news",
                query,
            ))

        for future, category, source_type, source_id in jobs:
            _record_outcome(corpus, undated, source_type, source_id, category, future.result())


def fetch_reddit_sources(corpus: dict[str, Any], sources: Sources,
                         cutoff: datetime, window_end: datetime,
                         window_hours: int, undated: dict[str, int]) -> None:
    """Fetch each subreddit serially, pausing between requests."""
    # Reddit rate-limits concurrent requests; fetch serially with a pause. The
    # authenticated provider is only reached if both free paths yield no items.
    scrapecreators_api_key = os.environ.get("SCRAPECREATORS_API_KEY") or None
    for index, sub in enumerate(sources.subreddits):
        outcome = timed_fetch(
            fetch_reddit,
            sub,
            cutoff,
            window_end,
            window_hours,
            scrapecreators_api_key,
        )
        _record_outcome(corpus, undated, "reddit", sub, sources.reddit_category, outcome)
        if index < len(sources.subreddits) - 1:
            time.sleep(REDDIT_PAUSE_SECONDS)


def fetch_all_sources(corpus: dict[str, Any], sources: Sources, cutoff: datetime,
                      window_end: datetime, window_hours: int) -> dict[str, int]:
    """Fetch every source into `corpus`; return undated-entry counts per category."""
    fetch_started = time.perf_counter()
    undated = dict.fromkeys(corpus["categories"], 0)
    fetch_concurrent_sources(corpus, sources, cutoff, window_end, undated)
    fetch_reddit_sources(corpus, sources, cutoff, window_end, window_hours, undated)
    corpus["fetch_duration_ms"] = round((time.perf_counter() - fetch_started) * 1000)
    return undated
