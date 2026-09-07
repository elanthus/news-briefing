"""Evaluator grounding judge regression coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.adapters import (
    Adapter,
    Generation,
)
from evaluator.grounding_machine_review import (
    _checkpoint_cost as _grounding_checkpoint_cost,
)
from evaluator.grounding_machine_review import (
    _load_review_map as _load_grounding_review_map,
)
from evaluator.grounding_machine_review import (
    _parse_reviews as _parse_grounding_machine_reviews,
)
from evaluator.grounding_machine_review import _review_batch, run_grounding_machine_review


class FakeGroundingJudgeAdapter(Adapter):
    provider = "offline-grounding-judge"

    def __init__(
        self,
        model: str,
        labels: dict[str, bool],
        controls: dict[str, Any] | None = None,
        cost_usd: float | None = 0.01,
    ):
        super().__init__(model)
        self.labels = labels
        self.controls = controls
        self.cost_usd = cost_usd
        self.calls = 0

    def generation_controls(self) -> dict[str, Any]:
        return self.controls or super().generation_controls()

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        raw_topics = prompt.split("TOPICS:\n", 1)[1].split("\n\nReturn JSON only", 1)[0]
        topics = json.loads(raw_topics)
        return Generation(
            text=json.dumps({
                "reviews": [
                    {
                        "review_id": topic["review_id"],
                        "grounding_error": self.labels[topic["review_id"]],
                        "rationale": "Fixture evidence supports this decision.",
                    }
                    for topic in topics
                ],
            }),
            latency_ms=1.0,
            input_tokens=100,
            output_tokens=25,
            cost_usd=self.cost_usd,
        )



class GroundingMachineReviewTest(unittest.TestCase):
    def test_parser_requires_exact_ids_boolean_labels_and_rationales(self) -> None:
        parsed = _parse_grounding_machine_reviews(
            '{"reviews":[{"review_id":"ground-00001","grounding_error":false,'
            '"rationale":"The claim is supported."}]}',
            ["ground-00001"],
        )
        self.assertFalse(parsed[0]["grounding_error"])
        unfenced_close = _parse_grounding_machine_reviews(
            '```json\n{"reviews":[{"review_id":"ground-00001",'
            '"grounding_error":false,"rationale":"Supported."}]}',
            ["ground-00001"],
        )
        self.assertFalse(unfenced_close[0]["grounding_error"])
        with self.assertRaisesRegex(ValueError, "grounding_error must be true or false"):
            _parse_grounding_machine_reviews(
                '{"reviews":[{"review_id":"ground-00001","grounding_error":null,'
                '"rationale":"Unclear."}]}',
                ["ground-00001"],
            )
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            _parse_grounding_machine_reviews('{"reviews":[]}', ["ground-00001"])
        with self.assertRaisesRegex(ValueError, "rationale must be non-empty"):
            _parse_grounding_machine_reviews(
                '{"reviews":[{"review_id":"ground-00001","grounding_error":false,'
                '"rationale":"   "}]}',
                ["ground-00001"],
            )
        with self.assertRaisesRegex(ValueError, "IDs must be unique strings"):
            _parse_grounding_machine_reviews(
                '{"reviews":[{"review_id":"ground-00001","grounding_error":false,'
                '"rationale":"Supported."},{"review_id":"ground-00001",'
                '"grounding_error":true,"rationale":"Unsupported."}]}',
                ["ground-00001"],
            )
        with self.assertRaisesRegex(ValueError, "must contain exactly review_id"):
            _parse_grounding_machine_reviews(
                '{"reviews":[{"review_id":"ground-00001","grounding_error":false,'
                '"rationale":"Supported.","extra":1}]}',
                ["ground-00001"],
            )

    def test_review_map_is_validated_before_judging(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            review_map_path = Path(directory) / "review-map.json"
            review_map_path.write_text(
                json.dumps({
                    "primary": [{
                        "review_id": "ground-00001",
                        "artifact_dir": "artifact-a",
                    }],
                    "double": [],
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "primary entries are invalid"):
                _load_grounding_review_map(
                    review_map_path,
                    {"primary": ["ground-00001"], "double": []},
                )

    def test_machine_review_is_separate_resumable_and_costed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest = {
                "results": [
                    {
                        "artifact_dir": "artifact-a",
                        "provider": "generation-provider",
                        "model": "generation-model",
                        "prompt_version": "production",
                    },
                    {
                        "artifact_dir": "artifact-b",
                        "provider": "generation-provider",
                        "model": "generation-model",
                        "prompt_version": "candidate",
                    },
                ],
            }
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
            packet_dir = temporary / "packets"
            packet_dir.mkdir()
            rubric = {
                "grounding_error_true": "A material claim is unsupported.",
                "grounding_error_false": "Every material claim is supported.",
                "scope": "Use only supplied evidence.",
            }
            primary_reviews = [
                {
                    "review_id": "ground-00001",
                    "section": "News",
                    "title": "A",
                    "prose": "Supported A.",
                    "citations": ["https://example.test/a"],
                    "evidence": [{"url": "https://example.test/a", "feed_evidence": "Supported A."}],
                },
                {
                    "review_id": "ground-00002",
                    "section": "News",
                    "title": "B",
                    "prose": "Unsupported B.",
                    "citations": ["https://example.test/b"],
                    "evidence": [{"url": "https://example.test/b", "feed_evidence": "Different."}],
                },
            ]
            def write_packet(name: str, reviews: list[dict[str, Any]]) -> None:
                (packet_dir / name).write_text(
                    json.dumps({
                        "schema_version": 1,
                        "manifest_sha256": manifest_sha,
                        "rubric": rubric,
                        "reviews": reviews,
                    }),
                    encoding="utf-8",
                )
            write_packet("reviewer-primary.json", primary_reviews)
            write_packet(
                "reviewer-double.json",
                [{**primary_reviews[0], "review_id": "double-00001"}],
            )
            (packet_dir / "review-map.json").write_text(json.dumps({
                "primary": [
                    {
                        "review_id": "ground-00001",
                        "artifact_dir": "artifact-a",
                        "topic_index": 1,
                    },
                    {
                        "review_id": "ground-00002",
                        "artifact_dir": "artifact-b",
                        "topic_index": 1,
                    },
                ],
                "double": [{
                    "review_id": "double-00001",
                    "artifact_dir": "artifact-a",
                    "topic_index": 1,
                }],
            }), encoding="utf-8")

            primary = FakeGroundingJudgeAdapter(
                "primary-judge", {"ground-00001": False, "ground-00002": True}
            )
            audit = FakeGroundingJudgeAdapter("audit-judge", {"double-00001": False})
            output = temporary / "machine-review"
            result = run_grounding_machine_review(
                manifest_path,
                packet_dir,
                primary,
                audit,
                output,
                batch_size=1,
                cost_ceiling_usd=1.0,
                cost_headroom_usd=0.1,
            )

            self.assertFalse(result["human_review"])
            self.assertEqual(result["primary"]["reviewed_topics"], 2)
            self.assertEqual(result["primary"]["grounding_errors"]["successes"], 1)
            self.assertEqual(result["audit"]["agreement_with_primary"]["successes"], 1)
            self.assertAlmostEqual(result["observed_cost_usd"], 0.03)
            self.assertEqual(len(result["groups"]), 2)
            self.assertEqual(primary.calls, 2)
            self.assertEqual(audit.calls, 1)

            resumed = run_grounding_machine_review(
                manifest_path,
                packet_dir,
                primary,
                audit,
                output,
                batch_size=1,
                cost_ceiling_usd=1.0,
                cost_headroom_usd=0.1,
            )
            self.assertEqual(resumed["observed_cost_usd"], result["observed_cost_usd"])
            self.assertEqual(primary.calls, 2)
            self.assertEqual(audit.calls, 1)

            changed_primary = FakeGroundingJudgeAdapter(
                "primary-judge",
                {"ground-00001": False, "ground-00002": True},
                controls={"temperature": 0.5},
            )
            with self.assertRaisesRegex(
                ValueError, "different machine grounding review"
            ):
                run_grounding_machine_review(
                    manifest_path,
                    packet_dir,
                    changed_primary,
                    audit,
                    output,
                    batch_size=1,
                    cost_ceiling_usd=1.0,
                    cost_headroom_usd=0.1,
                )

    def test_machine_review_stops_before_reserved_cost_headroom(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            output = temporary / "output"
            output.mkdir()
            generation = Generation(text="not valid", latency_ms=1, cost_usd=0.91)
            (output / "primary-batch-0001-attempt-01.json").write_text(
                json.dumps(generation.record()), encoding="utf-8"
            )
            adapter = FakeGroundingJudgeAdapter("judge", {"ground-00001": False})
            with self.assertRaisesRegex(RuntimeError, r"preserve \$0.10 headroom"):
                _review_batch(
                    adapter,
                    "prompt",
                    ["ground-00001"],
                    output,
                    "primary-batch-0001",
                    cost_ceiling_usd=1.0,
                    cost_headroom_usd=0.1,
                )
            self.assertEqual(adapter.calls, 0)

    def test_unpriced_provider_error_can_resume_but_success_is_not_checkpointed(self) -> None:
        prompt = 'TOPICS:\n[{"review_id":"ground-00001"}]\n\nReturn JSON only'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "primary-batch-0001-attempt-01.json").write_text(
                json.dumps({"kind": "provider_error", "cost_usd": None}),
                encoding="utf-8",
            )
            adapter = FakeGroundingJudgeAdapter(
                "judge", {"ground-00001": False}
            )
            labels, resumed = _review_batch(
                adapter,
                prompt,
                ["ground-00001"],
                output,
                "primary-batch-0001",
                cost_ceiling_usd=1.0,
                cost_headroom_usd=0.1,
            )
            self.assertFalse(resumed)
            self.assertFalse(labels[0]["grounding_error"])
            self.assertAlmostEqual(_grounding_checkpoint_cost(output), 0.01)

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            adapter = FakeGroundingJudgeAdapter(
                "judge", {"ground-00001": False}, cost_usd=None
            )
            with self.assertRaisesRegex(ValueError, "has no reported cost"):
                _review_batch(
                    adapter,
                    prompt,
                    ["ground-00001"],
                    output,
                    "primary-batch-0001",
                    cost_ceiling_usd=1.0,
                    cost_headroom_usd=0.1,
                )
            self.assertEqual(list(output.iterdir()), [])

