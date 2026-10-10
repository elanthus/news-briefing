"""Hacker News Algolia search adapter."""

from __future__ import annotations

import json
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from news_fetch import transport
from news_fetch.model import FetchResult, Item, SourceDataError, _raise_data_error
from news_fetch.sources.common import publication_in_window, strip_html

HN_MIN_POINTS = 20  # minimum engagement required for a Hacker News item
HN_HITS_PER_PAGE = 25


def _hn_engagement(hit: dict[str, Any], field: str) -> int:
    """Return a Hacker News engagement count; a missing or null count is zero."""
    value = hit.get(field)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SourceDataError(ValueError(
            f"Hacker News hit has a non-integer or negative {field}"
        ))
    return value


def fetch_hn(query: str, cutoff: datetime, window_end: datetime) -> FetchResult:
    """HN Algolia API with an exact fixed publication window.

    The request applies both `created_at_i` bounds; this function repeats the
    exact half-open check and applies the points threshold to returned hits.
    Hits that arrive in-window but below the points floor are counted as
    `filtered_entries` rather than silently dropped, so a narrow query that
    retrieved stories none of which reached the floor is reported as quiet
    (retrieval succeeded, popularity did not) rather than indistinguishable
    from a query that found nothing in the window at all.
    """
    numeric_filters = (
        f"created_at_i>={int(cutoff.timestamp())},"
        f"created_at_i<{int(window_end.timestamp())}"
    )
    url = "https://hn.algolia.com/api/v1/search?" + urllib.parse.urlencode({
        "tags": "story",
        "query": query,
        "numericFilters": numeric_filters,
        "hitsPerPage": HN_HITS_PER_PAGE,
    })
    payload = transport.http_get(url)
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _raise_data_error(exc)
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise SourceDataError(ValueError("Hacker News response has no hits array"))
    items: list[Item] = []
    parsed_entries = 0
    dated_entries = 0
    filtered = 0
    malformed = 0
    hits = data["hits"]
    for hit in hits:
        if not isinstance(hit, dict):
            malformed += 1
            continue
        parsed_entries += 1
        timestamp = hit.get("created_at_i")
        if timestamp is None:
            continue
        try:
            if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
                raise ValueError("Hacker News timestamp is not a number")
            published = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            malformed += 1
            continue
        # A malformed engagement/body field does not undo a parsed date.
        dated_entries += 1
        if not publication_in_window(published, cutoff, window_end):
            continue
        try:
            points = _hn_engagement(hit, "points")
            comments = _hn_engagement(hit, "num_comments")
            if points < HN_MIN_POINTS:
                filtered += 1
                continue
            title = hit.get("title", "")
            story_text = hit.get("story_text")
            if story_text is None:
                story_text = ""
            destination = hit.get("url")
            object_id = hit.get("objectID")
            if (not isinstance(title, str) or not title.strip()
                    or not isinstance(story_text, str)
                    or (destination is not None and not isinstance(destination, str))
                    or not isinstance(object_id, str) or not object_id.isdecimal()):
                raise ValueError("Hacker News hit has malformed story fields")
            discussion = f"https://news.ycombinator.com/item?id={object_id}"
            items.append({
                "title": title,
                "url": destination or discussion,
                "discussion": discussion,
                "published": published.isoformat(),
                "summary": strip_html(story_text),
                "points": points,
                "comments": comments,
                "source": "Hacker News",
                "query": query,
            })
        except (TypeError, ValueError):
            malformed += 1
    return FetchResult(items, parsed_entries, dated_entries, filtered, malformed)
