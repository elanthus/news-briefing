"""Builders and runners for the committed gold-label deterministic suite."""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import briefing_config

from evaluator.case_fixtures import (
    CLAIM_PAIR_VARIANTS,
    _apply_claim_pair_variant,
    _degraded_corpus,
    _fixture_usage,
    _health_block,
    _item,
    _replace,
    _replace_tool_three_exclusion,
)
from evaluator.case_fixtures import (
    baseline as baseline,
)
from evaluator.case_runner import (
    DEFAULT_SUITE as DEFAULT_SUITE,
)
from evaluator.case_runner import (
    HEURISTIC_CLAIM_CHECKS as HEURISTIC_CLAIM_CHECKS,
)
from evaluator.case_runner import (
    run_deterministic_suite as run_deterministic_suite,
)

Variant = Callable[
    [dict[str, Any], str, briefing_config.BriefingConfig], tuple[dict[str, Any], str, briefing_config.BriefingConfig]
]
VARIANTS: dict[str, Variant] = {}


def variant(name: str) -> Callable[[Variant], Variant]:
    """Register an authored corpus, prose, and configuration transform."""

    def register(transform: Variant) -> Variant:
        VARIANTS[name] = transform
        return transform

    return register


def text_only(transform: Callable[[str], str]) -> Variant:
    """Adapt a prose-only mutation to the shared variant signature."""

    def apply(
        corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
    ) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
        return corpus, transform(text), config

    return apply


@variant("valid-baseline")
def _valid_baseline(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    return corpus, text, config


@variant("valid-trailing-slash")
@text_only
def _valid_trailing_slash(text: str) -> str:
    text = _replace(text, "https://example.test/tool-one", "https://EXAMPLE.test/tool-one/")
    return text


@variant("valid-tracking-parameter")
@text_only
def _valid_tracking_parameter(text: str) -> str:
    text = _replace(text, "https://example.test/tool-three", "https://example.test/tool-three?utm_source=rss")
    return text


@variant("valid-query-order")
@text_only
def _valid_query_order(text: str) -> str:
    text = _replace(text, "id=2&output=1", "output=1&id=2")
    return text


@variant("ungrounded-bare-url-in-prose")
@text_only
def _ungrounded_bare_url_in_prose(text: str) -> str:
    text += "\nReader note: https://not-a-citation.example.test/context\n"
    return text


@variant("valid-doctype-as-text")
def _valid_doctype_as_text(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    text = _replace(text, "Tool two added", "The <!DOCTYPE html> text is discussed; tool two added")
    corpus["categories"]["dev_community"][1]["summary"] = (
        "The <!DOCTYPE html> text is discussed; tool two added a review mode for proposed patches."
    )
    return corpus, text, config


@variant("valid-category-overlap")
def _valid_category_overlap(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["other_news"].append(copy.deepcopy(corpus["categories"]["dev_community"][1]))
    corpus["processing"]["other_news"]["fetched"] = 2
    corpus["processing"]["other_news"]["kept"] = 2
    return corpus, text, config


@variant("valid-hn-without-discussion")
def _valid_hn_without_discussion(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"][0].pop("discussion")
    text = _replace(text, "🔗 HN: https://news.ycombinator.com/item?id=101\n", "")
    return corpus, text, config


@variant("valid-exclusions-exhausted")
def _valid_exclusions_exhausted(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"] = corpus["categories"]["dev_community"][:3]
    corpus["processing"]["dev_community"]["fetched"] = 3
    corpus["processing"]["dev_community"]["kept"] = 3
    config = briefing_config.BriefingConfig(
        schema_version=1,
        sections=(config.sections[0]._replace(excluded_stories=5),),
    )
    return corpus, text, config


@variant("valid-grouped-multisection")
def _valid_grouped_multisection(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
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
    return corpus, text, config


@variant("category-ambiguity-clean")
def _category_ambiguity_clean(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
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
    return corpus, text, config


@variant("fabricated-included")
@text_only
def _fabricated_included(text: str) -> str:
    text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://publisher.test/invented")
    return text


@variant("fabricated-excluded")
@text_only
def _fabricated_excluded(text: str) -> str:
    text = _replace(text, "https://example.test/tool-three", "https://example.test/invented")
    return text


@variant("altered-publisher-url")
@text_only
def _altered_publisher_url(text: str) -> str:
    text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://publisher.test/story?id=2")
    return text


@variant("bare-included-url")
@text_only
def _bare_included_url(text: str) -> str:
    text = _replace(text, "🔗 https://publisher.test/story?id=2&output=1", "https://publisher.test/story?id=2&output=1")
    return text


@variant("markdown-included-url")
@text_only
def _markdown_included_url(text: str) -> str:
    text = _replace(
        text,
        "🔗 https://publisher.test/story?id=2&output=1",
        "[Source](https://publisher.test/story?id=2&output=1)",
    )
    return text


@variant("markdown-excluded-url")
@text_only
def _markdown_excluded_url(text: str) -> str:
    text = _replace(
        text,
        "🔗 https://example.test/tool-three",
        "[Source](https://example.test/tool-three)",
    )
    return text


@variant("duplicate-url-same-topic")
@text_only
def _duplicate_url_same_topic(text: str) -> str:
    text = _replace(
        text,
        "🔗 https://publisher.test/story?id=2&output=1",
        "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://publisher.test/story?id=2&output=1",
    )
    return text


@variant("duplicate-url-canonical")
@text_only
def _duplicate_url_canonical(text: str) -> str:
    text = _replace(
        text,
        "🔗 https://publisher.test/story?id=2&output=1",
        "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://PUBLISHER.test/story/?output=1&id=2&utm_source=x",
    )
    return text


@variant("included-and-excluded")
@text_only
def _included_and_excluded(text: str) -> str:
    text = _replace(text, "https://example.test/tool-three", "https://publisher.test/story?id=2&output=1")
    return text


@variant("topic-without-link")
@text_only
def _topic_without_link(text: str) -> str:
    text = _replace(text, "🔗 https://publisher.test/story?id=2&output=1", "")
    return text


@variant("excluded-without-link")
@text_only
def _excluded_without_link(text: str) -> str:
    text = _replace(text, "🔗 https://example.test/tool-three", "")
    return text


@variant("missing-section")
@text_only
def _missing_section(text: str) -> str:
    text = _replace(text, "## AI Dev Tools", "## Developer News")
    return text


@variant("missing-exclusion-section")
@text_only
def _missing_exclusion_section(text: str) -> str:
    text = text.split("### Excluded Topics", 1)[0]
    return text


@variant("overfilled")
@text_only
def _overfilled(text: str) -> str:
    text = _replace(
        text,
        "---\n\n### Excluded Topics",
        "**Tool three** — Tool three updated its editor extension.\n"
        "🔗 https://example.test/tool-three\n\n---\n\n### Excluded Topics",
    )
    text = _replace_tool_three_exclusion(text)
    return text


@variant("underfilled")
def _underfilled(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    start = text.index("**Tool two adds review mode**")
    end = text.index("---", start)
    text = text[:start] + text[end:]
    return corpus, text, config


@variant("wrong-category")
@text_only
def _wrong_category(text: str) -> str:
    text = _replace(text, "https://publisher.test/story?id=2&output=1", "https://example.test/market")
    return text


@variant("hn-discussion-missing")
@text_only
def _hn_discussion_missing(text: str) -> str:
    text = _replace(text, "🔗 HN: https://news.ycombinator.com/item?id=101\n", "")
    return text


@variant("unsupported-figure")
@text_only
def _unsupported_figure(text: str) -> str:
    text = _replace(text, "version 2 with", "version 87 with")
    return text


@variant("unsupported-quotation")
@text_only
def _unsupported_quotation(text: str) -> str:
    text = _replace(text, "Tool two added a review mode", 'Tool two called review mode "perfectly safe"')
    return text


@variant("claim-too-long")
@text_only
def _claim_too_long(text: str) -> str:
    text = _replace(
        text,
        "Tool two added a review mode for proposed patches.",
        "Tool two added a review mode for proposed patches and transformed every stage of engineering "
        "with universal safety guarantees, automatic deployment, perfect rollback, and complete compliance.",
    )
    return text


@variant("valid-semantic-figure")
@text_only
def _valid_semantic_figure(text: str) -> str:
    text = _replace(
        text,
        "**Tool two adds review mode** — Tool two added a review mode for proposed patches.\n"
        "🔗 https://publisher.test/story?id=2&output=1",
        "**Half of users enable feature** — 50 percent of users enabled the optional feature.\n"
        "🔗 https://example.test/half-users",
    )
    return text


@variant("valid-thin-evidence-paraphrase")
def _valid_thin_evidence_paraphrase(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"][1]["summary"] = "Review mode arrived."
    text = _replace(
        text,
        "Tool two added a review mode for proposed patches.",
        "The release introduced a mode that lets developers review proposed patches before accepting them.",
    )
    return corpus, text, config


def _claim_pair(variant_name: str) -> Variant:
    def apply(
        corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
    ) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
        corpus, text = _apply_claim_pair_variant(corpus, text, variant_name)
        return corpus, text, config

    return apply


for _variant_name in CLAIM_PAIR_VARIANTS:
    variant(_variant_name)(_claim_pair(_variant_name))


@variant("thin-evidence-unsupported")
def _thin_evidence_unsupported(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"][1]["summary"] = "Review mode arrived."
    text = _replace(
        text,
        "Tool two added a review mode for proposed patches.",
        "Review mode guarantees zero defects.",
    )
    return corpus, text, config


@variant("conflicting-evidence")
def _conflicting_evidence(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"].append(
        _item(
            "Tool two review mode delayed",
            "https://example.test/tool-two-delay",
            "The maintainer says review mode has not shipped.",
        )
    )
    corpus["processing"]["dev_community"]["fetched"] = 5
    corpus["processing"]["dev_community"]["kept"] = 5
    text = _replace(
        text,
        "🔗 https://publisher.test/story?id=2&output=1",
        "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://example.test/tool-two-delay",
    )
    return corpus, text, config


@variant("over-consolidated")
@text_only
def _over_consolidated(text: str) -> str:
    text = _replace(
        text,
        "🔗 https://publisher.test/story?id=2&output=1",
        "🔗 https://publisher.test/story?id=2&output=1\n🔗 https://example.test/tool-three",
    )
    text = _replace(text, "https://example.test/tool-three", "https://example.test/half-users")
    return text


@variant("health-valid")
def _health_valid(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += _health_block(
        [
            ("rss", "Feed A", "error"),
        ]
    )
    return corpus, text, config


@variant("health-missing")
def _health_missing(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    return corpus, text, config


@variant("health-prose-only")
def _health_prose_only(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += "\n---\n\n### Corpus health\nFeed A failed.\n"
    return corpus, text, config


@variant("health-malformed-json")
def _health_malformed_json(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += "\n---\n\n### Corpus health\n```json\n{bad json}\n```\n"
    return corpus, text, config


@variant("health-wrong-schema")
def _health_wrong_schema(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += '\n---\n\n### Corpus health\n```json\n{"failures": []}\n```\n'
    return corpus, text, config


@variant("health-partial")
def _health_partial(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=True)
    text += _health_block([("rss", "Feed A", "error")])
    return corpus, text, config


@variant("health-unexpected")
def _health_unexpected(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += _health_block(
        [
            ("rss", "Feed A", "error"),
            ("rss", "Imaginary", "error"),
        ]
    )
    return corpus, text, config


@variant("health-wrong-status")
def _health_wrong_status(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=False)
    text += _health_block([("rss", "Feed A", "empty")])
    return corpus, text, config


@variant("health-duplicate")
def _health_duplicate(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus = _degraded_corpus(corpus, two_failures=True)
    text += _health_block(
        [
            ("rss", "Feed A", "error"),
            ("rss", "Feed B", "empty"),
            ("rss", "Feed B", "empty"),
        ]
    )
    return corpus, text, config


@variant("selection-ambiguity")
def _selection_ambiguity(
    corpus: dict[str, Any], text: str, config: briefing_config.BriefingConfig
) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus["categories"]["dev_community"][2].update(
        title="Tool three release combines an extension update with staged patch review",
        summary=("The release is both an editor-extension product update and a staged patch-review workflow change."),
    )
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
        "**Tool three release combines an extension update with staged patch review** — "
        "The release is both an editor-extension product update and a staged "
        "patch-review workflow change.\n"
        "🔗 https://example.test/tool-three\n\n"
        "---\n\n### Excluded Topics",
    )
    text = _replace(text, "`↑ 45 pts · 12 comments`\n", "")
    text = _replace_tool_three_exclusion(text)
    return corpus, text, config


@variant("formatting-ambiguity")
@text_only
def _formatting_ambiguity(text: str) -> str:
    text = _replace(text, "## AI Dev Tools", "**AI Dev Tools (2 slots)**")
    return text


def apply_variant(variant: str) -> tuple[dict[str, Any], str, briefing_config.BriefingConfig]:
    corpus, text, config = baseline()

    if variant == "valid-baseline":
        corpus, text, config = _valid_baseline(corpus, text, config)
    elif variant == "valid-trailing-slash":
        corpus, text, config = _valid_trailing_slash(corpus, text, config)
    elif variant == "valid-tracking-parameter":
        corpus, text, config = _valid_tracking_parameter(corpus, text, config)
    elif variant == "valid-query-order":
        corpus, text, config = _valid_query_order(corpus, text, config)
    elif variant == "ungrounded-bare-url-in-prose":
        corpus, text, config = _ungrounded_bare_url_in_prose(corpus, text, config)
    elif variant == "valid-doctype-as-text":
        corpus, text, config = _valid_doctype_as_text(corpus, text, config)
    elif variant == "valid-category-overlap":
        corpus, text, config = _valid_category_overlap(corpus, text, config)
    elif variant == "valid-hn-without-discussion":
        corpus, text, config = _valid_hn_without_discussion(corpus, text, config)
    elif variant == "valid-exclusions-exhausted":
        corpus, text, config = _valid_exclusions_exhausted(corpus, text, config)
    elif variant == "valid-grouped-multisection":
        corpus, text, config = _valid_grouped_multisection(corpus, text, config)
    elif variant == "category-ambiguity-clean":
        corpus, text, config = _category_ambiguity_clean(corpus, text, config)
    elif variant == "fabricated-included":
        corpus, text, config = _fabricated_included(corpus, text, config)
    elif variant == "fabricated-excluded":
        corpus, text, config = _fabricated_excluded(corpus, text, config)
    elif variant == "altered-publisher-url":
        corpus, text, config = _altered_publisher_url(corpus, text, config)
    elif variant == "bare-included-url":
        corpus, text, config = _bare_included_url(corpus, text, config)
    elif variant == "markdown-included-url":
        corpus, text, config = _markdown_included_url(corpus, text, config)
    elif variant == "markdown-excluded-url":
        corpus, text, config = _markdown_excluded_url(corpus, text, config)
    elif variant == "duplicate-url-same-topic":
        corpus, text, config = _duplicate_url_same_topic(corpus, text, config)
    elif variant == "duplicate-url-canonical":
        corpus, text, config = _duplicate_url_canonical(corpus, text, config)
    elif variant == "included-and-excluded":
        corpus, text, config = _included_and_excluded(corpus, text, config)
    elif variant == "topic-without-link":
        corpus, text, config = _topic_without_link(corpus, text, config)
    elif variant == "excluded-without-link":
        corpus, text, config = _excluded_without_link(corpus, text, config)
    elif variant == "missing-section":
        corpus, text, config = _missing_section(corpus, text, config)
    elif variant == "missing-exclusion-section":
        corpus, text, config = _missing_exclusion_section(corpus, text, config)
    elif variant == "overfilled":
        corpus, text, config = _overfilled(corpus, text, config)
    elif variant == "underfilled":
        corpus, text, config = _underfilled(corpus, text, config)
    elif variant == "wrong-category":
        corpus, text, config = _wrong_category(corpus, text, config)
    elif variant == "hn-discussion-missing":
        corpus, text, config = _hn_discussion_missing(corpus, text, config)
    elif variant == "unsupported-figure":
        corpus, text, config = _unsupported_figure(corpus, text, config)
    elif variant == "unsupported-quotation":
        corpus, text, config = _unsupported_quotation(corpus, text, config)
    elif variant == "claim-too-long":
        corpus, text, config = _claim_too_long(corpus, text, config)
    elif variant == "valid-semantic-figure":
        corpus, text, config = _valid_semantic_figure(corpus, text, config)
    elif variant == "valid-thin-evidence-paraphrase":
        corpus, text, config = _valid_thin_evidence_paraphrase(corpus, text, config)
    elif variant in CLAIM_PAIR_VARIANTS:
        corpus, text, config = _claim_pair(variant)(corpus, text, config)
    elif variant == "thin-evidence-unsupported":
        corpus, text, config = _thin_evidence_unsupported(corpus, text, config)
    elif variant == "conflicting-evidence":
        corpus, text, config = _conflicting_evidence(corpus, text, config)
    elif variant == "over-consolidated":
        corpus, text, config = _over_consolidated(corpus, text, config)
    elif variant.startswith("health-"):
        if variant == "health-valid":
            corpus, text, config = _health_valid(corpus, text, config)
        elif variant == "health-missing":
            corpus, text, config = _health_missing(corpus, text, config)
        elif variant == "health-prose-only":
            corpus, text, config = _health_prose_only(corpus, text, config)
        elif variant == "health-malformed-json":
            corpus, text, config = _health_malformed_json(corpus, text, config)
        elif variant == "health-wrong-schema":
            corpus, text, config = _health_wrong_schema(corpus, text, config)
        elif variant == "health-partial":
            corpus, text, config = _health_partial(corpus, text, config)
        elif variant == "health-unexpected":
            corpus, text, config = _health_unexpected(corpus, text, config)
        elif variant == "health-wrong-status":
            corpus, text, config = _health_wrong_status(corpus, text, config)
        elif variant == "health-duplicate":
            corpus, text, config = _health_duplicate(corpus, text, config)
        else:
            raise ValueError(f"unknown variant {variant!r}")
    elif variant == "selection-ambiguity":
        corpus, text, config = _selection_ambiguity(corpus, text, config)
    elif variant == "formatting-ambiguity":
        corpus, text, config = _formatting_ambiguity(corpus, text, config)
    else:
        raise ValueError(f"unknown variant {variant!r}")
    _fixture_usage(corpus)
    return corpus, text, config
