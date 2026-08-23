#!/usr/bin/env python3
"""Contract checker for a generated briefing.

The fetcher's rules are deterministic for fixed inputs, so they can be unit
tested even though live source responses are not. The ranking and summarizing
step is not deterministic either — but most of the ways it goes wrong are
structural, not editorial, and structural failures can be checked exactly:

    * a link that isn't in the corpus (the model invented or recalled it)
    * a link altered from the corpus URL it was supposed to reproduce
    * an included or excluded topic with no source citation
    * a story quietly present in both the briefing and the exclusion log
    * a sub-category crowded out of its reserved slots
    * a degraded run reported as if it were healthy

None of that requires a second model to judge. It requires the corpus the
briefing was supposed to be derived from.

Findings come in two levels. ERRORs are contract violations — the briefing
says something the corpus does not support, so the run is not trustworthy.
WARNs are quality targets that a thin corpus can legitimately miss: if only
two dev-practices posts cleared the cutoff, three slots cannot be filled and
that is the corpus's fault, not the model's.

Usage:
    python3 eval_briefing.py --corpus corpus.json --briefing briefing.md
    python3 eval_briefing.py --corpus c.json --briefing b.md --config briefing-config.json --strict
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections.abc import Iterator
from decimal import Decimal, InvalidOperation
from typing import Any, NamedTuple

import briefing_config
import corpus_schema


class Finding(NamedTuple):
    level: str
    check: str
    message: str


class CorpusEvidenceItem(NamedTuple):
    title: str
    support: str


# One parsed briefing section: its topic headlines and the links they cite.
Section = dict[str, Any]

ERROR = "ERROR"
WARN = "WARN"

EXCLUDED = "Excluded Topics"
CORPUS_HEALTH = "Corpus health"

# A heading (## / ###) or a bold sub-header, either of which starts a section.
_SECTION_LINE = re.compile(
    r"^\s*(?:#{2,4}\s*(?P<heading>.+?)\s*$|\*\*(?P<bold>[^*]+?)\*\*\s*$)")
# A topic entry: **Headline** — summary. The em dash is what separates a topic
# from a bold sub-header like **AI News (4 slots)**.
_TOPIC_LINE = re.compile(
    r"^\s*\*\*(?P<title>.+?)\*\*\s*(?:\*\([^)]*\)\*\s*)?"
    r"(?:\[verbatim\]\s*)?[—-]\s*(?P<prose>\S.*)$")
# High-risk assertions, checkable without a second model: a figure or a
# quotation that does not appear in the evidence for the item being cited.
_FIGURE = re.compile(
    r"\d[\d,.]*(?:\s*[-\u2013\u2014]\s*\d[\d,.]*)?(?:\s*(?:%|percent))?"
)
_ABBREVIATED_YEAR_RANGE = re.compile(
    r"^(?P<start>\d{4})\s*[-\u2013\u2014]\s*(?P<end>\d{2})$"
)
_SCALAR_FIGURE = r"\d[\d,.]*"
_WORD_RANGE = re.compile(
    rf"\b(?:between\s+(?P<between_start>{_SCALAR_FIGURE})\s+and\s+"
    rf"(?P<between_end>{_SCALAR_FIGURE})|from\s+(?P<from_start>{_SCALAR_FIGURE})"
    rf"\s+to\s+(?P<from_end>{_SCALAR_FIGURE}))\b",
    re.IGNORECASE,
)
_DURATION = re.compile(
    rf"(?P<figure>{_SCALAR_FIGURE})\s*(?P<unit>milliseconds?|seconds?|minutes?|hours?)\b",
    re.IGNORECASE,
)
_DURATION_MILLISECONDS = {
    "millisecond": Decimal(1),
    "milliseconds": Decimal(1),
    "second": Decimal(1000),
    "seconds": Decimal(1000),
    "minute": Decimal(60_000),
    "minutes": Decimal(60_000),
    "hour": Decimal(3_600_000),
    "hours": Decimal(3_600_000),
}
_QUOTATION = re.compile(r"[\"\u201c\u201d]([^\"\u201c\u201d]{4,80})[\"\u201c\u201d]")
# A summary meaningfully longer than the text supporting it has added
# something. The corpus already holds a truncated blurb, not the article,
# so prose outgrowing it by this much is elaboration rather than compression.
CLAIM_EVIDENCE_RATIO = 2.0
# Links appear on their own line in the body but inline in the exclusion log
# ("- *Title* — reason. 🔗 url"), so scan anywhere in the line rather than
# anchoring to the start. Both locations are part of the citation contract and
# must be validated.
_LINK = re.compile(r"🔗\s*(?:HN:\s*)?(?P<url>\S+)")
# Security validation is deliberately independent of the presentation parser.
# The model controls the complete Markdown document, so every web destination
# is checked whether it is bare, Markdown, an autolink, or in HTML. Scan raw
# text first: unescaping the whole document alone would corrupt legitimate bare
# query parameters such as ``&copy=1``. A second, masked/unescaped pass catches
# entity-encoded schemes without reinterpreting URLs already found in raw text.
_HTTP_URL = re.compile(r"\bhttps?://[^\s<>\"']+", re.IGNORECASE)
_PROTOCOL_RELATIVE_URL = re.compile(r"(?<![:/])//[^\s<>\"']+")
_WWW_URL = re.compile(r"(?<![\w@/.-])www\.[^\s<>\"']+", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^\s*[-*]\s+\S")
_SLOT_SUFFIX = re.compile(r"\s*\(\d+\s+(?:slots?|stories)\)\s*$", re.IGNORECASE)


def _clean_link_url(value: str) -> str:
    """Remove sentence punctuation without damaging balanced URL parentheses."""
    url = value.strip().rstrip(".,;")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(".,;")
    return url


def url_spellings(text: str) -> list[tuple[int, int, str, str]]:
    """Return non-overlapping web destinations and their absolute forms."""
    candidates: list[tuple[int, int, str, str]] = []
    occupied: list[tuple[int, int]] = []
    patterns = (
        (_HTTP_URL, lambda value: value),
        (_PROTOCOL_RELATIVE_URL, lambda value: f"https:{value}"),
        (_WWW_URL, lambda value: f"https://{value}"),
    )
    for pattern, make_absolute in patterns:
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < prior_end and prior_start < end
                   for prior_start, prior_end in occupied):
                continue
            spelled = _clean_link_url(match.group())
            candidates.append((start, end, spelled, make_absolute(spelled)))
            occupied.append((start, end))
    return candidates


def output_urls(text: str) -> list[tuple[str, str]]:
    """Return every distinct clickable web destination in model output.

    Each pair is ``(canonical URL, output spelling)``. HTML character
    references are decoded in a second pass because browsers decode them in
    attributes. Raw destinations are masked first so semicolonless HTML named
    character references inside a legitimate bare URL cannot change its identity.
    """
    found: dict[str, str] = {}
    raw = url_spellings(text)
    for _start, _end, spelled, absolute in raw:
        canonical = corpus_schema.canonicalize_url(absolute)
        found.setdefault(canonical, spelled)

    masked = list(text)
    for start, end, _spelled, _absolute in raw:
        masked[start:end] = " " * (end - start)
    decoded = html.unescape("".join(masked))
    for _start, _end, spelled, absolute in url_spellings(decoded):
        canonical = corpus_schema.canonicalize_url(absolute)
        found.setdefault(canonical, spelled)
    return list(found.items())


def load_corpus(path: str) -> dict[str, Any]:
    """Load a corpus, refusing one this checker cannot read correctly.

    An older corpus is fine — the fields read here have only been added to.
    A newer one may have moved something, and misreading it would produce
    confident findings about the wrong fields, which is worse than stopping.
    """
    with open(path, encoding="utf-8") as f:
        corpus = json.load(f)
    if not isinstance(corpus, dict):
        raise ValueError("corpus is not a JSON object")
    if not corpus_schema.is_readable(corpus):
        raise ValueError(
            f"corpus schema v{corpus_schema.corpus_version(corpus)} is newer than "
            f"v{corpus_schema.SCHEMA_VERSION}, which is the newest this checker "
            f"understands — upgrade eval_briefing.py")
    problems = corpus_schema.validate_corpus(corpus)
    if problems:
        detail = "; ".join(problems)
        raise ValueError(f"corpus violates schema v{corpus_schema.corpus_version(corpus)}: {detail}")
    return corpus


def _corpus_urls(corpus: dict[str, Any]) -> Iterator[tuple[str, str]]:
    """Yield (category, url) for every citable URL in the corpus."""
    for category, items in corpus.get("categories", {}).items():
        for item in items:
            for key in ("url", "discussion"):
                value = (item.get(key) or "").strip()
                if value:
                    yield category, value


def corpus_links(corpus: dict[str, Any]) -> dict[str, str]:
    """Canonical form -> the URL as the corpus spells it.

    Keyed canonically so a citation that differs only in trailing slash, host
    case, parameter order or `utm_` noise is recognized as the same article.
    The original spelling is the value because a mismatch report has to show
    the reader what to paste back.
    """
    return {corpus_schema.canonicalize_url(url): url
            for _, url in _corpus_urls(corpus)}


def corpus_link_routes(corpus: dict[str, Any]) -> dict[str, list[tuple[frozenset[tuple[str, str]], str]]]:
    """Location -> every (query parameters, URL) the corpus holds under it.

    A list rather than a single entry per location, because query-routed
    publishers put many distinct articles under one path. Keying those by
    location alone would keep whichever was seen last and silently discard
    the rest.
    """
    routes: dict[str, list[tuple[frozenset[tuple[str, str]], str]]] = {}
    for _, url in _corpus_urls(corpus):
        location, params = corpus_schema.url_route(url)
        if location:
            routes.setdefault(location, []).append(
                (params, corpus_schema.canonicalize_url(url)))
    return routes


def altered_from(url: str, routes: dict[str, list[tuple[frozenset[tuple[str, str]], str]]]) -> str | None:
    """The corpus URL a citation was tidied from, if exactly one can be named.

    A citation counts as tidied only when a corpus URL at the same location
    carries every parameter the citation carries, plus at least one more: the
    model dropped parameters and changed nothing else. That is the whole of
    what a mechanical rewrite can be.

    Anything else is a different request. `item?id=999` does not drop a
    parameter from `item?id=123`, it supplies a different value, so it is a
    different article and no rewrite is claimed. An added parameter is not a
    tidy-up either. Where two corpus URLs would both fit, none is named:
    guessing between them would print a URL the reader never cited.
    """
    location, params = corpus_schema.url_route(url)
    if not location:
        return None
    candidates = [candidate for corpus_params, candidate in routes.get(location, [])
                  if params < corpus_params]
    return candidates[0] if len(candidates) == 1 else None


def corpus_link_categories(corpus: dict[str, Any]) -> dict[str, set[str]]:
    """Map every article and discussion URL to its corpus categories."""
    categories: dict[str, set[str]] = {}
    for category, url in _corpus_urls(corpus):
        categories.setdefault(corpus_schema.canonicalize_url(url), set()).add(category)
    return categories


def hacker_news_links(corpus: dict[str, Any]) -> dict[str, str]:
    """Map each Hacker News article URL to its discussion URL."""
    pairs = {}
    for items in corpus.get("categories", {}).values():
        for item in items:
            discussion = (item.get("discussion") or "").strip()
            url = (item.get("url") or "").strip()
            if discussion and url:
                pairs[corpus_schema.canonicalize_url(url)] = \
                    corpus_schema.canonicalize_url(discussion)
    return pairs


def _match_section(label: str, config: briefing_config.BriefingConfig) -> str | None:
    """Map a heading or sub-header to a known section name, or None."""
    normalized = _SLOT_SUFFIX.sub("", label.strip()).casefold()
    for section in config.sections:
        if normalized == section.name.casefold():
            return section.name
    if normalized.startswith(EXCLUDED.casefold()):
        return EXCLUDED
    health = CORPUS_HEALTH.casefold()
    if (normalized == health
            or any(normalized.startswith(f"{health}{separator}")
                   for separator in (":", " —", " -"))):
        return CORPUS_HEALTH
    return None


def _new_section() -> Section:
    """Create one complete parser bucket so both section paths stay in sync."""
    return {"topics": [], "topic_texts": [], "topic_links": [], "topic_link_spellings": [],
            "links": [], "spelled": {}, "excluded": {}, "lines": []}


def parse_briefing(text: str, config: briefing_config.BriefingConfig | None = None) -> dict[str, Section]:
    """Split a briefing into sections.

    Deliberately tolerant: it keys off section labels and 🔗 lines rather than
    an exact template, so cosmetic prompt edits don't break the checker. It
    reports what it found; the checks decide whether that's acceptable.
    """
    config = config or briefing_config.load_config()
    sections: dict[str, Section] = {}
    current = None
    in_excluded = False
    excluded_current = None

    for line in text.splitlines():
        marker = _SECTION_LINE.match(line)
        if marker:
            heading, bold = marker.group("heading"), marker.group("bold")
            matched = _match_section(heading or bold, config)
            if heading is not None:
                # A real heading always ends the previous section. An
                # unrecognized one (e.g. "AI/Tech", a container) parks the
                # parser until a known sub-header appears.
                current = matched
                in_excluded = matched == EXCLUDED
                excluded_current = None
                if current:
                    bucket = sections.setdefault(current, _new_section())
                    if current == CORPUS_HEALTH:
                        bucket["lines"].append(heading)
                continue
            if in_excluded:
                # Inside the exclusion log, bold labels are per-section
                # sub-headers, not a return to the top-level section.
                excluded_current = matched or bold.strip()
                sections[EXCLUDED]["excluded"].setdefault(excluded_current, [])
                continue
            if matched:
                current = matched
                sections.setdefault(current, _new_section())
                continue

        if current is None:
            continue
        bucket = sections[current]
        if current == CORPUS_HEALTH:
            # Health matching is deliberately section-scoped. Retain only this
            # small section rather than duplicating the whole briefing.
            bucket["lines"].append(line)

        if in_excluded and _LIST_ITEM.match(line) and excluded_current:
            bucket["excluded"][excluded_current].append(line.strip())
        elif not in_excluded:
            topic = _TOPIC_LINE.match(line)
            if topic:
                bucket["topics"].append(topic.group("title").strip())
                # Inline citations belong to the grounding contract, not to
                # the model-authored claim. Keep their destinations below,
                # but exclude them from figure, quotation, and length checks.
                claim_prose = _LINK.sub("", topic.group("prose")).strip()
                bucket["topic_texts"].append(claim_prose)
                bucket["topic_links"].append([])
                bucket["topic_link_spellings"].append([])

        for link in _LINK.finditer(line):
            # A sentence-closing parenthesis is not part of the citation, but
            # preserve balanced parentheses that genuinely belong to a URL.
            spelled = _clean_link_url(link.group("url"))
            # Links are compared in canonical form everywhere downstream, so
            # normalize once here. The spelling the author used is kept beside
            # it: a report about a link is useless if the reader cannot find
            # the line it came from.
            url = corpus_schema.canonicalize_url(spelled)
            bucket["spelled"].setdefault(url, spelled)
            bucket["links"].append(url)
            if not in_excluded and bucket["topic_links"]:
                bucket["topic_links"][-1].append(url)
                bucket["topic_link_spellings"][-1].append(spelled)

    return sections


def check_sections_present(sections: dict[str, Section],
                           config: briefing_config.BriefingConfig) -> list[Finding]:
    """Every configured section must appear, and so must the exclusion log.

    The log is required only while some section still contributes to it.
    `excluded_stories: 0` exempts a section, so a configuration that exempts
    every section has nothing to put under the heading, and demanding it
    anyway would contradict the configuration this check enforces.
    """
    required = [section.name for section in config.sections]
    if any(section.excluded_stories > 0 for section in config.sections):
        required.append(EXCLUDED)
    findings: list[Finding] = []
    for name in required:
        if name not in sections:
            findings.append(Finding(ERROR, "missing_section",
                                    f"required section {name!r} is absent"))
    return findings


def check_links_grounded(sections: dict[str, Section], text: str,
                         corpus: dict[str, Any]) -> list[Finding]:
    """Every web destination in the complete output must exist in the corpus.

    Both failures are ERRORs — every output destination must come from the corpus —
    but they are named apart because the reader's next action differs. An
    `altered_link` is a real article with a rewritten URL and the fix is to
    paste the corpus spelling back; an `ungrounded_link` has no corpus article
    behind it at all, which is the fabrication case and warrants re-reading
    the whole topic.
    """
    findings: list[Finding] = []
    allowed = corpus_links(corpus)
    routes = corpus_link_routes(corpus)
    marked_sections = {
        url: name
        for name, bucket in sections.items()
        for url in bucket["links"]
    }
    for url, spelled in output_urls(text):
        if url in allowed:
            continue
        context = marked_sections.get(url, "complete output")
        corpus_url = altered_from(url, routes)
        if corpus_url:
            findings.append(Finding(
                ERROR, "altered_link",
                f"{context}: HTTP(S) URL was altered from the corpus URL — "
                f"output has {spelled}, corpus has {corpus_url}"))
        else:
            findings.append(Finding(
                ERROR, "ungrounded_link",
                f"{context}: HTTP(S) URL is not in the corpus — {spelled}"))
    return findings


def check_section_categories(sections: dict[str, Section], corpus: dict[str, Any],
                             config: briefing_config.BriefingConfig) -> list[Finding]:
    """Cited items must come from a category eligible for their section."""
    findings: list[Finding] = []
    link_categories = corpus_link_categories(corpus)
    configured = {section.name: set(section.corpus_categories)
                  for section in config.sections}

    for name, eligible in configured.items():
        for url in sections.get(name, {}).get("links", []):
            actual = link_categories.get(url)
            if actual is not None and actual.isdisjoint(eligible):
                findings.append(Finding(
                    ERROR, "category_ineligible",
                    f"{name}: cited item belongs to {', '.join(sorted(actual))}, "
                    f"not an eligible category — {url}"))

    excluded = sections.get(EXCLUDED, {}).get("excluded", {})
    for name, eligible in configured.items():
        for entry in excluded.get(name, []):
            for match in _LINK.finditer(entry):
                url = corpus_schema.canonicalize_url(_clean_link_url(match.group("url")))
                actual = link_categories.get(url)
                if actual is not None and actual.isdisjoint(eligible):
                    findings.append(Finding(
                        ERROR, "category_ineligible",
                        f"{name} exclusion: cited item belongs to "
                        f"{', '.join(sorted(actual))}, not an eligible category — {url}"))
    return findings


def check_every_entry_cites_source(sections: dict[str, Section]) -> list[Finding]:
    """Every included topic and excluded row must carry corpus provenance."""
    findings: list[Finding] = []
    for name, bucket in sections.items():
        if name == EXCLUDED:
            for excluded_name, entries in bucket["excluded"].items():
                for index, entry in enumerate(entries, 1):
                    if not _LINK.search(entry):
                        findings.append(Finding(
                            ERROR, "excluded_topic_without_link",
                            f"{excluded_name}: excluded entry {index} has no cited link"))
            continue
        for index, links in enumerate(bucket["topic_links"], 1):
            if not links:
                findings.append(Finding(
                    ERROR, "topic_without_link",
                    f"{name}: topic {index} has no cited link"))
    return findings


def check_slot_allocation(sections: dict[str, Section],
                          config: briefing_config.BriefingConfig) -> list[Finding]:
    findings: list[Finding] = []
    for section in config.sections:
        name = section.name
        expected = section.target_stories
        if name not in sections:
            continue
        actual = len(sections[name]["topics"])
        if actual < expected:
            findings.append(Finding(
                WARN, "slots_underfilled",
                f"{name}: {actual} topics, expected {expected} "
                f"(thin corpus is a legitimate cause)"))
        elif actual > expected:
            findings.append(Finding(
                ERROR, "slots_overfilled",
                f"{name}: {actual} topics, expected {expected} — a section "
                f"exceeding its reserved slots crowds out the others"))
    return findings


def check_no_double_listing(sections: dict[str, Section]) -> list[Finding]:
    """A story counted as both included and excluded is an accounting error."""
    findings: list[Finding] = []
    if EXCLUDED not in sections:
        return findings
    included = {url for name, bucket in sections.items() if name != EXCLUDED
                for url in bucket["links"]}
    for url in sections[EXCLUDED]["links"]:
        if url in included:
            findings.append(Finding(
                ERROR, "included_and_excluded",
                f"link appears in both the briefing and the exclusion log — {url}"))
    return findings


def check_no_repeated_topics(sections: dict[str, Section]) -> list[Finding]:
    """A story is reported once; an exactly repeated citation is named separately.

    Canonical-equivalent spellings identify the same story. Repeating the exact
    spelling inside one topic is a duplicate citation; citing the same story via
    different spellings, topics, or sections is a repeated topic. The same event
    filed by two outlets under two URLs remains a semantic consolidation question.
    """
    findings: list[Finding] = []
    occurrences: dict[str, list[tuple[str, int, str]]] = {}
    for name, bucket in sections.items():
        if name == EXCLUDED:
            continue
        for index, (links, spellings) in enumerate(zip(
                bucket["topic_links"], bucket["topic_link_spellings"], strict=True), 1):
            for url, spelling in zip(links, spellings, strict=True):
                occurrences.setdefault(url, []).append((name, index, spelling))

    for url, rows in occurrences.items():
        if len(rows) <= 1:
            continue
        topic_locations = {(name, index) for name, index, _spelling in rows}
        spellings = {spelling for _name, _index, spelling in rows}
        if len(topic_locations) == 1 and len(spellings) == 1:
            findings.append(Finding(
                ERROR, "duplicate_citation",
                f"citation is printed {len(rows)} times in one topic — {url}"))
        else:
            findings.append(Finding(
                ERROR, "repeated_topic",
                f"topic is cited {len(rows)} times across distinct spellings or topics — {url}"))
    return findings


def check_exclusion_log(sections: dict[str, Section], corpus: dict[str, Any],
                        config: briefing_config.BriefingConfig) -> list[Finding]:
    """Require as many exclusions as the corpus can actually supply.

    ``excluded_stories`` is a target, not permission to fabricate padding. A
    story reported in any section is unavailable to every exclusion log, and
    overlapping corpus categories still identify one story by canonical URL.
    """
    findings: list[Finding] = []
    if EXCLUDED not in sections:
        return findings
    logged = sections[EXCLUDED]["excluded"]
    included = {
        url
        for name, bucket in sections.items()
        if name != EXCLUDED
        for url in bucket["links"]
    }
    for section in config.sections:
        target = section.excluded_stories
        if target == 0:
            continue
        name = section.name
        eligible_urls = {
            corpus_schema.canonicalize_url(item["url"])
            for category in section.corpus_categories
            for item in corpus.get("categories", {}).get(category, [])
            if isinstance(item, dict) and isinstance(item.get("url"), str)
        }
        expected = min(target, len(eligible_urls - included))
        if expected == 0:
            continue
        entries = logged.get(name, [])
        if not entries:
            findings.append(Finding(
                WARN, "exclusion_log_missing",
                f"exclusion log has no entries for {name!r}"))
        elif len(entries) < expected:
            findings.append(Finding(
                WARN, "exclusion_log_short",
                f"exclusion log for {name!r}: {len(entries)} entries, "
                f"expected {expected}"))
    return findings


def check_hn_discussion_links(sections: dict[str, Section],
                              hn_pairs: dict[str, str]) -> list[Finding]:
    """Keep the useful HN discussion link even though metrics are omitted."""
    findings: list[Finding] = []
    for name, bucket in sections.items():
        if name == EXCLUDED:
            continue
        cited = set(bucket["links"])
        for url in bucket["links"]:
            discussion = hn_pairs.get(url)
            if discussion and discussion not in cited:
                findings.append(Finding(
                    WARN, "missing_discussion_link",
                    f"{name}: Hacker News item cited without its discussion "
                    f"link — {url}"))
    return findings


def _failed_source(error: str) -> str:
    """Return the exact source ID from the fetcher's human-readable error.

    Source configuration rejects the ``": "`` delimiter, while the ``HN:``
    namespace deliberately uses a colon without a following space.
    """
    return error.split(": ", 1)[0].strip()


def _normalize_source_mention(value: str) -> str:
    """Ignore harmless case, wrapping, and HN colon-spacing differences."""
    normalized = re.sub(r"\s+", " ", value.casefold()).strip()
    return re.sub(r"\bhn:\s+", "hn:", normalized)


def check_corpus_health_reported(sections: dict[str, Section],
                                 corpus: dict[str, Any]) -> list[Finding]:
    """A degraded run must look degraded, or the briefing overstates coverage."""
    errors = corpus.get("errors", [])
    undated_sources = corpus_schema.undated_source_records(corpus)
    undated_total = sum(
        stats.get("undated_dropped", 0)
        for stats in corpus.get("processing", {}).values()
        if isinstance(stats, dict)
    )
    if not errors and not undated_total:
        return []
    if CORPUS_HEALTH not in sections:
        degradation = f"{len(errors)} fetch error(s)"
        if undated_total:
            degradation += f" and {undated_total} undated item drop(s)"
        missing_findings = [Finding(
            ERROR, "corpus_health_missing",
            f"corpus recorded {degradation} but the "
            f"briefing has no {CORPUS_HEALTH!r} section")]
        if corpus_schema.corpus_version(corpus) >= 4:
            for error in errors:
                missing_findings.append(Finding(
                    ERROR, "failed_source_unnamed",
                    f"failed source {error['source_type']}:{error['source_id']} "
                    f"({error['status']}) is absent because the health manifest is missing"))
            for undated_source in undated_sources:
                missing_findings.append(Finding(
                    ERROR, "undated_source_unnamed",
                    f"source {undated_source['source_type']}:{undated_source['source_id']} dropped "
                    f"{undated_source['count']} undated item(s) and is absent because the health "
                    "manifest is missing"))
        return missing_findings
    if corpus_schema.corpus_version(corpus) >= 4:
        return _check_structured_corpus_health(
            sections[CORPUS_HEALTH], errors, undated_sources)

    findings: list[Finding] = []
    health_text = _normalize_source_mention(
        "\n".join(sections[CORPUS_HEALTH]["lines"]))
    for error in errors:
        source = _failed_source(error)
        normalized_source = _normalize_source_mention(source)
        if source and not re.search(
                rf"(?<!\w){re.escape(normalized_source)}(?![\w/])", health_text):
            findings.append(Finding(
                ERROR, "failed_source_unnamed",
                f"failed source {source!r} is not named in the briefing"))
    return findings


def _check_structured_corpus_health(section: Section,
                                    errors: list[dict[str, Any]],
                                    undated_sources: list[corpus_schema.UndatedSourceRecord],
                                    ) -> list[Finding]:
    """Require exact degraded-source identities in a JSON health manifest."""
    text = "\n".join(section["lines"])
    blocks = re.findall(r"```json\s*(.*?)\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if len(blocks) != 1:
        return [Finding(
            ERROR, "corpus_health_not_machine_readable",
            "Corpus health must contain exactly one fenced JSON health manifest")]
    try:
        manifest = json.loads(blocks[0])
    except json.JSONDecodeError as exc:
        return [Finding(
            ERROR, "corpus_health_not_machine_readable",
            f"Corpus health JSON is invalid at line {exc.lineno}, column {exc.colno}")]
    expected_keys = {"failed_sources"}
    if undated_sources:
        expected_keys.add("undated_sources")
    if not isinstance(manifest, dict) or set(manifest) != expected_keys:
        shape_problem = (
            "Corpus health JSON must be an object containing only failed_sources"
            if expected_keys == {"failed_sources"}
            else "Corpus health JSON must contain exactly " + ", ".join(sorted(expected_keys))
        )
        return [Finding(
            ERROR, "corpus_health_not_machine_readable",
            shape_problem)]
    reported = manifest["failed_sources"]
    if not isinstance(reported, list):
        return [Finding(ERROR, "corpus_health_not_machine_readable",
                        "failed_sources must be a JSON array")]

    findings: list[Finding] = []
    expected = {(error["source_type"], error["source_id"], error["status"])
                for error in errors}
    actual: set[tuple[str, str, str]] = set()
    for index, record in enumerate(reported):
        if (not isinstance(record, dict)
                or set(record) != {"source_type", "source_id", "status"}
                or any(not isinstance(record.get(field), str)
                       for field in ("source_type", "source_id", "status"))):
            findings.append(Finding(
                ERROR, "corpus_health_not_machine_readable",
                f"failed_sources[{index}] must contain string source_type, source_id, and status only"))
            continue
        actual.add((record["source_type"], record["source_id"], record["status"]))

    expected_by_source = {(source_type, source_id): status
                          for source_type, source_id, status in expected}
    actual_by_source: dict[tuple[str, str], set[str]] = {}
    for source_type, source_id, status in actual:
        actual_by_source.setdefault((source_type, source_id), set()).add(status)
    shared_sources = expected_by_source.keys() & actual_by_source.keys()

    for source_type, source_id in sorted(shared_sources):
        expected_status = expected_by_source[(source_type, source_id)]
        reported_statuses = actual_by_source[(source_type, source_id)]
        if reported_statuses != {expected_status}:
            findings.append(Finding(
                ERROR, "failed_source_status_mismatch",
                f"failed source {source_type}:{source_id!s} has status {expected_status!r} "
                f"in the corpus but {', '.join(repr(value) for value in sorted(reported_statuses))} "
                "in the health manifest"))

    missing_sources = expected_by_source.keys() - actual_by_source.keys()
    for source_type, source_id in sorted(missing_sources):
        status = expected_by_source[(source_type, source_id)]
        findings.append(Finding(
            ERROR, "failed_source_unnamed",
            f"failed source {source_type}:{source_id!s} ({status}) is absent from the health manifest"))
    unexpected_sources = actual_by_source.keys() - expected_by_source.keys()
    for source_type, source_id in sorted(unexpected_sources):
        for status in sorted(actual_by_source[(source_type, source_id)]):
            findings.append(Finding(
                ERROR, "unexpected_failed_source",
                f"health manifest reports unrecorded failure {source_type}:{source_id!s} ({status})"))
    if len(actual) != len(reported):
        findings.append(Finding(ERROR, "duplicate_failed_source",
                                "health manifest contains a duplicate failed-source record"))
    if undated_sources:
        reported_undated = manifest["undated_sources"]
        if not isinstance(reported_undated, list):
            findings.append(Finding(ERROR, "corpus_health_not_machine_readable",
                                    "undated_sources must be a JSON array"))
            return findings
        expected_undated = {
            (record["source_type"], record["source_id"]): record["count"]
            for record in undated_sources
        }
        actual_undated: dict[tuple[str, str], set[int]] = {}
        for index, record in enumerate(reported_undated):
            if (not isinstance(record, dict)
                    or set(record) != {"source_type", "source_id", "count"}
                    or not isinstance(record.get("source_type"), str)
                    or not isinstance(record.get("source_id"), str)
                    or not isinstance(record.get("count"), int)
                    or isinstance(record.get("count"), bool)
                    or record["count"] <= 0):
                findings.append(Finding(
                    ERROR, "corpus_health_not_machine_readable",
                    f"undated_sources[{index}] must contain string source_type, source_id, "
                    "and positive integer count only"))
                continue
            key = (record["source_type"], record["source_id"])
            actual_undated.setdefault(key, set()).add(record["count"])
        for key in sorted(expected_undated.keys() | actual_undated.keys()):
            if key not in actual_undated:
                findings.append(Finding(
                    ERROR, "undated_source_unnamed",
                    f"source {key[0]}:{key[1]} with {expected_undated[key]} undated item(s) "
                    "is absent from the health manifest"))
            elif key not in expected_undated:
                findings.append(Finding(
                    ERROR, "unexpected_undated_source",
                    f"health manifest reports unrecorded undated drops for {key[0]}:{key[1]}"))
            elif actual_undated[key] != {expected_undated[key]}:
                findings.append(Finding(
                    ERROR, "undated_source_count_mismatch",
                    f"source {key[0]}:{key[1]} dropped {expected_undated[key]} undated item(s) "
                    f"but the health manifest reports {sorted(actual_undated[key])}"))
        if len(reported_undated) != len(actual_undated):
            findings.append(Finding(ERROR, "duplicate_undated_source",
                                    "health manifest contains a duplicate undated-source record"))
    return findings


def _normalize(text: str) -> str:
    """Collapse to comparable characters so punctuation can't hide a match."""
    return re.sub(r"[^a-z0-9%]+", "", text.lower())


def _normalize_figure(text: str) -> str:
    """Normalize figures while expanding abbreviated year-range endings."""
    value = text.strip()
    if match := _ABBREVIATED_YEAR_RANGE.fullmatch(value):
        start = int(match.group("start"))
        end = (start // 100) * 100 + int(match.group("end"))
        if end < start:
            end += 100
        return f"{start}{end}"
    return _normalize(value)


def _decimal_token(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")


def _duration_token(figure: str, unit: str) -> str | None:
    try:
        value = Decimal(figure.replace(",", ""))
    except InvalidOperation:
        return None
    milliseconds = value * _DURATION_MILLISECONDS[unit.casefold()]
    return f"duration-ms:{_decimal_token(milliseconds)}"


def _figure_tokens_for_match(text: str, match: re.Match[str]) -> set[str]:
    """Return exact and deterministic equivalent tokens for one figure."""
    tokens = {_normalize_figure(match.group())}
    for duration in _DURATION.finditer(text):
        if (
            duration.start("figure") == match.start()
            and duration.end("figure") == match.end()
            and (token := _duration_token(duration.group("figure"), duration.group("unit")))
        ):
            tokens.add(token)
    return {token for token in tokens if token}


def corpus_evidence(corpus: dict[str, Any]) -> dict[str, str]:
    """Cited URL -> the text the briefing is entitled to draw claims from.

    That is the item's title and summary, and nothing else. Notably it is not
    the article: the fetcher stores a truncated feed blurb, so this is the
    ceiling on what any claim about the story can be grounded in.
    """
    evidence = {}
    for items in corpus.get("categories", {}).values():
        for item in items:
            support = f"{item.get('title', '')} {item.get('summary', '')}".strip()
            for key in ("url", "discussion"):
                url = (item.get(key) or "").strip()
                if url:
                    # Canonical, because the briefing's links are: a citation
                    # that differs cosmetically must still find its evidence,
                    # or the claim checks silently skip the topic.
                    evidence[corpus_schema.canonicalize_url(url)] = support
    return evidence


def corpus_evidence_items(corpus: dict[str, Any]) -> list[CorpusEvidenceItem]:
    """Return item-level evidence for conservative same-story cross-checks."""
    evidence = []
    for items in corpus.get("categories", {}).values():
        for item in items:
            title = item.get("title", "")
            support = f"{title} {item.get('summary', '')}".strip()
            if isinstance(title, str) and support:
                evidence.append(CorpusEvidenceItem(title, support))
    return evidence


_TITLE_STOP_WORDS = frozenset({
    "a", "an", "and", "as", "at", "by", "for", "from", "in", "into",
    "of", "on", "the", "to", "with",
})


def _title_terms(title: str) -> set[str]:
    return {
        term
        for term in re.findall(r"[a-z0-9]+", title.casefold())
        if term not in _TITLE_STOP_WORDS and not term.isdigit()
    }


def _figure_tokens(text: str) -> set[str]:
    """Extract whole figures plus deterministic range and duration equivalents."""
    tokens = {
        token
        for match in _FIGURE.finditer(text)
        for token in _figure_tokens_for_match(text, match)
    }
    for match in _WORD_RANGE.finditer(text):
        start = match.group("between_start") or match.group("from_start")
        end = match.group("between_end") or match.group("from_end")
        tokens.add(_normalize_figure(f"{start}-{end}"))
    return tokens


def _topically_matching_support(
    title: str,
    figure_tokens: set[str],
    all_evidence: list[CorpusEvidenceItem],
) -> CorpusEvidenceItem | None:
    """Find strong title overlap plus exact figure support in another item.

    A number appearing somewhere else in a large corpus proves nothing by
    itself. Requiring at least three shared title terms and 60% overlap with
    the shorter title keeps this a conservative same-story signal.
    """
    title_terms = _title_terms(title)
    if len(title_terms) < 3:
        return None
    for item in all_evidence:
        item_terms = _title_terms(item.title)
        shared = title_terms & item_terms
        if (len(shared) >= 3
                and len(shared) / min(len(title_terms), len(item_terms)) >= 0.6
                and figure_tokens & _figure_tokens(item.support)):
            return item
    return None


def check_claims_supported(
    sections: dict[str, Section],
    evidence: dict[str, str],
    all_evidence: list[CorpusEvidenceItem] | None = None,
) -> list[Finding]:
    """Flag prose that asserts more than its cited items can support.

    Entailment can't be settled without a second model, so this does not try.
    It checks the parts that can be settled exactly — figures and quotations —
    and flags summaries that outgrew their evidence. All WARN: citation
    grounding is a contract, claim grounding is a signal for a human to read.
    """
    findings = []
    for name, bucket in sections.items():
        if name == EXCLUDED:
            continue
        # Appended in lockstep by parse_briefing, so strict= documents that
        # invariant and fails loudly if it is ever broken.
        for title, prose, links in zip(bucket.get("topics", []),
                                       bucket.get("topic_texts", []),
                                       bucket.get("topic_links", []),
                                       strict=True):
            # A Hacker News item is reachable by both its article and its
            # discussion URL; counting its text twice would forgive twice as
            # much unsupported prose.
            support = " ".join(dict.fromkeys(
                evidence[url] for url in links if evidence.get(url))).strip()
            if not support:
                continue
            support_figures = _figure_tokens(support)
            prose_ranges = list(_WORD_RANGE.finditer(prose))

            for match in _FIGURE.finditer(prose):
                figure = match.group()
                tokens = _figure_tokens_for_match(prose, match)
                for range_match in prose_ranges:
                    if range_match.start() <= match.start() and match.end() <= range_match.end():
                        start = (
                            range_match.group("between_start")
                            or range_match.group("from_start")
                        )
                        end = (
                            range_match.group("between_end")
                            or range_match.group("from_end")
                        )
                        tokens.add(_normalize_figure(f"{start}-{end}"))
                if tokens and not tokens & support_figures:
                    elsewhere = _topically_matching_support(
                        title, tokens, all_evidence or [])
                    if elsewhere:
                        findings.append(Finding(
                            WARN, "figure_supported_elsewhere",
                            f"{name}: {title!r} states {figure.strip()!r}; it is absent "
                            f"from the cited corpus excerpts but appears in the "
                            f"topically matching uncited item {elsewhere.title!r}"))
                    else:
                        findings.append(Finding(
                            WARN, "unsupported_figure",
                            f"{name}: {title!r} states {figure.strip()!r}, which is not "
                            f"supported by the cited corpus excerpts or a topically "
                            f"matching corpus item"))

            for quotation in _QUOTATION.findall(prose):
                if _normalize(quotation) not in _normalize(support):
                    findings.append(Finding(
                        WARN, "unsupported_quotation",
                        f"{name}: {title!r} quotes \"{quotation}\", which does not "
                        f"appear in the item(s) it cites"))

            if len(prose) > CLAIM_EVIDENCE_RATIO * len(support):
                findings.append(Finding(
                    WARN, "claim_exceeds_evidence",
                    f"{name}: {title!r} has {len(prose)} characters of summary "
                    f"from {len(support)} characters of evidence — the surplus "
                    f"is not grounded in the corpus"))
    return findings


def evaluate_parsed(corpus: dict[str, Any], text: str, sections: dict[str, Section],
                    config: briefing_config.BriefingConfig | None = None) -> list[Finding]:
    """Run every check against an already-parsed briefing and return findings."""
    config = config or briefing_config.load_config()
    findings: list[Finding] = []
    category_problems = briefing_config.validate_corpus_categories(
        config, set(corpus.get("categories", {})))
    findings += [Finding(ERROR, "config_category_missing", problem)
                 for problem in category_problems]
    findings += check_sections_present(sections, config)
    findings += check_links_grounded(sections, text, corpus)
    findings += check_section_categories(sections, corpus, config)
    findings += check_every_entry_cites_source(sections)
    findings += check_no_double_listing(sections)
    findings += check_slot_allocation(sections, config)
    findings += check_no_repeated_topics(sections)
    findings += check_exclusion_log(sections, corpus, config)
    findings += check_hn_discussion_links(sections, hacker_news_links(corpus))
    findings += check_claims_supported(
        sections, corpus_evidence(corpus), corpus_evidence_items(corpus))
    findings += check_corpus_health_reported(sections, corpus)
    return sorted(findings, key=lambda f: f.level != ERROR)


def evaluate(corpus: dict[str, Any], text: str,
             config: briefing_config.BriefingConfig | None = None) -> list[Finding]:
    """Parse a briefing, run every check, and return findings, ERRORs first."""
    config = config or briefing_config.load_config()
    return evaluate_parsed(corpus, text, parse_briefing(text, config), config)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True, help="corpus JSON the briefing came from")
    parser.add_argument("--briefing", required=True, help="generated briefing markdown")
    parser.add_argument("--config", default=briefing_config.DEFAULT_CONFIG_PATH,
                        help="trusted briefing structure JSON "
                             f"(default {briefing_config.DEFAULT_CONFIG_PATH})")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures too")
    args = parser.parse_args()

    # A bad path or an unreadable corpus is a user error, not a crash: the one
    # line worth reading is the message, not the stack that produced it.
    try:
        corpus = load_corpus(args.corpus)
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load corpus from {args.corpus}: {exc}")
    try:
        config = briefing_config.load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.error(f"cannot load briefing config from {args.config}: {exc}")
    # Briefings carry 🔗, em dashes and accented names, so the encoding is
    # pinned rather than inherited from the platform's locale.
    try:
        with open(args.briefing, encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as exc:
        parser.error(f"cannot read briefing from {args.briefing}: {exc}")

    findings = evaluate(corpus, text, config)
    for finding in findings:
        print(f"{finding.level:5} [{finding.check}] {finding.message}")

    errors = sum(1 for f in findings if f.level == ERROR)
    warnings = len(findings) - errors
    print(f"\n{errors} error(s), {warnings} warning(s)")

    if errors or (args.strict and warnings):
        return 1
    print("Briefing is consistent with its corpus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
