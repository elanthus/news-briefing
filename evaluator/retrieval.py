"""Offline-friendly retrieval helpers for the evaluator's deduplication study."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from typing import Literal, TypedDict


class PairItem(TypedDict):
    """The evidence retained for one side of a labeled pair."""

    title: str
    summary: str
    url: str


class PairLabel(TypedDict):
    """One human-reviewable near-duplicate classification example."""

    id: str
    left: PairItem
    right: PairItem
    label: Literal["duplicate", "distinct"]
    stratum: Literal["duplicate", "clear_negative", "hard_negative"]
    rationale: str


def embedding_text(item: PairItem) -> str:
    """Return the title-plus-summary representation used by the study."""
    return f"{item['title'].strip()}\n{item['summary'].strip()}"


def embedding_key(text: str) -> str:
    """Return the stable cache key for an embedded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Return cosine similarity for equal-size, nonzero vectors."""
    if len(a) != len(b):
        raise ValueError("vectors must have the same dimension")
    dot = sum(left * right for left, right in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(value * value for value in a))
    norm_b = math.sqrt(sum(value * value for value in b))
    if norm_a == 0.0 or norm_b == 0.0:
        raise ValueError("cosine similarity is undefined for a zero vector")
    return dot / (norm_a * norm_b)


def classify_pairs(
    pairs: Sequence[PairLabel],
    embeddings: Mapping[str, Sequence[float]],
    threshold: float,
) -> dict[str, bool]:
    """Classify pairs from SHA-256-keyed precomputed vectors."""
    predictions: dict[str, bool] = {}
    for pair in pairs:
        left_key = embedding_key(embedding_text(pair["left"]))
        right_key = embedding_key(embedding_text(pair["right"]))
        try:
            left = embeddings[left_key]
            right = embeddings[right_key]
        except KeyError as exc:
            raise KeyError(f"missing cached embedding for pair {pair['id']!r}") from exc
        predictions[pair["id"]] = cosine(left, right) >= threshold
    return predictions
