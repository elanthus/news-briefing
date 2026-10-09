"""Reddit adapter: anonymous RSS, then Arctic Shift, then ScrapeCreators."""

from __future__ import annotations

import json
import math
import re
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from news_fetch import transport
from news_fetch.feed_xml import parse_feed_xml
from news_fetch.model import FetchResult, Item, _raise_data_error
from news_fetch.sources.common import parse_feed_date, publication_in_window, strip_html
from news_fetch.transport import REDDIT_TIMEOUT

REDDIT_FALLBACK_LIMIT = 100
REDDIT_MIN_SCORE = 2
ARCTIC_SHIFT_POSTS_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
SCRAPECREATORS_SUBREDDIT_URL = "https://api.scrapecreators.com/v1/reddit/subreddit"
REDDIT_PAUSE_SECONDS = 2  # Reddit rate-limits bursts; space serial requests
REDDIT_RSS_LIMIT = 25


def _reddit_md_text(atom_content: str) -> str:
    """Extract post body from Reddit's atom:content HTML (the <div class="md"> block)."""
    m = re.search(r'class="md">(.*?)</div>', atom_content, re.DOTALL | re.IGNORECASE)
    return strip_html(m.group(1)).strip() if m else ""


def reddit_top_bucket(hours: int) -> str:
    """Reddit's coarse `t=` bucket for a window.

    The `top` endpoints take a bucket, not an arbitrary window. Real windows
    run 23-25 hours, so "day" or "week" covers them; the exact publication
    window filter applied to every backend does the real work.
    """
    return "day" if hours <= 24 else "week"


def fetch_reddit_rss(
    subreddit: str, cutoff: datetime, window_end: datetime, hours: int
) -> FetchResult:
    """Fetch top posts via anonymous RSS.

    The RSS response does not expose vote counts, so these items carry no
    engagement score.
    """
    url = (f"https://www.reddit.com/r/{subreddit}/top/.rss"
           f"?t={reddit_top_bucket(hours)}&limit={REDDIT_RSS_LIMIT}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    # A 429 propagates so fetch_reddit() falls through to Arctic Shift, which
    # serves the exact window, instead of sleeping on a retry here.
    payload = transport.http_get(url, timeout=REDDIT_TIMEOUT)
    try:
        root = parse_feed_xml(payload)
    except (ET.ParseError, ValueError) as exc:
        _raise_data_error(exc)

    items: list[Item] = []
    undated = 0
    entries = root.findall("atom:entry", ns)
    dated_entries = 0
    filtered = 0
    for entry in entries:
        published = parse_feed_date(
            entry.findtext("atom:updated", namespaces=ns)
            or entry.findtext("atom:published", namespaces=ns))
        if published is None:
            undated += 1
            continue
        dated_entries += 1
        if not publication_in_window(published, cutoff, window_end):
            continue
        link = entry.find("atom:link", ns)
        raw_content = entry.findtext("atom:content", namespaces=ns) or ""
        title = strip_html(entry.findtext("atom:title", namespaces=ns) or "")
        # atom:content has the post HTML; extract just the body text
        summary = _reddit_md_text(raw_content)
        if _reddit_post_was_removed({"title": title, "selftext": summary}):
            filtered += 1
            continue
        items.append({
            "title": title,
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            "summary": summary,
            "source": f"r/{subreddit}",
        })
    return FetchResult(items, undated, len(entries), dated_entries, filtered)


def _reddit_json_datetime(post: dict[str, Any]) -> datetime | None:
    """Read either Reddit's epoch timestamp or a provider ISO timestamp."""
    epoch = post.get("created_utc")
    if isinstance(epoch, (int, float)) and not isinstance(epoch, bool):
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(epoch, str):
        try:
            return datetime.fromtimestamp(float(epoch), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            pass
    for field in ("created_at_iso", "created_at"):
        value = post.get(field)
        if isinstance(value, str) and (published := parse_feed_date(value)) is not None:
            return published
    return None


def _reddit_post_id(post: dict[str, Any]) -> str:
    raw_id = str(post.get("id") or post.get("post_id") or "").removeprefix("t3_")
    if not raw_id:
        permalink = str(post.get("permalink") or "")
        match = re.search(r"/comments/([0-9a-z]+)/", permalink, re.IGNORECASE)
        raw_id = match.group(1) if match else ""
    return raw_id.lower() if re.fullmatch(r"[0-9a-z]+", raw_id, re.IGNORECASE) else ""


def _reddit_post_was_removed(post: dict[str, Any]) -> bool:
    """Whether a JSON provider exposes a definite removal or deletion signal."""
    markers = {"[deleted]", "[removed]"}
    # author is deliberately not checked: "[deleted]" there means the account
    # is gone, not that the post was removed.
    for field in ("title", "selftext"):
        if str(post.get(field) or "").strip().casefold() in markers:
            return True
    return any(
        post.get(field) not in (None, "", False)
        for field in (
            "banned_at_utc",
            "banned_by",
            "removal_reason",
            "removed_by",
            "removed_by_category",
        )
    )


def _reddit_post_has_low_score(post: dict[str, Any]) -> bool:
    """Apply the score floor only when a provider supplies a numeric score."""
    score = post.get("score")
    if isinstance(score, bool):
        return False
    if isinstance(score, int):
        return score < REDDIT_MIN_SCORE
    if isinstance(score, float) and math.isfinite(score):
        return score < REDDIT_MIN_SCORE
    if isinstance(score, str) and re.fullmatch(r"[+-]?\d+", score.strip()):
        try:
            return int(score) < REDDIT_MIN_SCORE
        except ValueError:
            return False
    return False


def _reddit_json_result(
    posts: list[Any], subreddit: str, cutoff: datetime, window_end: datetime,
    apply_score_floor: bool = True,
) -> FetchResult:
    """Normalize one JSON provider without trusting its outbound destinations."""
    items: list[Item] = []
    parsed_entries = 0
    dated_entries = 0
    undated = 0
    filtered = 0
    encoded_subreddit = urllib.parse.quote(subreddit, safe="")
    for raw_post in posts:
        if not isinstance(raw_post, dict):
            continue
        title = strip_html(str(raw_post.get("title") or ""))
        post_id = _reddit_post_id(raw_post)
        if not title or not post_id:
            continue
        parsed_entries += 1
        published = _reddit_json_datetime(raw_post)
        if published is None:
            undated += 1
            continue
        dated_entries += 1
        if not publication_in_window(published, cutoff, window_end):
            continue
        if _reddit_post_was_removed(raw_post) or (
            apply_score_floor and _reddit_post_has_low_score(raw_post)
        ):
            filtered += 1
            continue
        item: Item = {
            "title": title,
            "url": f"https://www.reddit.com/r/{encoded_subreddit}/comments/{post_id}/",
            "published": published.isoformat(),
            "source": f"r/{subreddit}",
        }
        selftext = str(raw_post.get("selftext") or "").strip()
        if selftext:
            item["summary"] = selftext
        items.append(item)
    return FetchResult(items, undated, parsed_entries, dated_entries, filtered)


def _json_object(payload: bytes, provider: str) -> dict[str, Any]:
    try:
        parsed = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        _raise_data_error(exc)
    if not isinstance(parsed, dict):
        _raise_data_error(ValueError(f"{provider} response was not a JSON object"))
    return parsed


def fetch_reddit_arctic_shift(
    subreddit: str, cutoff: datetime, window_end: datetime
) -> FetchResult:
    """Fetch an exact subreddit window from the free Arctic Shift archive."""
    params = urllib.parse.urlencode({
        "subreddit": subreddit,
        # Arctic Shift documents ISO 8601 with ``Z`` but rejects Python's
        # equivalent ``+00:00`` spelling, so use its unambiguous epoch form.
        "after": int(cutoff.timestamp()),
        "before": math.ceil(window_end.timestamp()),
        "limit": REDDIT_FALLBACK_LIMIT,
        "sort": "desc",
        # Arctic Shift rejects unknown field names with HTTP 400, and Reddit's
        # removal metadata (banned_by, removed_by_category, ...) is not among
        # the names it accepts, so request only fields it serves.
        "fields": "id,title,selftext,created_utc,subreddit,score,num_comments",
    })
    payload = transport.http_get(f"{ARCTIC_SHIFT_POSTS_URL}?{params}", timeout=REDDIT_TIMEOUT)
    response = _json_object(payload, "Arctic Shift")
    posts = response.get("data")
    if not isinstance(posts, list):
        _raise_data_error(ValueError("Arctic Shift response did not contain a data list"))
    # Arctic Shift archives the score captured at ingest, which is ~1 for
    # every post this window is young enough to contain, so a live-score
    # floor would empty the backend.
    return _reddit_json_result(posts, subreddit, cutoff, window_end,
                               apply_score_floor=False)


def fetch_reddit_scrapecreators(
    subreddit: str,
    cutoff: datetime,
    window_end: datetime,
    hours: int,
    api_key: str,
) -> FetchResult:
    """Fetch recent subreddit posts through the authenticated final fallback."""
    params = urllib.parse.urlencode({
        "subreddit": subreddit,
        "timeframe": reddit_top_bucket(hours),
        # ScrapeCreators rejects a timeframe unless sorting by top, which
        # also mirrors the RSS backend's top-of-bucket semantics.
        "sort": "top",
        "trim": "true",
    })
    payload = transport.scrapecreators_get(
        f"{SCRAPECREATORS_SUBREDDIT_URL}?{params}", api_key, timeout=REDDIT_TIMEOUT
    )
    response = _json_object(payload, "ScrapeCreators")
    if response.get("success") is False:
        _raise_data_error(ValueError("ScrapeCreators reported an unsuccessful request"))
    posts = response.get("posts")
    if not isinstance(posts, list):
        _raise_data_error(ValueError("ScrapeCreators response did not contain a posts list"))
    return _reddit_json_result(posts, subreddit, cutoff, window_end)


def fetch_reddit(
    subreddit: str,
    cutoff: datetime,
    window_end: datetime,
    hours: int,
    scrapecreators_api_key: str | None = None,
) -> FetchResult:
    """Fetch one subreddit through RSS, Arctic Shift, then ScrapeCreators."""
    errors: list[str] = []
    empty_result: FetchResult | None = None
    free_backends: tuple[tuple[str, Callable[..., FetchResult], tuple[Any, ...]], ...] = (
        ("RSS", fetch_reddit_rss, (subreddit, cutoff, window_end, hours)),
        ("Arctic Shift", fetch_reddit_arctic_shift, (subreddit, cutoff, window_end)),
    )
    for backend, fetcher, arguments in free_backends:
        try:
            result = fetcher(*arguments)
        except Exception as exc:
            errors.append(f"{backend}: {type(exc).__name__}: {exc}")
            continue
        if result.items:
            return result
        empty_result = result

    if scrapecreators_api_key:
        try:
            return fetch_reddit_scrapecreators(
                subreddit,
                cutoff,
                window_end,
                hours,
                scrapecreators_api_key,
            )
        except Exception as exc:
            errors.append(f"ScrapeCreators: {type(exc).__name__}: {exc}")
            if empty_result is not None:
                return empty_result
            raise RuntimeError(
                f"all Reddit backends failed or returned no usable posts for r/{subreddit}: "
                + "; ".join(errors)
            ) from exc

    if empty_result is not None:
        return empty_result
    raise RuntimeError(
        f"all free Reddit backends failed for r/{subreddit}: " + "; ".join(errors)
    )
