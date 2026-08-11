#!/usr/bin/env python3
"""Offline end-to-end tests for the provider-neutral model evaluation harness."""

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import run_ai_eval


class AiEvalHarnessTest(unittest.TestCase):
    def test_real_subprocess_run_writes_complete_audit_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "run"
            argv = [
                "run_ai_eval.py",
                "--model-command", f"{sys.executable} fixtures/fake_eval_model.py",
                "--provider", "test-provider",
                "--model", "deterministic-fixture",
                "--model-version", "1",
                "--generation-settings", '{"temperature":0}',
                "--output-dir", str(output),
            ]
            with patch.object(sys, "argv", argv), redirect_stdout(io.StringIO()):
                self.assertEqual(run_ai_eval.main(), 0)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["summary"]["attack_cases"], 3)
            self.assertEqual(manifest["summary"]["utility_cases"], 1)
            self.assertEqual(manifest["model"]["generation_settings"], {"temperature": 0})
            self.assertEqual(set(manifest["hashes"]),
                             {"prompt_sha256", "suite_sha256", "checker_sha256"})
            self.assertIn("commit", manifest["code"])
            for case in manifest["cases"]:
                case_dir = output / case["id"]
                for filename in ("corpus.json", "raw-first-output.md", "corrected-output.md",
                                 "findings-before.json", "findings-after.json"):
                    self.assertTrue((case_dir / filename).is_file())


if __name__ == "__main__":
    unittest.main()
