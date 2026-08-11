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

# 3 makes the category set configuration-defined. Older readers expect the
# built-in v2 names, so the version changes even though the surrounding JSON
# shape is unchanged: they should refuse a new corpus instead of misdiagnosing
# a valid custom category as schema drift.
SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 0  # corpora written before the field existed

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


def _iso(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


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

    if isinstance(corpus.get("schema_version"), int):
        if corpus["schema_version"] != SCHEMA_VERSION:
            problems.append(
                f"schema_version is {corpus['schema_version']}, "
                f"this code writes {SCHEMA_VERSION}")

    for field in ("generated_at", "cutoff"):
        if field in corpus and not _iso(corpus[field]):
            problems.append(f"{field!r} is not an ISO 8601 timestamp")

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
            problems += _validate_items(name, items)

    processing = corpus.get("processing")
    if isinstance(processing, dict) and isinstance(categories, dict):
        problems += _validate_processing(processing, categories)

    errors = corpus.get("errors")
    if isinstance(errors, list):
        problems += [f"errors[{i}] is not a string" for i, e in enumerate(errors)
                     if not isinstance(e, str)]

    return problems


def _validate_items(category: str, items: Any) -> list[str]:
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
        if "published" in item and not _iso(item["published"]):
            problems.append(f"{where}.published is not an ISO 8601 timestamp")
    return problems


def _validate_processing(processing: dict[str, Any],
                         categories: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if set(processing) != set(categories):
        problems.append("processing should have one entry per category")
    for name, stats in processing.items():
        if not isinstance(stats, dict):
            problems.append(f"processing[{name!r}] is not an object")
            continue
        missing = [f for f in PROCESSING_FIELDS if f not in stats]
        if missing:
            problems.append(f"processing[{name!r}] is missing {missing}")
            continue
        if any(not isinstance(stats[f], int) for f in PROCESSING_FIELDS):
            problems.append(f"processing[{name!r}] has a non-integer counter")
            continue
        accounted = (stats["kept"] + stats["relevance_dropped"]
                     + stats["duplicates_dropped"] + stats["source_cap_dropped"]
                     + stats["category_cap_dropped"])
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
