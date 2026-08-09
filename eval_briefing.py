#!/usr/bin/env python3
"""Contract checker for a generated briefing.

The fetch step is deterministic, so it can be unit tested. The ranking and
summarizing step is not — but most of the ways it goes wrong are structural,
not editorial, and structural failures can be checked exactly:

    * a link that isn't in the corpus (the model invented or recalled it)
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
    python3 eval_briefing.py --corpus c.json --briefing b.md --strict
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from typing import Any, NamedTuple

import corpus_schema


class Finding(NamedTuple):
    level: str
    check: str
    message: str


# One parsed briefing section: its topic headlines and the links they cite.
Section = dict[str, Any]

ERROR = "ERROR"
WARN = "WARN"

# section label -> reserved topic slots (None = not slot-constrained)
SECTIONS = {
    "US Politics": 5,
    "World Events": 5,
    "AI News": 4,
    "AI Dev Tools": 3,
    "AI Dev Practices": 3,
}
EXCLUDED = "Excluded Topics"
CORPUS_HEALTH = "Corpus health"

# Sections the prompt requires an exclusion log for (AI News is exempt).
EXCLUSION_SECTIONS = ("US Politics", "World Events", "AI Dev Tools", "AI Dev Practices")
EXCLUSIONS_PER_SECTION = 5

# A heading (## / ###) or a bold sub-header, either of which starts a section.
_SECTION_LINE = re.compile(
    r"^\s*(?:#{2,4}\s*(?P<heading>.+?)\s*$|\*\*(?P<bold>[^*]+?)\*\*\s*$)")
# A topic entry: **Headline** — summary. The em dash is what separates a topic
# from a bold sub-header like **AI News (4 slots)**.
_TOPIC_LINE = re.compile(r"^\s*\*\*(?P<title>.+?)\*\*\s*(?:\*\([^)]*\)\*\s*)?[—-]\s*\S")
# Links appear on their own line in the body but inline in the exclusion log
# ("- *Title* — reason. 🔗 url"), so scan anywhere in the line rather than
# anchoring to the start. Anchoring here silently left the exclusion log
# unvalidated, which is exactly where an invented link would hide.
_LINK = re.compile(r"🔗\s*(?:HN:\s*)?(?P<url>\S+)")
_LIST_ITEM = re.compile(r"^\s*[-*]\s+\S")


def load_corpus(path: str) -> dict[str, Any]:
    """Load a corpus, refusing one this checker cannot read correctly.

    An older corpus is fine — the fields read here have only been added to.
    A newer one may have moved something, and misreading it would produce
    confident findings about the wrong fields, which is worse than stopping.
    """
    with open(path, encoding="utf-8") as f:
        corpus = json.load(f)
    if not corpus_schema.is_readable(corpus):
        raise ValueError(
            f"corpus schema v{corpus_schema.corpus_version(corpus)} is newer than "
            f"v{corpus_schema.SCHEMA_VERSION}, which is the newest this checker "
            f"understands — upgrade eval_briefing.py")
    return corpus


def corpus_links(corpus: dict[str, Any]) -> set[str]:
    """Every URL the briefing is allowed to cite, article and discussion alike."""
    links = set()
    for items in corpus.get("categories", {}).values():
        for item in items:
            for key in ("url", "discussion"):
                value = (item.get(key) or "").strip()
                if value:
                    links.add(value)
    return links


def hacker_news_links(corpus: dict[str, Any]) -> dict[str, str]:
    """Article URL -> discussion URL, for items that carry engagement signal."""
    pairs = {}
    for items in corpus.get("categories", {}).values():
        for item in items:
            discussion = (item.get("discussion") or "").strip()
            url = (item.get("url") or "").strip()
            if discussion and url:
                pairs[url] = discussion
    return pairs


def _match_section(label: str) -> str | None:
    """Map a heading or sub-header to a known section name, or None."""
    for name in list(SECTIONS) + [EXCLUDED, CORPUS_HEALTH]:
        if name.lower() in label.lower():
            return name
    return None


def parse_briefing(text: str) -> dict[str, Section]:
    """Split a briefing into sections.

    Deliberately tolerant: it keys off section labels and 🔗 lines rather than
    an exact template, so cosmetic prompt edits don't break the checker. It
    reports what it found; the checks decide whether that's acceptable.
    """
    sections: dict[str, Section] = {}
    current = None
    in_excluded = False
    excluded_current = None

    for line in text.splitlines():
        marker = _SECTION_LINE.match(line)
        if marker:
            heading, bold = marker.group("heading"), marker.group("bold")
            matched = _match_section(heading or bold)
            if heading is not None:
                # A real heading always ends the previous section. An
                # unrecognized one (e.g. "AI/Tech", a container) parks the
                # parser until a known sub-header appears.
                current = matched
                in_excluded = matched == EXCLUDED
                excluded_current = None
                if current:
                    sections.setdefault(current, {
                        "topics": [], "topic_links": [], "links": [], "excluded": {}})
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
                    "topics": [], "topic_links": [], "links": [], "excluded": {}})
                continue

        if current is None:
            continue
        bucket = sections[current]

        if in_excluded and _LIST_ITEM.match(line) and excluded_current:
            bucket["excluded"][excluded_current].append(line.strip())
        elif not in_excluded:
            topic = _TOPIC_LINE.match(line)
            if topic:
                bucket["topics"].append(topic.group("title").strip())
                bucket["topic_links"].append([])

        for link in _LINK.finditer(line):
            url = link.group("url").strip()
            bucket["links"].append(url)
            if not in_excluded and bucket["topic_links"]:
                bucket["topic_links"][-1].append(url)

    return sections


def check_sections_present(sections: dict[str, Section]) -> list[Finding]:
    findings: list[Finding] = []
    for name in list(SECTIONS) + [EXCLUDED]:
        if name not in sections:
            findings.append(Finding(ERROR, "missing_section",
                                    f"required section {name!r} is absent"))
    return findings


def check_links_grounded(sections: dict[str, Section], allowed: set[str]) -> list[Finding]:
    """Every cited link must exist in the corpus. This is the core invariant."""
    findings: list[Finding] = []
    for name, bucket in sections.items():
        for url in bucket["links"]:
            if url not in allowed:
                findings.append(Finding(
                    ERROR, "ungrounded_link",
                    f"{name}: cited link is not in the corpus — {url}"))
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


def check_slot_allocation(sections: dict[str, Section]) -> list[Finding]:
    findings: list[Finding] = []
    for name, expected in SECTIONS.items():
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
    findings: list[Finding] = []
    included = [url for name, bucket in sections.items() if name != EXCLUDED
                for url in bucket["links"]]
    for url, count in Counter(included).items():
        if count > 1:
            findings.append(Finding(
                WARN, "repeated_link",
                f"link cited {count} times across sections — {url}"))
    return findings


def check_exclusion_log(sections: dict[str, Section]) -> list[Finding]:
    findings: list[Finding] = []
    if EXCLUDED not in sections:
        return findings
    logged = sections[EXCLUDED]["excluded"]
    for name in EXCLUSION_SECTIONS:
        entries = logged.get(name, [])
        if not entries:
            findings.append(Finding(
                WARN, "exclusion_log_missing",
                f"exclusion log has no entries for {name!r}"))
        elif len(entries) < EXCLUSIONS_PER_SECTION:
            findings.append(Finding(
                WARN, "exclusion_log_short",
                f"exclusion log for {name!r}: {len(entries)} entries, "
                f"expected {EXCLUSIONS_PER_SECTION}"))
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
    for error in errors:
        source = error.split(":")[0].strip()
        if source and source not in text:
            findings.append(Finding(
                WARN, "failed_source_unnamed",
                f"failed source {source!r} is not named in the briefing"))
    return findings


def evaluate(corpus: dict[str, Any], text: str) -> list[Finding]:
    """Run every check and return findings, ERRORs first."""
    sections = parse_briefing(text)
    findings: list[Finding] = []
    findings += check_sections_present(sections)
    findings += check_links_grounded(sections, corpus_links(corpus))
    findings += check_every_entry_cites_source(sections)
    findings += check_no_double_listing(sections)
    findings += check_slot_allocation(sections)
    findings += check_no_repeated_topics(sections)
    findings += check_exclusion_log(sections)
    findings += check_hn_discussion_links(sections, hacker_news_links(corpus))
    findings += check_corpus_health_reported(sections, corpus, text)
    return sorted(findings, key=lambda f: f.level != ERROR)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corpus", required=True, help="corpus JSON the briefing came from")
    parser.add_argument("--briefing", required=True, help="generated briefing markdown")
    parser.add_argument("--strict", action="store_true",
                        help="treat warnings as failures too")
    args = parser.parse_args()

    corpus = load_corpus(args.corpus)
    with open(args.briefing) as f:
        text = f.read()

    findings = evaluate(corpus, text)
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
