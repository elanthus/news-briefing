"""Source configuration loading and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, NamedTuple

import corpus_schema
from news_fetch.destinations import validate_source_url
from news_fetch.limits import SOURCE_ID_BYTES


class Sources(NamedTuple):
    # Preserved from configuration: this order drives both corpus JSON keys and
    # the section order of the human-readable --markdown digest.
    categories: tuple[str, ...]
    rss_feeds: dict[str, list[tuple[str, str]]]
    hn_category: str
    hn_queries: list[str]
    reddit_category: str
    subreddits: list[str]


def _source_id_problem(value: Any) -> str | None:
    """Why a value cannot be used as an exact machine-readable source ID."""
    if not isinstance(value, str) or not value.strip():
        return "must be a non-empty string"
    if "\n" in value or "\r" in value:
        return "must be single-line"
    if len(value.encode("utf-8")) > SOURCE_ID_BYTES:
        return f"must not exceed {SOURCE_ID_BYTES} UTF-8 bytes"
    return None


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(f"{field} must be a list")
    for index, item in enumerate(value):
        if problem := _source_id_problem(item):
            raise ValueError(f"{field}[{index}] {problem}")
    if len(value) != len(set(value)):
        raise ValueError(f"{field} contains a duplicate source ID")
    return value


def load_sources(path: str | Path) -> Sources:
    """Load and validate source configuration from a JSON file."""
    source_path = Path(path)
    try:
        raw = json.loads(source_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc

    if not isinstance(raw, dict):
        raise ValueError("top level must be a JSON object")

    expected = {
        "categories",
        "rss_feeds",
        "hn_category",
        "hn_queries",
        "reddit_category",
        "subreddits",
    }
    missing = expected - raw.keys()
    unknown = raw.keys() - expected
    if missing:
        raise ValueError(f"missing field(s): {', '.join(sorted(missing))}")
    if unknown:
        raise ValueError(f"unknown field(s): {', '.join(sorted(unknown))}")

    category_values = raw["categories"]
    if (not isinstance(category_values, list) or not category_values
            or any(not corpus_schema.valid_category_name(category)
                   for category in category_values)):
        raise ValueError("categories must be a non-empty list of category names")
    if len(category_values) != len(set(category_values)):
        raise ValueError("categories contains a duplicate")
    categories = tuple(category_values)
    category_set = set(categories)

    rss_raw = raw["rss_feeds"]
    if not isinstance(rss_raw, dict):
        raise ValueError("rss_feeds must be an object mapping categories to feeds")

    invalid_categories = set(rss_raw) - category_set
    if invalid_categories:
        raise ValueError(
            f"rss_feeds contains undeclared categories: {', '.join(sorted(invalid_categories))}")

    rss_feeds: dict[str, list[tuple[str, str]]] = {}
    rss_source_ids: set[str] = set()
    for category, feeds in rss_raw.items():
        if not isinstance(category, str) or not isinstance(feeds, list):
            raise ValueError("rss_feeds must map category names to lists")
        parsed_feeds: list[tuple[str, str]] = []
        for index, feed in enumerate(feeds):
            if (not isinstance(feed, list) or len(feed) != 2
                    or any(not isinstance(part, str) or not part.strip() for part in feed)):
                raise ValueError(
                    f"rss_feeds.{category}[{index}] must be a [source name, URL] pair of non-empty strings"
                )
            name, url = feed
            if problem := _source_id_problem(name):
                raise ValueError(
                    f"rss_feeds.{category}[{index}] source name {problem}")
            try:
                validate_source_url(url)
            except ValueError as exc:
                raise ValueError(
                    f"rss_feeds.{category}[{index}] has unsafe URL: {exc}") from exc
            if name in rss_source_ids:
                raise ValueError(f"rss_feeds contains duplicate source ID {name!r}")
            rss_source_ids.add(name)
            parsed_feeds.append((name, url))
        rss_feeds[category] = parsed_feeds

    destinations: dict[str, str] = {}
    for field in ("hn_category", "reddit_category"):
        destination = raw[field]
        if not corpus_schema.valid_category_name(destination):
            raise ValueError(f"{field} must be a category name")
        if destination not in category_set:
            raise ValueError(f"{field} references undeclared category: {destination}")
        destinations[field] = destination

    hn_queries = _string_list(raw["hn_queries"], "hn_queries")
    subreddits = _string_list(raw["subreddits"], "subreddits")

    routed_categories = {
        category for category, feeds in rss_feeds.items() if feeds
    }
    if hn_queries:
        routed_categories.add(destinations["hn_category"])
    if subreddits:
        routed_categories.add(destinations["reddit_category"])
    unrouted = category_set - routed_categories
    if unrouted:
        raise ValueError(
            "categories without a source destination: "
            + ", ".join(sorted(unrouted)))

    return Sources(
        categories=categories,
        rss_feeds=rss_feeds,
        hn_category=destinations["hn_category"],
        hn_queries=hn_queries,
        reddit_category=destinations["reddit_category"],
        subreddits=subreddits,
    )
