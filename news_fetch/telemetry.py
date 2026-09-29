"""Per-source fetch timing and machine-readable source-health records."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from news_fetch.curation import item_context_usage
from news_fetch.model import FetchResult, Item, SourceDataError, SourceStatus, TimedFetchResult


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
    parsed = (result.parsed_entries if result and result.parsed_entries is not None
              else len(result.items) + result.undated if result else 0)
    dated = (result.dated_entries if result and result.dated_entries is not None
             else len(result.items) if result else 0)
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
