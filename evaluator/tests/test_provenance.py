"""Evaluator provenance regression coverage."""
from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from evaluator.runner import (
    _git_provenance,
    final_source_provenance,
)


class FinalSourceProvenanceTest(unittest.TestCase):
    @patch("evaluator.runner._git_provenance")
    def test_final_source_requires_a_clean_tagged_head(self, provenance: Any) -> None:
        provenance.return_value = {
            "commit": "abc",
            "tree": "def",
            "dirty": False,
            "tags": ["portfolio-v2-source"],
            "runtime_source_sha256": {"evaluator/runner.py": "123"},
        }
        result = final_source_provenance("portfolio-v2-source")
        self.assertEqual(result["source_tag"], "portfolio-v2-source")

        provenance.return_value["dirty"] = True
        with self.assertRaisesRegex(ValueError, "clean Git worktree"):
            final_source_provenance("portfolio-v2-source")

        provenance.return_value["dirty"] = False
        with self.assertRaisesRegex(ValueError, "does not point at HEAD"):
            final_source_provenance("portfolio-v2-source-unreleased")

        provenance.return_value["commit"] = None
        with self.assertRaisesRegex(ValueError, "readable Git commit and tree"):
            final_source_provenance("portfolio-v2-source")

    @patch("evaluator.runner.subprocess.run")
    def test_git_provenance_fails_closed_when_status_probe_fails(self, run: Any) -> None:
        def result(command: list[str], **_kwargs: Any) -> Any:
            if command[1:] == ["status", "--porcelain"]:
                return type("Result", (), {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": "fatal: status unavailable",
                })()
            return type("Result", (), {
                "returncode": 0,
                "stdout": "abc\n",
                "stderr": "",
            })()

        run.side_effect = result
        with self.assertRaisesRegex(ValueError, "git status --porcelain exited 1"):
            _git_provenance()

