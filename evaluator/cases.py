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
    elif variant == "valid-bare-url-in-prose":
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

    heuristic_cases = [case for case in records if case.get("heuristic_claim_case")]
    heuristic_positive = {"unsupported_figure", "unsupported_quotation", "claim_exceeds_evidence"}
    false_positives = sum(
        bool(set(case["predicted_labels"]) & heuristic_positive)
        and not bool(set(case["human_labels"]) & heuristic_positive)
        for case in heuristic_cases
    )
    heuristic_negatives = sum(
        not bool(set(case["human_labels"]) & heuristic_positive)
        for case in heuristic_cases
    )
    return {
        "schema_version": 1,
        "suite": str(path),
        "label_provenance": suite["label_provenance"],
        "case_count": len(records),
        "components": components,
        "heuristic_claim_false_positive_rate": rate(false_positives, heuristic_negatives),
        "cases": records,
    }
