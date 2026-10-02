"""Checkpoint reuse must retain the actual assessment and reviewer identity."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluator.adapters import Adapter, Generation
from evaluator.judge_io import checkpointed_generate


class Judge(Adapter):
    provider = "offline"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        return Generation(text='{"supported":true}', latency_ms=1)


class JudgmentCheckpointTest(unittest.TestCase):
    def test_identity_mismatch_and_legacy_records_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "judgment.json"
            original = Judge("first")
            checkpointed_generate(original, "Evidence output proposition rubric", checkpoint, json.loads)
            frozen = checkpoint.read_bytes()
            second = Judge("second")
            for adapter, prompt in (
                (second, "Evidence output proposition rubric"),
                (original, "Changed evidence output proposition rubric"),
            ):
                with self.assertRaisesRegex(ValueError, "incompatible or legacy"):
                    checkpointed_generate(adapter, prompt, checkpoint, json.loads)
                self.assertEqual(checkpoint.read_bytes(), frozen)
            with patch.object(original, "generation_controls", return_value={"temperature": 1}):
                with self.assertRaisesRegex(ValueError, "incompatible or legacy"):
                    checkpointed_generate(original, "Evidence output proposition rubric", checkpoint, json.loads)
            original.provider = "different-provider"
            with self.assertRaisesRegex(ValueError, "incompatible or legacy"):
                checkpointed_generate(original, "Evidence output proposition rubric", checkpoint, json.loads)
            original.provider = "offline"
            _, parsed, resumed = checkpointed_generate(
                original, "Evidence output proposition rubric", checkpoint, json.loads
            )
            self.assertTrue(resumed)
            self.assertTrue(parsed["supported"])
            self.assertEqual(original.calls, 1)
            self.assertEqual(second.calls, 0)
            checkpoint.write_text(json.dumps(Generation(text='{"supported":false}', latency_ms=1).record()))
            legacy = checkpoint.read_bytes()
            with self.assertRaisesRegex(ValueError, "incompatible or legacy"):
                checkpointed_generate(original, "Evidence output proposition rubric", checkpoint, json.loads)
            self.assertEqual(checkpoint.read_bytes(), legacy)
            self.assertEqual(original.calls, 1)
