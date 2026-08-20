#!/usr/bin/env python3
"""Code-enforced news corpus fetcher for the daily briefing.

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

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import math
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path
from typing import Any, NamedTuple, Never, NotRequired, TypedDict
from xml.parsers import expat

import corpus_schema
from corpus_schema import canonicalize_url

# Feed operators see this traffic from every clone. Naming the project and
# linking it gives them something to look up, and someone to reach, before a
# block is their only option.
USER_AGENT = "news-briefing/1.0 (personal daily digest; +https://github.com/elanthus/news-briefing)"
TIMEOUT = 20
REDDIT_TIMEOUT = 10
REDDIT_MAX_ATTEMPTS = 2
REDDIT_FALLBACK_LIMIT = 100
ARCTIC_SHIFT_POSTS_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
SCRAPECREATORS_SUBREDDIT_URL = "https://api.scrapecreators.com/v1/reddit/subreddit"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_URL_BYTES = corpus_schema.ITEM_URL_MAX_BYTES
DEFAULT_WINDOW_HOURS = 24
DEFAULT_SOURCE_CAP = 25
DEFAULT_CATEGORY_CAP = 60

FETCH_WORKERS = 8
SUMMARY_CHARS = corpus_schema.ITEM_SUMMARY_MAX_CHARS
TITLE_BYTES = corpus_schema.ITEM_TITLE_MAX_BYTES
TITLE_TOKENS = corpus_schema.ITEM_TITLE_MAX_TOKENS
SUMMARY_BYTES = corpus_schema.ITEM_SUMMARY_MAX_BYTES
SUMMARY_TOKENS = corpus_schema.ITEM_SUMMARY_MAX_TOKENS
SOURCE_ID_BYTES = corpus_schema.ITEM_SOURCE_MAX_BYTES
SOURCE_ID_TOKENS = corpus_schema.ITEM_SOURCE_MAX_TOKENS
QUERY_BYTES = corpus_schema.ITEM_QUERY_MAX_BYTES
QUERY_TOKENS = corpus_schema.ITEM_QUERY_MAX_TOKENS
SOURCE_CONTEXT_BYTES = corpus_schema.SOURCE_CONTEXT_MAX_BYTES
SOURCE_CONTEXT_TOKENS = corpus_schema.SOURCE_CONTEXT_MAX_TOKENS
GLOBAL_CONTEXT_BYTES = corpus_schema.GLOBAL_CONTEXT_MAX_BYTES
GLOBAL_CONTEXT_TOKENS = corpus_schema.GLOBAL_CONTEXT_MAX_TOKENS
TLS_CONTEXT = ssl.create_default_context()
HN_MIN_POINTS = 20  # minimum engagement required for a Hacker News item
HN_HITS_PER_PAGE = 25
REDDIT_PAUSE_SECONDS = 2  # Reddit rate-limits bursts; space serial requests
REDDIT_RETRY_MAX_SLEEP = 30  # ceiling on a server-supplied Retry-After

DEFAULT_SOURCES_PATH = Path(__file__).with_name("sources.json")


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

# Reddit's "top" RSS endpoint takes a coarse bucket (t=), not an arbitrary
# window, so it can't express arbitrary hour ranges directly. Over-fetch the
# smallest bucket that fully covers the requested window and let the exact
# fixed publication-window filter in fetch_reddit_rss() does the real work — the
# same lower and upper bounds used for every other source.
REDDIT_TOP_BUCKETS = ((1, "hour"), (24, "day"), (168, "week"),
                      (720, "month"), (8760, "year"))
REDDIT_BASE_LIMIT = 25
REDDIT_MAX_LIMIT = 100  # cap applied to scaled anonymous RSS requests

# The Verge, Ars, and Wired feeds cover all of technology (and sometimes
# shopping/entertainment), while the GitHub Changelog covers the whole product.
# Filter only those broad feeds; category-specific and community sources pass
# through unchanged. This cuts obvious corpus noise before it consumes model
# context without pretending that a keyword filter can rank importance.
AI_RELEVANCE = re.compile(
    r"\b(?:ai|artificial intelligence|machine learning|deep learning|llm|"
    r"language model|neural|openai|anthropic|claude|chatgpt|gpt-?\d|gemini|"
    r"deepmind|mistral|xai|grok|llama|copilot|codex|cursor|agentic|ai agent|"
    r"model training|model inference|prompt injection|"
    # Infrastructure and autonomy are AI topics even when a feed item does not
    # contain the literal term "AI".
    r"data ?cent(?:er|re)s?|gpus?|tpus?|nvidia|semiconductors?|compute|"
    r"inference|training run|self-driving|autonomous|robotaxis?|robotics?|"
    r"algorithmic|facial recognition|surveillance|agi|superintelligence)\b",
    re.IGNORECASE,
)
# Consumer-tech feeds carry a lot of commerce: promo codes, coupon roundups,
# buying guides. None of it is briefing material, and the vocabulary above
# would otherwise readmit things like "Best GPU deals (2026)". Checked before
# relevance, so one signal cannot rescue the other.
#
# Deliberately no bare "deal": industry contracts and acquisitions use that
# word too, while the more specific patterns identify the commerce noise.
COMMERCE_NOISE = re.compile(
    r"promo code|coupon|\d+%\s*off|\$\d[\d,.]*\s*off|on sale|"
    r"\bbest\b[^.]*\(20\d\d\)|buying guide|review\s*\(20\d\d\)",
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
FEED_NAMESPACES = {
    "atom": "http://www.w3.org/2005/Atom",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

class Item(TypedDict):
    """A corpus item. Field names are fixed by corpus_schema."""

    title: str
    url: str
    published: str
    source: str
    summary: NotRequired[str]
    discussion: NotRequired[str]
    points: NotRequired[int]
    comments: NotRequired[int]
    query: NotRequired[str]


class SourceStatus(TypedDict):
    """Observable outcome for one configured source request."""

    source_type: str
    source_id: str
    category: str
    status: str
    requested: bool
    http_success: bool
    parsed_entries: int
    dated_entries: int
    retained_entries: int
    retained_bytes: int
    estimated_tokens: int
    duration_ms: int
    error_type: NotRequired[str]
    message: NotRequired[str]


class FetchResult(NamedTuple):
    """Fetched items plus parsed, dated, and undated entry counts.

    Undated entries never reach category processing, so this separate count is
    required to distinguish an incompatible date format from an empty source.
    """

    items: list[Item]
    undated: int
    parsed_entries: int | None = None
    dated_entries: int | None = None


class TimedFetchResult(NamedTuple):
    result: FetchResult | None
    error_type: str | None
    message: str | None
    duration_ms: int
    http_success: bool


class ResolvedAddress(NamedTuple):
    """One DNS result captured before a connection is opened."""

    family: int
    sockaddr: tuple[Any, ...]


class HttpResult(NamedTuple):
    status: int
    reason: str
    headers: Any
    data: bytes


def timed_fetch(fetcher: Callable[..., FetchResult], *args: Any) -> TimedFetchResult:
    """Run one source fetch and retain its outcome and wall-clock latency."""
    started = time.perf_counter()
    error_type: str | None
    message: str | None
    try:
        result = fetcher(*args)
    except Exception as exc:
        error_type = (exc.error_type if isinstance(exc, SourceDataError)
                      else exc.__class__.__name__)
        message = str(exc) or error_type
        result = None
        http_success = isinstance(exc, SourceDataError)
    else:
        error_type = None
        message = None
        http_success = True
    duration_ms = round((time.perf_counter() - started) * 1000)
    return TimedFetchResult(result, error_type, message, duration_ms, http_success)


class SourceDataError(ValueError):
    """A response arrived successfully but its payload could not be consumed."""

    def __init__(self, cause: Exception):
        self.error_type = cause.__class__.__name__
        super().__init__(str(cause) or self.error_type)


def _raise_data_error(exc: Exception) -> Never:
    raise SourceDataError(exc) from exc


def _public_ip(value: str) -> bool:
    """Whether an address is globally routable, including mapped IPv4."""
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_global


def _http_destination(url: str) -> tuple[urllib.parse.SplitResult, str, int]:
    """Validate URL syntax before DNS resolution or a network request."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("destination must be a non-empty URL")
    if len(url.encode("utf-8")) > MAX_URL_BYTES:
        raise ValueError(f"destination URL exceeded {MAX_URL_BYTES} bytes")
    if any(ord(character) < 0x20 or character.isspace() for character in url):
        raise ValueError("destination URL contains whitespace or control characters")
    try:
        parts = urllib.parse.urlsplit(url)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid destination URL: {exc}") from exc
    if parts.scheme.lower() not in {"http", "https"}:
        raise ValueError("destination must use HTTP or HTTPS")
    if not parts.netloc or not hostname:
        raise ValueError("destination must have a hostname")
    if parts.username is not None or parts.password is not None:
        raise ValueError("destination URL must not contain credentials")
    if "%" in hostname:
        raise ValueError("destination hostname must not contain an address scope")
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("destination hostname is not valid IDNA") from exc
    try:
        literal = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        pass
    else:
        if not _public_ip(str(literal)):
            raise ValueError("destination resolves to a non-public address")
    return parts, ascii_hostname, port or (443 if parts.scheme.lower() == "https" else 80)


def validate_source_url(url: str) -> None:
    """Validate a configured source without performing network I/O."""
    _http_destination(url)


def _resolve_public_addresses(hostname: str, port: int) -> tuple[ResolvedAddress, ...]:
    """Resolve once, reject any private answer, and return pinned addresses."""
    answers = socket.getaddrinfo(
        hostname, port, family=socket.AF_UNSPEC,
        type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP,
    )
    resolved: list[ResolvedAddress] = []
    seen: set[tuple[int, tuple[Any, ...]]] = set()
    for family, _socktype, _proto, _canonname, sockaddr in answers:
        address = str(sockaddr[0])
        if not _public_ip(address):
            raise ValueError(
                f"destination {hostname!r} resolved to non-public address {address}")
        candidate = ResolvedAddress(family, tuple(sockaddr))
        key = (candidate.family, candidate.sockaddr)
        if key not in seen:
            seen.add(key)
            resolved.append(candidate)
    if not resolved:
        raise ValueError(f"destination {hostname!r} resolved to no usable addresses")
    return tuple(resolved)


def _connect_pinned(address: ResolvedAddress, timeout: int) -> socket.socket:
    sock = socket.socket(address.family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(address.sockaddr)
    except Exception:
        sock.close()
        raise
    return sock


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: ResolvedAddress, timeout: int):
        super().__init__(host, port=port, timeout=timeout)
        self._address = address
        self._pinned_timeout = timeout

    def connect(self) -> None:
        self.sock = _connect_pinned(self._address, self._pinned_timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: ResolvedAddress,
                 timeout: int, context: ssl.SSLContext):
        super().__init__(host, port=port, timeout=timeout, context=context)
        self._address = address
        self._pinned_timeout = timeout
        self._pinned_context = context

    def connect(self) -> None:
        sock = _connect_pinned(self._address, self._pinned_timeout)
        try:
            self.sock = self._pinned_context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _request_once(url: str, parts: urllib.parse.SplitResult, hostname: str,
                  port: int, address: ResolvedAddress, user_agent: str,
                  timeout: int, extra_headers: dict[str, str] | None = None) -> HttpResult:
    """Make one request to an already validated and DNS-pinned address."""
    if parts.scheme.lower() == "https":
        connection: http.client.HTTPConnection = _PinnedHTTPSConnection(
            hostname, port, address, timeout, TLS_CONTEXT)
    else:
        connection = _PinnedHTTPConnection(hostname, port, address, timeout)
    target = urllib.parse.urlunsplit(("", "", parts.path or "/", parts.query, ""))
    target = urllib.parse.quote(target, safe="/%?&=;:+,$@!~*'()[]")
    headers = {"User-Agent": user_agent}
    if extra_headers:
        headers.update(extra_headers)
    try:
        connection.request("GET", target, headers=headers)
        response = connection.getresponse()
        data = (b"" if response.status in {301, 302, 303, 307, 308}
                else response.read(MAX_RESPONSE_BYTES + 1))
        return HttpResult(response.status, str(response.reason), response.headers, data)
    finally:
        connection.close()


def http_get(url: str, user_agent: str = USER_AGENT, timeout: int = TIMEOUT) -> bytes:
    """Fetch a public HTTP(S) URL with DNS pinning and safe redirects."""
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        parts, hostname, port = _http_destination(current)
        addresses = _resolve_public_addresses(hostname, port)
        result: HttpResult | None = None
        last_error: OSError | None = None
        for address in addresses:
            try:
                result = _request_once(
                    current, parts, hostname, port, address, user_agent, timeout)
                break
            except OSError as exc:
                last_error = exc
        if result is None:
            if last_error is not None:
                raise last_error
            raise OSError(f"could not connect to {hostname}")
        if result.status in {301, 302, 303, 307, 308}:
            location = result.headers.get("Location")
            if not location:
                raise urllib.error.HTTPError(
                    current, result.status, "redirect has no Location header",
                    result.headers, None)
            if redirect_count == MAX_REDIRECTS:
                raise ValueError(f"redirect limit of {MAX_REDIRECTS} exceeded")
            current = urllib.parse.urljoin(current, location)
            # The next loop revalidates syntax, credentials, DNS and address
            # scope before making the redirected request.
            continue
        if len(result.data) > MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
        if result.status >= 400:
            raise urllib.error.HTTPError(
                current, result.status, result.reason, result.headers, None)
        return result.data
    raise AssertionError("unreachable redirect loop")


def scrapecreators_get(url: str, api_key: str, timeout: int = REDDIT_TIMEOUT) -> bytes:
    """Fetch one ScrapeCreators URL without ever forwarding its API key.

    This authenticated transport is intentionally locked to one HTTPS origin and
    refuses redirects. A generic redirect-following helper could disclose the
    ``x-api-key`` header to a destination selected by the response.
    """
    key = api_key.strip()
    if not key or "\r" in key or "\n" in key:
        raise ValueError("SCRAPECREATORS_API_KEY must be a non-empty single-line value")
    parts, hostname, port = _http_destination(url)
    if parts.scheme.lower() != "https" or hostname != "api.scrapecreators.com" or port != 443:
        raise ValueError("ScrapeCreators credentials may only be sent to its HTTPS API origin")
    addresses = _resolve_public_addresses(hostname, port)
    result: HttpResult | None = None
    last_error: OSError | None = None
    for address in addresses:
        try:
            result = _request_once(
                url,
                parts,
                hostname,
                port,
                address,
                USER_AGENT,
                timeout,
                {"Accept": "application/json", "x-api-key": key},
            )
            break
        except OSError as exc:
            last_error = exc
    if result is None:
        if last_error is not None:
            raise last_error
        raise OSError(f"could not connect to {hostname}")
    if result.status in {301, 302, 303, 307, 308}:
        raise urllib.error.HTTPError(
            url, result.status, "authenticated endpoint redirected", result.headers, None
        )
    if len(result.data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
    if result.status >= 400:
        raise urllib.error.HTTPError(
            url, result.status, result.reason, result.headers, None
        )
    return result.data


def _decode_bom_marked_utf32_xml(data: bytes) -> str | bytes:
    """Decode BOM-marked UTF-32 for parsers whose Expat lacks UTF-32 support."""
    byte_order: str | None = None
    if data.startswith(b"\xff\xfe\x00\x00"):
        byte_order = "le"
    elif data.startswith(b"\x00\x00\xfe\xff"):
        byte_order = "be"
    if byte_order is None:
        return data
    if len(data) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")

    # The generic codec requires and strips the BOM and rejects incomplete or
    # otherwise invalid code units. Passing Unicode to Expat then avoids its
    # platform-dependent "multi-byte encodings are not supported" failure.
    text = data.decode("utf-32", errors="strict")
    declaration = re.match(r"\s*<\?xml\s+([^?]*?)\?>", text, re.IGNORECASE)
    if declaration:
        encoding = re.search(
            r"\bencoding\s*=\s*(['\"])([^'\"]+)\1",
            declaration.group(1),
            re.IGNORECASE,
        )
        if encoding:
            normalized = re.sub(r"[-_]", "", encoding.group(2)).lower()
            accepted = {"utf32", f"utf32{byte_order}"}
            if normalized not in accepted:
                raise ValueError(
                    "UTF-32 byte order mark contradicts XML encoding declaration "
                    f"{encoding.group(2)!r}"
                )
    return text


def parse_feed_xml(data: bytes) -> ET.Element:
    """Parse feed XML, refusing any DOCTYPE declaration.

    ElementTree expands internal entities, so a compact set of nested entity
    declarations can amplify far beyond the wire-size bound. Entity
    declarations and external entity references both require a DOCTYPE, which
    the supported RSS/Atom subset does not need. Refusing it closes both paths
    without adding defusedxml as a runtime dependency.

    Expat recognizes the document's declared encoding before calling the DTD
    handler, so UTF-16 and other supported encodings cannot hide a declaration
    from this guard. BOM-marked UTF-32 is first decoded strictly and bounded,
    because Python's Expat does not support that byte encoding; the same DTD
    guard then runs over Unicode. A separate validation pass keeps
    ElementTree's convenient tree API without relying on private internals.
    """
    def reject_doctype(_name: str, _system_id: str | None,
                       _public_id: str | None, _has_internal_subset: int) -> None:
        raise ValueError("XML DOCTYPE declarations are not accepted")

    class RootReached(Exception):
        """A DOCTYPE cannot follow the root element, so the guard can stop."""

    def stop_at_root(_name: str, _attributes: dict[str, str]) -> None:
        raise RootReached

    parseable = _decode_bom_marked_utf32_xml(data)
    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = reject_doctype
    parser.StartElementHandler = stop_at_root
    try:
        parser.Parse(parseable, True)
    except RootReached:
        pass
    except expat.ExpatError as exc:
        # Keep malformed prologs on the public exception type callers already
        # handle, without swallowing a guard failure and hoping a second parser
        # happens to reject the same bytes.
        raise ET.ParseError(str(exc)) from exc
    return ET.fromstring(parseable)


def parse_feed_date(text: str | None) -> datetime | None:
    """Parse RFC822 or ISO8601 dates; return aware UTC datetime or None."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = parsedate_to_datetime(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def strip_html(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or "")).strip()


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


def publication_in_window(
    published: datetime, cutoff: datetime, window_end: datetime
) -> bool:
    """Whether a publication belongs to the fixed inclusive corpus window."""
    return cutoff <= published <= window_end


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
    data = http_get(url)
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
    return FetchResult(items, undated, parsed_entries, dated_entries)


def fetch_hn(query: str, cutoff: datetime, window_end: datetime) -> FetchResult:
    """HN Algolia API with an exact fixed publication window.

    The request applies the lower `created_at_i` bound; this function applies
    both bounds and the points threshold to returned hits.
    """
    ts = int(cutoff.timestamp())
    url = ("https://hn.algolia.com/api/v1/search?tags=story"
           f"&query={urllib.parse.quote(query)}"
           f"&numericFilters=created_at_i%3E{ts}&hitsPerPage={HN_HITS_PER_PAGE}")
    payload = http_get(url)
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        _raise_data_error(exc)
    if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
        raise SourceDataError(ValueError("Hacker News response has no hits array"))
    items: list[Item] = []
    undated = 0
    dated_entries = 0
    hits = data["hits"]
    for hit in hits:
        if not isinstance(hit, dict):
            raise SourceDataError(ValueError("Hacker News hits array contains a non-object"))
        if hit.get("created_at_i") is None:
            undated += 1
            continue
        dated_entries += 1
        published = datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc)
        if not publication_in_window(published, cutoff, window_end):
            continue
        if hit.get("points", 0) < HN_MIN_POINTS:
            continue
        items.append({
            "title": hit.get("title", ""),
            "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "discussion": f"https://news.ycombinator.com/item?id={hit['objectID']}",
            "published": published.isoformat(),
            "summary": strip_html(hit.get("story_text") or ""),
            "points": hit.get("points", 0),
            "comments": hit.get("num_comments", 0),
            "source": "Hacker News",
            "query": query,
        })
    return FetchResult(items, undated, len(hits), dated_entries)


def _reddit_md_text(atom_content: str) -> str:
    """Extract post body from Reddit's atom:content HTML (the <div class="md"> block)."""
    m = re.search(r'class="md">(.*?)</div>', atom_content, re.DOTALL | re.IGNORECASE)
    return strip_html(m.group(1)).strip() if m else ""


def reddit_top_bucket(hours: int) -> str:
    """Smallest Reddit `t=` bucket that fully covers `hours`."""
    for span, name in REDDIT_TOP_BUCKETS:
        if hours <= span:
            return name
    return "all"


def reddit_limit(hours: int) -> int:
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


def retry_after_seconds(exc: urllib.error.HTTPError, fallback: int) -> int:
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


def fetch_reddit_rss(
    subreddit: str, cutoff: datetime, window_end: datetime, hours: int
) -> FetchResult:
    """Fetch top posts via anonymous RSS.

    The RSS response does not expose vote counts, so these items carry no
    engagement score.
    """
    url = (f"https://www.reddit.com/r/{subreddit}/top/.rss"
           f"?t={reddit_top_bucket(hours)}&limit={reddit_limit(hours)}")
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    for attempt in range(REDDIT_MAX_ATTEMPTS):
        try:
            payload = http_get(url, timeout=REDDIT_TIMEOUT)
            try:
                root = parse_feed_xml(payload)
            except (ET.ParseError, ValueError) as exc:
                _raise_data_error(exc)
            break
        except urllib.error.HTTPError as exc:
            if exc.code != 429 or attempt == REDDIT_MAX_ATTEMPTS - 1:
                raise
            time.sleep(retry_after_seconds(exc, 5 * (attempt + 1)))

    items: list[Item] = []
    undated = 0
    entries = root.findall("atom:entry", ns)
    dated_entries = 0
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
        items.append({
            "title": strip_html(entry.findtext("atom:title", namespaces=ns) or ""),
            "url": link.get("href", "") if link is not None else "",
            "published": published.isoformat(),
            # atom:content has the post HTML; extract just the body text
            "summary": _reddit_md_text(raw_content),
            "source": f"r/{subreddit}",
        })
    return FetchResult(items, undated, len(entries), dated_entries)


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


def _reddit_json_result(
    posts: list[Any], subreddit: str, cutoff: datetime, window_end: datetime
) -> FetchResult:
    """Normalize one JSON provider without trusting its outbound destinations."""
    items: list[Item] = []
    parsed_entries = 0
    dated_entries = 0
    undated = 0
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
        item: Item = {
            "title": title,
            "url": f"https://www.reddit.com/r/{encoded_subreddit}/comments/{post_id}/",
            "published": published.isoformat(),
            "source": f"r/{subreddit}",
        }
        selftext = str(raw_post.get("selftext") or "").strip()
        if selftext and selftext not in {"[deleted]", "[removed]"}:
            item["summary"] = selftext
        items.append(item)
    return FetchResult(items, undated, parsed_entries, dated_entries)


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
        "before": int(window_end.timestamp()),
        "limit": REDDIT_FALLBACK_LIMIT,
        "sort": "desc",
        "fields": "id,title,selftext,created_utc,subreddit,score,num_comments",
    })
    payload = http_get(f"{ARCTIC_SHIFT_POSTS_URL}?{params}", timeout=REDDIT_TIMEOUT)
    response = _json_object(payload, "Arctic Shift")
    posts = response.get("data")
    if not isinstance(posts, list):
        _raise_data_error(ValueError("Arctic Shift response did not contain a data list"))
    return _reddit_json_result(posts, subreddit, cutoff, window_end)


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
        "sort": "new",
        "trim": "true",
    })
    payload = scrapecreators_get(
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
            raise RuntimeError(
                f"all Reddit backends failed or returned no usable posts for r/{subreddit}: "
                + "; ".join(errors)
            ) from exc

    if empty_result is not None:
        return empty_result
    raise RuntimeError(
        f"all free Reddit backends failed for r/{subreddit}: " + "; ".join(errors)
    )


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
    Reddit. Engagement stays on the item for the model to weigh but does not
    determine corpus order or override the prompt's impact-based ranking.
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


def is_relevant_item(item: Item) -> bool:
    """Apply deterministic relevance filtering only to known broad feeds.

    The filter removes noise; it does not decide importance. Ranking is the
    model's job under the prompt, and over-filtering is the more expensive
    mistake — an item dropped here cannot be ranked at all, and a starved
    sub-category cannot fill its reserved slots.
    """
    pattern = SOURCE_RELEVANCE_FILTERS.get(item.get("source", ""))
    if pattern is None:
        return True
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    if COMMERCE_NOISE.search(text):
        return False
    return bool(pattern.search(text))


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
                or len(source.encode("utf-8")) > SOURCE_ID_BYTES
                or not isinstance(url, str)):
            telemetry["field_budget_dropped"] += 1
            continue
        try:
            _http_destination(url)
            discussion = candidate.get("discussion")
            if discussion is not None:
                if not isinstance(discussion, str):
                    raise ValueError("discussion URL is not a string")
                _http_destination(discussion)
        except ValueError:
            # URLs are identities and destinations. Truncating one would turn
            # it into a different, possibly unsafe request, so reject the item.
            telemetry["field_budget_dropped"] += 1
            continue
        query = candidate.get("query")
        if (query is not None
                and (not isinstance(query, str)
                     or len(query.encode("utf-8")) > QUERY_BYTES)):
            telemetry["field_budget_dropped"] += 1
            continue
        candidate["title"], title_truncated = _truncate_utf8(
            title, TITLE_BYTES)
        telemetry["title_truncated"] += int(title_truncated)
        summary = candidate.get("summary")
        if summary is not None:
            if not isinstance(summary, str):
                telemetry["field_budget_dropped"] += 1
                continue
            candidate["summary"], summary_truncated = _truncate_utf8(
                summary, SUMMARY_BYTES, SUMMARY_CHARS)
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
                     source_byte_budget: int = SOURCE_CONTEXT_BYTES,
                     source_token_budget: int = SOURCE_CONTEXT_TOKENS,
                     ) -> tuple[list[Item], dict[str, int]]:
    """Filter, deduplicate, diversify, and bound one category for model input.

    Returns both the retained items and counts for observability. Caps are
    applied newest-first, and field bounding works on copies rather than
    mutating the input items.

    `undated_dropped` is counted by the fetchers, before an item ever reaches
    this function, and is carried through so every reason an item is missing
    from the corpus appears in one place.
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
        if by_source.get(source, 0) >= source_cap:
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
                                byte_budget: int = GLOBAL_CONTEXT_BYTES,
                                token_budget: int = GLOBAL_CONTEXT_TOKENS,
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


def positive_int(value: str) -> int:
    """argparse type for strictly positive integer options."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def source_status(source_type: str, source_id: str, category: str,
                  outcome: TimedFetchResult) -> SourceStatus:
    """Convert a fetch outcome into the stable, machine-readable health record."""
    result = outcome.result
    parsed = (result.parsed_entries if result and result.parsed_entries is not None
              else len(result.items) + result.undated if result else 0)
    dated = (result.dated_entries if result and result.dated_entries is not None
             else len(result.items) if result else 0)
    status: SourceStatus = {
        "source_type": source_type,
        "source_id": source_id,
        "category": category,
        "status": "ok",
        "requested": True,
        "http_success": outcome.http_success,
        "parsed_entries": parsed,
        "dated_entries": dated,
        "retained_entries": 0,
        "retained_bytes": 0,
        "estimated_tokens": 0,
        "duration_ms": outcome.duration_ms,
    }
    if result is None:
        status["status"] = "error"
        status["error_type"] = outcome.error_type or "FetchError"
        status["message"] = outcome.message or "unknown fetch error"
    elif parsed == 0:
        status["status"] = "empty"
        status["error_type"] = "EmptySource"
        status["message"] = "response contained zero recognized entries"
    elif dated == 0:
        status["status"] = "empty"
        status["error_type"] = "NoDatedEntries"
        status["message"] = "response contained zero entries with parseable dates"
    return status


def error_record(status: SourceStatus) -> dict[str, Any]:
    """Project a non-healthy source outcome into the compact errors list."""
    return {
        "source_type": status["source_type"],
        "source_id": status["source_id"],
        "status": status["status"],
        "error_type": status.get("error_type", "FetchError"),
        "message": status.get("message", "unknown fetch error"),
        "duration_ms": status["duration_ms"],
    }


def _item_belongs_to_source(item: Item, status: SourceStatus) -> bool:
    source_type = status["source_type"]
    source_id = status["source_id"]
    if source_type == "rss":
        return item.get("source") == source_id
    if source_type == "hacker_news":
        return item.get("source") == "Hacker News" and item.get("query") == source_id
    return item.get("source") == f"r/{source_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, default=DEFAULT_SOURCES_PATH,
                        help=f"source configuration JSON (default: {DEFAULT_SOURCES_PATH})")
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

    try:
        sources = load_sources(args.sources)
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load sources from {args.sources}: {exc}")

    window_end = datetime.now(timezone.utc)
    cutoff = window_end - timedelta(hours=args.hours)

    fetch_started = time.perf_counter()
    corpus: dict[str, Any] = {
        "schema_version": corpus_schema.SCHEMA_VERSION,
        "generated_at": window_end.isoformat(),
        "cutoff": cutoff.isoformat(),
        "window_hours": args.hours,
        "limits": {"source_cap": args.source_cap, "category_cap": args.category_cap},
        "categories": {name: [] for name in sources.categories},
        "processing": {},
        "errors": [],
        "sources": [],
    }

    undated = dict.fromkeys(corpus["categories"], 0)

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

    # Reddit rate-limits concurrent requests; fetch serially with a pause. The
    # authenticated provider is only reached if both free paths yield no items.
    scrapecreators_api_key = os.environ.get("SCRAPECREATORS_API_KEY") or None
    for index, sub in enumerate(sources.subreddits):
        outcome = timed_fetch(
            fetch_reddit,
            sub,
            cutoff,
            window_end,
            args.hours,
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

    corpus["fetch_duration_ms"] = round((time.perf_counter() - fetch_started) * 1000)

    for category in corpus["categories"]:
        items, stats = prepare_category(
            corpus["categories"][category],
            source_cap=args.source_cap,
            category_cap=args.category_cap,
            undated_dropped=undated[category],
        )
        corpus["categories"][category] = items
        corpus["processing"][category] = stats

    used_bytes, estimated_tokens = apply_global_context_budget(
        corpus["categories"], corpus["processing"])

    for status in corpus["sources"]:
        retained = [
            item for item in corpus["categories"][status["category"]]
            if _item_belongs_to_source(item, status)
        ]
        retained_usage = [item_context_usage(item) for item in retained]
        status["retained_entries"] = len(retained)
        status["retained_bytes"] = sum(size for size, _tokens in retained_usage)
        status["estimated_tokens"] = sum(tokens for _size, tokens in retained_usage)
    corpus["errors"] = [error_record(status) for status in corpus["sources"]
                        if status["status"] != "ok"]
    corpus["context_budget"] = {
        "field_limits": {
            "title_bytes": TITLE_BYTES,
            "title_tokens": TITLE_TOKENS,
            "url_bytes": MAX_URL_BYTES,
            "url_tokens": corpus_schema.ITEM_URL_MAX_TOKENS,
            "summary_chars": SUMMARY_CHARS,
            "summary_bytes": SUMMARY_BYTES,
            "summary_tokens": SUMMARY_TOKENS,
            "source_bytes": SOURCE_ID_BYTES,
            "source_tokens": SOURCE_ID_TOKENS,
            "query_bytes": QUERY_BYTES,
            "query_tokens": QUERY_TOKENS,
        },
        "source_max_bytes": SOURCE_CONTEXT_BYTES,
        "source_max_tokens": SOURCE_CONTEXT_TOKENS,
        "global_max_bytes": GLOBAL_CONTEXT_BYTES,
        "global_max_tokens": GLOBAL_CONTEXT_TOKENS,
        "used_bytes": used_bytes,
        "estimated_tokens": estimated_tokens,
        "title_truncated": sum(
            stats["title_truncated"] for stats in corpus["processing"].values()),
        "summary_truncated": sum(
            stats["summary_truncated"] for stats in corpus["processing"].values()),
        "field_budget_dropped": sum(
            stats["field_budget_dropped"] for stats in corpus["processing"].values()),
        "source_budget_dropped": sum(
            stats["source_budget_dropped"] for stats in corpus["processing"].values()),
        "global_budget_dropped": sum(
            stats["global_budget_dropped"] for stats in corpus["processing"].values()),
    }

    total = sum(len(v) for v in corpus["categories"].values())
    # Validate before reporting success: a contract violation identifies a
    # producer bug, and downstream consumers require this exact shape.
    schema_problems = corpus_schema.validate_corpus(corpus)

    if args.markdown:
        lines = [f"# News corpus — last {args.hours}h "
                 f"(generated {window_end:%Y-%m-%d %H:%M} UTC)\n"]
        for category, items in corpus["categories"].items():
            lines.append(f"\n## {category} ({len(items)} items)\n")
            for item in items:
                lines.append(f"- **{item['title']}** ({item['source']}, {item['published'][:16]})\n"
                             f"  {item['url']}")
        if corpus["errors"]:
            lines.append("\n## Fetch errors\n")
            lines.extend(
                f"- {e['source_type']}:{e['source_id']}: "
                f"{e['error_type']}: {e['message']}"
                for e in corpus["errors"])
        text = "\n".join(lines)
    else:
        text = json.dumps(corpus, indent=1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Wrote {total} items ({len(corpus['errors'])} fetch errors) to {args.output}")
    else:
        print(text)

    # The serialized value is emitted before the diagnostic either way because
    # it contains more useful failure context than the violation list alone.
    if schema_problems:
        print(f"error: corpus violates schema v{corpus_schema.SCHEMA_VERSION}:",
              file=sys.stderr)
        for problem in schema_problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    if total == 0:
        print("error: no usable items fetched from any source", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
