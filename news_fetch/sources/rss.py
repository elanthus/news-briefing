"""RSS 2.0 and Atom feed adapter."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime

from news_fetch import transport
from news_fetch.feed_xml import parse_feed_xml
from news_fetch.model import FetchResult, Item, _raise_data_error
from news_fetch.sources.common import parse_feed_date, publication_in_window, strip_html

FEED_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}


def _feed_summary(element: ET.Element, *paths: str) -> str:
    """Return the first non-empty summary/content element as plain text."""
    for path in paths:
        child = element.find(path, FEED_NAMESPACES)
        if child is None:
            continue
        summary = strip_html("".join(child.itertext()))
        if summary:
            return summary
    return ""


def fetch_rss(
    source_name: str, url: str, cutoff: datetime, window_end: datetime
) -> FetchResult:
    """Return items inside the fixed corpus window, plus undated count.

    Handles RSS 2.0 and Atom. An entry whose timestamp won't parse is counted
    rather than silently skipped: that is how a feed changing its date format
    shows up, instead of quietly contributing nothing to a healthy-looking run.
    """
    items: list[Item] = []
    undated = 0
    data = transport.http_get(url)
    try:
        root = parse_feed_xml(data)
    except (ET.ParseError, ValueError) as exc:
        _raise_data_error(exc)
    ns = FEED_NAMESPACES

    rss_entries = list(root.iter("item"))
    atom_entries = root.findall("atom:entry", ns)
    parsed_entries = len(rss_entries) + len(atom_entries)
    dated_entries = 0

    for item in rss_entries:  # RSS 2.0
        published = parse_feed_date(item.findtext("pubDate"))
        if published is None:
            undated += 1
            continue
        dated_entries += 1
        if not publication_in_window(published, cutoff, window_end):
            continue
        items.append({
            "title": strip_html(item.findtext("title")),
            "url": (item.findtext("link") or "").strip(),
            "published": published.isoformat(),
            "summary": _feed_summary(item, "description", "content:encoded"),
            "source": source_name,
        })

    for entry in atom_entries:  # Atom
        published = parse_feed_date(
            entry.findtext("atom:published", namespaces=ns)
            or entry.findtext("atom:updated", namespaces=ns))
        if published is None:
            undated += 1
            continue
        dated_entries += 1
        if not publication_in_window(published, cutoff, window_end):
            continue
        links = entry.findall("atom:link", ns)
        # Atom defines an omitted rel as "alternate". Feeds commonly put a
        # rel="self" API/feed URL first, so selecting the first link can cite
        # the feed endpoint instead of the human-readable article.
        link = next(
            (candidate for candidate in links
             if (candidate.get("href") or "").strip()
             and candidate.get("rel", "alternate") == "alternate"),
            next(
                (candidate for candidate in links if (candidate.get("href") or "").strip()),
                None,
            ),
        )
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns)),
            "url": (link.get("href") or "").strip() if link is not None else "",
            "published": published.isoformat(),
            "summary": _feed_summary(entry, "atom:summary", "atom:content"),
            "source": source_name,
        })
    return FetchResult(items, undated, parsed_entries, dated_entries, 0)
