"""Blinded, URL-scoped review of meaning-preservation propositions.

Deterministic case assertions answer observable questions such as whether a
story was cited or placed in the configured section. They cannot fairly decide
whether a paraphrase preserved a particular meaning. This module supplies that
second layer without making exact fixture wording part of the generation
oracle or exposing hidden propositions to the correction pass.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
import eval_briefing
from agent_runner.checkpoint import sha256_bytes, write_json_atomic, write_text_atomic

from evaluator.adapters import Adapter
from evaluator.judge_io import (
    checkpointed_generate,
    judgment_identity,
    parse_json_response,
    reviewer_identity,
    verified_generation_artifact,
)
from evaluator.report import markdown_report, summarize
from evaluator.scoring import _semantic_adjudication_template, apply_adjudications

JUDGMENTS = {"conveyed", "not_conveyed", "unclear"}


def _parse_judgment(text: str) -> dict[str, str]:
    """Validate one fence-tolerant semantic judgment response."""
    payload = parse_json_response(text, "semantic judgment")
    if not isinstance(payload, dict) or set(payload) != {"judgment", "rationale"}:
        raise ValueError("semantic judgment must contain exactly judgment and rationale")
    if not isinstance(payload["judgment"], str) or payload["judgment"] not in JUDGMENTS:
        raise ValueError(
            "semantic judgment must be 'conveyed', 'not_conveyed', or 'unclear'"
        )
    if not isinstance(payload["rationale"], str) or not payload["rationale"].strip():
        raise ValueError("semantic judgment rationale must be a non-empty string")
    return payload


def _judgment_prompt(
    evidence: str, topics: list[dict[str, str]], proposition: str
) -> str:
    """Build a blinded prompt that considers every topic citing the required URL."""
    rendered_topics = "\n\n".join(
        f"TOPIC {index}:\n{topic['title']}\n{topic['prose']}"
        for index, topic in enumerate(topics, 1)
    )
    return f"""You are an independent semantic reviewer for a news-briefing evaluation.
You are not told which model, prompt, provider, or test case produced the topic. Use only the
evidence below; do not use outside knowledge.

Decide whether at least one GENERATED TOPIC conveys the REQUIRED PROPOSITION. Exact words are
neither required nor preferred: a faithful paraphrase passes. Mark "not_conveyed" only if every
topic omits, contradicts, or materially weakens the proposition. Use "unclear" only when the
topics are genuinely ambiguous and need human review. Do not award credit merely because the
proposition appears in the evidence; it must be communicated by a generated topic.

EVIDENCE (title and possibly truncated feed summary):
{evidence}

GENERATED TOPICS CITING THE REQUIRED URL:
{rendered_topics}

REQUIRED PROPOSITION:
{proposition}

Return JSON only:
{{"judgment":"conveyed","rationale":"brief evidence-based reason"}}
The judgment must be exactly "conveyed", "not_conveyed", or "unclear".
"""


def _judge_call(
    adapter: Adapter, prompt: str, checkpoint: Path
) -> tuple[dict[str, str], bool]:
    """Return one semantic judgment and whether it came from a checkpoint."""
    _, judgment, resumed = checkpointed_generate(
        adapter, prompt, checkpoint, _parse_judgment
    )
    return judgment, resumed


def _assessment_inputs(
    manifest: dict[str, Any], run_dir: Path
) -> list[dict[str, Any]]:
    """Resolve frozen inputs and verify adjudication topics against final output."""
    suite_path = Path(manifest["suite"])
    if not suite_path.is_absolute():
        suite_path = run_dir / suite_path
    suite_bytes = suite_path.read_bytes()
    suite_hash = sha256_bytes(suite_bytes)
    if manifest.get("suite_sha256") not in (None, suite_hash):
        raise ValueError("semantic source suite differs from the frozen generation suite")
    suite = json.loads(suite_bytes)
    cases = {case["id"]: case for case in suite["cases"]}
    configs = {
        case["id"]: briefing_config.load_config(suite_path.parent / case["config"])
        for case in suite["cases"]
    }
    for case in suite["cases"]:
        if case.get("matched_pair"):
            configs[f"{case['id']}__clean"] = configs[case["id"]]
            cases[f"{case['id']}__clean"] = case
    inputs = []
    for row in manifest["results"]:
        relative_path = row.get("semantic_adjudication")
        if not relative_path or not isinstance(row.get("final"), dict):
            continue
        payload = json.loads((run_dir / relative_path).read_bytes())
        case_dir = run_dir / row["artifact_dir"]
        corpus_bytes = verified_generation_artifact(case_dir / "corpus.json", row, "trial_corpus_sha256")
        final_bytes = verified_generation_artifact(case_dir / "final.md", row, "final_output_sha256")
        evidence = eval_briefing.corpus_evidence(json.loads(corpus_bytes))
        sections = eval_briefing.parse_briefing(
            final_bytes.decode("utf-8"), configs[row["case_id"]]
        )
        case = cases[row["case_id"]]
        config_hash = sha256_bytes((suite_path.parent / case["config"]).read_bytes())
        recorded_config_hash = manifest.get("config_sha256", {}).get(case["config"])
        if recorded_config_hash not in (None, config_hash):
            raise ValueError("semantic configuration differs from the frozen generation configuration")
        expected_judgments = _semantic_adjudication_template(case, sections)["judgments"]
        if [(item["url"], item["proposition"]) for item in payload.get("judgments", [])] != [
            (item["url"], item["proposition"]) for item in expected_judgments
        ]:
            raise ValueError("semantic adjudication requirements differ from the frozen suite")
        judgments = []
        for item in payload.get("judgments", []):
            expected = _semantic_adjudication_template(
                {"must_convey": [{"url": item["url"], "propositions": [item["proposition"]]}]},
                sections,
            )["judgments"][0]["topics"]
            if item.get("topics") != expected:
                raise ValueError("semantic adjudication topics do not match frozen final output")
            canonical = corpus_schema.canonicalize_url(item["url"])
            judgments.append({
                "url": item["url"],
                "proposition": item["proposition"],
                "topics": expected,
                "evidence": evidence.get(canonical, ""),
            })
        inputs.append({
            "artifact_dir": row["artifact_dir"],
            "relative_path": relative_path,
            "corpus_sha256": sha256_bytes(corpus_bytes),
            "final_sha256": sha256_bytes(final_bytes),
            "suite_sha256": suite_hash,
            "config_sha256": config_hash,
            "judgments": judgments,
        })
    return inputs


def run_semantic_judging(
    manifest_path: Path,
    judge: Adapter,
    output_dir: Path,
) -> dict[str, Any]:
    """Resume this assessment or independently judge in a fresh output directory.

    Labels embedded in generation artifacts are never reused as model responses.
    Only the default assessment directory updates the generation run's report;
    other directories preserve separately attributed independent assessments.
    """
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    run_dir = manifest_path.parent
    inputs = _assessment_inputs(manifest, run_dir)
    identity = {
        "schema_version": 3,
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": sha256_bytes(manifest_bytes),
        "judge": reviewer_identity(judge),
        "inputs": inputs,
        "prompt_template_sha256": sha256_bytes(_judgment_prompt("", [], "").encode()),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    identity_path = output_dir / "semantic-judging-run.json"
    if identity_path.exists():
        if json.loads(identity_path.read_bytes()) != identity:
            raise ValueError("output directory belongs to a different semantic-judge run")
    else:
        if any(output_dir.glob("judgment-*.json")):
            raise ValueError("output directory has unbound semantic-judge checkpoints")
        write_json_atomic(identity_path, identity)

    model_calls = 0
    legacy_labels_ignored = 0
    records: list[dict[str, Any]] = []
    assessed_payloads = []
    for source in inputs:
        adjudication_path = run_dir / source["relative_path"]
        payload = json.loads(adjudication_path.read_bytes())
        for index, (item, frozen) in enumerate(
            zip(payload["judgments"], source["judgments"], strict=True), 1
        ):
            legacy_labels_ignored += (
                item.get("judgment") in JUDGMENTS and not item.get("provenance")
            )
            topics = frozen["topics"]
            support = frozen["evidence"]
            resumed = False
            if not topics:
                judgment = {
                    "judgment": "not_conveyed",
                    "rationale": "The generated briefing has no topic citing the required URL.",
                }
                reviewer = {"kind": "deterministic", "name": "missing-topic"}
            elif not support:
                judgment = {
                    "judgment": "unclear",
                    "rationale": "No corpus evidence could be resolved for the required URL.",
                }
                reviewer = {"kind": "deterministic", "name": "missing-evidence"}
            else:
                key = f"{source['artifact_dir']}__{index:03d}"
                safe_key = "".join(
                    char if char.isalnum() or char in "-_." else "_" for char in key
                )
                checkpoint = output_dir / f"judgment-{safe_key}.json"
                prompt = _judgment_prompt(support, topics, frozen["proposition"])
                judgment, resumed = _judge_call(judge, prompt, checkpoint)
                model_calls += not resumed
                reviewer = {"kind": "model", **reviewer_identity(judge)}
            provenance = {
                "input_sha256": sha256_bytes(json.dumps(frozen, sort_keys=True).encode()),
                "corpus_sha256": source["corpus_sha256"],
                "final_sha256": source["final_sha256"],
            }
            if reviewer["kind"] == "model":
                provenance.update(judgment_identity(judge, prompt))
            item.update({
                "judgment": judgment["judgment"],
                "notes": judgment["rationale"],
                "reviewer": reviewer,
                "provenance": provenance,
            })
            records.append({
                "artifact_dir": source["artifact_dir"],
                "index": index,
                "judgment": judgment["judgment"],
                "rationale": judgment["rationale"],
                "reviewer": reviewer,
                "provenance": provenance,
                "resumed": resumed,
            })
        assessed_payloads.append((adjudication_path, payload))

    updates_run = output_dir.resolve() == (run_dir / "semantic-judgments").resolve()
    if updates_run:
        for adjudication_path, payload in assessed_payloads:
            write_json_atomic(adjudication_path, payload)
        apply_adjudications(manifest, run_dir)
        report = summarize(manifest, run_dir)
        write_json_atomic(run_dir / "report.json", report)
        write_text_atomic(run_dir / "report.md", markdown_report(report))
    result = {
        "schema_version": 2,
        "manifest": str(manifest_path),
        "requested_judge": reviewer_identity(judge),
        "assessment_kind": "independent_machine_review",
        "updates_generation_report": updates_run,
        "legacy_labels_ignored": legacy_labels_ignored,
        "judgments_available": len(records),
        "model_calls": model_calls,
        "counts": {
            label: sum(record["judgment"] == label for record in records)
            for label in sorted(JUDGMENTS)
        },
        "records": records,
    }
    write_json_atomic(output_dir / "semantic-judgments.json", result)
    return result
