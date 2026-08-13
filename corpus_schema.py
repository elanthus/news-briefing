#!/usr/bin/env python3
"""The corpus contract, in one place.

Three things depend on the shape of `corpus.json`: `fetch_news.py` writes it,
`briefing-prompt.md` instructs a model to read specific fields out of it, and
`eval_briefing.py` checks a briefing against it. Until now that agreement
existed only by convention — renaming a key in the fetcher produced no error
anywhere, just a quietly worse briefing, because the prompt would ask for a
field that had stopped existing and the model would carry on without it.

This module is the agreement. The fetcher validates against it before writing,
so drift fails at the point it is introduced rather than surfacing later as
degraded output that nobody can attribute.

`SCHEMA_VERSION` is bumped when a change would break a reader. A corpus with no
version predates versioning and is readable; one with a version this code does
not know is not, and says so instead of guessing.
"""

from __future__ import annotations

import re
import urllib.parse
from datetime import datetime
from typing import Any

# 5 adds enforced context budgets and their truncation/drop telemetry. Older
# readers would silently omit those controls when presenting corpus health, so
# they must refuse this shape instead of guessing.
SCHEMA_VERSION = 5
LEGACY_SCHEMA_VERSION = 0  # corpora written before the field existed

ITEM_TITLE_MAX_BYTES = 512
ITEM_TITLE_MAX_TOKENS = 128
ITEM_URL_MAX_BYTES = 2048
ITEM_URL_MAX_TOKENS = 512
ITEM_SUMMARY_MAX_CHARS = 300
ITEM_SUMMARY_MAX_BYTES = 1200
ITEM_SUMMARY_MAX_TOKENS = 300
ITEM_SOURCE_MAX_BYTES = 256
ITEM_SOURCE_MAX_TOKENS = 64
ITEM_QUERY_MAX_BYTES = 256
ITEM_QUERY_MAX_TOKENS = 64
SOURCE_CONTEXT_MAX_BYTES = 96 * 1024
SOURCE_CONTEXT_MAX_TOKENS = 24_000
GLOBAL_CONTEXT_MAX_BYTES = 512 * 1024
GLOBAL_CONTEXT_MAX_TOKENS = 128_000

# Per-field token ceilings are the dependency-free planning estimate implied
# by their byte ceilings, not a second rejection criterion. Source and global
# token budgets remain independently enforced because their decimal token
# ceilings are lower than one quarter of their binary byte ceilings.

CATEGORY_NAME = re.compile(r"^[a-z][a-z0-9_]*$")

# Query keys that identify a referral, not an article. `utm_*` is handled by
# prefix alongside these.
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

# Fields the prompt and the checker are entitled to rely on.
ITEM_REQUIRED_FIELDS = ("title", "url", "published", "source")
# Present only for sources that carry them; readers must tolerate absence.
ITEM_OPTIONAL_FIELDS = ("summary", "discussion", "points", "comments", "query")

# Every reason an item can be absent, plus what survived. These reconcile:
# the drops after `undated_dropped` plus `kept` equal `fetched`. Undated items
# are counted by the fetchers before `fetched` is measured, so they sit
# outside that sum by construction.
PROCESSING_FIELDS = (
    "fetched",
    "undated_dropped",
    "relevance_dropped",
    "duplicates_dropped",
    "source_cap_dropped",
    "category_cap_dropped",
    "kept",
)
V5_PROCESSING_FIELDS = (
    "field_budget_dropped",
    "source_budget_dropped",
    "global_budget_dropped",
    "title_truncated",
    "summary_truncated",
    "context_bytes",
    "estimated_tokens",
)

TOP_LEVEL_TYPES: dict[str, type | tuple[type, ...]] = {
    "schema_version": int,
    "generated_at": str,
    "cutoff": str,
    "window_hours": int,
    "limits": dict,
    "categories": dict,
    "processing": dict,
    "errors": list,
}

V4_TOP_LEVEL_TYPES: dict[str, type] = {
    "sources": list,
    "fetch_duration_ms": int,
}

V5_TOP_LEVEL_TYPES: dict[str, type] = {
    "context_budget": dict,
}


def corpus_version(corpus: dict[str, Any]) -> int:
    """Schema version of a loaded corpus, treating absence as legacy."""
    version = corpus.get("schema_version", LEGACY_SCHEMA_VERSION)
    return version if isinstance(version, int) else LEGACY_SCHEMA_VERSION


def is_readable(corpus: dict[str, Any]) -> bool:
    """Whether this code understands the corpus well enough to act on it.

    Older is fine — the fields this code reads have only been added to, never
    removed. Newer is not: an unknown version may have moved something, and
    silently misreading it is worse than refusing.
    """
    return corpus_version(corpus) <= SCHEMA_VERSION


def _parse_timestamp(value: Any) -> datetime | None:
    """Accept only full ISO timestamps with an explicit UTC offset."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _timestamp(value: Any) -> bool:
    return _parse_timestamp(value) is not None


def _http_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parts = urllib.parse.urlsplit(value)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.netloc)


def estimated_tokens_for_bytes(size: int) -> int:
    """Dependency-free planning estimate shared by writers and validators."""
    return max(1, (size + 3) // 4)


def valid_category_name(value: Any) -> bool:
    """Whether a value can safely identify a corpus category."""
    return isinstance(value, str) and CATEGORY_NAME.fullmatch(value) is not None


def canonicalize_url(url: str | None) -> str:
    """Normalize a URL for comparison while preserving meaningful parameters.

    Part of the contract rather than the fetcher, because two modules have to
    agree on it: `fetch_news.py` deduplicates with it, and `eval_briefing.py`
    decides whether a citation is in the corpus with it. When only the fetcher
    knew the rule, the checker compared raw strings and reported a cited link
    that differed by a trailing slash as one the corpus did not contain.
    """
    url = (url or "").strip()
    if not url:
        return ""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return url
    query = sorted(
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_QUERY_KEYS
    )
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        urllib.parse.urlencode(query, doseq=True),
        "",
    ))


def url_route(url: str | None) -> tuple[str, frozenset[tuple[str, str]]]:
    """Split a canonical URL into its location and its query parameters.

    The location is scheme, host and path — everything that is the same for
    two URLs differing only in query. It deliberately is *not* an article
    identity: for query-routed publishers, `item?id=123` and `item?id=456`
    share a location and are different articles. Callers must compare the
    parameters too.
    """
    parts = urllib.parse.urlsplit(canonicalize_url(url))
    if not parts.netloc:
        return "", frozenset()
    location = f"{parts.scheme}://{parts.netloc}{parts.path}"
    return location, frozenset(
        urllib.parse.parse_qsl(parts.query, keep_blank_values=True))


def validate_corpus(corpus: Any) -> list[str]:
    """Return a list of contract violations; empty means the corpus conforms."""
    problems: list[str] = []
    if not isinstance(corpus, dict):
        return ["corpus is not a JSON object"]

    for field, expected in TOP_LEVEL_TYPES.items():
        if field not in corpus:
            problems.append(f"missing top-level field {field!r}")
        elif not isinstance(corpus[field], expected):
            problems.append(
                f"{field!r} should be {getattr(expected, '__name__', expected)}, "
                f"got {type(corpus[field]).__name__}")

    version = corpus_version(corpus)
    if version >= 4:
        for field, expected in V4_TOP_LEVEL_TYPES.items():
            if field not in corpus:
                problems.append(f"missing top-level field {field!r}")
            elif not isinstance(corpus[field], expected):
                problems.append(f"{field!r} should be {expected.__name__}, "
                                f"got {type(corpus[field]).__name__}")
    if version >= 5:
        for field, expected in V5_TOP_LEVEL_TYPES.items():
            if field not in corpus:
                problems.append(f"missing top-level field {field!r}")
            elif not isinstance(corpus[field], expected):
                problems.append(f"{field!r} should be {expected.__name__}, "
                                f"got {type(corpus[field]).__name__}")

    if isinstance(corpus.get("schema_version"), int):
        if corpus["schema_version"] > SCHEMA_VERSION:
            problems.append(
                f"schema_version is {corpus['schema_version']}, "
                f"this code understands through {SCHEMA_VERSION}")

    for field in ("generated_at", "cutoff"):
        if field in corpus and not _timestamp(corpus[field]):
            problems.append(
                f"{field!r} is not an ISO 8601 timestamp with a UTC offset")

    generated_at = _parse_timestamp(corpus.get("generated_at"))
    cutoff = _parse_timestamp(corpus.get("cutoff"))
    if generated_at is not None and cutoff is not None and cutoff > generated_at:
        problems.append("cutoff must not be later than generated_at")

    window_hours = corpus.get("window_hours")
    if (isinstance(window_hours, bool) or not isinstance(window_hours, int)
            or window_hours <= 0):
        if "window_hours" in corpus:
            problems.append("'window_hours' should be a positive integer")

    limits = corpus.get("limits")
    if isinstance(limits, dict):
        if set(limits) != {"source_cap", "category_cap"}:
            problems.append("limits should contain source_cap and category_cap only")
        for field in ("source_cap", "category_cap"):
            value = limits.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                problems.append(f"limits.{field} should be a positive integer")

    categories = corpus.get("categories")
    if isinstance(categories, dict):
        if not categories:
            problems.append("categories must define at least one category")
        invalid = [name for name in categories if not valid_category_name(name)]
        if invalid:
            problems.append(
                "categories contains invalid name(s): "
                + ", ".join(sorted(repr(name) for name in invalid)))
        for name, items in categories.items():
            problems += _validate_items(name, items, cutoff, generated_at, version)

    processing = corpus.get("processing")
    if isinstance(processing, dict) and isinstance(categories, dict):
        problems += _validate_processing(processing, categories, version)

    errors = corpus.get("errors")
    if isinstance(errors, list):
        if version >= 4:
            problems += _validate_errors(errors)
        else:
            problems += [f"errors[{i}] is not a string" for i, e in enumerate(errors)
                         if not isinstance(e, str)]

    if "fetch_duration_ms" in corpus and (
            not isinstance(corpus["fetch_duration_ms"], int)
            or corpus["fetch_duration_ms"] < 0):
        problems.append("'fetch_duration_ms' should be a non-negative integer")

    sources = corpus.get("sources")
    if sources is not None:
        problems += (_validate_sources(sources, version) if version >= 4
                     else _validate_legacy_sources(sources))
    if (version >= 4 and isinstance(sources, list) and isinstance(errors, list)
            and isinstance(categories, dict)):
        problems += _validate_health_consistency(sources, errors, set(categories))
    if version >= 5 and isinstance(corpus.get("context_budget"), dict):
        problems += _validate_context_budget(corpus["context_budget"], processing)

    return problems


def _validate_items(category: str, items: Any, cutoff: datetime | None,
                    generated_at: datetime | None, version: int) -> list[str]:
    problems: list[str] = []
    if not isinstance(items, list):
        return [f"categories[{category!r}] is not a list"]
    allowed = set(ITEM_REQUIRED_FIELDS) | set(ITEM_OPTIONAL_FIELDS)
    for index, item in enumerate(items):
        where = f"categories[{category!r}][{index}]"
        if not isinstance(item, dict):
            problems.append(f"{where} is not an object")
            continue
        for field in ITEM_REQUIRED_FIELDS:
            if field not in item:
                problems.append(f"{where} is missing required field {field!r}")
        unknown = sorted(set(item) - allowed)
        if unknown:
            problems.append(f"{where} has unknown field(s) {unknown}")
        if "published" in item and not _timestamp(item["published"]):
            problems.append(
                f"{where}.published is not an ISO 8601 timestamp with a UTC offset")
        published = _parse_timestamp(item.get("published"))
        if published is not None and cutoff is not None and published < cutoff:
            problems.append(f"{where}.published is earlier than cutoff")
        if published is not None and generated_at is not None and published > generated_at:
            problems.append(f"{where}.published is later than generated_at")
        for field in ("title", "source"):
            if field in item and (not isinstance(item[field], str) or not item[field].strip()):
                problems.append(f"{where}.{field} should be a non-empty string")
        if "url" in item and not _http_url(item["url"]):
            problems.append(f"{where}.url should be an absolute HTTP(S) URL")
        if "discussion" in item and not _http_url(item["discussion"]):
            problems.append(f"{where}.discussion should be an absolute HTTP(S) URL")
        for field in ("summary", "query"):
            if field in item and not isinstance(item[field], str):
                problems.append(f"{where}.{field} should be a string")
        for field in ("points", "comments"):
            value = item.get(field)
            if field in item and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 0):
                problems.append(f"{where}.{field} should be a non-negative integer")
        if version >= 5:
            byte_limits = {
                "title": ITEM_TITLE_MAX_BYTES,
                "url": ITEM_URL_MAX_BYTES,
                "summary": ITEM_SUMMARY_MAX_BYTES,
                "source": ITEM_SOURCE_MAX_BYTES,
                "discussion": ITEM_URL_MAX_BYTES,
                "query": ITEM_QUERY_MAX_BYTES,
            }
            for field, byte_limit in byte_limits.items():
                value = item.get(field)
                if isinstance(value, str) and len(value.encode("utf-8")) > byte_limit:
                    problems.append(
                        f"{where}.{field} exceeds {byte_limit} UTF-8 bytes")
            summary = item.get("summary")
            if isinstance(summary, str) and len(summary) > ITEM_SUMMARY_MAX_CHARS:
                problems.append(
                    f"{where}.summary exceeds {ITEM_SUMMARY_MAX_CHARS} characters")
    return problems


def _validate_sources(sources: Any, version: int) -> list[str]:
    """Validate optional per-source fetch observability records."""
    if not isinstance(sources, list):
        return ["'sources' should be a list"]
    required = {
        "source_type": str,
        "source_id": str,
        "category": str,
        "status": str,
        "requested": bool,
        "http_success": bool,
        "parsed_entries": int,
        "dated_entries": int,
        "retained_entries": int,
        "duration_ms": int,
    }
    allowed = set(required) | {"error_type", "message"}
    if version >= 5:
        required.update({"retained_bytes": int, "estimated_tokens": int})
        allowed = set(required) | {"error_type", "message"}
    problems: list[str] = []
    for index, status in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(status, dict):
            problems.append(f"{where} is not an object")
            continue
        missing = set(required) - set(status)
        unknown = set(status) - allowed
        if missing:
            problems.append(f"{where} is missing {sorted(missing)}")
        if unknown:
            problems.append(f"{where} has unknown field(s) {sorted(unknown)}")
        for field, expected in required.items():
            if field in status and not isinstance(status[field], expected):
                problems.append(f"{where}.{field} has the wrong type")
        if status.get("source_type") not in {"rss", "hacker_news", "reddit"}:
            problems.append(f"{where}.source_type is not recognized")
        if not isinstance(status.get("source_id"), str) or not status.get("source_id", "").strip():
            problems.append(f"{where}.source_id should be a non-empty string")
        if status.get("status") not in {"ok", "empty", "error"}:
            problems.append(f"{where}.status should be 'ok', 'empty', or 'error'")
        for field in ("parsed_entries", "dated_entries", "retained_entries", "duration_ms"):
            value = status.get(field)
            if isinstance(value, bool) or (isinstance(value, int) and value < 0):
                problems.append(f"{where}.{field} should be non-negative")
        for field, limit in (("retained_bytes", SOURCE_CONTEXT_MAX_BYTES),
                             ("estimated_tokens", SOURCE_CONTEXT_MAX_TOKENS)):
            value = status.get(field)
            if version >= 5 and (isinstance(value, bool) or not isinstance(value, int)
                                 or value < 0 or value > limit):
                problems.append(f"{where}.{field} should be between 0 and {limit}")
        parsed = status.get("parsed_entries")
        dated = status.get("dated_entries")
        retained = status.get("retained_entries")
        if (isinstance(parsed, int) and not isinstance(parsed, bool)
                and isinstance(dated, int) and not isinstance(dated, bool)
                and isinstance(retained, int) and not isinstance(retained, bool)):
            if not 0 <= retained <= dated <= parsed:
                problems.append(f"{where} entry counts should satisfy retained <= dated <= parsed")
        has_error = "error_type" in status or "message" in status
        if status.get("status") in {"error", "empty"}:
            if not isinstance(status.get("error_type"), str) or not status.get("error_type", "").strip():
                problems.append(f"{where}.error_type should describe the failure")
            if not isinstance(status.get("message"), str) or not status.get("message", "").strip():
                problems.append(f"{where}.message should describe the failure")
        elif has_error:
            problems.append(f"{where} error fields are only valid for non-ok sources")
    return problems


def _validate_legacy_sources(sources: Any) -> list[str]:
    """Validate v3 observability records so frozen historical runs stay readable."""
    if not isinstance(sources, list):
        return ["'sources' should be a list"]
    required = {
        "source": str,
        "category": str,
        "status": str,
        "item_count": int,
        "undated_dropped": int,
        "duration_ms": int,
    }
    allowed = set(required) | {"error"}
    problems: list[str] = []
    for index, status in enumerate(sources):
        where = f"sources[{index}]"
        if not isinstance(status, dict):
            problems.append(f"{where} is not an object")
            continue
        if missing := set(required) - set(status):
            problems.append(f"{where} is missing {sorted(missing)}")
        if unknown := set(status) - allowed:
            problems.append(f"{where} has unknown field(s) {sorted(unknown)}")
        for field, expected in required.items():
            if field in status and not isinstance(status[field], expected):
                problems.append(f"{where}.{field} has the wrong type")
        if status.get("status") not in {"ok", "error"}:
            problems.append(f"{where}.status should be 'ok' or 'error'")
    return problems


def _validate_errors(errors: list[Any]) -> list[str]:
    problems: list[str] = []
    required = {"source_type", "source_id", "status", "error_type", "message", "duration_ms"}
    for index, error in enumerate(errors):
        where = f"errors[{index}]"
        if not isinstance(error, dict):
            problems.append(f"{where} is not an object")
            continue
        if set(error) != required:
            problems.append(f"{where} should contain exactly {sorted(required)}")
            continue
        for field in required - {"duration_ms"}:
            if not isinstance(error[field], str) or not error[field].strip():
                problems.append(f"{where}.{field} should be a non-empty string")
        if error.get("status") not in {"empty", "error"}:
            problems.append(f"{where}.status should be 'empty' or 'error'")
        duration = error.get("duration_ms")
        if isinstance(duration, bool) or not isinstance(duration, int) or duration < 0:
            problems.append(f"{where}.duration_ms should be a non-negative integer")
    return problems


def _validate_health_consistency(sources: list[Any], errors: list[Any],
                                 categories: set[str]) -> list[str]:
    """Cross-check the full outcomes against their compact error projections."""
    problems: list[str] = []
    valid_sources = [source for source in sources if isinstance(source, dict)]
    identities = [(source.get("source_type"), source.get("source_id"))
                  for source in valid_sources]
    if len(identities) != len(set(identities)):
        problems.append("sources contains a duplicate source_type/source_id identity")
    for index, source in enumerate(valid_sources):
        if source.get("category") not in categories:
            problems.append(f"sources[{index}].category is not present in categories")
        parsed = source.get("parsed_entries")
        dated = source.get("dated_entries")
        status = source.get("status")
        if status == "ok" and (not isinstance(parsed, int) or not isinstance(dated, int)
                               or parsed == 0 or dated == 0):
            problems.append(f"sources[{index}] cannot be ok with zero parsed or dated entries")
        if status == "empty" and source.get("http_success") is not True:
            problems.append(f"sources[{index}] empty status requires HTTP success")

    expected = {
        (source.get("source_type"), source.get("source_id"), source.get("status"),
         source.get("error_type"), source.get("message"), source.get("duration_ms"))
        for source in valid_sources if source.get("status") in {"empty", "error"}
    }
    actual_records = [
        (error.get("source_type"), error.get("source_id"), error.get("status"),
         error.get("error_type"), error.get("message"), error.get("duration_ms"))
        for error in errors if isinstance(error, dict)
    ]
    actual = set(actual_records)
    if len(actual_records) != len(actual):
        problems.append("errors contains a duplicate failure record")
    if actual != expected:
        problems.append("errors must exactly project every empty or failed source")
    return problems


def _validate_context_budget(context: dict[str, Any], processing: Any) -> list[str]:
    """Validate global limits and reconcile their aggregate telemetry."""
    required = {
        "field_limits": dict,
        "source_max_bytes": int,
        "source_max_tokens": int,
        "global_max_bytes": int,
        "global_max_tokens": int,
        "used_bytes": int,
        "estimated_tokens": int,
        "title_truncated": int,
        "summary_truncated": int,
        "field_budget_dropped": int,
        "source_budget_dropped": int,
        "global_budget_dropped": int,
    }
    problems: list[str] = []
    if set(context) != set(required):
        return [f"context_budget should contain exactly {sorted(required)}"]
    for field, expected_type in required.items():
        if not isinstance(context[field], expected_type) or isinstance(context[field], bool):
            problems.append(f"context_budget.{field} has the wrong type")
    field_limits = context.get("field_limits")
    expected_fields = {
        "title_bytes": ITEM_TITLE_MAX_BYTES,
        "title_tokens": ITEM_TITLE_MAX_TOKENS,
        "url_bytes": ITEM_URL_MAX_BYTES,
        "url_tokens": ITEM_URL_MAX_TOKENS,
        "summary_chars": ITEM_SUMMARY_MAX_CHARS,
        "summary_bytes": ITEM_SUMMARY_MAX_BYTES,
        "summary_tokens": ITEM_SUMMARY_MAX_TOKENS,
        "source_bytes": ITEM_SOURCE_MAX_BYTES,
        "source_tokens": ITEM_SOURCE_MAX_TOKENS,
        "query_bytes": ITEM_QUERY_MAX_BYTES,
        "query_tokens": ITEM_QUERY_MAX_TOKENS,
    }
    if field_limits != expected_fields:
        problems.append("context_budget.field_limits does not match schema limits")
    exact_limits = {
        "source_max_bytes": SOURCE_CONTEXT_MAX_BYTES,
        "source_max_tokens": SOURCE_CONTEXT_MAX_TOKENS,
        "global_max_bytes": GLOBAL_CONTEXT_MAX_BYTES,
        "global_max_tokens": GLOBAL_CONTEXT_MAX_TOKENS,
    }
    for field, expected_limit in exact_limits.items():
        if context.get(field) != expected_limit:
            problems.append(f"context_budget.{field} should be {expected_limit}")
    for field in required.keys() - {"field_limits"}:
        value = context.get(field)
        if isinstance(value, int) and not isinstance(value, bool) and value < 0:
            problems.append(f"context_budget.{field} should be non-negative")
    if isinstance(context.get("used_bytes"), int) and context["used_bytes"] > GLOBAL_CONTEXT_MAX_BYTES:
        problems.append("context_budget.used_bytes exceeds global_max_bytes")
    if (isinstance(context.get("estimated_tokens"), int)
            and context["estimated_tokens"] > GLOBAL_CONTEXT_MAX_TOKENS):
        problems.append("context_budget.estimated_tokens exceeds global_max_tokens")
    if isinstance(processing, dict):
        aggregate_fields = (
            "context_bytes", "estimated_tokens", "title_truncated",
            "summary_truncated", "field_budget_dropped",
            "source_budget_dropped", "global_budget_dropped",
        )
        expected_names = {
            "context_bytes": "used_bytes",
            **{field: field for field in aggregate_fields if field != "context_bytes"},
        }
        for processing_field, context_field in expected_names.items():
            values = [stats.get(processing_field) for stats in processing.values()
                      if isinstance(stats, dict)]
            numeric_values = [value for value in values
                              if isinstance(value, int) and not isinstance(value, bool)]
            if len(numeric_values) == len(values):
                if sum(numeric_values) != context.get(context_field):
                    problems.append(
                        f"context_budget.{context_field} does not match processing totals")
    return problems


def _validate_processing(processing: dict[str, Any],
                         categories: dict[str, Any], version: int) -> list[str]:
    problems: list[str] = []
    if set(processing) != set(categories):
        problems.append("processing should have one entry per category")
    for name, stats in processing.items():
        if not isinstance(stats, dict):
            problems.append(f"processing[{name!r}] is not an object")
            continue
        fields = PROCESSING_FIELDS + (V5_PROCESSING_FIELDS if version >= 5 else ())
        missing = [f for f in fields if f not in stats]
        if missing:
            problems.append(f"processing[{name!r}] is missing {missing}")
            continue
        if any(not isinstance(stats[f], int) for f in fields):
            problems.append(f"processing[{name!r}] has a non-integer counter")
            continue
        if any(isinstance(stats[f], bool) or stats[f] < 0 for f in fields):
            problems.append(f"processing[{name!r}] has a negative or boolean counter")
            continue
        accounted = (stats["kept"] + stats["relevance_dropped"]
                     + stats["duplicates_dropped"] + stats["source_cap_dropped"]
                     + stats["category_cap_dropped"]
                     + (stats["field_budget_dropped"] + stats["source_budget_dropped"]
                        + stats["global_budget_dropped"] if version >= 5 else 0))
        if accounted != stats["fetched"]:
            problems.append(
                f"processing[{name!r}] does not reconcile: kept plus drops is "
                f"{accounted}, fetched is {stats['fetched']}")
        items = categories.get(name)
        if isinstance(items, list) and stats["kept"] != len(items):
            problems.append(
                f"processing[{name!r}].kept is {stats['kept']} but the category "
                f"holds {len(items)} items")
    return problems
