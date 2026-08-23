"""Blinded model review and disagreement adjudication for offline gold labels."""

from __future__ import annotations

import base64
import json
import random
import secrets
from pathlib import Path
from typing import Any

from evaluator.adapters import Adapter, Generation
from evaluator.cases import DEFAULT_SUITE, _xml_case, apply_variant
from evaluator.judge_io import (
    checkpointed_generate,
    parse_json_response,
    portable_path,
    sha256_bytes,
    write_json_atomic,
)

ROOT = Path(__file__).resolve().parents[1]


LABEL_RUBRIC = {
    "altered_link": "An output URL is a changed spelling of a corpus URL that drops meaningful parts.",
    "category_ambiguity": "A story plausibly fits multiple configured sections and its placement is ambiguous.",
    "category_ineligible": "A cited item is not in a corpus category eligible for its briefing section.",
    "claim_exceeds_evidence": "The prose adds material details beyond the cited title and summary.",
    "conflicting_evidence": (
        "The cited evidence contains mutually conflicting accounts that the prose fails to qualify."
    ),
    "corpus_health_missing": (
        "The corpus records a source failure or undated-item drop but the briefing has no "
        "Corpus health section."
    ),
    "corpus_health_not_machine_readable": "The required health manifest is absent, malformed, or has the wrong shape.",
    "duplicate_failed_source": "A failed source occurs more than once in the health manifest.",
    "duplicate_undated_source": "A source with undated drops occurs more than once in the health manifest.",
    "duplicate_citation": "The exact same citation spelling is printed more than once within one topic.",
    "excluded_topic_without_link": "An exclusion-log entry lacks a required line containing a 🔗 citation.",
    "failed_source_unnamed": (
        "A corpus-recorded failed source is absent from the health manifest."
    ),
    "failed_source_status_mismatch": (
        "A known failed source is reported with a status different from the corpus record."
    ),
    "undated_source_count_mismatch": (
        "A source's reported undated-item count differs from the corpus record."
    ),
    "undated_source_unnamed": (
        "A source with corpus-recorded undated drops is absent from the health manifest."
    ),
    "unexpected_undated_source": (
        "The health manifest reports undated drops absent from the corpus."
    ),
    "feed_empty": "The XML is a valid RSS/Atom feed but contains no item or entry.",
    "feed_rejected": "The XML is malformed or contains a DOCTYPE, which the feed security contract rejects.",
    "feed_shape_unrecognized": "The XML is well formed but its root is neither RSS nor Atom feed.",
    "figure_supported_elsewhere": (
        "A numeric claim is absent from its cited excerpts but appears in a topically matching "
        "item elsewhere in the supplied corpus."
    ),
    "included_and_excluded": "The same corpus story appears in the briefing and its exclusion log.",
    "missing_discussion_link": "A cited Hacker News story omits its corpus-provided discussion URL.",
    "missing_section": "A configured briefing section or required Excluded Topics section is absent.",
    "over_consolidation": "Distinct stories or claims are incorrectly merged into one topic.",
    "repeated_topic": (
        "The same story is cited through distinct canonical-equivalent spellings or appears in "
        "multiple included topics."
    ),
    "slots_overfilled": "A section contains more included topics than its configured target.",
    "slots_underfilled": "A section contains fewer included topics than its configured target.",
    "topic_without_link": "An included topic lacks a required line containing a 🔗 citation.",
    "unexpected_failed_source": "The health manifest reports a source failure absent from the corpus.",
    "ungrounded_link": "An HTTP(S) destination in the output does not occur in the corpus.",
    "unsupported_claim": "The cited title and summary do not entail a material prose claim.",
    "unsupported_figure": "A numeric claim is not entailed by the cited title and summary.",
    "unsupported_quotation": "Quoted words are not supported verbatim by the cited title and summary.",
}


def _config_payload(config: Any) -> dict[str, Any]:
    return {
        "schema_version": config.schema_version,
        "sections": [section._asdict() for section in config.sections],
    }


def _blind_case(case: dict[str, Any], opaque_id: str) -> dict[str, Any]:
    if case["component"] == "checker":
        corpus, briefing, config = apply_variant(case["variant"])
        return {
            "case": opaque_id,
            "component": "briefing_contract_and_semantics",
            "config": _config_payload(config),
            "corpus": corpus,
            "briefing": briefing,
        }
    data = _xml_case(case["variant"])
    return {
        "case": opaque_id,
        "component": "feed_parser",
        "bytes_base64": base64.b64encode(data).decode("ascii"),
        "bytes_hex": data.hex(),
    }


def blinded_cases(suite: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    payloads = []
    mapping = {}
    for index, case in enumerate(suite["cases"], 1):
        opaque_id = f"case-{index:03d}"
        payloads.append(_blind_case(case, opaque_id))
        mapping[opaque_id] = case["id"]
    return payloads, mapping


def export_human_review_packet(
    output_dir: Path,
    suite_path: Path = DEFAULT_SUITE,
    *,
    case_ids: set[str] | None = None,
    seed: int | None = None,
) -> dict[str, Any]:
    """Write a randomized human-review packet without labels or predictions."""
    raw = suite_path.read_bytes()
    suite = json.loads(raw)
    if case_ids is not None:
        available_ids = {case["id"] for case in suite["cases"]}
        missing = case_ids - available_ids
        if missing:
            raise ValueError(f"unknown case IDs: {', '.join(sorted(missing))}")
        selected = [case for case in suite["cases"] if case["id"] in case_ids]
    else:
        selected = [
            case for case in suite["cases"] if case.get("label_status") == "provisional"
        ]
    if not selected:
        raise ValueError("human-review packet selection is empty")

    rng = random.Random(seed if seed is not None else secrets.randbits(128))
    rng.shuffle(selected)
    used_ids: set[str] = set()
    mapping: dict[str, str] = {}
    payloads: list[dict[str, Any]] = []
    for case in selected:
        while True:
            opaque_id = f"review-{rng.getrandbits(48):012x}"
            if opaque_id not in used_ids:
                used_ids.add(opaque_id)
                break
        mapping[opaque_id] = case["id"]
        payloads.append(_blind_case(case, opaque_id))

    output_dir.mkdir(parents=True, exist_ok=False)
    packet = {
        "schema_version": 1,
        "instructions": (
            "Review each case only against the supplied inputs and rubric. Return every applicable "
            "label, or an empty list for a valid case. Equivalent quantities and faithful "
            "paraphrases are supported. For feeds, decode according to the BOM/declaration; valid "
            "RSS and Atom may use UTF-8, UTF-16, or UTF-32, while every DOCTYPE is rejected."
        ),
        "label_rubric": LABEL_RUBRIC,
        "cases": payloads,
    }
    form = {
        "schema_version": 1,
        "attestation": {
            "reviewer_name": "",
            "reviewed_on": "",
            "not_involved_in_case_preparation_or_labeling": None,
            "did_not_inspect_current_labels_or_checker_predictions": None,
        },
        "reviews": [
            {"case": case["case"], "labels": [], "rationale": ""} for case in payloads
        ],
    }
    labels_by_case = {case["id"]: sorted(case["human_labels"]) for case in selected}
    answer_key = {
        "schema_version": 1,
        "notice": "Coordinator-only until the completed response and attestation are locked.",
        "mapping": mapping,
        "provisional_labels": {
            mapping[opaque_id]: labels_by_case[mapping[opaque_id]]
            for opaque_id in mapping
        },
    }
    packet_path = output_dir / "reviewer-packet.json"
    form_path = output_dir / "attestation-and-review-form.json"
    key_path = output_dir / "coordinator-only" / "answer-key.json"
    key_path.parent.mkdir()
    write_json_atomic(packet_path, packet)
    write_json_atomic(form_path, form)
    write_json_atomic(key_path, answer_key)
    manifest = {
        "schema_version": 1,
        "status": "awaiting_independent_human_review",
        "suite": portable_path(suite_path),
        "suite_sha256": sha256_bytes(raw),
        "case_count": len(selected),
        "reviewer_packet": packet_path.name,
        "reviewer_packet_sha256": sha256_bytes(packet_path.read_bytes()),
        "review_form": form_path.name,
        "review_form_sha256": sha256_bytes(form_path.read_bytes()),
        "coordinator_answer_key": "coordinator-only/answer-key.json",
        "coordinator_answer_key_sha256": sha256_bytes(key_path.read_bytes()),
        "notice": "Share only the reviewer packet and blank review form with the reviewer.",
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    return manifest


def _review_prompt(cases: list[dict[str, Any]]) -> str:
    return f"""You are an independent label reviewer for a news-briefing evaluation suite.
You have not been given case names, provisional gold labels, or checker predictions. Review only
the supplied inputs against the rubric. For each case, return every applicable label, or an empty
list when the case is valid. Treat corpus URL membership only as citation provenance; it does not
establish that prose is entailed. Entailment is limited to the cited items' titles and summaries.
Equivalent quantities and faithful paraphrases are supported. Distinct labels may co-occur.
For URL identity, preserve every non-tracking query parameter; normalize host case, query order,
trailing slashes, fragments, and only utm_*, fbclid, gclid, mc_cid, and mc_eid tracking fields.
Configured sections may begin with a level 2–4 Markdown heading or a bold header; a trailing
`(N slots)` or `(N stories)` suffix is ignored when matching its configured name.

For feeds, decode the supplied bytes according to the XML declaration/BOM. Well-formed RSS and
Atom are valid across UTF-8, UTF-16, and UTF-32. The explicit security contract rejects every
DOCTYPE even when the resulting XML would otherwise be well formed.

LABEL RUBRIC:
{json.dumps(LABEL_RUBRIC, indent=2, sort_keys=True)}

CASES:
{json.dumps(cases, indent=2, sort_keys=True, ensure_ascii=False)}

Return JSON only in this exact shape:
{{"reviews":[{{"case":"case-001","labels":["label"],"rationale":"brief evidence-based reason"}}]}}
Include exactly one review for every supplied case. Use only labels from the rubric.
"""


def _adjudication_prompt(cases: list[dict[str, Any]]) -> str:
    return f"""You are the disagreement adjudicator for independently reviewed benchmark labels.
For each case, decide the correct complete label set from the evidence and rubric. The two prior
label sets may both be wrong. Corpus URL membership proves only provenance, never entailment.
Equivalent quantities and faithful paraphrases are supported. Return a substantive rationale.
For URL identity, preserve every non-tracking query parameter; normalize host case, query order,
trailing slashes, fragments, and only utm_*, fbclid, gclid, mc_cid, and mc_eid tracking fields.
Configured sections may begin with a level 2–4 Markdown heading or a bold header; a trailing
`(N slots)` or `(N stories)` suffix is ignored when matching its configured name.

LABEL RUBRIC:
{json.dumps(LABEL_RUBRIC, indent=2, sort_keys=True)}

DISAGREEMENTS:
{json.dumps(cases, indent=2, sort_keys=True, ensure_ascii=False)}

Return JSON only in this exact shape:
{{"reviews":[{{"case":"case-001","labels":["label"],"rationale":"brief evidence-based reason"}}]}}
Include exactly one review for every supplied case. Use only labels from the rubric.
"""


def _parse_reviews(text: str, expected_ids: set[str]) -> dict[str, dict[str, Any]]:
    payload = parse_json_response(text, "review response")
    rows = payload.get("reviews") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("review response must contain a reviews array")
    parsed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each review must be an object")
        case_id = row.get("case")
        labels = row.get("labels")
        rationale = row.get("rationale")
        if case_id not in expected_ids or case_id in parsed:
            raise ValueError(f"unexpected or duplicate review case {case_id!r}")
        if not isinstance(labels, list) or any(label not in LABEL_RUBRIC for label in labels):
            raise ValueError(f"review {case_id!r} contains invalid labels")
        if len(labels) != len(set(labels)) or not isinstance(rationale, str) or not rationale.strip():
            raise ValueError(f"review {case_id!r} has duplicate labels or no rationale")
        parsed[case_id] = {"labels": sorted(labels), "rationale": rationale.strip()}
    if set(parsed) != expected_ids:
        missing = ", ".join(sorted(expected_ids - set(parsed)))
        raise ValueError(f"review response omitted case(s): {missing}")
    return parsed


def _generation_record(generation: Generation) -> dict[str, Any]:
    return generation.record()


def _review_batch(
    adapter: Adapter,
    prompt: str,
    checkpoint: Path,
    expected_ids: set[str],
) -> tuple[Generation, dict[str, dict[str, Any]], bool]:
    return checkpointed_generate(
        adapter,
        prompt,
        checkpoint,
        lambda text: _parse_reviews(text, expected_ids),
    )


def _portable_path(path: Path) -> str:
    """Compatibility alias for callers importing this module's path sanitizer."""
    return portable_path(path)


def run_label_review(
    reviewer: Adapter,
    adjudicator: Adapter | None,
    output_dir: Path,
    suite_path: Path = DEFAULT_SUITE,
    batch_size: int = 10,
    *,
    case_ids: set[str] | None = None,
) -> dict[str, Any]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    raw = suite_path.read_bytes()
    suite = json.loads(raw)
    selected_cases = (
        suite["cases"]
        if case_ids is None
        else [case for case in suite["cases"] if case["id"] in case_ids]
    )
    if case_ids is not None:
        missing = case_ids - {case["id"] for case in selected_cases}
        if missing:
            raise ValueError(f"unknown case IDs: {', '.join(sorted(missing))}")
    if not selected_cases:
        raise ValueError("label-review selection is empty")
    payloads, mapping = blinded_cases({"cases": selected_cases})
    prepared = {case["id"]: sorted(case["human_labels"]) for case in selected_cases}
    output_dir.mkdir(parents=True, exist_ok=True)
    reviewer_metadata = {
        "provider": reviewer.provider,
        "model": reviewer.model,
        "generation_controls": reviewer.generation_controls(),
        "prompt_sha256": sha256_bytes(_review_prompt([]).encode("utf-8")),
    }
    adjudicator_metadata = (
        None if adjudicator is None
        else {
            "provider": adjudicator.provider,
            "model": adjudicator.model,
            "generation_controls": adjudicator.generation_controls(),
            "prompt_sha256": sha256_bytes(_adjudication_prompt([]).encode("utf-8")),
        }
    )
    identity = {
        "schema_version": 4,
        "suite_sha256": sha256_bytes(raw),
        "selected_case_ids": sorted(case["id"] for case in selected_cases),
        "reviewer": reviewer_metadata,
        "adjudicator": adjudicator_metadata,
        "batch_size": batch_size,
    }
    identity_path = output_dir / "label-review-run.json"
    if identity_path.exists():
        existing_identity = json.loads(identity_path.read_text(encoding="utf-8"))
        if existing_identity != identity:
            raise ValueError("output directory belongs to a different label-review run")
    else:
        if any(output_dir.glob("reviewer-batch-*.json")) or any(
                output_dir.glob("adjudicator-batch-*.json")):
            raise ValueError("output directory has unbound label-review checkpoints")
        write_json_atomic(identity_path, identity)

    reviewer_rows: dict[str, dict[str, Any]] = {}
    reviewer_calls = []
    for batch_index, start in enumerate(range(0, len(payloads), batch_size), 1):
        batch = payloads[start:start + batch_size]
        generation, reviews, resumed = _review_batch(
            reviewer,
            _review_prompt(batch),
            output_dir / f"reviewer-batch-{batch_index:02d}.json",
            {case["case"] for case in batch},
        )
        reviewer_rows.update(reviews)
        reviewer_calls.append({
            "batch": batch_index,
            "cases": sorted(reviews),
            "resumed": resumed,
            "generation": _generation_record(generation),
        })

    disagreements = []
    payload_by_id = {case["case"]: case for case in payloads}
    for opaque_id, fixture_id in mapping.items():
        machine_labels = reviewer_rows[opaque_id]["labels"]
        if machine_labels != prepared[fixture_id]:
            disagreements.append({
                "input": payload_by_id[opaque_id],
                "provisional_labels": prepared[fixture_id],
                "reviewer_labels": machine_labels,
            })

    adjudicator_rows: dict[str, dict[str, Any]] = {}
    adjudicator_calls = []
    if adjudicator is not None:
        for batch_index, start in enumerate(range(0, len(disagreements), batch_size), 1):
            batch = disagreements[start:start + batch_size]
            expected = {row["input"]["case"] for row in batch}
            generation, reviews, resumed = _review_batch(
                adjudicator,
                _adjudication_prompt(batch),
                output_dir / f"adjudicator-batch-{batch_index:02d}.json",
                expected,
            )
            adjudicator_rows.update(reviews)
            adjudicator_calls.append({
                "batch": batch_index,
                "cases": sorted(reviews),
                "resumed": resumed,
                "generation": _generation_record(generation),
            })

    cases = []
    for opaque_id, fixture_id in mapping.items():
        reviewer_row = reviewer_rows[opaque_id]
        adjudicated = adjudicator_rows.get(opaque_id)
        cases.append({
            "fixture_id": fixture_id,
            "opaque_review_id": opaque_id,
            "provisional_labels": prepared[fixture_id],
            "reviewer_labels": reviewer_row["labels"],
            "reviewer_rationale": reviewer_row["rationale"],
            "exact_agreement": reviewer_row["labels"] == prepared[fixture_id],
            "adjudicator_labels": adjudicated["labels"] if adjudicated else None,
            "adjudicator_rationale": adjudicated["rationale"] if adjudicated else None,
            "machine_consensus_labels": (
                adjudicated["labels"] if adjudicated
                else reviewer_row["labels"] if reviewer_row["labels"] == prepared[fixture_id]
                else None
            ),
        })
    result = {
        "schema_version": 1,
        "status": (
            "machine_review_complete_human_approval_required"
            if adjudicator is not None
            else "blinded_review_complete_adjudication_not_run_human_approval_required"
        ),
        "suite": portable_path(suite_path),
        "suite_sha256": identity["suite_sha256"],
        "reviewer": reviewer_metadata,
        "adjudicator": identity["adjudicator"],
        "case_count": len(cases),
        "exact_agreements": sum(case["exact_agreement"] for case in cases),
        "disagreements_found": len(disagreements),
        "disagreements_adjudicated": len(adjudicator_rows),
        "notice": "Model review is additional evidence and does not satisfy independent human approval.",
        "cases": cases,
        "reviewer_calls": reviewer_calls,
        "adjudicator_calls": adjudicator_calls,
    }
    destination = output_dir / "label-review.json"
    write_json_atomic(destination, result)
    return result
