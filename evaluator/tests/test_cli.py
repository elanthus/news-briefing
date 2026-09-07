"""Evaluator cli regression coverage."""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

import evaluator.__main__ as evaluator_cli
from evaluator.__main__ import ProgressBar, _prompt_values, _provider_values
from evaluator.adapters import (
    production_adapter_for,
)
from evaluator.runner import (
    ROOT,
    run_evaluation,
)
from evaluator.tests.support import (
    FakeAdapter,
)


class CliTest(unittest.TestCase):
    def test_retired_generation_cli_option_is_rejected(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            with patch.object(sys, "argv", [
                "evaluator", "run", "--provider", "openrouter=fixture", "--generation-path", "markdown"
            ]):
                evaluator_cli.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --generation-path", stderr.getvalue())

    @patch.dict(os.environ, {
        "CODEX_MODEL": "gpt-5.6-terra, gpt-5.6-sol",
        "CLAUDE_CODE_MODEL": "claude-sonnet-5,claude-opus-5",
        "OPENROUTER_MODEL": "openai/gpt-5.6-terra, anthropic/claude-sonnet-5",
        "NVIDIA_MODEL": "nvidia/nemotron-3-ultra-550b-a55b,openai/gpt-oss-120b",
    })
    def test_all_providers_expands_comma_delimited_model_lists(self) -> None:
        self.assertEqual(_provider_values([], True), [
            ("codex-cli", "gpt-5.6-terra"),
            ("codex-cli", "gpt-5.6-sol"),
            ("claude-code-cli", "claude-sonnet-5"),
            ("claude-code-cli", "claude-opus-5"),
            ("openrouter", "openai/gpt-5.6-terra"),
            ("openrouter", "anthropic/claude-sonnet-5"),
        ])


    def test_production_parity_defaults_to_the_structured_runner_prompt(self) -> None:
        prompts = _prompt_values([])
        self.assertEqual(prompts, {"production": ROOT / "briefing-runner-prompt.md"})


    def test_production_parity_records_effective_reasoning_controls(self) -> None:
        codex = production_adapter_for("codex-cli", "gpt-5.6-terra")
        self.assertEqual(codex.generation_controls()["reasoning_enabled"], True)
        self.assertEqual(codex.generation_controls()["reasoning_effort"], "medium")

        openrouter = production_adapter_for(
            "openrouter",
            "deepseek/deepseek-v4-flash",
            reasoning_effort="high",
        )
        self.assertEqual(openrouter.generation_controls()["reasoning_enabled"], True)
        self.assertEqual(openrouter.generation_controls()["reasoning_effort"], "high")


    @patch.dict(os.environ, {"OPENROUTER_MODEL": "openai/gpt-5.6-terra,,anthropic/claude-sonnet-5"})
    def test_all_providers_rejects_empty_model_list_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENROUTER_MODEL must be a comma-delimited list"):
            _provider_values([], True)


    def test_local_env_is_ignored_and_template_keeps_model_provenance(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        ignored = (evaluator_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/.env", ignored)
        template = (evaluator_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("Model catalog provenance: 2026-08-11", template)
        for key in ("CODEX_MODEL=", "CLAUDE_CODE_MODEL=", "OPENROUTER_MODEL=", "NVIDIA_MODEL="):
            self.assertIn(key, template)


    def test_cli_progress_bar_names_provider_and_model(self) -> None:
        stream = io.StringIO()
        progress = ProgressBar(stream=stream, width=4, interactive=True)
        progress("nvidia", "free-model", 0, 2, "starting")
        progress("nvidia", "free-model", 1, 2, "completed")
        progress("nvidia", "free-model", 2, 2, "circuit open; skipped")

        rendered = stream.getvalue()
        self.assertIn("nvidia / free-model [##--] 1/2  50%", rendered)
        self.assertIn("nvidia / free-model [####] 2/2 100%", rendered)

