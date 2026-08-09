#!/usr/bin/env python3
"""Deterministic news fetcher for the daily briefing.

Pulls RSS feeds, the Hacker News Algolia API, and Reddit JSON endpoints,
drops everything older than the cutoff (default 48h) IN CODE, and emits a
JSON corpus grouped by category. The LLM only ranks and summarizes what
this script outputs — it never decides what counts as "recent."

Usage:
    python3 fetch_news.py                 # JSON to stdout, 48h window
    python3 fetch_news.py --hours 24
    python3 fetch_news.py --markdown      # human-readable digest instead
    python3 fetch_news.py -o corpus.json
"""

import argparse
import json
import math
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

USER_AGENT = "news-briefing/1.0 (personal daily digest script)"
TIMEOUT = 20

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
        ("AP via Google News",
         "https://news.google.com/rss/search?q=site:apnews.com%20when:2d&hl=en-US&gl=US&ceid=US:en"),
    ],
    "ai_tech": [
        ("The Verge", "https://www.theverge.com/rss/index.xml"),
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("Wired", "https://www.wired.com/feed/rss"),
        ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ],
}

# Dev tools / practices: engagement-bearing community sources
HN_QUERIES = ["claude code", "cursor", "codex", "mcp", "llm agent", "prompt engineering"]
SUBREDDITS = ["ClaudeAI", "ClaudeCode", "LocalLLaMA", "cursor"]

# Reddit's "top" RSS endpoint takes a coarse bucket (t=), not an arbitrary
# window, so it can't express "the last 48 hours" directly. Over-fetch the
# smallest bucket that fully covers the requested window and let the exact
# cutoff filter in fetch_reddit() do the real work — same rule as every
# other source.
REDDIT_TOP_BUCKETS = ((1, "hour"), (24, "day"), (168, "week"),
                      (720, "month"), (8760, "year"))
REDDIT_BASE_LIMIT = 25
REDDIT_MAX_LIMIT = 100  # Reddit's own ceiling for this endpoint


def http_get(url, user_agent=USER_AGENT):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


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
    """Return list of items newer than cutoff. Handles RSS 2.0 and Atom."""
    items = []
    root = ET.fromstring(http_get(url))
    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.iter("item"):  # RSS 2.0
        published = parse_feed_date(item.findtext("pubDate"))
        if published is None or published < cutoff:
            continue
        items.append({
            "title": strip_html(item.findtext("title")),
            "url": (item.findtext("link") or "").strip(),
            "published": published.isoformat(),
            "summary": strip_html(item.findtext("description"))[:300],
            "source": source_name,
        })

    for entry in root.findall("atom:entry", ns):  # Atom
        published = parse_feed_date(
            entry.findtext("atom:published", namespaces=ns)
            or entry.findtext("atom:updated", namespaces=ns))
        if published is None or published < cutoff:
            continue
        link = entry.find("atom:link", ns)
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns)),
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            "summary": strip_html(entry.findtext("atom:summary", namespaces=ns) or "")[:300],
            "source": source_name,
        })
    return items


def fetch_hn(query, cutoff):
    """HN Algolia API with an exact unix-timestamp cutoff — no fuzzy recency.

    Note: the Algolia API only supports numericFilters on created_at_i now;
    points filtering must be done client-side.
    """
    ts = int(cutoff.timestamp())
    url = ("https://hn.algolia.com/api/v1/search?tags=story"
           f"&query={urllib.request.quote(query)}"
           f"&numericFilters=created_at_i%3E{ts}&hitsPerPage=25")
    data = json.loads(http_get(url))
    items = []
    for hit in data.get("hits", []):
        if hit.get("points", 0) <= 20:
            continue
        items.append({
            "title": hit.get("title", ""),
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "discussion": f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "published": datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc).isoformat(),
            "points": hit.get("points", 0),
            "comments": hit.get("num_comments", 0),
            "source": "Hacker News",
            "query": query,
        })
    return items


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

    Reddit ranks across the whole bucket, so a 48h window served by t=week
    returns only the few weekly-top posts that happen to land in range. Scale
    the request by how much the bucket overshoots so in-window coverage stays
    roughly constant as --hours grows.
    """
    spans = {name: span for span, name in REDDIT_TOP_BUCKETS}
    span = spans.get(reddit_top_bucket(hours))
    if span is None or hours <= 0:
        return REDDIT_MAX_LIMIT
    return min(REDDIT_MAX_LIMIT, math.ceil(REDDIT_BASE_LIMIT * span / hours))


def fetch_reddit(subreddit, cutoff, hours):
    """Fetch top posts via RSS. Reddit's anonymous JSON API is blocked (403);
    vote counts are unavailable without OAuth credentials."""
    url = (f"https://www.reddit.com/r/{subreddit}/top/.rss"
           f"?t={reddit_top_bucket(hours)}&limit={reddit_limit(hours)}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for attempt in range(4):
        try:
            root = ET.fromstring(http_get(url))
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))

    items = []
    for entry in root.findall("atom:entry", ns):
        published = parse_feed_date(
            entry.findtext("atom:updated", namespaces=ns)
            or entry.findtext("atom:published", namespaces=ns))
        if published is None or published < cutoff:
            continue
        link = entry.find("atom:link", ns)
        raw_content = entry.findtext("atom:content", namespaces=ns) or ""
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns) or ""),
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            # atom:content has the post HTML; extract just the body text
            "summary": _reddit_md_text(raw_content)[:300],
            "source": f"r/{subreddit}",
        })
    return items


def dedupe(items):
    """Drop exact URL duplicates and near-duplicate titles, keep first seen."""
    seen_urls, seen_titles, out = set(), set(), []
    for item in items:
        url = item.get("url", "")
        title_key = re.sub(r"\W+", "", item.get("title", "").lower())[:60]
        if url in seen_urls or (title_key and title_key in seen_titles):
            continue
        seen_urls.add(url)
        seen_titles.add(title_key)
        out.append(item)
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=int, default=48,
                        help="hard cutoff applied to every source: drop anything "
                             "older (default 48)")
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
        "categories": {"us_politics": [], "world": [], "ai_tech": [], "dev_community": []},
        "errors": [],
    }

    jobs = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        for category, feeds in RSS_FEEDS.items():
            for name, url in feeds:
                jobs.append((pool.submit(fetch_rss, name, url, cutoff), category, name))
        for query in HN_QUERIES:
            jobs.append((pool.submit(fetch_hn, query, cutoff), "dev_community", f"HN:{query}"))

        for future, category, name in jobs:
            try:
                corpus["categories"][category].extend(future.result())
            except Exception as exc:
                corpus["errors"].append(f"{name}: {exc}")

    # Reddit rate-limits concurrent requests; fetch serially with a pause.
    for sub in SUBREDDITS:
        try:
            corpus["categories"]["dev_community"].extend(fetch_reddit(sub, cutoff, args.hours))
        except Exception as exc:
            corpus["errors"].append(f"r/{sub}: {exc}")
        time.sleep(2)

    for category in corpus["categories"]:
        items = dedupe(corpus["categories"][category])
        items.sort(key=lambda i: (i.get("points", 0) + i.get("score", 0), i["published"]), reverse=True)
        corpus["categories"][category] = items

    if args.markdown:
        lines = [f"# News corpus — last {args.window_hours if hasattr(args, 'window_hours') else args.hours}h "
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
        with open(args.output, "w") as f:
            f.write(text)
        total = sum(len(v) for v in corpus["categories"].values())
        print(f"Wrote {total} items ({len(corpus['errors'])} fetch errors) to {args.output}")
    else:
        print(text)


if __name__ == "__main__":
    main()
