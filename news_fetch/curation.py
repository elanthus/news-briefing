"""Category preparation: ordering, deduplication, caps, and context budgets."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import corpus_schema
from corpus_schema import canonicalize_url
from news_fetch.destinations import MAX_URL_BYTES, http_destination
from news_fetch.model import Item
from news_fetch.relevance import is_relevant_item

DEFAULT_SOURCE_CAP = 25
DEFAULT_CATEGORY_CAP = 60
# dev_community's five subreddits would otherwise each contribute up to
# DEFAULT_SOURCE_CAP items, and Reddit's higher post volume then crowds out
# the category's vendor release feeds and Hacker News coverage. A lower cap
# bounds Reddit's share without removing it as a source; prepare_category()
# takes min() with the configured --source-cap so an explicitly stricter cap still wins.
REDDIT_SOURCE_CAP = 8


def dedupe(items: list[Item]) -> list[Item]:
    """Drop canonical URL duplicates and near-duplicate titles, keep first seen."""
    seen_urls, seen_titles, out = set(), set(), []
    for item in items:
        url = canonicalize_url(item.get("url", ""))
        title_key = re.sub(r"\W+", "", item.get("title", "").lower())[:60]
        # An empty url means extraction failed, not that two items match.
        if (url and url in seen_urls) or (title_key and title_key in seen_titles):
            continue
        if url:
            seen_urls.add(url)
        if title_key:
            seen_titles.add(title_key)
        out.append(item)
    return out


def sort_items(items: list[Item]) -> list[Item]:
    """Order a category newest first.

    Recency is the only ordering that means the same thing across RSS, HN and
    Reddit. Engagement (``points``, ``comments``) stays on the corpus item but
    does not determine corpus order, and the runner's model projection omits
    it, so the model never sees it.
    """
    def timestamp(item: Item) -> datetime:
        try:
            parsed = datetime.fromisoformat(item["published"])
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=timezone.utc)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return datetime.min.replace(tzinfo=timezone.utc)
        return parsed

    return sorted(items, key=timestamp, reverse=True)


def _truncate_utf8(value: str, max_bytes: int,
                   max_chars: int | None = None) -> tuple[str, bool]:
    """Truncate without splitting a Unicode code point."""
    bounded = value[:max_chars] if max_chars is not None else value
    encoded = bounded.encode("utf-8")
    if len(encoded) > max_bytes:
        bounded = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return bounded, bounded != value


def _apply_field_budgets(items: list[Item]) -> tuple[list[Item], dict[str, int]]:
    """Bound model-visible strings and drop URLs that cannot be kept intact."""
    kept: list[Item] = []
    telemetry = {
        "title_truncated": 0,
        "summary_truncated": 0,
        "field_budget_dropped": 0,
    }
    for item in items:
        candidate = item.copy()
        title = candidate.get("title")
        source = candidate.get("source")
        url = candidate.get("url")
        if (not isinstance(title, str) or not title.strip()
                or not isinstance(source, str) or not source.strip()
                or len(source.encode("utf-8")) > corpus_schema.ITEM_SOURCE_MAX_BYTES
                or not isinstance(url, str)):
            telemetry["field_budget_dropped"] += 1
            continue
        try:
            http_destination(url)
            discussion = candidate.get("discussion")
            if discussion is not None:
                if not isinstance(discussion, str):
                    raise ValueError("discussion URL is not a string")
                http_destination(discussion)
        except ValueError:
            # URLs are identities and destinations. Truncating one would turn
            # it into a different, possibly unsafe request, so reject the item.
            telemetry["field_budget_dropped"] += 1
            continue
        query = candidate.get("query")
        if (query is not None
                and (not isinstance(query, str)
                     or len(query.encode("utf-8")) > corpus_schema.ITEM_QUERY_MAX_BYTES)):
            telemetry["field_budget_dropped"] += 1
            continue
        candidate["title"], title_truncated = _truncate_utf8(
            title, corpus_schema.ITEM_TITLE_MAX_BYTES)
        telemetry["title_truncated"] += int(title_truncated)
        summary = candidate.get("summary")
        if summary is not None:
            if not isinstance(summary, str):
                telemetry["field_budget_dropped"] += 1
                continue
            candidate["summary"], summary_truncated = _truncate_utf8(
                summary, corpus_schema.ITEM_SUMMARY_MAX_BYTES, corpus_schema.ITEM_SUMMARY_MAX_CHARS)
            telemetry["summary_truncated"] += int(summary_truncated)
        kept.append(candidate)
    return kept, telemetry


def item_context_usage(item: Item) -> tuple[int, int]:
    """Serialized bytes and a documented tokenizer-independent estimate.

    Four UTF-8 bytes per token is a conventional planning estimate. The hard
    byte budgets remain authoritative for memory even when a model tokenizes a
    particular language more densely.
    """
    size = len(json.dumps(
        item, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8"))
    return size, corpus_schema.estimated_tokens_for_bytes(size)


def _source_budget_key(item: Item) -> tuple[str, str]:
    return item.get("source", "unknown"), item.get("query", "")


def prepare_category(items: list[Item], source_cap: int = DEFAULT_SOURCE_CAP,
                     category_cap: int = DEFAULT_CATEGORY_CAP,
                     undated_dropped: int = 0,
                     source_byte_budget: int = corpus_schema.SOURCE_CONTEXT_MAX_BYTES,
                     source_token_budget: int = corpus_schema.SOURCE_CONTEXT_MAX_TOKENS,
                     ) -> tuple[list[Item], dict[str, int]]:
    """Filter, deduplicate, diversify, and bound one category for model input.

    Returns both the retained items and counts for observability. Caps are
    applied newest-first, and field bounding works on copies rather than
    mutating the input items.

    `undated_dropped` is counted by the fetchers, before an item ever reaches
    this function, and is carried through so every reason an item is missing
    from the corpus appears in one place.

    Reddit sources (`r/...`) use the smaller of `source_cap` and
    `REDDIT_SOURCE_CAP`, so a handful of subreddits cannot fill a shared
    category cap on their own while an explicitly stricter `source_cap` still
    wins. Every other source uses `source_cap` unchanged.
    """
    fetched = len(items)
    bounded, field_telemetry = _apply_field_budgets(items)
    relevant = [item for item in bounded if is_relevant_item(item)]
    # Dedupe after ordering so a syndicated/updated story keeps its newest
    # occurrence rather than whichever source happened to finish first.
    unique = dedupe(sort_items(relevant))
    kept: list[Item] = []
    by_source: dict[str, int] = {}
    source_usage: dict[tuple[str, str], tuple[int, int]] = {}
    source_cap_dropped = 0
    source_budget_dropped = 0
    category_cap_dropped = 0
    context_bytes = 0
    estimated_tokens = 0
    for index, item in enumerate(unique):
        if len(kept) >= category_cap:
            category_cap_dropped = len(unique) - index
            break
        source = item.get("source", "unknown")
        cap = min(source_cap, REDDIT_SOURCE_CAP) if source.startswith("r/") else source_cap
        if by_source.get(source, 0) >= cap:
            source_cap_dropped += 1
            continue
        size, tokens = item_context_usage(item)
        source_key = _source_budget_key(item)
        used_bytes, used_tokens = source_usage.get(source_key, (0, 0))
        if (used_bytes + size > source_byte_budget
                or used_tokens + tokens > source_token_budget):
            source_budget_dropped += 1
            continue
        kept.append(item)
        by_source[source] = by_source.get(source, 0) + 1
        source_usage[source_key] = used_bytes + size, used_tokens + tokens
        context_bytes += size
        estimated_tokens += tokens
    stats = {
        "fetched": fetched,
        "undated_dropped": undated_dropped,
        "relevance_dropped": len(bounded) - len(relevant),
        "duplicates_dropped": len(relevant) - len(unique),
        "source_cap_dropped": source_cap_dropped,
        "category_cap_dropped": category_cap_dropped,
        "field_budget_dropped": field_telemetry["field_budget_dropped"],
        "source_budget_dropped": source_budget_dropped,
        "global_budget_dropped": 0,
        "title_truncated": field_telemetry["title_truncated"],
        "summary_truncated": field_telemetry["summary_truncated"],
        "context_bytes": context_bytes,
        "estimated_tokens": estimated_tokens,
        "kept": len(kept),
    }
    return kept, stats


def apply_global_context_budget(categories: dict[str, list[Item]],
                                processing: dict[str, dict[str, int]],
                                byte_budget: int = corpus_schema.GLOBAL_CONTEXT_MAX_BYTES,
                                token_budget: int = corpus_schema.GLOBAL_CONTEXT_MAX_TOKENS,
                                ) -> tuple[int, int]:
    """Apply one final budget across every category in configured order."""
    used_bytes = 0
    used_tokens = 0
    for category, items in categories.items():
        retained: list[Item] = []
        category_bytes = 0
        category_tokens = 0
        for item in items:
            size, tokens = item_context_usage(item)
            if used_bytes + size > byte_budget or used_tokens + tokens > token_budget:
                processing[category]["global_budget_dropped"] += 1
                processing[category]["kept"] -= 1
                continue
            retained.append(item)
            used_bytes += size
            used_tokens += tokens
            category_bytes += size
            category_tokens += tokens
        categories[category] = retained
        processing[category]["context_bytes"] = category_bytes
        processing[category]["estimated_tokens"] = category_tokens
    return used_bytes, used_tokens


def curate_categories(corpus: dict[str, Any], undated: dict[str, int],
                      source_cap: int, category_cap: int) -> tuple[int, int]:
    """Prepare every category, then apply the global context budget."""
    for category in corpus["categories"]:
        items, stats = prepare_category(
            corpus["categories"][category],
            source_cap=source_cap,
            category_cap=category_cap,
            undated_dropped=undated[category],
        )
        corpus["categories"][category] = items
        corpus["processing"][category] = stats

    return apply_global_context_budget(
        corpus["categories"], corpus["processing"])


def context_budget_report(processing: dict[str, dict[str, int]], used_bytes: int,
                          estimated_tokens: int) -> dict[str, Any]:
    """The corpus `context_budget` record: limits, usage, and drop totals."""
    return {
        "field_limits": {
            "title_bytes": corpus_schema.ITEM_TITLE_MAX_BYTES,
            "title_tokens": corpus_schema.ITEM_TITLE_MAX_TOKENS,
            "url_bytes": MAX_URL_BYTES,
            "url_tokens": corpus_schema.ITEM_URL_MAX_TOKENS,
            "summary_chars": corpus_schema.ITEM_SUMMARY_MAX_CHARS,
            "summary_bytes": corpus_schema.ITEM_SUMMARY_MAX_BYTES,
            "summary_tokens": corpus_schema.ITEM_SUMMARY_MAX_TOKENS,
            "source_bytes": corpus_schema.ITEM_SOURCE_MAX_BYTES,
            "source_tokens": corpus_schema.ITEM_SOURCE_MAX_TOKENS,
            "query_bytes": corpus_schema.ITEM_QUERY_MAX_BYTES,
            "query_tokens": corpus_schema.ITEM_QUERY_MAX_TOKENS,
        },
        "source_max_bytes": corpus_schema.SOURCE_CONTEXT_MAX_BYTES,
        "source_max_tokens": corpus_schema.SOURCE_CONTEXT_MAX_TOKENS,
        "global_max_bytes": corpus_schema.GLOBAL_CONTEXT_MAX_BYTES,
        "global_max_tokens": corpus_schema.GLOBAL_CONTEXT_MAX_TOKENS,
        "used_bytes": used_bytes,
        "estimated_tokens": estimated_tokens,
        "title_truncated": sum(
            stats["title_truncated"] for stats in processing.values()),
        "summary_truncated": sum(
            stats["summary_truncated"] for stats in processing.values()),
        "field_budget_dropped": sum(
            stats["field_budget_dropped"] for stats in processing.values()),
        "source_budget_dropped": sum(
            stats["source_budget_dropped"] for stats in processing.values()),
        "global_budget_dropped": sum(
            stats["global_budget_dropped"] for stats in processing.values()),
    }
