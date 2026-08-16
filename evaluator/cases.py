"""Builders and runners for the committed human-labeled deterministic suite."""

from __future__ import annotations

import copy
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
import eval_briefing
import fetch_news

from evaluator.metrics import classification_metrics, rate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = Path(__file__).with_name("fixtures") / "checker-cases.json"


# Deliberately valid heuristic boundaries and minimally changed invalid
# neighbors. Each tuple is evidence title, evidence summary, output title,
# and output prose. The fixed checker is expected to miss some semantic
# equivalences; those misses are the false positives this cohort measures.
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


def _item(title: str, url: str, summary: str, source: str = "Test Wire") -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "published": "2026-08-11T10:00:00+00:00",
        "summary": summary,
        "source": source,
    }


def baseline() -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    first = _item(
        "Tool one reaches version 2",
        "https://example.test/tool-one",
        "Tool one released version 2 with safer local execution.",
        "Hacker News",
    )
    first.update(
        discussion="https://news.ycombinator.com/item?id=101",
        points=45,
        comments=12,
    )
    corpus = {
        "schema_version": 3,
        "generated_at": "2026-08-11T12:00:00+00:00",
        "cutoff": "2026-08-10T12:00:00+00:00",
        "window_hours": 24,
        "limits": {"source_cap": 25, "category_cap": 60},
        "categories": {
            "dev_community": [
                first,
                _item(
                    "Tool two adds review mode",
                    "https://publisher.test/story?id=2&output=1",
                    "Tool two added a review mode for proposed patches.",
                ),
                _item(
                    "Tool three updates its extension",
                    "https://example.test/tool-three",
                    "Tool three updated its editor extension.",
                ),
                _item(
                    "One in two users enabled the feature",
                    "https://example.test/half-users",
                    "One in two users enabled the optional feature.",
                ),
            ],
            "other_news": [
                _item(
                    "Unrelated market story",
                    "https://example.test/market",
                    "A market story unrelated to developer tools.",
                )
            ],
        },
        "processing": {
            "dev_community": {
                "fetched": 4,
                "undated_dropped": 0,
                "relevance_dropped": 0,
                "duplicates_dropped": 0,
                "source_cap_dropped": 0,
                "category_cap_dropped": 0,
                "kept": 4,
            },
            "other_news": {
                "fetched": 1,
                "undated_dropped": 0,
                "relevance_dropped": 0,
                "duplicates_dropped": 0,
                "source_cap_dropped": 0,
                "category_cap_dropped": 0,
                "kept": 1,
            },
        },
        "errors": [],
    }
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


def _replace(text: str, old: str, new: str) -> str:
    if old not in text:
        raise AssertionError(f"suite variant expected {old!r} in baseline")
    return text.replace(old, new, 1)


def _apply_claim_pair_variant(
    corpus: dict[str, Any], text: str, variant: str
) -> tuple[dict[str, Any], str]:
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
    return corpus, _replace(text, original, replacement)


def apply_variant(variant: str) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus, text, config = baseline()

    if variant == "valid-baseline":
        pass
    elif variant == "valid-trailing-slash":
        text = _replace(text, "https://example.test/tool-one", "https://EXAMPLE.test/tool-one/")
    elif variant == "valid-tracking-parameter":
        text = _replace(text, "https://example.test/tool-three", "https://example.test/tool-three?utm_source=rss")
    elif variant == "valid-query-order":
        text = _replace(text, "id=2&output=1", "output=1&id=2")
    elif variant == "ungrounded-bare-url-in-prose":
        text += "\nReader note: https://not-a-citation.example.test/context\n"
    elif variant == "valid-doctype-as-text":
        text = _replace(text, "Tool two added", "The <!DOCTYPE html> text is discussed; tool two added")
        corpus["categories"]["dev_community"][1]["summary"] = (
            "The <!DOCTYPE html> text is discussed; tool two added a review mode for proposed patches."
        )
    elif variant == "valid-category-overlap":
        corpus["categories"]["other_news"].append(copy.deepcopy(corpus["categories"]["dev_community"][1]))
        corpus["processing"]["other_news"]["fetched"] = 2
        corpus["processing"]["other_news"]["kept"] = 2
    elif variant == "valid-hn-without-discussion":
        corpus["categories"]["dev_community"][0].pop("discussion")
        text = _replace(text, "🔗 HN: https://news.ycombinator.com/item?id=101\n", "")
    elif variant == "valid-exclusions-exhausted":
        corpus["categories"]["dev_community"] = corpus["categories"]["dev_community"][:3]
        corpus["processing"]["dev_community"]["fetched"] = 3
        corpus["processing"]["dev_community"]["kept"] = 3
        config = briefing_config.BriefingConfig(
            schema_version=1,
            sections=(config.sections[0]._replace(excluded_stories=5),),
        )
    elif variant == "valid-grouped-multisection":
        practice = _item(
            "Teams adopt staged patch review",
            "https://example.test/staged-review",
            "Teams adopted a staged workflow for reviewing proposed patches.",
        )
        corpus["categories"]["dev_practices"] = [practice]
        corpus["processing"]["dev_practices"] = {
            "fetched": 1,
            "undated_dropped": 0,
            "relevance_dropped": 0,
            "duplicates_dropped": 0,
            "source_cap_dropped": 0,
            "category_cap_dropped": 0,
            "kept": 1,
        }
        config = briefing_config.BriefingConfig(
            schema_version=1,
            sections=(
                config.sections[0]._replace(group="AI/Tech"),
                briefing_config.BriefingSection(
                    name="AI Dev Practices",
                    group="AI/Tech",
                    target_stories=1,
                    corpus_categories=("dev_practices",),
                    guidance="Workflow patterns rather than product updates.",
                    excluded_stories=0,
                ),
            ),
        )
        text = """# Daily Briefing — August 11, 2026

## AI/Tech

**AI Dev Tools (2 slots)**

**Tool one reaches version 2** — Tool one released version 2 with safer local execution.
🔗 https://example.test/tool-one
🔗 HN: https://news.ycombinator.com/item?id=101
`↑ 45 pts · 12 comments`

**Tool two adds review mode** — Tool two added a review mode for proposed patches.
🔗 https://publisher.test/story?id=2&output=1

**AI Dev Practices (1 slot)**

**Teams adopt staged patch review** — Teams adopted a staged workflow for reviewing proposed patches.
🔗 https://example.test/staged-review

---

### Excluded Topics (accountability log)

**AI Dev Tools**
- *Tool three updates its extension* — lower immediate impact. 🔗 https://example.test/tool-three
"""
    elif variant == "category-ambiguity-clean":
        corpus["categories"]["dev_community"][1].update(
            title="Tool two review mode reshapes patch workflows",
            summary="Tool two's new review mode changes how developers inspect proposed patches.",
        )
        practice = _item(
            "Teams adopt staged patch review",
            "https://example.test/staged-review",
            "Teams adopted a staged workflow for reviewing proposed patches.",
        )
        corpus["categories"]["dev_community"].append(practice)
        corpus["processing"]["dev_community"]["fetched"] = 5
        corpus["processing"]["dev_community"]["kept"] = 5
        config = briefing_config.BriefingConfig(
            schema_version=1,
            sections=(
                config.sections[0]._replace(target_stories=1),
                briefing_config.BriefingSection(
                    name="AI Dev Practices",
                    group=None,
                    target_stories=1,
                    corpus_categories=("dev_community",),
                    guidance="Workflow patterns rather than product updates.",
                    excluded_stories=0,
                ),
            ),
        )
        text = """# Daily Briefing — August 11, 2026

## AI Dev Tools

**Tool two review mode reshapes patch workflows** — Tool two's review mode changes patch inspection workflows.
🔗 https://publisher.test/story?id=2&output=1

## AI Dev Practices

**Teams adopt staged patch review** — Teams adopted a staged workflow for reviewing proposed patches.
🔗 https://example.test/staged-review

---

### Excluded Topics (accountability log)

**AI Dev Tools**
- *Tool three updates its extension* — lower immediate impact. 🔗 https://example.test/tool-three
"""
    elif variant == "fabricated-included":
        text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://publisher.test/invented")
    elif variant == "fabricated-excluded":
        text = _replace(text, "https://example.test/tool-three", "https://example.test/invented")
    elif variant == "altered-publisher-url":
        text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://publisher.test/story?id=2")
    elif variant == "bare-included-url":
        text = _replace(text, "🔗 https://publisher.test/story?id=2&output=1", "https://publisher.test/story?id=2&output=1")
    elif variant == "markdown-included-url":
        text = _replace(
            text,
            "🔗 https://publisher.test/story?id=2&output=1",
            "[Source](https://publisher.test/story?id=2&output=1)",
        )
    elif variant == "markdown-excluded-url":
        text = _replace(
            text,
            "🔗 https://example.test/tool-three",
            "[Source](https://example.test/tool-three)",
        )
    elif variant == "duplicate-url-same-topic":
        text = _replace(
            text,
            "🔗 https://publisher.test/story?id=2&output=1",
            "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://publisher.test/story?id=2&output=1",
        )
    elif variant == "duplicate-url-canonical":
        text = _replace(
            text,
            "🔗 https://publisher.test/story?id=2&output=1",
            "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://PUBLISHER.test/story/?output=1&id=2&utm_source=x",
        )
    elif variant == "included-and-excluded":
        text = _replace(text, "https://example.test/tool-three", "https://publisher.test/story?id=2&output=1")
    elif variant == "topic-without-link":
        text = _replace(text, "🔗 https://publisher.test/story?id=2&output=1", "")
    elif variant == "excluded-without-link":
        text = _replace(text, "🔗 https://example.test/tool-three", "")
    elif variant == "missing-section":
        text = _replace(text, "## AI Dev Tools", "## Developer News")
    elif variant == "missing-exclusion-section":
        text = text.split("### Excluded Topics", 1)[0]
    elif variant == "overfilled":
        text = _replace(
            text,
            "---\n\n### Excluded Topics",
            "**Tool three** — Tool three updated its editor extension.\n"
            "🔗 https://example.test/tool-three\n\n---\n\n### Excluded Topics",
        )
        text = _replace(text, "🔗 https://example.test/tool-three", "🔗 https://example.test/half-users")
    elif variant == "underfilled":
        start = text.index("**Tool two adds review mode**")
        end = text.index("---", start)
        text = text[:start] + text[end:]
    elif variant == "wrong-category":
        text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://example.test/market")
    elif variant == "hn-discussion-missing":
        text = _replace(text, "🔗 HN: https://news.ycombinator.com/item?id=101\n", "")
    elif variant == "unsupported-figure":
        text = _replace(text, "version 2 with", "version 87 with")
    elif variant == "unsupported-quotation":
        text = _replace(text, "Tool two added a review mode", 'Tool two called review mode "perfectly safe"')
    elif variant == "claim-too-long":
        text = _replace(
            text,
            "Tool two added a review mode for proposed patches.",
            "Tool two added a review mode for proposed patches and transformed every stage of engineering "
            "with universal safety guarantees, automatic deployment, perfect rollback, and complete compliance.",
        )
    elif variant == "valid-semantic-figure":
        text = _replace(
            text,
            "**Tool two adds review mode** — Tool two added a review mode for proposed patches.\n"
            "🔗 https://publisher.test/story?id=2&output=1",
            "**Half of users enable feature** — 50 percent of users enabled the optional feature.\n"
            "🔗 https://example.test/half-users",
        )
    elif variant == "valid-thin-evidence-paraphrase":
        corpus["categories"]["dev_community"][1]["summary"] = "Review mode arrived."
        text = _replace(
            text,
            "Tool two added a review mode for proposed patches.",
            "The release introduced a mode that lets developers review proposed patches before accepting them.",
        )
    elif variant in CLAIM_PAIR_VARIANTS:
        corpus, text = _apply_claim_pair_variant(corpus, text, variant)
    elif variant == "thin-evidence-unsupported":
        corpus["categories"]["dev_community"][1]["summary"] = "Review mode arrived."
        text = _replace(
            text,
            "Tool two added a review mode for proposed patches.",
            "Review mode guarantees zero defects.",
        )
    elif variant == "conflicting-evidence":
        corpus["categories"]["dev_community"].append(_item(
            "Tool two review mode delayed",
            "https://example.test/tool-two-delay",
            "The maintainer says review mode has not shipped.",
        ))
        corpus["processing"]["dev_community"]["fetched"] = 5
        corpus["processing"]["dev_community"]["kept"] = 5
        text = _replace(
            text,
            "🔗 https://publisher.test/story?id=2&output=1",
            "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://example.test/tool-two-delay",
        )
    elif variant == "over-consolidated":
        text = _replace(
            text,
            "🔗 https://publisher.test/story?id=2&output=1",
            "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://example.test/tool-three",
        )
        text = _replace(text, "https://example.test/tool-three", "https://example.test/half-users")
    elif variant.startswith("health-"):
        corpus = _degraded_corpus(corpus, two_failures=variant in {"health-partial", "health-duplicate"})
        if variant == "health-valid":
            text += _health_block([
                ("rss", "Feed A", "error"),
            ])
        elif variant == "health-missing":
            pass
        elif variant == "health-prose-only":
            text += "\n---\n\n### Corpus health\nFeed A failed.\n"
        elif variant == "health-malformed-json":
            text += "\n---\n\n### Corpus health\n```json\n{bad json}\n```\n"
        elif variant == "health-wrong-schema":
            text += "\n---\n\n### Corpus health\n```json\n{\"failures\": []}\n```\n"
        elif variant == "health-partial":
            text += _health_block([("rss", "Feed A", "error")])
        elif variant == "health-unexpected":
            text += _health_block([
                ("rss", "Feed A", "error"),
                ("rss", "Imaginary", "error"),
            ])
        elif variant == "health-wrong-status":
            text += _health_block([("rss", "Feed A", "empty")])
        elif variant == "health-duplicate":
            text += _health_block([
                ("rss", "Feed A", "error"),
                ("rss", "Feed B", "empty"),
                ("rss", "Feed B", "empty"),
            ])
        else:
            raise ValueError(f"unknown variant {variant!r}")
    elif variant == "selection-ambiguity":
        config = briefing_config.BriefingConfig(
            schema_version=1,
            sections=(
                config.sections[0],
                briefing_config.BriefingSection(
                    name="AI Dev Practices",
                    group=None,
                    target_stories=1,
                    corpus_categories=("dev_community",),
                    guidance="Workflow patterns rather than product updates.",
                    excluded_stories=0,
                ),
            ),
        )
        text = _replace(
            text,
            "---\n\n### Excluded Topics",
            "## AI Dev Practices\n\n"
            "**Tool three updates its extension** — Tool three updated its editor extension.\n"
            "🔗 https://example.test/tool-three\n\n"
            "---\n\n### Excluded Topics",
        )
        text = _replace(text, "🔗 https://example.test/tool-three", "🔗 https://example.test/half-users")
    elif variant == "formatting-ambiguity":
        text = _replace(text, "## AI Dev Tools", "**AI Dev Tools (2 slots)**")
    else:
        raise ValueError(f"unknown variant {variant!r}")
    return corpus, text, config


def _degraded_corpus(corpus: dict[str, Any], two_failures: bool) -> dict[str, Any]:
    corpus["schema_version"] = 4
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
        "duration_ms": error["duration_ms"],
        "error_type": error["error_type"],
        "message": error["message"],
    } for error in errors]
    return corpus


def _health_block(records: list[tuple[str, str, str]]) -> str:
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


def _feed_prediction(variant: str) -> set[str]:
    try:
        root = fetch_news.parse_feed_xml(_xml_case(variant))
    except (ET.ParseError, ValueError, UnicodeError):
        return {"feed_rejected"}
    local_name = re.sub(r"^\{[^}]+\}", "", root.tag)
    if local_name not in {"rss", "feed"}:
        return {"feed_shape_unrecognized"}
    entries = list(root.iter("item")) + root.findall("{http://www.w3.org/2005/Atom}entry")
    return {"feed_empty"} if not entries else set()


def run_deterministic_suite(path: Path = DEFAULT_SUITE) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for case in suite["cases"]:
        expected = set(case["human_labels"])
        if case["component"] == "checker":
            corpus, text, config = apply_variant(case["variant"])
            problems = corpus_schema.validate_corpus(corpus)
            if problems:
                raise ValueError(f"case {case['id']} built an invalid corpus: {'; '.join(problems)}")
            findings = eval_briefing.evaluate(corpus, text, config)
            predicted = {finding.check for finding in findings}
            detail = [finding._asdict() for finding in findings]
        elif case["component"] == "feed_parser":
            predicted = _feed_prediction(case["variant"])
            detail = []
        else:
            raise ValueError(f"case {case['id']} has unknown component")
        records.append({
            **case,
            "predicted_labels": sorted(predicted),
            "matched": sorted(expected & predicted),
            "missed": sorted(expected - predicted),
            "unexpected": sorted(predicted - expected),
            "findings": detail,
        })

    components: dict[str, Any] = {}
    for component in sorted({case["component"] for case in records}):
        rows = [case for case in records if case["component"] == component]
        metrics = classification_metrics(
            [set(case["human_labels"]) for case in rows],
            [set(case["predicted_labels"]) for case in rows],
        )
        metrics["cases"] = len(rows)
        metrics["exact_case_match"] = rate(
            sum(not case["missed"] and not case["unexpected"] for case in rows), len(rows)
        )
        components[component] = metrics

    heuristic_cases = [
        case
        for case in records
        if case.get("heuristic_claim_case") and not case["human_labels"]
    ]
    heuristic_positive = {"unsupported_figure", "unsupported_quotation", "claim_exceeds_evidence"}
    false_positives = sum(
        bool(set(case["predicted_labels"]) & heuristic_positive)
        and not bool(set(case["human_labels"]) & heuristic_positive)
        for case in heuristic_cases
    )
    heuristic_negatives = len(heuristic_cases)
    per_check_false_positive_rates: dict[str, Any] = {}
    all_heuristic_cases = [case for case in records if case.get("heuristic_claim_case")]
    for check in sorted(heuristic_positive):
        negatives = [case for case in all_heuristic_cases if check not in case["human_labels"]]
        per_check_false_positive_rates[check] = rate(
            sum(check in case["predicted_labels"] for case in negatives),
            len(negatives),
        )
    return {
        "schema_version": 1,
        "suite": str(path),
        "label_provenance": suite["label_provenance"],
        "case_count": len(records),
        "components": components,
        "heuristic_claim_false_positive_rate": rate(false_positives, heuristic_negatives),
        "heuristic_claim_false_positive_rates": per_check_false_positive_rates,
        "cases": records,
    }
