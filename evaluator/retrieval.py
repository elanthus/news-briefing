"""Offline-friendly retrieval helpers for the evaluator's deduplication study."""

from __future__ import annotations

import hashlib
import json
import math
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, TypedDict

from evaluator.adapters import (
    API_MAX_ATTEMPTS,
    RETRYABLE_HTTP_STATUSES,
    _retry_after_seconds,
)

EMBEDDINGS_ENDPOINT = "https://openrouter.ai/api/v1/embeddings"
EMBEDDINGS_TIMEOUT_SECONDS = 60
EMBEDDING_DIMENSIONS = 512
EVALUATOR_DIR = Path(__file__).resolve().parent
DEFAULT_PAIR_FIXTURE = EVALUATOR_DIR / "fixtures" / "dedup-pairs.json"
DEFAULT_EMBEDDING_CACHE = EVALUATOR_DIR / "fixtures" / "dedup-embeddings.json"
DEFAULT_EMBEDDING_MODEL = "openai/text-embedding-3-small"


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


class EmbeddingCache(TypedDict):
    """A committed, credential-free embedding snapshot."""

    schema_version: int
    model: str
    generated_on: str
    dimensions: int
    text_representation: str
    embeddings: dict[str, list[float]]


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


def _embedding_vectors(payload: Any, expected_count: int) -> list[list[float]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise RuntimeError("OpenRouter returned an unexpected embeddings response")
    ordered: list[list[float] | None] = [None] * expected_count
    for entry in payload["data"]:
        if not isinstance(entry, dict):
            raise RuntimeError("OpenRouter returned an invalid embedding entry")
        index = entry.get("index")
        raw_vector = entry.get("embedding")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < expected_count
            or not isinstance(raw_vector, list)
            or not raw_vector
            or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in raw_vector)
            or ordered[index] is not None
        ):
            raise RuntimeError("OpenRouter returned an invalid embedding entry")
        ordered[index] = [float(value) for value in raw_vector]
    if any(vector is None for vector in ordered):
        raise RuntimeError("OpenRouter returned fewer embeddings than requested")
    vectors = [vector for vector in ordered if vector is not None]
    dimensions = {len(vector) for vector in vectors}
    if len(dimensions) > 1:
        raise RuntimeError("OpenRouter returned embeddings with inconsistent dimensions")
    return vectors


def embed_texts(texts: list[str], model: str, api_key: str) -> list[list[float]]:
    """Embed one batch through OpenRouter with bounded retry and ordering checks."""
    if not texts:
        return []
    if not model.strip():
        raise ValueError("embedding model must be non-empty")
    if not api_key.strip():
        raise ValueError("OpenRouter API key must be non-empty")
    request = urllib.request.Request(
        EMBEDDINGS_ENDPOINT,
        data=json.dumps({
            "dimensions": EMBEDDING_DIMENSIONS,
            "input": texts,
            "model": model,
        }).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(1, API_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(
                request, timeout=EMBEDDINGS_TIMEOUT_SECONDS
            ) as response:
                payload = json.loads(response.read())
            return _embedding_vectors(payload, len(texts))
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            transient = exc.code in RETRYABLE_HTTP_STATUSES or 500 <= exc.code <= 599
            status = exc.code
            exc.close()
            if not transient or attempt == API_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"OpenRouter embeddings request failed with HTTP {status} "
                    f"after {attempt} attempt(s)"
                ) from exc
            delay = retry_after if retry_after is not None else float(2 ** (attempt - 1))
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            if attempt == API_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"OpenRouter embeddings request failed after {attempt} attempt(s): {exc}"
                ) from exc
            delay = float(2 ** (attempt - 1))
        if delay:
            time.sleep(delay)
    raise AssertionError("embedding retry loop exhausted without returning or raising")


def _pair_item(value: object, pair_id: str, side: str) -> PairItem:
    if not isinstance(value, dict):
        raise ValueError(f"pair {pair_id!r} {side} must be an object")
    fields: dict[str, str] = {}
    for name in ("title", "summary", "url"):
        field = value.get(name)
        if not isinstance(field, str):
            raise ValueError(f"pair {pair_id!r} {side}.{name} must be a string")
        fields[name] = field
    return {"title": fields["title"], "summary": fields["summary"], "url": fields["url"]}


def load_pairs(path: Path = DEFAULT_PAIR_FIXTURE) -> list[PairLabel]:
    """Load and validate the fields consumed by the retrieval study."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("pairs"), list):
        raise ValueError("dedup pair fixture must contain a pairs array")
    pairs: list[PairLabel] = []
    seen_ids: set[str] = set()
    for raw_pair in payload["pairs"]:
        if not isinstance(raw_pair, dict):
            raise ValueError("each dedup pair must be an object")
        pair_id = raw_pair.get("id")
        label = raw_pair.get("label")
        stratum = raw_pair.get("stratum")
        rationale = raw_pair.get("rationale")
        if not isinstance(pair_id, str) or not pair_id or pair_id in seen_ids:
            raise ValueError("dedup pair ids must be unique non-empty strings")
        if label not in {"duplicate", "distinct"}:
            raise ValueError(f"pair {pair_id!r} has an invalid label")
        if stratum not in {"duplicate", "clear_negative", "hard_negative"}:
            raise ValueError(f"pair {pair_id!r} has an invalid stratum")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"pair {pair_id!r} must have a rationale")
        seen_ids.add(pair_id)
        pairs.append({
            "id": pair_id,
            "left": _pair_item(raw_pair.get("left"), pair_id, "left"),
            "right": _pair_item(raw_pair.get("right"), pair_id, "right"),
            "label": label,
            "stratum": stratum,
            "rationale": rationale,
        })
    return pairs


def embedding_inputs(pairs: Sequence[PairLabel]) -> dict[str, str]:
    """Return unique cache-keyed texts in stable first-seen order."""
    inputs: dict[str, str] = {}
    for pair in pairs:
        for item in (pair["left"], pair["right"]):
            text = embedding_text(item)
            key = embedding_key(text)
            previous = inputs.get(key)
            if previous is not None and previous != text:
                raise ValueError(f"SHA-256 collision for embedding key {key}")
            inputs[key] = text
    return inputs


def build_embedding_cache(
    pairs: Sequence[PairLabel],
    model: str,
    api_key: str,
    generated_on: str,
) -> EmbeddingCache:
    """Fetch all unique study texts and return a serializable offline cache."""
    inputs = embedding_inputs(pairs)
    vectors = embed_texts(list(inputs.values()), model, api_key)
    if len(vectors) != len(inputs):
        raise RuntimeError("embedding response count does not match the requested cache inputs")
    return {
        "schema_version": 1,
        "model": model,
        "generated_on": generated_on,
        "dimensions": len(vectors[0]) if vectors else 0,
        "text_representation": "UTF-8 title, newline, then summary",
        "embeddings": dict(zip(inputs, vectors, strict=True)),
    }
