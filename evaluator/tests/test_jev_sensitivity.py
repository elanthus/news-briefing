"""The seeded reviewer-sensitivity harness, checked with fake judges and no network."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from typing import Any

from agent_runner.decisions import JEV_MODEL, MAX_REQUEST_BYTES
from agent_runner.jev_review import RUBRICS, _payload
from evaluator.jev_sensitivity import (
    CLAIM_CHECKS,
    KINDS,
    apply_edit,
    arm_tasks,
    arms,
    batches,
    load_fixture,
    markdown_summary,
    run,
    summarize,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jev-sensitivity-seeds.json"
# One phrase unique to each production prose-support rubric.
RUBRIC_PHRASES = {
    "unsupported_claim": "assert any material fact unsupported",
    "strengthened_claim": "strengthen certainty",
    "reversed_claim": "contradict or reverse",
}


class FakeJudge:
    """Answer by looking up which seeded item and rubric each question carries."""

    def __init__(self, model: str, fixture: dict[str, Any], flags: dict[tuple[str, str], float],
                 cost: float | None = 0.001):
        self.model = model
        self.kind_by_prose = {item["prose"]: item["kind"] for item in fixture["items"]}
        self.flags = flags
        self.cost = cost
        self.requests: list[dict[str, Any]] = []

    def evaluate(self, state: dict[str, Any], questions: dict[str, Any], *, timeout: int = 30) -> dict[str, Any]:
        self.requests.append(state)
        probabilities = {}
        for index, check_state in enumerate(state["checks"]):
            instructions = questions[f"q{index}"]["instructions"]
            check = next(name for name, phrase in RUBRIC_PHRASES.items() if phrase in instructions)
            kind = self.kind_by_prose[check_state["topic"]["prose"]]
            probabilities[f"q{index}"] = self.flags.get((kind, check), 0.05)
        return {"probabilities": probabilities, "model": self.model, "cost_usd": self.cost,
                "provider_request_id": "gen-secret", "input_tokens": 10, "output_tokens": 1, "latency_ms": 1.0}


def _protocol(directory: Path, *, authorized: bool) -> Path:
    path = directory / "protocol.json"
    path.write_text(json.dumps({
        "protocol_id": "fixture",
        "status": "frozen_for_run" if authorized else "draft_pending_freeze",
        "authorization": {"authorized_at": "2026-11-19T00:00:00Z" if authorized else None},
        "fixture": {"path": "fixture", "sha256": hashlib.sha256(FIXTURE.read_bytes()).hexdigest()},
    }), encoding="utf-8")
    return path


class SeedFixtureTests(unittest.TestCase):
    def test_each_rubric_phrase_identifies_one_claim_check(self) -> None:
        for check, phrase in RUBRIC_PHRASES.items():
            self.assertEqual([name for name in CLAIM_CHECKS if phrase in RUBRICS[name]], [check])

    def test_fixture_derives_every_item_from_its_topic(self) -> None:
        fixture = load_fixture(FIXTURE)
        splits = Counter(topic["split"] for topic in fixture["topics"])
        self.assertEqual(splits, {"measured": 60, "dry_run": 3})
        self.assertEqual(Counter(item["kind"] for item in fixture["items"]), {kind: 63 for kind in KINDS})
        topics = {topic["topic_id"]: topic for topic in fixture["topics"]}
        for item in fixture["items"]:
            with self.subTest(item=item["item_id"]):
                self.assertEqual(apply_edit(topics[item["topic_id"]]["prose"], item["edit"]), item["prose"])
                ops = {op["op"] for op in item["edit"]}
                self.assertEqual(ops, {"control": set(), "unsupported": {"append"}}.get(item["kind"], {"replace"}))

    def test_fixture_refuses_an_item_that_does_not_follow_from_its_edit(self) -> None:
        fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        tampered = copy.deepcopy(fixture)
        tampered["items"][1]["prose"] += " Extra."
        for broken, message in (
            (tampered, "does not follow from its edit"),
            ({**fixture, "schema_version": 2}, "schema_version must be 1"),
        ):
            with tempfile.TemporaryDirectory() as directory, self.subTest(message=message):
                path = Path(directory) / "fixture.json"
                path.write_text(json.dumps(broken), encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    load_fixture(path)

    def test_arms_hold_one_version_of_each_topic_and_mix_the_kinds(self) -> None:
        fixture = load_fixture(FIXTURE)
        measured = arms(fixture, "measured")
        placed = [item["item_id"] for arm in measured for item in arm]
        self.assertEqual(len(placed), 240)
        self.assertEqual(len(set(placed)), 240)
        for arm in measured:
            self.assertEqual(len({item["topic_id"] for item in arm}), 60)
            self.assertEqual(Counter(item["kind"] for item in arm), {kind: 15 for kind in KINDS})
            for batch in batches(arm_tasks(fixture, arm), JEV_MODEL):
                self.assertLessEqual(len(batch), 50)
                self.assertLessEqual(_payload(batch, JEV_MODEL)[2], MAX_REQUEST_BYTES)
                topics = {task["item_id"].split("-")[0] for task in batch}
                kinds_per_topic = Counter(
                    (task["item_id"].split("-")[0], task["item_id"].split("-")[1]) for task in batch
                )
                self.assertEqual(len({topic for topic, _ in kinds_per_topic}), len(topics))


class SensitivityRunTests(unittest.TestCase):
    def _run(self, directory: Path, *, split: str = "dry_run", authorized: bool = False,
             jev_flags: dict[tuple[str, str], float] | None = None,
             decider_flags: dict[tuple[str, str], float] | None = None,
             ceiling: float = 1.0, cost: float | None = 0.001) -> tuple[dict[str, Any], FakeJudge, FakeJudge]:
        fixture = load_fixture(FIXTURE)
        jev = FakeJudge("typesafe/jev-1.14", fixture, jev_flags or {}, cost)
        decider = FakeJudge("perplexity/pplx-decider-v1.2", fixture, decider_flags or {}, cost)
        results = run(FIXTURE, split, directory / "out", protocol_path=_protocol(directory, authorized=authorized),
                      cost_ceiling_usd=ceiling, judge=jev, confirmer=decider)
        return results, jev, decider

    def test_flags_are_confirmed_in_isolation_and_scored_per_kind(self) -> None:
        jev_flags = {("reversed", "reversed_claim"): 0.95, ("strengthened", "unsupported_claim"): 0.9,
                     ("control", "unsupported_claim"): 0.85}
        decider_flags = {("reversed", "reversed_claim"): 0.9, ("strengthened", "unsupported_claim"): 0.2}
        with tempfile.TemporaryDirectory() as directory:
            results, jev, decider = self._run(Path(directory), jev_flags=jev_flags, decider_flags=decider_flags)
            saved = json.loads((Path(directory) / "out" / "results.json").read_text(encoding="utf-8"))
        self.assertEqual(results["status"], "complete")
        self.assertEqual(saved["status"], "complete")
        self.assertNotIn("gen-secret", json.dumps(saved))
        self.assertEqual(len(results["checks"]), 36)
        # Three topics, one confirmation per flagged check, each request holding one check.
        self.assertEqual([len(state["checks"]) for state in decider.requests], [1] * 9)
        summary = summarize(load_fixture(FIXTURE), results)
        by_kind = summary["by_kind"]
        self.assertEqual(summary["scored_items"], 12)
        self.assertEqual(by_kind["reversed"]["confirmed_any"]["successes"], 3)
        self.assertEqual(by_kind["reversed"]["confirmed_expected_check"]["successes"], 3)
        self.assertEqual(by_kind["strengthened"]["jev_flagged_any"]["successes"], 3)
        self.assertEqual(by_kind["strengthened"]["jev_flagged_expected_check"]["successes"], 0)
        self.assertEqual(by_kind["strengthened"]["miss_after_confirmation"]["successes"], 3)
        self.assertEqual(by_kind["unsupported"]["miss_jev_alone"]["successes"], 3)
        # The control flag was not confirmed, so Decider removed the false alarm.
        self.assertEqual(by_kind["control"]["jev_flagged_any"]["successes"], 3)
        self.assertEqual(by_kind["control"]["confirmed_any"]["successes"], 0)
        self.assertEqual(by_kind["reversed"]["paired_with_control"]["seeded_probability_higher"], 3)
        self.assertIn("| reversed | 3 |", markdown_summary(summary))

    def test_measured_run_requires_an_authorized_protocol_with_the_fixture_hash(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "frozen, authorized protocol"):
                self._run(Path(directory), split="measured")
            protocol = _protocol(Path(directory), authorized=True)
            data = json.loads(protocol.read_text(encoding="utf-8"))
            data["fixture"]["sha256"] = "0" * 64
            protocol.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "differs from the protocol hash"):
                run(FIXTURE, "dry_run", Path(directory) / "out", protocol_path=protocol, cost_ceiling_usd=1.0)

    def test_budget_and_unknown_cost_stop_further_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            results, jev, _ = self._run(Path(directory), split="measured", authorized=True,
                                        cost=0.3, ceiling=0.5)
        self.assertEqual(results["status"], "partial")
        self.assertEqual(results["stop_reason"], "call or estimated cost budget reached")
        self.assertEqual(len(jev.requests), 2)
        with tempfile.TemporaryDirectory() as directory:
            results, jev, decider = self._run(Path(directory), cost=None,
                                              jev_flags={("strengthened", "strengthened_claim"): 0.95})
        self.assertEqual(results["status"], "partial")
        self.assertIn("did not report cost", results["stop_reason"])
        self.assertEqual((len(jev.requests), len(decider.requests)), (1, 0))
        # The answer of the unknown-cost call is kept: the first arm's nine checks.
        self.assertEqual(len(results["checks"]), 9)
        self.assertEqual(results["unknown_cost_calls"], 1)
        summary = summarize(load_fixture(FIXTURE), results)
        # A flag never confirmed leaves its item unscored rather than counted as a miss.
        self.assertEqual(summary["scored_items"], 2)
        self.assertIn("T63-strengthened", summary["unreviewed_items"])
        self.assertEqual(summary["by_kind"]["strengthened"]["items"], 0)


EVIDENCE = Path(__file__).parents[2] / "docs" / "results" / "jev-sensitivity-v1-evidence"
PROTOCOL = Path(__file__).parents[1] / "protocols" / "jev-sensitivity-v1.json"


class PublishedSensitivityRunTests(unittest.TestCase):
    def test_bundle_matches_its_checksums_and_the_frozen_protocol(self) -> None:
        for line in (EVIDENCE / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
            digest, name = line.split("  ")
            with self.subTest(file=name):
                self.assertEqual(hashlib.sha256((EVIDENCE / name).read_bytes()).hexdigest(), digest)
        results = json.loads((EVIDENCE / "results.json").read_text(encoding="utf-8"))
        protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
        self.assertEqual(results["protocol_sha256"], hashlib.sha256(PROTOCOL.read_bytes()).hexdigest())
        self.assertEqual(results["fixture_sha256"], protocol["fixture"]["sha256"])
        self.assertEqual((results["split"], results["status"]), ("measured", "complete"))
        self.assertEqual(protocol["status"], "frozen_for_run")
        self.assertLess(protocol["authorization"]["authorized_at"], results["started_at"])

    def test_committed_summary_regenerates_from_the_committed_results(self) -> None:
        results = json.loads((EVIDENCE / "results.json").read_text(encoding="utf-8"))
        committed = json.loads((EVIDENCE / "summary.json").read_text(encoding="utf-8"))
        self.assertEqual(summarize(load_fixture(FIXTURE), results), committed)


if __name__ == "__main__":
    unittest.main()
