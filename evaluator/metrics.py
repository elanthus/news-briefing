"""Small, dependency-free metric helpers used by offline and live evaluation."""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable
from typing import Any


def percentile(values: list[float], probability: float) -> float:
    """Return a linearly interpolated percentile without a numerical dependency."""
    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0 <= probability <= 1:
        raise ValueError("probability must be between zero and one")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def wilson_interval(successes: int, trials: int, z: float = 1.959963984540054) -> list[float] | None:
    """Return a two-sided 95% Wilson score interval for a binomial rate."""
    if trials == 0:
        return None
    rate = successes / trials
    z2 = z * z
    denominator = 1 + z2 / trials
    center = (rate + z2 / (2 * trials)) / denominator
    margin = z * math.sqrt((rate * (1 - rate) + z2 / (4 * trials)) / trials) / denominator
    return [max(0.0, center - margin), min(1.0, center + margin)]


def rate(successes: int, trials: int) -> dict[str, Any]:
    return {
        "successes": successes,
        "trials": trials,
        "rate": successes / trials if trials else None,
        "ci95_wilson": wilson_interval(successes, trials),
    }


def classification_counts(expected: Iterable[set[str]], predicted: Iterable[set[str]]) -> dict[str, int]:
    expected_rows = list(expected)
    predicted_rows = list(predicted)
    if len(expected_rows) != len(predicted_rows):
        raise ValueError("expected and predicted rows must have equal length")
    labels = set().union(*expected_rows, *predicted_rows)
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for wanted, found in zip(expected_rows, predicted_rows, strict=True):
        for label in labels:
            if label in wanted and label in found:
                counts["tp"] += 1
            elif label in found:
                counts["fp"] += 1
            elif label in wanted:
                counts["fn"] += 1
            else:
                counts["tn"] += 1
    return counts


def classification_metrics(expected: Iterable[set[str]], predicted: Iterable[set[str]]) -> dict[str, Any]:
    counts = classification_counts(expected, predicted)
    return {
        **counts,
        "precision": rate(counts["tp"], counts["tp"] + counts["fp"]),
        "recall": rate(counts["tp"], counts["tp"] + counts["fn"]),
        "false_positive_rate": rate(counts["fp"], counts["fp"] + counts["tn"]),
    }


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"trials": 0, "mean_ms": None, "median_ms": None, "p95_ms": None}
    ordered = sorted(values)
    p95_index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return {
        "trials": len(values),
        "mean_ms": statistics.fmean(values),
        "median_ms": statistics.median(values),
        "p95_ms": ordered[p95_index],
    }
