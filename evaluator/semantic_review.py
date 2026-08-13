"""Blinded, URL-scoped review of meaning-preservation propositions.

Deterministic case assertions answer observable questions such as whether a
story was cited or placed in the configured section. They cannot fairly decide
whether a paraphrase preserved a particular meaning. This module supplies that
second layer without making exact fixture wording part of the generation
oracle or exposing hidden propositions to the correction pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import corpus_schema
import eval_briefing

from evaluator.adapters import Adapter, Generation
from evaluator.runner import apply_adjudications, markdown_report, summarize

JUDGMENTS = {"conveyed", "not_conveyed", "unclear"}


def _write_json_atomic(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _parse_judgment(text: str) -> dict[str, str]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if not lines or lines[-1].strip() != "```":
            raise ValueError("semantic judgment has an unterminated code fence")
        value = "\n".join(lines[1:-1])
    elif not value.startswith("{"):
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start:end + 1]
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"semantic judgment is not valid JSON: {exc}") from exc
    if not isinstance(payload, dict) or set(payload) != {"judgment", "rationale"}:
        raise ValueError("semantic judgment must contain exactly judgment and rationale")
    if payload["judgment"] not in JUDGMENTS:
        raise ValueError(
            "semantic judgment must be 'conveyed', 'not_conveyed', or 'unclear'"
        )
    if not isinstance(payload["rationale"], str) or not payload["rationale"].strip():
        raise ValueError("semantic judgment rationale must be a non-empty string")
    return payload


def _judgment_prompt(evidence: str, topic: dict[str, str], proposition: str) -> str:
    return f"""You are an independent semantic reviewer for a news-briefing evaluation.
You are not told which model, prompt, provider, or test case produced the topic. Use only the
evidence below; do not use outside knowledge.

Decide whether the GENERATED TOPIC conveys the REQUIRED PROPOSITION. Exact words are neither
required nor preferred: a faithful paraphrase passes. Mark "not_conveyed" if the proposition is
omitted, contradicted, or materially weakened. Use "unclear" only when the topic is genuinely
ambiguous and needs human review. Do not award credit merely because the proposition appears in
the evidence; it must be communicated by the generated topic.

EVIDENCE (title and possibly truncated feed summary):
{evidence}

GENERATED TOPIC:
{topic['title']}
{topic['prose']}

REQUIRED PROPOSITION:
{proposition}

Return JSON only:
{{"judgment":"conveyed","rationale":"brief evidence-based reason"}}
The judgment must be exactly "conveyed", "not_conveyed", or "unclear".
"""


def _judge_call(
    adapter: Adapter, prompt: str, checkpoint: Path
) -> tuple[dict[str, str], bool]:
    if checkpoint.exists():
        try:
            generation = Generation(**json.loads(checkpoint.read_text(encoding="utf-8")))
            return _parse_judgment(generation.text), True
        except (OSError, TypeError, ValueError):
            pass
    generation = adapter.generate(prompt)
    _write_json_atomic(checkpoint, generation.record())
    return _parse_judgment(generation.text), False


def _identity(manifest_path: Path, manifest_bytes: bytes, judge: Adapter) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manifest": str(manifest_path.resolve()),
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "judge": {"provider": judge.provider, "model": judge.model},
    }


def run_semantic_judging(
    manifest_path: Path,
    judge: Adapter,
    output_dir: Path,
) -> dict[str, Any]:
    """Judge every unresolved must-convey proposition and refresh the run report."""
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    run_dir = manifest_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    identity = _identity(manifest_path, manifest_bytes, judge)
    identity_path = output_dir / "semantic-judging-run.json"
    if identity_path.exists():
        if json.loads(identity_path.read_text(encoding="utf-8")) != identity:
            raise ValueError("output directory belongs to a different semantic-judge run")
    else:
        if any(output_dir.glob("judgment-*.json")):
            raise ValueError("output directory has unbound semantic-judge checkpoints")
        _write_json_atomic(identity_path, identity)

    available = 0
    model_calls = 0
    records: list[dict[str, Any]] = []
    for row in manifest["results"]:
        relative_path = row.get("semantic_adjudication")
        if not relative_path or not isinstance(row.get("final"), dict):
            continue
        adjudication_path = run_dir / relative_path
        payload = json.loads(adjudication_path.read_text(encoding="utf-8"))
        case_dir = run_dir / row["artifact_dir"]
        corpus = json.loads((case_dir / "corpus.json").read_text(encoding="utf-8"))
        evidence = eval_briefing.corpus_evidence(corpus)
        changed = False
        for index, item in enumerate(payload.get("judgments", []), 1):
            available += 1
            if item.get("judgment") in JUDGMENTS:
                records.append({
                    "artifact_dir": row["artifact_dir"],
                    "index": index,
                    "judgment": item["judgment"],
                    "resumed": True,
                })
                continue

            topic = item.get("topic")
            canonical = corpus_schema.canonicalize_url(item["url"])
            support = evidence.get(canonical, "")
            if not isinstance(topic, dict):
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
                key = f"{row['artifact_dir']}__{index:03d}"
                safe_key = "".join(
                    char if char.isalnum() or char in "-_." else "_" for char in key
                )
                checkpoint = output_dir / f"judgment-{safe_key}.json"
                judgment, resumed = _judge_call(
                    judge,
                    _judgment_prompt(support, topic, item["proposition"]),
                    checkpoint,
                )
                model_calls += not resumed
                reviewer = {
                    "kind": "model",
                    "provider": judge.provider,
                    "model": judge.model,
                }

            item["judgment"] = judgment["judgment"]
            item["notes"] = judgment["rationale"]
            item["reviewer"] = reviewer
            changed = True
            records.append({
                "artifact_dir": row["artifact_dir"],
                "index": index,
                "judgment": judgment["judgment"],
                "resumed": False,
            })
        if changed:
            _write_json_atomic(adjudication_path, payload)

    apply_adjudications(manifest, run_dir)
    report = summarize(manifest)
    _write_json_atomic(run_dir / "report.json", report)
    (run_dir / "report.md").write_text(markdown_report(report), encoding="utf-8")

    result = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "judge": {"provider": judge.provider, "model": judge.model},
        "judgments_available": available,
        "model_calls": model_calls,
        "counts": {
            label: sum(record["judgment"] == label for record in records)
            for label in sorted(JUDGMENTS)
        },
        "records": records,
    }
    _write_json_atomic(output_dir / "semantic-judgments.json", result)
    return result
