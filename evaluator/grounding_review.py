"""Blinded human-review packets for final-output grounding labels."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
import eval_briefing

from evaluator.judge_io import portable_path, sha256_bytes, write_json_atomic


def _case_configs(manifest: dict[str, Any], manifest_path: Path) -> dict[str, briefing_config.BriefingConfig]:
    suite_path = Path(manifest["suite"])
    if not suite_path.is_absolute():
        suite_path = (manifest_path.parent / suite_path).resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    return {
        case["id"]: briefing_config.load_config(suite_path.parent / case["config"])
        for case in suite["cases"]
    }


def _review_topics(manifest_path: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    run_dir = manifest_path.parent
    configs = _case_configs(manifest, manifest_path)
    records = []
    for row in manifest["results"]:
        if row.get("case_kind") != "utility" or not isinstance(row.get("final"), dict):
            continue
        config = configs[row["case_id"]]
        case_dir = run_dir / row["artifact_dir"]
        sections = eval_briefing.parse_briefing(
            (case_dir / "final.md").read_text(encoding="utf-8"), config
        )
        corpus = json.loads((case_dir / "corpus.json").read_text(encoding="utf-8"))
        evidence = eval_briefing.corpus_evidence(corpus)
        topic_index = 0
        for section, bucket in sections.items():
            if section in {eval_briefing.EXCLUDED, eval_briefing.CORPUS_HEALTH}:
                continue
            for title, prose, links in zip(
                bucket.get("topics", []),
                bucket.get("topic_texts", []),
                bucket.get("topic_links", []),
                strict=True,
            ):
                topic_index += 1
                canonical = list(dict.fromkeys(corpus_schema.canonicalize_url(url) for url in links))
                records.append({
                    "artifact_dir": row["artifact_dir"],
                    "topic_index": topic_index,
                    "stratum": row["case_family"],
                    "private": {"case_id": row["case_id"], "trial": row["trial"]},
                    "public": {
                        "section": section,
                        "title": title,
                        "prose": prose,
                        "citations": canonical,
                        "evidence": [
                            {
                                "url": url,
                                "corpus_match": url in evidence,
                                "feed_evidence": evidence.get(url),
                            }
                            for url in canonical
                        ],
                    },
                })
    return records


def _double_sample(records: list[dict[str, Any]], count: int, rng: random.Random) -> list[dict[str, Any]]:
    by_stratum: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_stratum[record["stratum"]].append(record)
    if not records or count <= 0:
        return []
    target = min(len(records), max(count, len(by_stratum)))
    chosen = []
    for stratum in sorted(by_stratum):
        chosen.append(rng.choice(by_stratum[stratum]))
    chosen_keys = {(row["artifact_dir"], row["topic_index"]) for row in chosen}
    remaining = [
        row for row in records
        if (row["artifact_dir"], row["topic_index"]) not in chosen_keys
    ]
    rng.shuffle(remaining)
    return chosen + remaining[: target - len(chosen)]


def _packet(
    records: list[dict[str, Any]], prefix: str, rng: random.Random
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    shuffled = list(records)
    rng.shuffle(shuffled)
    public = []
    mapping = []
    for index, record in enumerate(shuffled, 1):
        review_id = f"{prefix}-{index:05d}"
        public.append({"review_id": review_id, **record["public"]})
        mapping.append({
            "review_id": review_id,
            "artifact_dir": record["artifact_dir"],
            "topic_index": record["topic_index"],
            "stratum": record["stratum"],
            **record["private"],
        })
    return public, mapping


def export_grounding_review_packets(
    manifest_path: Path,
    output_dir: Path,
    *,
    seed: int = 8142026,
    double_fraction: float = 0.20,
) -> dict[str, Any]:
    """Export fully blinded primary and stratified double-review packets."""
    if not 0 < double_fraction <= 1:
        raise ValueError("double_fraction must be greater than zero and at most one")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    records = _review_topics(manifest_path, manifest)
    rng = random.Random(seed)
    double_count = max(1, round(len(records) * double_fraction)) if records else 0
    double_records = _double_sample(records, double_count, rng)
    primary, primary_map = _packet(records, "ground", rng)
    secondary, secondary_map = _packet(double_records, "double", rng)
    rubric = {
        "grounding_error_true": (
            "The topic lacks a citation, cites evidence that does not support a material claim, "
            "or adds/reverses/strengthens a claim beyond the supplied feed evidence."
        ),
        "grounding_error_false": (
            "Every material claim is supported by at least one cited feed-evidence item; faithful "
            "paraphrase is allowed."
        ),
        "scope": "Use only the supplied feed evidence. Do not use outside knowledge or infer from the URL.",
        "missing_citation_evidence": (
            "An evidence item with corpus_match=false was not found in the corpus; its "
            "feed_evidence is null. An empty feed_evidence string with corpus_match=true "
            "means the corpus item exists but supplies no textual evidence."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    packet_meta = {
        "schema_version": 1,
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "rubric": rubric,
    }
    write_json_atomic(output_dir / "reviewer-primary.json", {**packet_meta, "reviews": primary})
    write_json_atomic(output_dir / "reviewer-double.json", {**packet_meta, "reviews": secondary})
    write_json_atomic(output_dir / "response-primary.json", {
        "schema_version": 1,
        "attestation": {"reviewer": "", "reviewed_on": "", "blinded_to_model_and_prompt": True},
        "reviews": [
            {"review_id": row["review_id"], "grounding_error": None, "rationale": ""}
            for row in primary
        ],
    })
    write_json_atomic(output_dir / "response-double.json", {
        "schema_version": 1,
        "attestation": {"reviewer": "", "reviewed_on": "", "blinded_to_model_and_prompt": True},
        "reviews": [
            {
                "review_id": row["review_id"],
                "grounding_error": None,
                "rationale": "",
                "final_grounding_error_if_disputed": None,
            }
            for row in secondary
        ],
    })
    write_json_atomic(output_dir / "review-map.json", {
        "schema_version": 1,
        "manifest": portable_path(manifest_path),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "seed": seed,
        "double_fraction": double_fraction,
        "primary": primary_map,
        "double": secondary_map,
    })
    return {
        "topic_count": len(records),
        "double_review_count": len(double_records),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "output_dir": portable_path(output_dir),
    }
