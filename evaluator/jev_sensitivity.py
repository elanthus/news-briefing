"""Measure how often the Jev plus Decider review flags seeded prose-support errors.

The fixture holds real briefing topics with the frozen evidence they cited. Each
topic has an untouched control and three seeded edits of its prose: unsupported,
strengthened, and reversed. ``run`` asks Jev the three production prose-support
questions for every item, in production-shaped batches, and asks Decider to
confirm each flag in isolation. ``summarize`` reports detection and miss rates per
seeded kind and the flag rate on controls, with Wilson intervals.

Run with ``python3 -S -m evaluator.jev_sensitivity run|summarize ...``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import utc_now, write_json_atomic
from agent_runner.decisions import DECIDER_MODEL, JEV_MODEL, MAX_REQUEST_BYTES, DecisionsClient, DecisionsJudge
from agent_runner.hashing import sha256_bytes
from agent_runner.jev_review import MAX_CALLS, _payload
from agent_runner.models import ProviderError

from evaluator.metrics import wilson_interval

FIXTURE_SCHEMA_VERSION = 1
PROTOCOL_SCHEMA_VERSION = 1
RESULTS_SCHEMA_VERSION = 1
KINDS = ("control", "unsupported", "strengthened", "reversed")
SEEDED_KINDS = KINDS[1:]
EXPECTED_CHECK = {
    "control": None,
    "unsupported": "unsupported_claim",
    "strengthened": "strengthened_claim",
    "reversed": "reversed_claim",
}
CLAIM_CHECKS = ("unsupported_claim", "strengthened_claim", "reversed_claim")
SPLITS = ("measured", "dry_run")
# agent_runner.jev_review.review_run's default flag and confirmation cutoff for claim checks.
THRESHOLD = 0.8
# The production spending guard's per-byte input rate; reported cost accumulates separately.
ESTIMATED_USD_PER_REQUEST_BYTE = 0.042 / 1_000_000
MAX_BATCH_QUESTIONS = 50


def apply_edit(prose: str, ops: list[dict[str, str]]) -> str:
    """Reproduce a seeded item's prose from its topic's prose and recorded edit."""
    for op in ops:
        if op.get("op") == "append" and set(op) == {"op", "text"}:
            prose += op["text"]
        elif op.get("op") == "replace" and set(op) == {"op", "old", "new"}:
            if prose.count(op["old"]) != 1:
                raise ValueError(f"edit target must occur exactly once: {op['old']!r}")
            prose = prose.replace(op["old"], op["new"])
        else:
            raise ValueError(f"invalid edit operation: {op!r}")
    return prose


def load_fixture(path: Path) -> dict[str, Any]:
    """Read the one fixture format and check every item against its topic."""
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict) or fixture.get("schema_version") != FIXTURE_SCHEMA_VERSION:
        raise ValueError(f"seed fixture schema_version must be {FIXTURE_SCHEMA_VERSION}")
    if fixture.get("kinds") != list(KINDS):
        raise ValueError(f"seed fixture kinds must be {list(KINDS)}")
    topics = {topic["topic_id"]: topic for topic in fixture["topics"]}
    if len(topics) != len(fixture["topics"]):
        raise ValueError("seed fixture topic ids must be unique")
    for topic in topics.values():
        if topic.get("split") not in SPLITS:
            raise ValueError(f"topic {topic['topic_id']} has an invalid split")
        if not topic.get("evidence") or not topic.get("headline") or not topic.get("prose"):
            raise ValueError(f"topic {topic['topic_id']} needs headline, prose, and evidence")
    seen: dict[str, list[str]] = {topic_id: [] for topic_id in topics}
    for item in fixture["items"]:
        topic = topics.get(item.get("topic_id"))
        if topic is None or item.get("kind") not in KINDS:
            raise ValueError(f"item {item.get('item_id')!r} has an unknown topic or kind")
        if item["item_id"] != f"{item['topic_id']}-{item['kind']}":
            raise ValueError(f"item {item['item_id']!r} id does not name its topic and kind")
        if item.get("expected_check") != EXPECTED_CHECK[item["kind"]]:
            raise ValueError(f"item {item['item_id']} expected_check does not match its kind")
        if (item["kind"] == "control") != (item["edit"] == []):
            raise ValueError(f"item {item['item_id']}: only controls have no edit")
        if apply_edit(topic["prose"], item["edit"]) != item["prose"]:
            raise ValueError(f"item {item['item_id']} prose does not follow from its edit")
        if item["kind"] != "control" and item["prose"] == topic["prose"]:
            raise ValueError(f"item {item['item_id']} edit leaves the prose unchanged")
        seen[item["topic_id"]].append(item["kind"])
    for topic_id, kinds in seen.items():
        if sorted(kinds) != sorted(KINDS):
            raise ValueError(f"topic {topic_id} must have exactly one item of each kind")
    return fixture


def arms(fixture: dict[str, Any], split: str) -> list[list[dict[str, Any]]]:
    """Assign one item per topic to each of four pseudo-briefings in a Latin square.

    Topic ``i`` contributes kind ``KINDS[(i + arm) % 4]`` to ``arm``, so every arm
    mixes the kinds and no request ever holds two versions of the same topic.
    """
    topics = [topic for topic in fixture["topics"] if topic["split"] == split]
    by_key = {(item["topic_id"], item["kind"]): item for item in fixture["items"]}
    return [
        [by_key[(topic["topic_id"], KINDS[(index + arm) % len(KINDS)])] for index, topic in enumerate(topics)]
        for arm in range(len(KINDS))
    ]


def arm_tasks(fixture: dict[str, Any], arm: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build the production review state for each item and prose-support check."""
    topics = {topic["topic_id"]: topic for topic in fixture["topics"]}
    tasks = []
    for index, item in enumerate(arm):
        topic = topics[item["topic_id"]]
        state_topic = {
            "position": {"bucket": "sections", "section": topic["source"]["section"], "index": index},
            "included": True,
            "headline": topic["headline"],
            "prose": item["prose"],
            "evidence": topic["evidence"],
        }
        for check in CLAIM_CHECKS:
            tasks.append({"check": check, "item_id": item["item_id"], "state": {"topic": state_topic}})
    return tasks


def batches(tasks: list[dict[str, Any]], model: str) -> list[list[dict[str, Any]]]:
    """Pack tasks in order under the production question and request-byte bounds."""
    packed: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for task in tasks:
        tentative = [*current, task]
        if len(tentative) > MAX_BATCH_QUESTIONS or _payload(tentative, model)[2] > MAX_REQUEST_BYTES:
            if not current:
                raise ValueError(f"one check for {task['item_id']} exceeds the request bound")
            packed.append(current)
            current = [task]
        else:
            current = tentative
    if current:
        packed.append(current)
    return packed


def run(
    fixture_path: Path,
    split: str,
    output_dir: Path,
    *,
    protocol_path: Path,
    cost_ceiling_usd: float,
    judge: DecisionsJudge | None = None,
    confirmer: DecisionsJudge | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """Review every item of ``split`` and confirm each flag, stopping at the cost ceiling."""
    fixture = load_fixture(fixture_path)
    fixture_sha256 = hashlib.sha256(fixture_path.read_bytes()).hexdigest()
    protocol = json.loads(protocol_path.read_text(encoding="utf-8"))
    version = protocol.get("schema_version") if isinstance(protocol, dict) else None
    if type(version) is not int or version != PROTOCOL_SCHEMA_VERSION:
        raise ValueError(f"protocol schema_version must be {PROTOCOL_SCHEMA_VERSION}")
    pinned = protocol.get("fixture", {}).get("sha256")
    if pinned != fixture_sha256:
        raise ValueError("seed fixture differs from the protocol hash")
    authorized = protocol.get("status") == "frozen_for_run" and protocol.get("authorization", {}).get("authorized_at")
    if split == "measured" and not authorized:
        raise ValueError("the measured run requires a frozen, authorized protocol")
    if split not in SPLITS:
        raise ValueError(f"split must be one of {SPLITS}")
    if not 0 < cost_ceiling_usd <= 1:
        raise ValueError("cost ceiling must be in (0, 1] USD")
    output_dir.mkdir(parents=True, exist_ok=False)
    path = output_dir / "results.json"
    first: DecisionsJudge = judge or DecisionsClient(JEV_MODEL)
    second: DecisionsJudge = confirmer or judge or DecisionsClient(DECIDER_MODEL)
    results: dict[str, Any] = {
        "schema_version": RESULTS_SCHEMA_VERSION, "split": split, "status": "running",
        "fixture_sha256": fixture_sha256,
        "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
        "model": first.model, "confirmation_model": second.model, "threshold": THRESHOLD,
        "cost_ceiling_usd": cost_ceiling_usd, "started_at": utc_now(), "calls": [], "checks": [],
    }
    write_json_atomic(path, results)
    total = 0.0

    def call(phase: str, arm: int, batch: list[dict[str, Any]], by: DecisionsJudge) -> dict[str, Any] | None:
        nonlocal total
        state, questions, size = _payload(batch, by.model)
        if len(results["calls"]) >= MAX_CALLS or total + size * ESTIMATED_USD_PER_REQUEST_BYTE > cost_ceiling_usd:
            results["stop_reason"] = "call or estimated cost budget reached"
            return None
        record: dict[str, Any] = {
            "index": len(results["calls"]), "phase": phase, "arm": arm, "checks": len(batch),
            "requested_model": by.model, "status": "in_flight", "started_at": utc_now(),
            "request_sha256": sha256_bytes(json.dumps(
                {"model": by.model, "state": state, "questions": questions}, ensure_ascii=True).encode("ascii")),
        }
        results["calls"].append(record)
        write_json_atomic(path, results)
        started = time.monotonic()
        answer = by.evaluate(state, questions, timeout=timeout)
        # Provider request ids are redacted from published evidence, so they are not kept.
        record.update({key: answer.get(key) for key in ("model", "cost_usd", "input_tokens", "output_tokens")})
        record.update(status="complete", completed_at=utc_now(),
                      latency_ms=answer.get("latency_ms", (time.monotonic() - started) * 1000))
        if answer.get("cost_usd") is None:
            # Keep the answer this call returned, then refuse every further call.
            results["stop_reason"] = "provider did not report cost; further calls refused"
        else:
            total += answer["cost_usd"]
        write_json_atomic(path, results)
        return answer

    try:
        stopped = False
        for arm_index, arm in enumerate(arms(fixture, split)):
            for batch in batches(arm_tasks(fixture, arm), first.model):
                answer = call("review", arm_index, batch, first)
                if answer is not None:
                    for offset, task in enumerate(batch):
                        probability = answer["probabilities"][f"q{offset}"]
                        results["checks"].append({
                            "item_id": task["item_id"], "check": task["check"], "arm": arm_index,
                            "probability": probability, "flagged": probability >= THRESHOLD,
                            "confirmation_probability": None,
                            "confirmation_label": "unconfirmed" if probability >= THRESHOLD else "not_flagged",
                            "task": task,
                        })
                if answer is None or "stop_reason" in results:
                    stopped = True
                    break
            if stopped:
                break
        if not stopped:
            for row in results["checks"]:
                if not row["flagged"]:
                    continue
                answer = call("confirmation", row["arm"], [row["task"]], second)
                if answer is not None:
                    row["confirmation_probability"] = answer["probabilities"]["q0"]
                    row["confirmation_label"] = (
                        "confirmed" if row["confirmation_probability"] >= THRESHOLD else "disputed"
                    )
                if answer is None or "stop_reason" in results:
                    stopped = True
                    break
        planned = sum(len(arm) for arm in arms(fixture, split)) * len(CLAIM_CHECKS)
        results["status"] = "complete" if not stopped and len(results["checks"]) == planned else "partial"
    except (ProviderError, ValueError, KeyError, TypeError) as exc:
        results["status"] = "failed" if not results["checks"] else "partial"
        results["stop_reason"] = type(exc).__name__
        if results["calls"] and results["calls"][-1]["status"] == "in_flight":
            results["calls"][-1]["status"] = "failed_billing_unknown"
    for row in results["checks"]:
        row.pop("task", None)
    results["completed_at"] = utc_now()
    results["reported_cost_usd"] = sum(call.get("cost_usd") or 0 for call in results["calls"])
    results["unknown_cost_calls"] = sum(call.get("cost_usd") is None for call in results["calls"])
    write_json_atomic(path, results)
    return results


def _rate(successes: int, trials: int) -> dict[str, Any]:
    return {"successes": successes, "trials": trials,
            "rate": successes / trials if trials else None, "ci95_wilson": wilson_interval(successes, trials)}


def summarize(fixture: dict[str, Any], results: dict[str, Any]) -> dict[str, Any]:
    """Score each completely reviewed item; incompletely reviewed items stay visible, uncounted."""
    if results.get("schema_version") != RESULTS_SCHEMA_VERSION:
        raise ValueError(f"results schema_version must be {RESULTS_SCHEMA_VERSION}")
    split = results["split"]
    topics = {topic["topic_id"] for topic in fixture["topics"] if topic["split"] == split}
    items = [item for item in fixture["items"] if item["topic_id"] in topics]
    rows: dict[str, dict[str, dict[str, Any]]] = {}
    for row in results["checks"]:
        rows.setdefault(row["item_id"], {})[row["check"]] = row
    scored: dict[str, dict[str, Any]] = {}
    unreviewed = []
    for item in items:
        checks = rows.get(item["item_id"], {})
        complete = set(checks) == set(CLAIM_CHECKS) and all(
            not row["flagged"] or row["confirmation_label"] in {"confirmed", "disputed"} for row in checks.values()
        )
        if not complete:
            unreviewed.append(item["item_id"])
            continue
        expected = item["expected_check"]
        scored[item["item_id"]] = {
            "kind": item["kind"], "topic_id": item["topic_id"],
            "max_probability": max(row["probability"] for row in checks.values()),
            "jev_flagged": any(row["flagged"] for row in checks.values()),
            "confirmed": any(row["confirmation_label"] == "confirmed" for row in checks.values()),
            "jev_flagged_expected": bool(expected and checks[expected]["flagged"]),
            "confirmed_expected": bool(expected and checks[expected]["confirmation_label"] == "confirmed"),
        }
    by_kind: dict[str, Any] = {}
    for kind in KINDS:
        values = [row for row in scored.values() if row["kind"] == kind]
        n = len(values)
        summary: dict[str, Any] = {
            "items": n,
            "jev_flagged_any": _rate(sum(row["jev_flagged"] for row in values), n),
            "confirmed_any": _rate(sum(row["confirmed"] for row in values), n),
            "median_max_probability": statistics.median(row["max_probability"] for row in values) if values else None,
        }
        if kind != "control":
            summary["jev_flagged_expected_check"] = _rate(sum(row["jev_flagged_expected"] for row in values), n)
            summary["confirmed_expected_check"] = _rate(sum(row["confirmed_expected"] for row in values), n)
            summary["miss_after_confirmation"] = _rate(sum(not row["confirmed"] for row in values), n)
            summary["miss_jev_alone"] = _rate(sum(not row["jev_flagged"] for row in values), n)
            controls = {row["topic_id"]: row for row in scored.values() if row["kind"] == "control"}
            paired = [(row, controls[row["topic_id"]]) for row in values if row["topic_id"] in controls]
            summary["paired_with_control"] = {
                "topics": len(paired),
                "seeded_probability_higher": sum(a["max_probability"] > b["max_probability"] for a, b in paired),
                "tied": sum(a["max_probability"] == b["max_probability"] for a, b in paired),
                "control_probability_higher": sum(a["max_probability"] < b["max_probability"] for a, b in paired),
            }
        by_kind[kind] = summary
    return {
        "schema_version": RESULTS_SCHEMA_VERSION, "split": split, "run_status": results["status"],
        "fixture_sha256": results["fixture_sha256"], "protocol_sha256": results["protocol_sha256"],
        "model": results["model"], "confirmation_model": results["confirmation_model"],
        "threshold": results["threshold"], "planned_items": len(items), "scored_items": len(scored),
        "unreviewed_items": sorted(unreviewed), "by_kind": by_kind,
        "reported_cost_usd": results["reported_cost_usd"], "unknown_cost_calls": results["unknown_cost_calls"],
        "calls": len(results["calls"]),
    }


def _cell(rate: dict[str, Any]) -> str:
    interval = rate["ci95_wilson"]
    if interval is None:
        return "not estimable"
    low, high = (bound * 100 for bound in interval)
    return f"{rate['successes']}/{rate['trials']}; {rate['rate'] * 100:.1f}% [{low:.1f}, {high:.1f}]"


def markdown_summary(summary: dict[str, Any]) -> str:
    lines = [
        "| Kind | Items | Jev flags any check | Confirmed by Decider | Jev flags the matching check "
        "| Miss after confirmation |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for kind in KINDS:
        row = summary["by_kind"][kind]
        matching = _cell(row["jev_flagged_expected_check"]) if kind != "control" else "not applicable"
        miss = _cell(row["miss_after_confirmation"]) if kind != "control" else "not applicable"
        lines.append(f"| {kind} | {row['items']} | {_cell(row['jev_flagged_any'])} | "
                     f"{_cell(row['confirmed_any'])} | {matching} | {miss} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    run_parser = commands.add_parser("run", help="review a fixture split with Jev and Decider")
    run_parser.add_argument("--fixture", type=Path, required=True)
    run_parser.add_argument("--protocol", type=Path, required=True)
    run_parser.add_argument("--split", choices=SPLITS, required=True)
    run_parser.add_argument("--output-dir", type=Path, required=True)
    run_parser.add_argument("--cost-ceiling-usd", type=float, required=True)
    summary_parser = commands.add_parser("summarize", help="score a results file")
    summary_parser.add_argument("--fixture", type=Path, required=True)
    summary_parser.add_argument("--results", type=Path, required=True)
    summary_parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.command == "run":
        results = run(args.fixture, args.split, args.output_dir, protocol_path=args.protocol,
                      cost_ceiling_usd=args.cost_ceiling_usd)
        print(json.dumps({key: results.get(key) for key in (
            "status", "stop_reason", "reported_cost_usd", "unknown_cost_calls")}, indent=2))
        return 0 if results["status"] == "complete" else 1
    summary = summarize(load_fixture(args.fixture), json.loads(args.results.read_text(encoding="utf-8")))
    write_json_atomic(args.output, summary)
    sys.stdout.write(markdown_summary(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
