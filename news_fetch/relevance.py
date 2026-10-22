"""Deterministic relevance filters for broad sources."""

from __future__ import annotations

import re

from news_fetch.model import Item

# The Verge, Ars Technica, and Wired feeds cover all of technology (and
# sometimes shopping/entertainment), the GitHub Changelog covers the whole
# product, Simon Willison's Weblog covers more than developer tooling, and
# Hacker News covers everything voted up — each gets a relevance filter in
# SOURCE_RELEVANCE_FILTERS below. Category-specific feeds and subreddits pass through unchanged. This
# cuts obvious corpus noise before it consumes model context without
# pretending that a keyword filter can rank importance.
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
    "Simon Willison's Weblog": DEV_TOOL_RELEVANCE,
    "Hacker News": HN_RELEVANCE,
}
# Some release feeds tag every prerelease build. The Codex feed publishes
# several alpha tags a day, and each one is a version bump, not a story. The
# noise pattern drops prerelease tags before they reach the model corpus and
# keeps stable releases. Matched against the title and summary, so a stable
# release whose notes mention an earlier alpha is still dropped; that is an
# accepted cost for a feed where the title is the version tag.
PRERELEASE_NOISE = re.compile(
    r"\d-(?:alpha|beta|rc|pre|dev|nightly)(?:[.\-]?\d+)*\b",
    re.IGNORECASE,
)
SOURCE_NOISE_FILTERS = {
    "Codex Releases": PRERELEASE_NOISE,
}


def is_relevant_item(item: Item) -> bool:
    """Apply deterministic relevance filtering only to known broad feeds.

    The filter removes noise; it does not decide importance. Ranking is the
    model's job under the prompt, and over-filtering is the more expensive
    mistake — an item dropped here cannot be ranked at all, and a starved
    sub-category cannot fill its reserved slots.
    """
    source = item.get("source", "")
    text = f"{item.get('title', '')} {item.get('summary', '')}"
    noise = SOURCE_NOISE_FILTERS.get(source)
    if noise is not None and noise.search(text):
        return False
    pattern = SOURCE_RELEVANCE_FILTERS.get(source)
    if pattern is None:
        return True
    if COMMERCE_NOISE.search(text):
        return False
    return bool(pattern.search(text))
