"""Authored evidence and supporting fixture mutations for evaluator cases."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
from news_fetch import curation, feed_xml

# Authored valid-side candidates and minimally changed invalid-side neighbors.
# Each tuple is evidence title, evidence summary, output title, and output
# prose. The committed gold labels decide which candidates are actually clean;
# checker findings on those clean sides form the false-positive cohort.
CLAIM_PAIR_VARIANTS: dict[str, tuple[str, str, str, str]] = {
    "claim-fraction-valid": (
        "One in two users enabled the feature",
        "One in two users enabled the optional feature.",
        "Half of users enable feature",
        "50 percent of users enabled the optional feature.",
    ),
    "claim-fraction-invalid": (
        "One in two users enabled the feature",
        "One in two users enabled the optional feature.",
        "Most users enable feature",
        "60 percent of users enabled the optional feature.",
    ),
    "claim-rounded-valid": (
        "Feature adoption reaches 49.6 percent",
        "Adoption reached 49.6 percent in the measured cohort.",
        "Feature adoption reaches about half",
        "About 50 percent of the measured cohort adopted the feature.",
    ),
    "claim-rounded-invalid": (
        "Feature adoption reaches 49.6 percent",
        "Adoption reached 49.6 percent in the measured cohort.",
        "Feature adoption reaches a large majority",
        "About 70 percent of the measured cohort adopted the feature.",
    ),
    "claim-range-valid": (
        "Task completes between 10 and 12 minutes",
        "Measured runs completed between 10 and 12 minutes.",
        "Task completes in a narrow range",
        "Measured runs completed in 10–12 minutes.",
    ),
    "claim-range-invalid": (
        "Task completes between 10 and 12 minutes",
        "Measured runs completed between 10 and 12 minutes.",
        "Task completion range widens",
        "Measured runs completed in 10–15 minutes.",
    ),
    "claim-currency-valid": (
        "Project receives USD 1.2 million",
        "The project received USD 1.2 million in funding.",
        "Project receives new funding",
        "The project received $1.2m in funding.",
    ),
    "claim-currency-invalid": (
        "Project receives USD 1.2 million",
        "The project received USD 1.2 million in funding.",
        "Project receives larger funding round",
        "The project received $1.8m in funding.",
    ),
    "claim-date-valid": (
        "Release scheduled for August 14, 2026",
        "The release is scheduled for August 14, 2026.",
        "Release scheduled for mid-August",
        "The release is scheduled for 2026-08-14.",
    ),
    "claim-date-invalid": (
        "Release scheduled for August 14, 2026",
        "The release is scheduled for August 14, 2026.",
        "Release scheduled one day later",
        "The release is scheduled for 2026-08-15.",
    ),
    "claim-count-valid": (
        "Twelve teams join the pilot",
        "Twelve teams joined the pilot program.",
        "Pilot expands to twelve teams",
        "12 teams joined the pilot program.",
    ),
    "claim-count-invalid": (
        "Twelve teams join the pilot",
        "Twelve teams joined the pilot program.",
        "Pilot expands to thirteen teams",
        "13 teams joined the pilot program.",
    ),
    "claim-quote-punctuation-valid": (
        "Maintainer describes safe local execution",
        'The maintainer called it "safe, local execution" for developers.',
        "Maintainer emphasizes local execution",
        'The maintainer called it "safe local execution" for developers.',
    ),
    "claim-quote-punctuation-invalid": (
        "Maintainer describes safe local execution",
        'The maintainer called it "safe, local execution" for developers.',
        "Maintainer makes stronger safety claim",
        'The maintainer called it "perfectly safe execution" for developers.',
    ),
    "claim-uncertainty-valid": (
        "Change could reduce latency by 20 percent",
        "Early tests suggest the change could reduce latency by 20 percent.",
        "Change may reduce latency",
        "Early tests suggest the change may reduce latency by 20%.",
    ),
    "claim-uncertainty-invalid": (
        "Change could reduce latency by 20 percent",
        "Early tests suggest the change could reduce latency by 20 percent.",
        "Change claims a larger latency reduction",
        "The change reduces latency by 35%.",
    ),
    "claim-multiple-figures-valid": (
        "Three of five tests finish in twenty minutes",
        "3 of 5 tests completed in 20 minutes.",
        "Most tests finish within the run",
        "In 20 minutes, 3 of 5 tests completed.",
    ),
    "claim-multiple-figures-invalid": (
        "Three of five tests finish in twenty minutes",
        "3 of 5 tests completed in 20 minutes.",
        "Most tests take longer",
        "In 30 minutes, 3 of 5 tests completed.",
    ),
    "claim-unit-valid": (
        "Startup time falls to 1,000 milliseconds",
        "Measured startup time fell to 1,000 milliseconds.",
        "Startup time falls to one second",
        "Measured startup time fell to 1 second.",
    ),
    "claim-unit-invalid": (
        "Startup time falls to 1,000 milliseconds",
        "Measured startup time fell to 1,000 milliseconds.",
        "Startup time reported as two seconds",
        "Measured startup time fell to 2 seconds.",
    ),
    "claim-quote-whitespace-valid": (
        "Documentation names local mode",
        'The documentation calls the setting "local   mode".',
        "Documentation names local mode",
        'The documentation calls the setting "local mode".',
    ),
    "claim-quote-whitespace-invalid": (
        "Documentation names local mode",
        'The documentation calls the setting "local   mode".',
        "Documentation names a different mode",
        'The documentation calls the setting "remote mode".',
    ),
    "claim-paraphrase-length-valid": (
        "Approved",
        "",
        "Proposal approved",
        "The proposal received approval.",
    ),
    "claim-paraphrase-length-invalid": (
        "Approved",
        "",
        "Proposal receives unsupported guarantees",
        "The proposal received approval and passed every security test.",
    ),
}


def _config() -> briefing_config.BriefingConfig:
    return briefing_config.BriefingConfig(
        schema_version=1,
        sections=(briefing_config.BriefingSection(
            name="AI Dev Tools",
            group=None,
            target_stories=2,
            corpus_categories=("dev_community",),
            guidance="Agentic coding tools and integrations.",
            excluded_stories=1,
        ),),
    )


def item(title: str, url: str, summary: str, source: str = "Test Wire") -> dict[str, Any]:
    """Build an article fixture with the shared publication timestamp."""
    return {
        "title": title,
        "url": url,
        "published": "2026-08-11T10:00:00+00:00",
        "summary": summary,
        "source": source,
    }


def baseline() -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = json.loads(
        (Path(__file__).with_name("fixtures") / "checker-corpus.json").read_text(encoding="utf-8")
    )
    text = """# Daily Briefing — August 11, 2026

## AI Dev Tools

**Tool one reaches version 2** — Tool one released version 2 with safer local execution.
🔗 https://example.test/tool-one
🔗 HN: https://news.ycombinator.com/item?id=101
`↑ 45 pts · 12 comments`

**Tool two adds review mode** — Tool two added a review mode for proposed patches.
🔗 https://publisher.test/story?id=2&output=1

---

### Excluded Topics (accountability log)

**AI Dev Tools**
- *Tool three updates its extension* — lower immediate impact. 🔗 https://example.test/tool-three
"""
    return corpus, text, _config()


def replace(text: str, old: str, new: str) -> str:
    """Replace the first expected baseline fragment, failing if it is absent."""
    if old not in text:
        raise AssertionError(f"suite variant expected {old!r} in baseline")
    return text.replace(old, new, 1)


def replace_tool_three_exclusion(text: str) -> str:
    """Move the baseline exclusion to a different, internally consistent story."""
    return replace(
        text,
        "- *Tool three updates its extension* — lower immediate impact. "
        "🔗 https://example.test/tool-three",
        "- *Half of users enable feature* — lower immediate impact. "
        "🔗 https://example.test/half-users",
    )


def apply_claim_pair_variant(
    corpus: dict[str, Any], text: str, variant: str
) -> tuple[dict[str, Any], str]:
    """Apply an authored evidence/prose pair to the baseline's second topic."""
    evidence_title, evidence_summary, output_title, output_prose = CLAIM_PAIR_VARIANTS[variant]
    corpus["categories"]["dev_community"][1].update(
        title=evidence_title,
        summary=evidence_summary,
    )
    original = (
        "**Tool two adds review mode** — Tool two added a review mode for proposed patches.\n"
        "🔗 https://publisher.test/story?id=2&output=1"
    )
    replacement = (
        f"**{output_title}** — {output_prose}\n"
        "🔗 https://publisher.test/story?id=2&output=1"
    )
    return corpus, replace(text, original, replacement)


def fixture_usage(corpus: dict[str, Any]) -> None:
    """Account for authored fixture mutations using the current budget fields."""
    for category, items in corpus["categories"].items():
        stats = corpus["processing"][category]
        for field in corpus_schema.PROCESSING_FIELDS:
            stats.setdefault(field, 0)
        usage = [curation.item_context_usage(item) for item in items]
        stats["context_bytes"] = sum(size for size, _ in usage)
        stats["estimated_tokens"] = sum(tokens for _, tokens in usage)
    for output_field, processing_field in (("used_bytes", "context_bytes"),
                                           ("estimated_tokens", "estimated_tokens")):
        corpus["context_budget"][output_field] = sum(
            stats[processing_field] for stats in corpus["processing"].values()
        )


def degraded_corpus(corpus: dict[str, Any], two_failures: bool) -> dict[str, Any]:
    """Populate fixture source-health records for one or two failed feeds."""
    corpus["schema_version"] = corpus_schema.SCHEMA_VERSION
    errors = [{
        "source_type": "rss",
        "source_id": "Feed A",
        "status": "error",
        "error_type": "HTTPError",
        "message": "HTTP 503",
        "duration_ms": 10,
    }]
    if two_failures:
        errors.append({
            "source_type": "rss",
            "source_id": "Feed B",
            "status": "empty",
            "error_type": "EmptyFeed",
            "message": "no dated entries",
            "duration_ms": 8,
        })
    corpus["errors"] = errors
    corpus["fetch_duration_ms"] = 18
    corpus["sources"] = [{
        "source_type": error["source_type"],
        "source_id": error["source_id"],
        "category": "dev_community",
        "status": error["status"],
        "requested": True,
        "http_success": error["status"] == "empty",
        "parsed_entries": 0,
        "dated_entries": 0,
        "retained_entries": 0,
        "retained_bytes": 0,
        "estimated_tokens": 0,
        "duration_ms": error["duration_ms"],
        "error_type": error["error_type"],
        "message": error["message"],
    } for error in errors]
    return corpus


def health_block(records: list[tuple[str, str, str]]) -> str:
    """Render fixture source-health records as a fenced JSON block."""
    payload = {
        "failed_sources": [
            {"source_type": source_type, "source_id": source_id, "status": status}
            for source_type, source_id, status in records
        ]
    }
    return "\n---\n\n### Corpus health\nCoverage was degraded.\n```json\n" + json.dumps(payload) + "\n```\n"


def _xml_case(variant: str) -> bytes:
    ordinary = '<?xml version="1.0" encoding="{decl}"?><rss><channel><item><title>ok</title></item></channel></rss>'
    if variant == "utf8-rss":
        return ordinary.format(decl="UTF-8").encode("utf-8")
    if variant == "utf16-rss":
        return ordinary.format(decl="UTF-16").encode("utf-16")
    if variant == "utf16le-rss":
        return ordinary.format(decl="UTF-16").encode("utf-16-le")
    if variant == "utf16be-rss":
        return ordinary.format(decl="UTF-16").encode("utf-16-be")
    if variant == "utf32-rss":
        return ordinary.format(decl="UTF-32").encode("utf-32")
    if variant == "atom-valid":
        return b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>ok</title></entry></feed>'
    if variant == "feed-empty":
        return b'<rss><channel /></rss>'
    if variant == "feed-wrong-shape":
        return b'<html><body>not a feed</body></html>'
    if variant == "malformed-unclosed":
        return b'<rss><channel><item></rss>'
    if variant == "malformed-entity":
        return b'<rss><item><title>&unknown;</title></item></rss>'
    if variant == "doctype-utf8":
        return b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY x "boom">]><rss><title>&x;</title></rss>'
    if variant == "doctype-utf16":
        return ('<?xml version="1.0" encoding="UTF-16"?>'
                '<!DOCTYPE rss [<!ENTITY x "boom">]><rss><title>&x;</title></rss>').encode("utf-16")
    if variant == "doctype-utf32":
        return ('<?xml version="1.0" encoding="UTF-32"?>'
                '<!DOCTYPE rss [<!ENTITY x "boom">]><rss><title>&x;</title></rss>').encode("utf-32")
    if variant == "malformed-utf32":
        return b"\xff\xfe\x00\x00\x3c\x00\x00"
    raise ValueError(f"unknown XML variant {variant!r}")


def feed_prediction(variant: str) -> set[str]:
    """Classify an authored XML fixture with the production feed parser."""
    try:
        root = feed_xml.parse_feed_xml(_xml_case(variant))
    except (ET.ParseError, ValueError, UnicodeError):
        return {"feed_rejected"}
    local_name = re.sub(r"^\{[^}]+\}", "", root.tag)
    if local_name not in {"rss", "feed"}:
        return {"feed_shape_unrecognized"}
    entries = list(root.iter("item")) + root.findall("{http://www.w3.org/2005/Atom}entry")
    return {"feed_empty"} if not entries else set()
