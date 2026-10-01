"""Fetch every configured source into a new corpus, with per-source health."""

from __future__ import annotations

import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from typing import Any

import corpus_schema
from news_fetch.config import Sources
from news_fetch.model import TimedFetchResult
from news_fetch.sources.hn import fetch_hn
from news_fetch.sources.reddit import REDDIT_PAUSE_SECONDS, fetch_reddit
from news_fetch.sources.rss import fetch_rss
from news_fetch.telemetry import source_status, timed_fetch

FETCH_WORKERS = 8


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
            outcome = future.result()
            status = source_status(source_type, source_id, category, outcome)
            if outcome.result is None:
                corpus["sources"].append(status)
                continue
            result = outcome.result
            corpus["categories"][category].extend(result.items)
            undated[category] += result.undated
            corpus["sources"].append(status)


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
        status = source_status("reddit", sub, sources.reddit_category, outcome)
        if outcome.result is not None:
            result = outcome.result
            corpus["categories"][sources.reddit_category].extend(result.items)
            undated[sources.reddit_category] += result.undated
        corpus["sources"].append(status)
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
