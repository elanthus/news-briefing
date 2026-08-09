#!/usr/bin/env python3
"""Deterministic news fetcher for the daily briefing.

Pulls RSS feeds, the Hacker News Algolia API, and Reddit RSS endpoints,
drops everything older than the cutoff (default 24h) IN CODE, and emits a
JSON corpus grouped by category. The LLM only ranks and summarizes what
this script outputs — it never decides what counts as "recent."

Usage:
    python3 fetch_news.py                 # JSON to stdout, 24h window
    python3 fetch_news.py --hours 12
    python3 fetch_news.py --markdown      # human-readable digest instead
    python3 fetch_news.py -o corpus.json
"""

import argparse
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import namedtuple
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

USER_AGENT = "news-briefing/1.0 (personal daily digest script)"
TIMEOUT = 20
REDDIT_TIMEOUT = 10
REDDIT_MAX_ATTEMPTS = 2
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
DEFAULT_WINDOW_HOURS = 24
DEFAULT_SOURCE_CAP = 25
DEFAULT_CATEGORY_CAP = 60

FETCH_WORKERS = 8
SUMMARY_CHARS = 300  # per-item summary budget handed to the model
HN_MIN_POINTS = 20  # below this a story hasn't cleared HN's noise floor
HN_HITS_PER_PAGE = 25
REDDIT_PAUSE_SECONDS = 2  # Reddit rate-limits bursts; space serial requests
REDDIT_RETRY_MAX_SLEEP = 30  # ceiling on a server-supplied Retry-After

# category -> list of (source_name, feed_url)
RSS_FEEDS = {
    "us_politics": [
        ("NPR Politics", "https://feeds.npr.org/1014/rss.xml"),
        ("Politico", "https://rss.politico.com/politics-news.xml"),
        ("The Hill", "https://thehill.com/homenews/feed/"),
        ("Axios", "https://api.axios.com/feed/"),
    ],
    "world": [
        ("BBC World", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("NPR World", "https://feeds.npr.org/1004/rss.xml"),
        ("Al Jazeera", "https://www.aljazeera.com/xml/rss/all.xml"),
    ],
    "ai_tech": [
        ("OpenAI News", "https://openai.com/news/rss.xml"),
        ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("Wired", "https://www.wired.com/feed/rss"),
        ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ],
    "dev_community": [
        ("GitHub Changelog", "https://github.blog/changelog/feed/"),
    ],
}

# Dev tools / practices: engagement-bearing community sources
HN_QUERIES = ["claude code", "cursor", "codex", "mcp", "llm agent", "prompt engineering"]
SUBREDDITS = ["ClaudeAI", "ClaudeCode", "LocalLLaMA", "cursor"]

# Reddit's "top" RSS endpoint takes a coarse bucket (t=), not an arbitrary
# window, so it can't express arbitrary hour ranges directly. Over-fetch the
# smallest bucket that fully covers the requested window and let the exact
# cutoff filter in fetch_reddit() do the real work — same rule as every
# other source.
REDDIT_TOP_BUCKETS = ((1, "hour"), (24, "day"), (168, "week"),
                      (720, "month"), (8760, "year"))
REDDIT_BASE_LIMIT = 25
REDDIT_MAX_LIMIT = 100  # Reddit's own ceiling for this endpoint

# The Verge, Ars, and Wired feeds cover all of technology (and sometimes
# shopping/entertainment), while the GitHub Changelog covers the whole product.
# Filter only those broad feeds; category-specific and community sources pass
# through unchanged. This cuts obvious corpus noise before it consumes model
# context without pretending that a keyword filter can rank importance.
AI_RELEVANCE = re.compile(
    r"\b(?:ai|artificial intelligence|machine learning|deep learning|llm|"
    r"language model|neural|openai|anthropic|claude|chatgpt|gpt-?\d|gemini|"
    r"deepmind|mistral|xai|grok|llama|copilot|codex|cursor|agentic|ai agent|"
    r"model training|model inference|prompt injection)\b",
    re.IGNORECASE,
)
DEV_TOOL_RELEVANCE = re.compile(
    r"\b(?:ai|copilot|agent|coding agent|model|mcp|llm|prompt)\b",
    re.IGNORECASE,
)
HN_RELEVANCE = re.compile(
    r"\b(?:ai|artificial intelligence|llm|model|openai|anthropic|claude|"
    r"chatgpt|gpt-?\d|gemini|copilot|codex|cursor|agent|mcp|prompt|code|"
    r"coding|programmer|software|developer)\b",
    re.IGNORECASE,
)
SOURCE_RELEVANCE_FILTERS = {
    "The Verge": AI_RELEVANCE,
    "Ars Technica": AI_RELEVANCE,
    "Wired": AI_RELEVANCE,
    "GitHub Changelog": DEV_TOOL_RELEVANCE,
    "Hacker News": HN_RELEVANCE,
}
TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

# Whitespace, XML declarations and comments may legally precede the DOCTYPE.
_XML_PROLOG = re.compile(rb"\A(?:\xef\xbb\xbf)?(?:\s+|<\?.*?\?>|<!--.*?-->)*", re.DOTALL)

# Items and the number dropped because their timestamp could not be parsed.
# Undated items are invisible in the corpus otherwise: they never reach the
# category counters, so a feed that changes date format contributes nothing
# while the run still reports itself healthy.
FetchResult = namedtuple("FetchResult", "items undated")


def http_get(url, user_agent=USER_AGENT, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    return data


def parse_feed_xml(data):
    """Parse feed XML, refusing any DOCTYPE declaration.

    ElementTree expands internal entities, so a few hundred bytes of nested
    entity declarations expand to an unbounded string in memory — the
    "billion laughs" pattern, which MAX_RESPONSE_BYTES does not stop because
    the payload is tiny on the wire. Entity declarations and external entity
    references both require a DOCTYPE, and real RSS/Atom feeds don't carry
    one, so refusing it closes both without depending on defusedxml, which
    would cost the project its stdlib-only property.

    Only the prolog is inspected, so "<!DOCTYPE" appearing inside article
    text is not mistaken for a declaration.
    """
    prolog = _XML_PROLOG.match(data)
    remainder = data[prolog.end():] if prolog else data
    if remainder[:9].upper() == b"<!DOCTYPE":
        raise ValueError("XML DOCTYPE declarations are not accepted")
    return ET.fromstring(data)


def parse_feed_date(text):
    """Parse RFC822 or ISO8601 dates; return aware UTC datetime or None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def strip_html(text):
    return re.sub(r"<[^>]+>", "", unescape(text or "")).strip()


def fetch_rss(source_name, url, cutoff):
    """Return items newer than cutoff, plus a count of undated entries.

    Handles RSS 2.0 and Atom. An entry whose timestamp won't parse is counted
    rather than silently skipped: that is how a feed changing its date format
    shows up, instead of quietly contributing nothing to a healthy-looking run.
    """
    items = []
    undated = 0
    root = parse_feed_xml(http_get(url))
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.iter("item"):  # RSS 2.0
        published = parse_feed_date(item.findtext("pubDate"))
        if published is None:
            undated += 1
            continue
        if published < cutoff:
            continue
        items.append({
            "title": strip_html(item.findtext("title")),
            "url": (item.findtext("link") or "").strip(),
            "published": published.isoformat(),
            "summary": strip_html(item.findtext("description"))[:SUMMARY_CHARS],
            "source": source_name,
        })

    for entry in root.findall("atom:entry", ns):  # Atom
        published = parse_feed_date(
            entry.findtext("atom:published", namespaces=ns)
            or entry.findtext("atom:updated", namespaces=ns))
        if published is None:
            undated += 1
            continue
        if published < cutoff:
            continue
        link = entry.find("atom:link", ns)
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns)),
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            "summary": strip_html(
                entry.findtext("atom:summary", namespaces=ns) or "")[:SUMMARY_CHARS],
            "source": source_name,
        })
    return FetchResult(items, undated)


def fetch_hn(query, cutoff):
    """HN Algolia API with an exact unix-timestamp cutoff — no fuzzy recency.

    Note: the Algolia API only supports numericFilters on created_at_i now;
    points filtering must be done client-side.
    """
    ts = int(cutoff.timestamp())
    url = ("https://hn.algolia.com/api/v1/search?tags=story"
           f"&query={urllib.request.quote(query)}"
           f"&numericFilters=created_at_i%3E{ts}&hitsPerPage={HN_HITS_PER_PAGE}")
    data = json.loads(http_get(url))
    items = []
    undated = 0
    for hit in data.get("hits", []):
        if hit.get("created_at_i") is None:
            undated += 1
            continue
        if hit.get("points", 0) <= HN_MIN_POINTS:
            continue
        items.append({
            "title": hit.get("title", ""),
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "discussion": f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "published": datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc).isoformat(),
            "summary": strip_html(hit.get("story_text") or "")[:SUMMARY_CHARS],
            "points": hit.get("points", 0),
            "comments": hit.get("num_comments", 0),
            "source": "Hacker News",
            "query": query,
        })
    return FetchResult(items, undated)


def _reddit_md_text(atom_content):
    """Extract post body from Reddit's atom:content HTML (the <div class="md"> block)."""
    m = re.search(r'class="md">(.*?)</div>', atom_content, re.DOTALL | re.IGNORECASE)
    return strip_html(m.group(1)).strip() if m else ""


def reddit_top_bucket(hours):
    """Smallest Reddit `t=` bucket that fully covers `hours`."""
    for span, name in REDDIT_TOP_BUCKETS:
        if hours <= span:
            return name
    return "all"


def reddit_limit(hours):
    """Ask for proportionally more posts when the bucket over-covers the window.

    Reddit ranks across the whole bucket, so a 48h window served by
    t=week returns only the few weekly-top posts that happen to land in range. Scale
    the request by how much the bucket overshoots so in-window coverage stays
    roughly constant as --hours grows.
    """
    spans = {name: span for span, name in REDDIT_TOP_BUCKETS}
    span = spans.get(reddit_top_bucket(hours))
    if span is None or hours <= 0:
        return REDDIT_MAX_LIMIT
    return min(REDDIT_MAX_LIMIT, math.ceil(REDDIT_BASE_LIMIT * span / hours))


def retry_after_seconds(exc, fallback):
    """Seconds to wait after a 429, preferring the server's own instruction.

    Reddit sends Retry-After on rate limits. Backing off on our own guess
    either wastes time or retries too early and earns another 429, so use the
    server's number when it gives one — clamped, because the header is
    attacker-influenced and an hour-long sleep would hang the run.
    """
    header = ""
    try:
        header = (exc.headers.get("Retry-After") or "").strip()
    except AttributeError:
        pass
    if header.isdigit():
        return max(0, min(int(header), REDDIT_RETRY_MAX_SLEEP))
    return fallback


def fetch_reddit(subreddit, cutoff, hours):
    """Fetch top posts via RSS. Reddit's anonymous JSON API is blocked (403);
    vote counts are unavailable without OAuth credentials."""
    url = (f"https://www.reddit.com/r/{subreddit}/top/.rss"
           f"?t={reddit_top_bucket(hours)}&limit={reddit_limit(hours)}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for attempt in range(REDDIT_MAX_ATTEMPTS):
        try:
            root = parse_feed_xml(http_get(url, timeout=REDDIT_TIMEOUT))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == REDDIT_MAX_ATTEMPTS - 1:
                raise
            time.sleep(retry_after_seconds(exc, 5 * (attempt + 1)))

    items = []
    undated = 0
    for entry in root.findall("atom:entry", ns):
        published = parse_feed_date(
            entry.findtext("atom:updated", namespaces=ns)
            or entry.findtext("atom:published", namespaces=ns))
        if published is None:
            undated += 1
            continue
        if published < cutoff:
            continue
        link = entry.find("atom:link", ns)
        raw_content = entry.findtext("atom:content", namespaces=ns) or ""
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns) or ""),
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            # atom:content has the post HTML; extract just the body text
            "summary": _reddit_md_text(raw_content)[:SUMMARY_CHARS],
            "source": f"r/{subreddit}",
        })
    return FetchResult(items, undated)


def canonicalize_url(url):
    """Normalize a URL for comparison while preserving meaningful parameters."""
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


def dedupe(items):
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


def sort_items(items):
    """Order a category newest first.

    The previous key summed `points` with a never-populated `score` and used
    the timestamp only as a tiebreak, which meant every Reddit post (no
    points) sorted below every Hacker News post regardless of age — the dev
    community sources were permanently buried under the HN ones.

    Recency is the only ordering that means the same thing across RSS, HN and
    Reddit. Engagement stays on the item for the model to weigh; it just
    doesn't decide corpus order, which also stops this from quietly fighting
    the prompt's instruction to rank by impact rather than virality.
    """
    return sorted(items, key=lambda i: i["published"], reverse=True)


def is_relevant_item(item):
    """Apply deterministic relevance filtering only to known broad feeds."""
    pattern = SOURCE_RELEVANCE_FILTERS.get(item.get("source"))
    if pattern is None:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    return bool(pattern.search(text))


def prepare_category(items, source_cap=DEFAULT_SOURCE_CAP,
                     category_cap=DEFAULT_CATEGORY_CAP, undated_dropped=0):
    """Filter, deduplicate, diversify, and bound one category for model input.

    Returns both the retained items and counts for observability. Caps are
    applied newest-first and never change an item's source data.

    `undated_dropped` is counted by the fetchers, before an item ever reaches
    this function, and is carried through so every reason an item is missing
    from the corpus appears in one place.
    """
    fetched = len(items)
    relevant = [item for item in items if is_relevant_item(item)]
    # Dedupe after ordering so a syndicated/updated story keeps its newest
    # occurrence rather than whichever source happened to finish first.
    unique = dedupe(sort_items(relevant))
    kept = []
    by_source = {}
    source_cap_dropped = 0
    category_cap_dropped = 0
    for index, item in enumerate(unique):
        if len(kept) >= category_cap:
            category_cap_dropped = len(unique) - index
            break
        source = item.get("source", "unknown")
        if by_source.get(source, 0) >= source_cap:
            source_cap_dropped += 1
            continue
        kept.append(item)
        by_source[source] = by_source.get(source, 0) + 1
    stats = {
        "fetched": fetched,
        "undated_dropped": undated_dropped,
        "relevance_dropped": fetched - len(relevant),
        "duplicates_dropped": len(relevant) - len(unique),
        "source_cap_dropped": source_cap_dropped,
        "category_cap_dropped": category_cap_dropped,
        "kept": len(kept),
    }
    return kept, stats


def positive_int(value):
    """argparse type for strictly positive integer options."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=positive_int, default=DEFAULT_WINDOW_HOURS,
                        help="hard cutoff applied to every source: drop anything "
                             f"older (default {DEFAULT_WINDOW_HOURS})")
    parser.add_argument("--source-cap", type=positive_int, default=DEFAULT_SOURCE_CAP,
                        help=f"maximum items retained per source (default {DEFAULT_SOURCE_CAP})")
    parser.add_argument("--category-cap", type=positive_int, default=DEFAULT_CATEGORY_CAP,
                        help=f"maximum items retained per category (default {DEFAULT_CATEGORY_CAP})")
    parser.add_argument("--markdown", action="store_true",
                        help="emit a markdown digest instead of JSON")
    parser.add_argument("-o", "--output", help="write to file instead of stdout")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=args.hours)

    corpus = {
        "generated_at": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "window_hours": args.hours,
        "limits": {"source_cap": args.source_cap, "category_cap": args.category_cap},
        "categories": {"us_politics": [], "world": [], "ai_tech": [], "dev_community": []},
        "processing": {},
        "errors": [],
    }

    undated = dict.fromkeys(corpus["categories"], 0)

    jobs = []
    with ThreadPoolExecutor(max_workers=FETCH_WORKERS) as pool:
        for category, feeds in RSS_FEEDS.items():
            for name, url in feeds:
                jobs.append((pool.submit(fetch_rss, name, url, cutoff), category, name))
        for query in HN_QUERIES:
            jobs.append((pool.submit(fetch_hn, query, cutoff), "dev_community", f"HN:{query}"))

        for future, category, name in jobs:
            try:
                result = future.result()
            except Exception as exc:
                corpus["errors"].append(f"{name}: {exc}")
                continue
            corpus["categories"][category].extend(result.items)
            undated[category] += result.undated

    # Reddit rate-limits concurrent requests; fetch serially with a pause.
    for index, sub in enumerate(SUBREDDITS):
        try:
            result = fetch_reddit(sub, cutoff, args.hours)
        except Exception as exc:
            corpus["errors"].append(f"r/{sub}: {exc}")
        else:
            corpus["categories"]["dev_community"].extend(result.items)
            undated["dev_community"] += result.undated
        if index < len(SUBREDDITS) - 1:
            time.sleep(REDDIT_PAUSE_SECONDS)

    for category in corpus["categories"]:
        items, stats = prepare_category(
            corpus["categories"][category],
            source_cap=args.source_cap,
            category_cap=args.category_cap,
            undated_dropped=undated[category],
        )
        corpus["categories"][category] = items
        corpus["processing"][category] = stats

    total = sum(len(v) for v in corpus["categories"].values())

    if args.markdown:
        lines = [f"# News corpus — last {args.hours}h "
                 f"(generated {now:%Y-%m-%d %H:%M} UTC)\n"]
        for category, items in corpus["categories"].items():
            lines.append(f"\n## {category} ({len(items)} items)\n")
            for item in items:
                meta = f" · {item['points']} pts" if "points" in item else ""
                lines.append(f"- **{item['title']}** ({item['source']}{meta}, {item['published'][:16]})\n"
                             f"  {item['url']}")
        if corpus["errors"]:
            lines.append("\n## Fetch errors\n")
            lines.extend(f"- {e}" for e in corpus["errors"])
        text = "\n".join(lines)
    else:
        text = json.dumps(corpus, indent=1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {total} items ({len(corpus['errors'])} fetch errors) to {args.output}")
    else:
        print(text)

    if total == 0:
        print("error: no usable items fetched from any source", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
