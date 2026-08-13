"""Blinded pairwise LLM-judge comparison of briefing prose across a completed run.

The deterministic checker and the case oracles in runner.py both validate
*routing*: is a citation grounded, does a topic land in the right section, is
a story included or excluded correctly. None of that touches whether the
prose written about a correctly-routed story is any good. `check_claims_supported`
in eval_briefing.py says so directly: "Entailment can't be settled without a
second model, so this does not try." This module is that second model,
scoped narrowly to relative comparison rather than absolute scoring, because
pairwise preference is more reliable than an LLM rubric score in isolation.

It reuses the blinded-identifier and fence-tolerant-JSON conventions from
label_review.py, and reads its inputs from the artifacts a completed
`evaluator run` already wrote to disk (final.md, corpus.json per case-trial).
"""

from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Any

import briefing_config
import corpus_schema
import eval_briefing

from evaluator.adapters import Adapter, Generation
from evaluator.metrics import rate

QUALITY_AXES = ("faithfulness", "salience", "concision", "coherence")

AXIS_RUBRIC = {
    "faithfulness": (
        "Does the topic prose stay true to its evidence, without adding, dropping, or reversing "
        "a claim the evidence does not support? The evidence is a mechanically extracted, possibly "
        "truncated publisher feed blurb (title + summary), not the full article — judge faithfulness "
        "to that blurb only, never to outside knowledge of the underlying story."
    ),
    "salience": (
        "Does the prose lead with the most newsworthy fact in the evidence, rather than a "
        "secondary or incidental detail?"
    ),
    "concision": (
        "Is the prose appropriately terse for a briefing entry, free of padding, throat-clearing, "
        "or restating the headline in the body text?"
    ),
    "coherence": (
        "Is the prose grammatical, readable in one pass, and free of awkward phrasing?"
    ),
}

_FLIP = {"a": "b", "b": "a", "tie": "tie"}


def _group_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (row["provider"], row["model"], row["prompt_version"])


def _group_label(key: tuple[str, str, str]) -> str:
    return "__".join(key)


def _case_configs(suite: dict[str, Any], suite_path: Path) -> dict[str, briefing_config.BriefingConfig]:
    configs: dict[str, briefing_config.BriefingConfig] = {}
    for case in suite["cases"]:
        configs[case["id"]] = briefing_config.load_config(suite_path.parent / case["config"])
    return configs


def _topics(run_dir: Path, row: dict[str, Any], config: briefing_config.BriefingConfig) -> list[dict[str, Any]]:
    """Every judgeable topic in one case-trial's final output: title, prose, and its evidence."""
    case_dir = run_dir / row["artifact_dir"]
    text = (case_dir / "final.md").read_text(encoding="utf-8")
    corpus = json.loads((case_dir / "corpus.json").read_text(encoding="utf-8"))
    evidence = eval_briefing.corpus_evidence(corpus)
    sections = eval_briefing.parse_briefing(text, config)
    topics = []
    for name, bucket in sections.items():
        if name in {eval_briefing.EXCLUDED, eval_briefing.CORPUS_HEALTH}:
            continue
        for title, prose, links in zip(
            bucket.get("topics", []), bucket.get("topic_texts", []), bucket.get("topic_links", []), strict=True
        ):
            canonical = frozenset(corpus_schema.canonicalize_url(url) for url in links)
            support = " ".join(dict.fromkeys(evidence[url] for url in canonical if url in evidence)).strip()
            if not canonical or not support:
                continue
            topics.append({"section": name, "title": title, "prose": prose, "urls": canonical, "evidence": support})
    return topics


def matched_pairs(
    manifest: dict[str, Any], run_dir: Path, configs: dict[str, briefing_config.BriefingConfig]
) -> list[dict[str, Any]]:
    """Every pair of same-story topics written by two different (provider, model, prompt) groups.

    Same case, same trial, matched by exact canonical-URL-set identity so the two texts being
    compared are demonstrably about the same corpus item(s) rather than merely the same case.
    """
    by_case_trial: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in manifest["results"]:
        if isinstance(row.get("final"), dict) and row.get("artifact_dir"):
            by_case_trial[(row["case_id"], row["trial"])].append(row)

    pairs: list[dict[str, Any]] = []
    for (case_id, trial), rows in sorted(by_case_trial.items()):
        config = configs[case_id]
        topics_by_group: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in rows:
            key = _group_key(row)
            if key not in topics_by_group:
                topics_by_group[key] = _topics(run_dir, row, config)
        keys = sorted(topics_by_group)
        for index, key_a in enumerate(keys):
            for key_b in keys[index + 1:]:
                for topic_a in topics_by_group[key_a]:
                    for topic_b in topics_by_group[key_b]:
                        if topic_a["urls"] != topic_b["urls"]:
                            continue
                        pairs.append({
                            "case_id": case_id,
                            "trial": trial,
                            "section": topic_a["section"],
                            "urls": sorted(topic_a["urls"]),
                            "evidence": topic_a["evidence"],
                            "group_a": key_a,
                            "group_b": key_b,
                            "topic_a": {"title": topic_a["title"], "prose": topic_a["prose"]},
                            "topic_b": {"title": topic_b["title"], "prose": topic_b["prose"]},
                        })
    return pairs


def _judgment_prompt(pair: dict[str, Any], swapped: bool) -> str:
    left = pair["topic_b"] if swapped else pair["topic_a"]
    right = pair["topic_a"] if swapped else pair["topic_b"]
    return f"""You are an independent prose-quality judge for a news-briefing evaluation suite.
You are not told which model or prompt produced either option, and must not guess. Judge only
the two texts below against the evidence, which is the sole source of truth.

EVIDENCE (mechanically extracted, possibly truncated publisher feed blurb — not the full article):
{pair["evidence"]}

OPTION A:
{left["title"]}
{left["prose"]}

OPTION B:
{right["title"]}
{right["prose"]}

AXIS RUBRIC:
{json.dumps(AXIS_RUBRIC, indent=2, sort_keys=True)}

For each axis, pick the option that does better, or "tie" if you genuinely cannot distinguish
them. Also give an overall preference. Judge faithfulness only against the evidence above, never
against outside knowledge of the underlying story.

Return JSON only in this exact shape:
{{"faithfulness":"a","salience":"a","concision":"a","coherence":"a","overall":"a",
"rationale":"brief evidence-based reason"}}
Every value except "rationale" must be exactly "a", "b", or "tie".
"""


def _parse_judgment(text: str) -> dict[str, str]:
    value = text.strip()
    if value.startswith("```"):
        lines = value.splitlines()
        if lines[-1].strip() != "```":
            raise ValueError("judgment response has an unterminated code fence")
        value = "\n".join(lines[1:-1])
    elif not value.startswith("{"):
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start:end + 1]
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        preview = text.strip().replace("\n", " ")[:160]
        raise ValueError(f"judgment response is not valid JSON: {exc}; response starts {preview!r}") from exc
    if not isinstance(payload, dict):
        raise ValueError("judgment response must be a JSON object")
    expected_keys = {*QUALITY_AXES, "overall", "rationale"}
    if set(payload) != expected_keys:
        raise ValueError(f"judgment response must contain exactly {sorted(expected_keys)}")
    for axis in (*QUALITY_AXES, "overall"):
        if payload[axis] not in {"a", "b", "tie"}:
            raise ValueError(f"judgment {axis!r} must be 'a', 'b', or 'tie'")
    if not isinstance(payload["rationale"], str) or not payload["rationale"].strip():
        raise ValueError("judgment rationale must be a non-empty string")
    return payload


def _judge_call(adapter: Adapter, prompt: str, checkpoint: Path) -> dict[str, str]:
    """Resumable single judge call: a prior valid checkpoint is reused without a paid re-call."""
    if checkpoint.exists():
        try:
            payload = json.loads(checkpoint.read_text(encoding="utf-8"))
            generation = Generation(**payload)
            return _parse_judgment(generation.text)
        except (OSError, TypeError, ValueError):
            pass
    generation = adapter.generate(prompt)
    checkpoint.write_text(json.dumps(generation.record(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return _parse_judgment(generation.text)


def _pct(metric: dict[str, Any]) -> str:
    if metric["rate"] is None:
        return "n/a"
    low, high = metric["ci95_wilson"]
    return f"{metric['rate'] * 100:.1f}% ({low * 100:.1f}–{high * 100:.1f}%; {metric['successes']}/{metric['trials']})"


def _identity(manifest_path: Path, judge: Adapter, sample: int | None, seed: int) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "manifest": str(manifest_path.resolve()),
        "judge": {"provider": judge.provider, "model": judge.model},
        "sample": sample,
        "seed": seed,
    }


def run_quality_judging(
    manifest_path: Path,
    judge: Adapter,
    output_dir: Path,
    suite_path: Path | None = None,
    sample: int | None = None,
    seed: int = 0,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_dir = manifest_path.parent
    resolved_suite_path = suite_path or Path(manifest["suite"])
    suite = json.loads(resolved_suite_path.read_text(encoding="utf-8"))
    configs = _case_configs(suite, resolved_suite_path)

    all_pairs = matched_pairs(manifest, run_dir, configs)
    pairs = all_pairs
    if sample is not None and sample < len(pairs):
        pairs = random.Random(seed).sample(pairs, sample)

    output_dir.mkdir(parents=True, exist_ok=True)
    identity = _identity(manifest_path, judge, sample, seed)
    identity_path = output_dir / "quality-judging-run.json"
    if identity_path.exists():
        existing = json.loads(identity_path.read_text(encoding="utf-8"))
        if existing != identity:
            raise ValueError("output directory belongs to a different judge-quality run")
    else:
        if any(output_dir.glob("*-original.json")) or any(output_dir.glob("*-swapped.json")):
            raise ValueError("output directory has unbound judge-quality checkpoints")
        identity_path.write_text(json.dumps(identity, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    records = []
    for index, pair in enumerate(pairs, 1):
        raw_key = (
            f"{pair['case_id']}__t{pair['trial']}__{_group_label(pair['group_a'])}"
            f"__vs__{_group_label(pair['group_b'])}__{index:04d}"
        )
        safe_key = "".join(char if char.isalnum() or char in "-_." else "_" for char in raw_key)
        original = _judge_call(judge, _judgment_prompt(pair, swapped=False), output_dir / f"{safe_key}-original.json")
        swapped = _judge_call(judge, _judgment_prompt(pair, swapped=True), output_dir / f"{safe_key}-swapped.json")
        record = {
            "pair_key": safe_key,
            "case_id": pair["case_id"],
            "trial": pair["trial"],
            "section": pair["section"],
            "urls": pair["urls"],
            "group_a": list(pair["group_a"]),
            "group_b": list(pair["group_b"]),
            "original": original,
            "swapped": swapped,
        }
        for axis in (*QUALITY_AXES, "overall"):
            record[f"{axis}_consistent"] = original[axis] == _FLIP[swapped[axis]]
        records.append(record)

    win_counts: dict[tuple[tuple[str, str, str], str], dict[str, int]] = defaultdict(
        lambda: {"wins": 0, "losses": 0, "ties": 0}
    )
    consistency_counts = {axis: {"agree": 0, "total": 0} for axis in (*QUALITY_AXES, "overall")}
    for record in records:
        group_a = tuple(record["group_a"])
        group_b = tuple(record["group_b"])
        for axis in (*QUALITY_AXES, "overall"):
            outcome = record["original"][axis]
            if outcome == "a":
                win_counts[(group_a, axis)]["wins"] += 1
                win_counts[(group_b, axis)]["losses"] += 1
            elif outcome == "b":
                win_counts[(group_b, axis)]["wins"] += 1
                win_counts[(group_a, axis)]["losses"] += 1
            else:
                win_counts[(group_a, axis)]["ties"] += 1
                win_counts[(group_b, axis)]["ties"] += 1
            consistency_counts[axis]["total"] += 1
            consistency_counts[axis]["agree"] += record[f"{axis}_consistent"]

    win_rates = []
    for (group, axis), counts in sorted(win_counts.items()):
        decided = counts["wins"] + counts["losses"]
        win_rates.append({
            "provider": group[0],
            "model": group[1],
            "prompt_version": group[2],
            "axis": axis,
            "win_rate_excluding_ties": rate(counts["wins"], decided),
            "wins": counts["wins"],
            "losses": counts["losses"],
            "ties": counts["ties"],
        })

    result = {
        "schema_version": 1,
        "manifest": str(manifest_path),
        "judge": {"provider": judge.provider, "model": judge.model},
        "pairs_available": len(all_pairs),
        "pairs_judged": len(pairs),
        "position_consistency": {
            axis: rate(counts["agree"], counts["total"]) for axis, counts in consistency_counts.items()
        },
        "win_rates": win_rates,
        "records": records,
    }
    (output_dir / "quality-judgments.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "quality-report.md").write_text(markdown_quality_report(result), encoding="utf-8")
    return result


def markdown_quality_report(result: dict[str, Any]) -> str:
    lines = [
        "# Prose-quality pairwise judgments",
        "",
        f"Judge: {result['judge']['provider']} / {result['judge']['model']}",
        f"Pairs judged: {result['pairs_judged']} of {result['pairs_available']} available",
        "",
        "Every pair is judged twice, with option order swapped the second time. A judgment is only "
        "trustworthy on an axis where the two calls agree after accounting for the swap; a low "
        "consistency rate means the win rates below are not yet reliable for that axis.",
        "",
        "## Position-bias consistency (same pair, order swapped)",
        "",
        "| Axis | Agreement |",
        "|---|---:|",
    ]
    for axis in (*QUALITY_AXES, "overall"):
        lines.append(f"| {axis} | {_pct(result['position_consistency'][axis])} |")
    lines += [
        "",
        "## Win rate by group (original-order calls only; ties excluded from the rate)",
        "",
        "| Provider / model / prompt | Axis | Win rate | W-L-T |",
        "|---|---|---:|---:|",
    ]
    for row in result["win_rates"]:
        label = f"{row['provider']} / {row['model']} / {row['prompt_version']}"
        lines.append(
            f"| {label} | {row['axis']} | {_pct(row['win_rate_excluding_ties'])} | "
            f"{row['wins']}-{row['losses']}-{row['ties']} |"
        )
    lines.append("")
    return "\n".join(lines)
