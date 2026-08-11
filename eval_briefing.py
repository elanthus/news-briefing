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
import json
import re
import sys
from collections import Counter
from collections.abc import Iterator
from typing import Any, NamedTuple

import briefing_config
import corpus_schema


class Finding(NamedTuple):
    level: str
    check: str
    message: str


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
    r"^\s*\*\*(?P<title>.+?)\*\*\s*(?:\*\([^)]*\)\*\s*)?[—-]\s*(?P<prose>\S.*)$")
# High-risk assertions, checkable without a second model: a figure or a
# quotation that does not appear in the evidence for the item being cited.
_FIGURE = re.compile(r"\d[\d,.]*\s*(?:%|percent)?")
_QUOTATION = re.compile(r"[\"\u201c\u201d]([^\"\u201c\u201d]{4,80})[\"\u201c\u201d]")
# A summary meaningfully longer than the text supporting it has added
# something. The corpus already holds a truncated blurb, not the article,
# so prose outgrowing it by this much is elaboration rather than compression.
CLAIM_EVIDENCE_RATIO = 2.0
# Links appear on their own line in the body but inline in the exclusion log
# ("- *Title* — reason. 🔗 url"), so scan anywhere in the line rather than
# anchoring to the start. Anchoring here silently left the exclusion log
# unvalidated, which is exactly where an invented link would hide.
_LINK = re.compile(r"🔗\s*(?:HN:\s*)?(?P<url>\S+)")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+\S")
_SLOT_SUFFIX = re.compile(r"\s*\(\d+\s+(?:slots?|stories)\)\s*$", re.IGNORECASE)


def _clean_link_url(value: str) -> str:
    """Remove sentence punctuation without damaging balanced URL parentheses."""
    url = value.strip().rstrip(".,;")
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(".,;")
    return url


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
    """Article URL -> discussion URL, for items that carry engagement signal."""
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
    if normalized == CORPUS_HEALTH.casefold():
        return CORPUS_HEALTH
    return None


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
                    sections.setdefault(current, {
                        "topics": [], "topic_texts": [], "topic_links": [],
                        "links": [], "spelled": {}, "excluded": {}, "lines": []})
                continue
            if in_excluded:
                # Inside the exclusion log, bold labels are per-section
                # sub-headers, not a return to the top-level section.
                excluded_current = matched or bold.strip()
                sections[EXCLUDED]["excluded"].setdefault(excluded_current, [])
                continue
            if matched:
                current = matched
                sections.setdefault(current, {
                    "topics": [], "topic_texts": [], "topic_links": [],
                    "links": [], "spelled": {}, "excluded": {}, "lines": []})
                continue

        if current is None:
            continue
        bucket = sections[current]
        bucket["lines"].append(line)

        if in_excluded and _LIST_ITEM.match(line) and excluded_current:
            bucket["excluded"][excluded_current].append(line.strip())
        elif not in_excluded:
            topic = _TOPIC_LINE.match(line)
            if topic:
                bucket["topics"].append(topic.group("title").strip())
                bucket["topic_texts"].append(topic.group("prose").strip())
                bucket["topic_links"].append([])

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


def check_links_grounded(sections: dict[str, Section],
                         corpus: dict[str, Any]) -> list[Finding]:
    """Every cited link must exist in the corpus. This is the core invariant.

    Both failures are ERRORs — the briefing must cite the corpus as written —
    but they are named apart because the reader's next action differs. An
    `altered_link` is a real article with a rewritten URL and the fix is to
    paste the corpus spelling back; an `ungrounded_link` has no corpus article
    behind it at all, which is the fabrication case and warrants re-reading
    the whole topic.
    """
    findings: list[Finding] = []
    allowed = corpus_links(corpus)
    routes = corpus_link_routes(corpus)
    for name, bucket in sections.items():
        for url in bucket["links"]:
            if url in allowed:
                continue
            spelled = bucket["spelled"].get(url, url)
            corpus_url = altered_from(url, routes)
            if corpus_url:
                findings.append(Finding(
                    ERROR, "altered_link",
                    f"{name}: cited link was altered from the corpus URL — cited "
                    f"{spelled}, corpus has {corpus_url}"))
            else:
                findings.append(Finding(
                    ERROR, "ungrounded_link",
                    f"{name}: cited link is not in the corpus — {spelled}"))
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
    """A story is reported in exactly one section.

    Only exact URL repeats are decidable here. The same event filed by two
    outlets under two URLs is the model's job, under the consolidation rule.
    """
    findings: list[Finding] = []
    included = [url for name, bucket in sections.items() if name != EXCLUDED
                for url in bucket["links"]]
    for url, count in Counter(included).items():
        if count > 1:
            findings.append(Finding(
                ERROR, "repeated_topic",
                f"topic reported {count} times across sections — {url}"))
    return findings


def check_exclusion_log(sections: dict[str, Section],
                        config: briefing_config.BriefingConfig) -> list[Finding]:
    findings: list[Finding] = []
    if EXCLUDED not in sections:
        return findings
    logged = sections[EXCLUDED]["excluded"]
    for section in config.sections:
        expected = section.excluded_stories
        if expected == 0:
            continue
        name = section.name
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
    """Engagement signal is the reason HN is in the corpus; don't drop it."""
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


def check_corpus_health_reported(sections: dict[str, Section], corpus: dict[str, Any],
                                 text: str) -> list[Finding]:
    """A degraded run must look degraded, or the briefing overstates coverage."""
    errors = corpus.get("errors", [])
    if not errors:
        return []
    if CORPUS_HEALTH not in sections:
        return [Finding(ERROR, "corpus_health_missing",
                        f"corpus recorded {len(errors)} fetch error(s) but the "
                        f"briefing has no {CORPUS_HEALTH!r} section")]
    findings: list[Finding] = []
    health_text = "\n".join(sections[CORPUS_HEALTH]["lines"])
    for error in errors:
        # Fetch errors are serialized as "<source>: <message>". Split on the
        # delimiter, not every colon: Hacker News source IDs are "HN:<query>".
        source = error.split(": ", 1)[0].strip()
        named = source and re.search(
            rf"(?<![\w/]){re.escape(source)}(?![\w/])", health_text)
        if source and not named:
            findings.append(Finding(
                ERROR, "failed_source_unnamed",
                f"failed source {source!r} is not named in the briefing"))
    return findings


def _normalize(text: str) -> str:
    """Collapse to comparable characters so punctuation can't hide a match."""
    return re.sub(r"[^a-z0-9%]+", "", text.lower())


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


def check_claims_supported(sections: dict[str, Section],
                           evidence: dict[str, str]) -> list[Finding]:
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
            normalized = _normalize(support)

            for figure in _FIGURE.findall(prose):
                token = _normalize(figure)
                if token and token not in normalized:
                    findings.append(Finding(
                        WARN, "unsupported_figure",
                        f"{name}: {title!r} states {figure.strip()!r}, which is not "
                        f"in the cited item(s)"))

            for quotation in _QUOTATION.findall(prose):
                if _normalize(quotation) not in normalized:
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


def evaluate(corpus: dict[str, Any], text: str,
             config: briefing_config.BriefingConfig | None = None) -> list[Finding]:
    """Run every check and return findings, ERRORs first."""
    config = config or briefing_config.load_config()
    sections = parse_briefing(text, config)
    findings: list[Finding] = []
    category_problems = briefing_config.validate_corpus_categories(
        config, set(corpus.get("categories", {})))
    findings += [Finding(ERROR, "config_category_missing", problem)
                 for problem in category_problems]
    findings += check_sections_present(sections, config)
    findings += check_links_grounded(sections, corpus)
    findings += check_section_categories(sections, corpus, config)
    findings += check_every_entry_cites_source(sections)
    findings += check_no_double_listing(sections)
    findings += check_slot_allocation(sections, config)
    findings += check_no_repeated_topics(sections)
    findings += check_exclusion_log(sections, config)
    findings += check_hn_discussion_links(sections, hacker_news_links(corpus))
    findings += check_claims_supported(sections, corpus_evidence(corpus))
    findings += check_corpus_health_reported(sections, corpus, text)
    return sorted(findings, key=lambda f: f.level != ERROR)


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
