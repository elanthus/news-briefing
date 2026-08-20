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

from fetch_news import Item, dedupe

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
DEFAULT_STUDY_REPORT = EVALUATOR_DIR / "results" / "dedup-study.md"
DEFAULT_THRESHOLDS = (0.70, 0.75, 0.80, 0.85, 0.90, 0.95)


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


class BinaryMetrics(TypedDict):
    """Confusion counts and positive-class metrics."""

    tp: int
    fp: int
    tn: int
    fn: int
    precision: float
    recall: float
    f1: float


class ThresholdResult(TypedDict):
    """Embedding performance at one cosine threshold."""

    threshold: float
    metrics: BinaryMetrics


class StudyResult(TypedDict):
    """Complete deterministic comparison used to render the study report."""

    pair_count: int
    duplicate_count: int
    distinct_count: int
    embedding_results: list[ThresholdResult]
    chosen_threshold: float
    chosen_embedding_metrics: BinaryMetrics
    heuristic_metrics: BinaryMetrics
    embedding_predictions: dict[str, bool]
    heuristic_predictions: dict[str, bool]
    similarities: dict[str, float]


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
        data=json.dumps(
            {
                "dimensions": EMBEDDING_DIMENSIONS,
                "input": texts,
                "model": model,
            }
        ).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    for attempt in range(1, API_MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=EMBEDDINGS_TIMEOUT_SECONDS) as response:
                payload = json.loads(response.read())
            return _embedding_vectors(payload, len(texts))
        except urllib.error.HTTPError as exc:
            retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
            transient = exc.code in RETRYABLE_HTTP_STATUSES or 500 <= exc.code <= 599
            status = exc.code
            exc.close()
            if not transient or attempt == API_MAX_ATTEMPTS:
                raise RuntimeError(
                    f"OpenRouter embeddings request failed with HTTP {status} after {attempt} attempt(s)"
                ) from exc
            delay = retry_after if retry_after is not None else float(2 ** (attempt - 1))
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            if attempt == API_MAX_ATTEMPTS:
                raise RuntimeError(f"OpenRouter embeddings request failed after {attempt} attempt(s): {exc}") from exc
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
        if (stratum == "duplicate") != (label == "duplicate"):
            raise ValueError(f"pair {pair_id!r} label is inconsistent with its stratum")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"pair {pair_id!r} must have a rationale")
        seen_ids.add(pair_id)
        pairs.append(
            {
                "id": pair_id,
                "left": _pair_item(raw_pair.get("left"), pair_id, "left"),
                "right": _pair_item(raw_pair.get("right"), pair_id, "right"),
                "label": label,
                "stratum": stratum,
                "rationale": rationale,
            }
        )
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


def load_embedding_cache(path: Path = DEFAULT_EMBEDDING_CACHE) -> EmbeddingCache:
    """Load and validate the committed cache needed for an offline study."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("embedding cache must be an object")
    if payload.get("schema_version") != 1:
        raise ValueError("embedding cache schema_version must be 1")
    model = payload.get("model")
    generated_on = payload.get("generated_on")
    dimensions = payload.get("dimensions")
    raw_embeddings = payload.get("embeddings")
    if not isinstance(model, str) or not model:
        raise ValueError("embedding cache model must be a non-empty string")
    if not isinstance(generated_on, str) or not generated_on:
        raise ValueError("embedding cache generated_on must be a non-empty string")
    if not isinstance(dimensions, int) or isinstance(dimensions, bool) or dimensions <= 0:
        raise ValueError("embedding cache dimensions must be a positive integer")
    if not isinstance(raw_embeddings, dict) or not raw_embeddings:
        raise ValueError("embedding cache must contain embeddings")
    embeddings: dict[str, list[float]] = {}
    for key, raw_vector in raw_embeddings.items():
        valid_key = False
        if isinstance(key, str) and len(key) == 64:
            try:
                int(key, 16)
            except ValueError:
                pass
            else:
                valid_key = True
        if (
            not valid_key
            or not isinstance(raw_vector, list)
            or len(raw_vector) != dimensions
            or any(not isinstance(value, (int, float)) or isinstance(value, bool) for value in raw_vector)
        ):
            raise ValueError("embedding cache contains an invalid vector")
        embeddings[key] = [float(value) for value in raw_vector]
    return {
        "schema_version": 1,
        "model": model,
        "generated_on": generated_on,
        "dimensions": dimensions,
        "text_representation": str(payload.get("text_representation", "")),
        "embeddings": embeddings,
    }


def _binary_metrics(pairs: Sequence[PairLabel], predictions: Mapping[str, bool]) -> BinaryMetrics:
    tp = fp = tn = fn = 0
    for pair in pairs:
        expected = pair["label"] == "duplicate"
        try:
            predicted = predictions[pair["id"]]
        except KeyError as exc:
            raise ValueError(f"missing prediction for pair {pair['id']!r}") from exc
        if expected and predicted:
            tp += 1
        elif expected:
            fn += 1
        elif predicted:
            fp += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def production_heuristic_predictions(pairs: Sequence[PairLabel]) -> dict[str, bool]:
    """Run each pair through the exact production URL/title-key deduper."""
    predictions: dict[str, bool] = {}
    for pair in pairs:
        items: list[Item] = [
            {
                "title": side["title"],
                "summary": side["summary"],
                "url": side["url"],
                "published": "1970-01-01T00:00:00+00:00",
                "source": "dedup-study",
            }
            for side in (pair["left"], pair["right"])
        ]
        predictions[pair["id"]] = len(dedupe(items)) == 1
    return predictions


def run_study(
    pairs: Sequence[PairLabel],
    embeddings: Mapping[str, Sequence[float]],
    thresholds: Sequence[float] = DEFAULT_THRESHOLDS,
) -> StudyResult:
    """Compare a cosine-threshold sweep with the production pair heuristic."""
    if not pairs:
        raise ValueError("dedup study requires at least one labeled pair")
    if not thresholds:
        raise ValueError("dedup study requires at least one threshold")
    if len(set(thresholds)) != len(thresholds) or any(not -1.0 <= value <= 1.0 for value in thresholds):
        raise ValueError("thresholds must be unique values between -1 and 1")
    similarities: dict[str, float] = {}
    for pair in pairs:
        left_key = embedding_key(embedding_text(pair["left"]))
        right_key = embedding_key(embedding_text(pair["right"]))
        try:
            similarities[pair["id"]] = cosine(embeddings[left_key], embeddings[right_key])
        except KeyError as exc:
            raise KeyError(f"missing cached embedding for pair {pair['id']!r}") from exc

    embedding_results: list[ThresholdResult] = []
    embedding_predictions: dict[float, dict[str, bool]] = {}
    for threshold in thresholds:
        predictions = {pair_id: similarity >= threshold for pair_id, similarity in similarities.items()}
        embedding_predictions[threshold] = predictions
        embedding_results.append(
            {
                "threshold": threshold,
                "metrics": _binary_metrics(pairs, predictions),
            }
        )
    chosen = max(
        embedding_results,
        key=lambda result: (
            result["metrics"]["f1"],
            result["metrics"]["precision"],
            result["threshold"],
        ),
    )
    chosen_threshold = chosen["threshold"]
    heuristic = production_heuristic_predictions(pairs)
    duplicate_count = sum(pair["label"] == "duplicate" for pair in pairs)
    return {
        "pair_count": len(pairs),
        "duplicate_count": duplicate_count,
        "distinct_count": len(pairs) - duplicate_count,
        "embedding_results": embedding_results,
        "chosen_threshold": chosen_threshold,
        "chosen_embedding_metrics": chosen["metrics"],
        "heuristic_metrics": _binary_metrics(pairs, heuristic),
        "embedding_predictions": embedding_predictions[chosen_threshold],
        "heuristic_predictions": heuristic,
        "similarities": similarities,
    }


def _metric_row(name: str, threshold: str, metrics: BinaryMetrics) -> str:
    return (
        f"| {name} | {threshold} | {metrics['precision']:.3f} | "
        f"{metrics['recall']:.3f} | {metrics['f1']:.3f} | "
        f"{metrics['tp']} | {metrics['fp']} | {metrics['tn']} | {metrics['fn']} |"
    )


def markdown_study(
    study: StudyResult,
    pairs: Sequence[PairLabel],
    cache: EmbeddingCache,
    label_provenance: str,
) -> str:
    """Render the deterministic comparison and hard-negative audit surface."""
    lines = [
        "# Near-duplicate retrieval study",
        "",
        (
            f"This evaluator-only study embeds **title + summary** for {study['pair_count']} "
            f"labeled pairs ({study['duplicate_count']} duplicate, {study['distinct_count']} distinct) "
            "and compares cosine thresholds with the exact production 60-character title-key heuristic. "
            "URLs are retained for provenance but are not embedded. Nothing here changes production deduplication."
        ),
        "",
        f"- Embedding model: `{cache['model']}`",
        f"- Dimensions: {cache['dimensions']}",
        f"- Cache generated: {cache['generated_on']}",
        f"- Label provenance: **{label_provenance}**",
        "- CI posture: vectors are committed; report generation is offline and credential-free.",
        "",
        "## Comparison",
        "",
        "| Classifier | Threshold | Precision | Recall | F1 | TP | FP | TN | FN |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        _metric_row("Production title key", "—", study["heuristic_metrics"]),
    ]
    for result in study["embedding_results"]:
        name = "**Embedding (chosen)**" if result["threshold"] == study["chosen_threshold"] else "Embedding"
        lines.append(_metric_row(name, f"{result['threshold']:.2f}", result["metrics"]))
    lines.extend(
        [
            "",
            "## Operating point",
            "",
            (
                f"The deterministic selection rule maximizes F1, then precision, then threshold. It chooses "
                f"**{study['chosen_threshold']:.2f}** on this fixture. This is an in-sample descriptive operating "
                "point, not a production-ready threshold; the labels still require owner sign-off and the fixture "
                "is too small for a deployment claim."
            ),
            "",
            "## Hard-negative error analysis",
            "",
            "| Pair | Cosine | Embedding | Title key | Result | Rationale |",
            "|---|---:|---|---|---|---|",
        ]
    )
    for pair in pairs:
        if pair["stratum"] != "hard_negative":
            continue
        pair_id = pair["id"]
        embedding_prediction = study["embedding_predictions"][pair_id]
        heuristic_prediction = study["heuristic_predictions"][pair_id]
        errors = []
        if embedding_prediction:
            errors.append("embedding FP")
        if heuristic_prediction:
            errors.append("title-key FP")
        outcome = ", ".join(errors) if errors else "both correct"
        rationale = pair["rationale"].replace("|", "\\|")
        lines.append(
            f"| `{pair_id}` | {study['similarities'][pair_id]:.3f} | "
            f"{'duplicate' if embedding_prediction else 'distinct'} | "
            f"{'duplicate' if heuristic_prediction else 'distinct'} | {outcome} | {rationale} |"
        )

    other_errors = [
        pair
        for pair in pairs
        if pair["stratum"] != "hard_negative"
        and study["embedding_predictions"][pair["id"]] != (pair["label"] == "duplicate")
    ]
    lines.extend(
        [
            "",
            "## Other chosen-threshold errors",
            "",
        ]
    )
    if not other_errors:
        lines.append("No additional embedding errors occur outside the hard-negative stratum.")
    else:
        lines.extend(
            [
                "| Pair | Label | Cosine | Prediction | Rationale |",
                "|---|---|---:|---|---|",
            ]
        )
        for pair in other_errors:
            pair_id = pair["id"]
            prediction = study["embedding_predictions"][pair_id]
            rationale = pair["rationale"].replace("|", "\\|")
            lines.append(
                f"| `{pair_id}` | {pair['label']} | {study['similarities'][pair_id]:.3f} | "
                f"{'duplicate' if prediction else 'distinct'} | {rationale} |"
            )

    embedding_f1 = study["chosen_embedding_metrics"]["f1"]
    heuristic_f1 = study["heuristic_metrics"]["f1"]
    if embedding_f1 > heuristic_f1:
        comparison = f"the chosen embedding threshold leads by {embedding_f1 - heuristic_f1:.3f} F1"
    elif embedding_f1 < heuristic_f1:
        comparison = f"the production heuristic leads by {heuristic_f1 - embedding_f1:.3f} F1"
    else:
        comparison = "the approaches tie on F1"
    lines.extend(
        [
            "",
            "## Conclusion",
            "",
            (
                f"On this machine-proposed fixture, {comparison}. The useful result is the measured trade-off, "
                "not a predetermined embedding win. Before any production experiment, the owner must review the "
                "labels and the study should be repeated on a larger time-split sample with an explicit latency and "
                "cost budget. Until then, the production heuristic remains unchanged."
            ),
            "",
        ]
    )
    return "\n".join(lines)
