"""Execute authored evaluator cases and summarize checker and parser findings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import corpus_schema
import eval_briefing

from evaluator.case_fixtures import feed_prediction
from evaluator.cases import apply_variant
from evaluator.judge_io import portable_path
from evaluator.metrics import classification_metrics, rate

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = Path(__file__).with_name("fixtures") / "checker-cases.json"
HEURISTIC_CLAIM_CHECKS = (
    "unsupported_figure",
    "figure_supported_elsewhere",
    "unsupported_quotation",
    "claim_exceeds_evidence",
)


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
            predicted = feed_prediction(case["variant"])
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
    heuristic_positive = set(HEURISTIC_CLAIM_CHECKS)
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
        "suite": portable_path(path),
        "label_provenance": suite["label_provenance"],
        "case_count": len(records),
        "components": components,
        "heuristic_claim_false_positive_rate": rate(false_positives, heuristic_negatives),
        "heuristic_claim_false_positive_rates": per_check_false_positive_rates,
        "cases": records,
    }
