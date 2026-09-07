"""Evaluator grounding packets regression coverage."""
from __future__ import annotations

import json
import random
import tempfile
import unittest
from pathlib import Path

from evaluator.grounding_review import double_sample, export_grounding_review_packets


class GroundingReviewPacketTest(unittest.TestCase):
    def test_packet_blinds_model_prompt_and_stratifies_double_review(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            temporary = Path(temporary_dir)
            suite = {
                "cases": [{
                    "id": "utility-case",
                    "kind": "utility",
                    "family": "thin_evidence",
                    "config": "config.json",
                }],
            }
            (temporary / "suite.json").write_text(json.dumps(suite), encoding="utf-8")
            (temporary / "config.json").write_text(json.dumps({
                "schema_version": 1,
                "sections": [{
                    "name": "AI Dev Tools",
                    "group": None,
                    "target_stories": 3,
                    "corpus_categories": ["dev_community"],
                    "guidance": "AI",
                    "excluded_stories": 0,
                }],
            }), encoding="utf-8")
            artifact = temporary / "secret-model__secret-prompt__case"
            artifact.mkdir()
            (artifact / "final.md").write_text(
                "# Test\n\n## AI Dev Tools\n\n**Topic** — Supported claim.\n"
                "🔗 https://example.com/story\n"
                "🔗 https://example.com/missing\n",
                encoding="utf-8",
            )
            (artifact / "corpus.json").write_text(json.dumps({
                "categories": {
                    "dev_community": [{
                        "title": "Topic",
                        "url": "https://example.com/story",
                        "summary": "Supported claim.",
                    }],
                },
                "errors": [],
            }), encoding="utf-8")
            manifest = {
                "suite": str(temporary / "suite.json"),
                "results": [{
                    "provider": "secret-provider",
                    "model": "secret-model",
                    "prompt_version": "secret-prompt",
                    "case_id": "utility-case",
                    "case_kind": "utility",
                    "case_family": "thin_evidence",
                    "trial": 0,
                    "artifact_dir": artifact.name,
                    "final": {},
                }],
            }
            manifest_path = temporary / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = temporary / "review"
            result = export_grounding_review_packets(manifest_path, output, double_fraction=0.20)
            packet = (output / "reviewer-primary.json").read_text(encoding="utf-8")
            packet_payload = json.loads(packet)
            evidence = packet_payload["reviews"][0]["evidence"]
            self.assertEqual(result["topic_count"], 1)
            self.assertEqual(result["double_review_count"], 1)
            self.assertEqual(result["output_dir"], "review")
            self.assertNotIn("secret-model", packet)
            self.assertNotIn("secret-prompt", packet)
            self.assertIn("ground-00001", packet)
            self.assertEqual(
                evidence,
                [
                    {
                        "corpus_match": True,
                        "feed_evidence": "Topic Supported claim.",
                        "url": "https://example.com/story",
                    },
                    {
                        "corpus_match": False,
                        "feed_evidence": None,
                        "url": "https://example.com/missing",
                    },
                ],
            )
            review_map = json.loads((output / "review-map.json").read_text(encoding="utf-8"))
            self.assertEqual(review_map["manifest"], "manifest.json")
            with self.assertRaises(FileExistsError):
                export_grounding_review_packets(manifest_path, output)

    def test_double_review_sampling_keeps_every_stratum(self) -> None:
        records = [
            {"artifact_dir": f"artifact-{stratum}", "topic_index": 1, "stratum": stratum}
            for stratum in ("alpha", "beta", "gamma")
        ]
        sampled = double_sample(records, 1, random.Random(7))
        self.assertEqual(len(sampled), 3)
        self.assertEqual({record["stratum"] for record in sampled}, {"alpha", "beta", "gamma"})

