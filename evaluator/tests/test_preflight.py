"""The credential-free preflight rehearses a planned run without a provider call."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import evaluator.__main__ as evaluator_cli
from evaluator.preflight import preflight_run
from evaluator.runner import ROOT
from evaluator.tests.support import _resume_fixture

PARITY_V3 = ROOT / "evaluator" / "protocols" / "parity-v3.json"
PARITY_V3_ARGS: dict[str, Any] = dict(
    providers=[("openrouter", "deepseek/deepseek-v4-flash"), ("openrouter", "tencent/hy3")],
    prompt_versions={
        "production-runner": ROOT / "briefing-runner-prompt.md",
        "runner-deepseek-v4-flash": ROOT / "evaluator/prompts/briefing-runner-prompt-deepseek-v4-flash.md",
    },
    trials=5, timeout=300, temperature=0.0, reasoning="disabled", reasoning_effort=None,
    suite_path=ROOT / "evaluator/fixtures/generation-cases.json",
    corpus_path=ROOT / "evaluator/fixtures/generation-corpus.json",
    protocol_path=PARITY_V3, resume=False, run_kind="final", execution_seed=20260901,
    source_tag="parity-v3-source-20261015", cost_ceiling_usd=5.0, cost_ceiling_provider="openrouter",
)


def _failed(result: dict[str, Any]) -> dict[str, bool]:
    return {check["name"]: check["fatal"] for check in result["checks"] if not check["ok"]}


class ParityV3PreflightTest(unittest.TestCase):
    @patch.dict(os.environ, {"OPENROUTER_API_KEY": ""})
    def test_plan_command_binds_protocol_without_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = preflight_run(
                **PARITY_V3_ARGS, output_dir=Path(temporary) / "parity-v3-final", before_tag=True,
            )
        self.assertEqual(result["provider_calls"], 0)
        self.assertEqual(result["credentials"],
                         [{"provider": "openrouter", "requirement": "OPENROUTER_API_KEY", "present": False}])
        names = {check["name"] for check in result["checks"]}
        self.assertTrue({"protocol_hash", "protocol_suite", "protocol_prompts", "protocol_models",
                         "protocol_control:trials_per_case", "protocol_cost_ceiling", "plan",
                         "protocol_planned_rows", "output_dir"} <= names)
        hash_checks = [c for c in result["checks"] if c["name"] == "protocol_hash"]
        self.assertEqual(len(hash_checks), 9)
        self.assertTrue(all(c["ok"] for c in hash_checks))
        # Before the freeze and authorization only those protocol fields may fail; the
        # untagged source is deferred, never fatal, under before_tag.
        failed = _failed(result)
        self.assertLessEqual(set(failed), {"protocol_status", "protocol_authorized", "source"})
        self.assertFalse(failed.get("source", False))
        self.assertEqual(result["plan"]["planned_case_trials"], 1200)
        self.assertEqual(result["plan"]["execution_seed"], 20260901)
        self.assertEqual(result["plan"]["execution_order"], "prompt_interleaved_randomized")

    def test_untagged_final_source_is_fatal_without_before_tag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = preflight_run(**PARITY_V3_ARGS, output_dir=Path(temporary) / "out", before_tag=False)
        self.assertFalse(result["ok"])
        self.assertTrue(_failed(result)["source"])

    def test_control_drift_from_the_protocol_is_reported(self) -> None:
        drifted = {**PARITY_V3_ARGS, "trials": 3, "temperature": 0.2, "cost_ceiling_usd": 6.0,
                   "providers": [("openrouter", "deepseek/deepseek-v4-flash")]}
        with tempfile.TemporaryDirectory() as temporary:
            result = preflight_run(**drifted, output_dir=Path(temporary) / "out", before_tag=True)
        failed = _failed(result)
        for name in ("protocol_control:trials_per_case", "protocol_control:temperature",
                     "protocol_cost_ceiling", "protocol_models", "protocol_planned_rows"):
            self.assertTrue(failed.get(name), name)

    def test_hash_drift_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            protocol = json.loads(PARITY_V3.read_text(encoding="utf-8"))
            protocol["inputs"][0]["sha256"] = "0" * 64
            drifted = Path(temporary) / "protocol.json"
            drifted.write_text(json.dumps(protocol), encoding="utf-8")
            result = preflight_run(**{**PARITY_V3_ARGS, "protocol_path": drifted},
                                   output_dir=Path(temporary) / "out", before_tag=True)
        self.assertTrue(_failed(result)["protocol_hash"])
        detail = [c["detail"] for c in result["checks"] if c["name"] == "protocol_hash" and not c["ok"]]
        self.assertEqual(detail, ["evaluator/fixtures/generation-corpus.json differs from the protocol hash"])


class OutputDirectoryPreflightTest(unittest.TestCase):
    def _development(self, temporary: Path, **overrides: Any) -> dict[str, Any]:
        suite, prompt, results = _resume_fixture(temporary)
        protocol = temporary / "protocol.json"
        protocol.write_text(json.dumps({"protocol_id": "fixture"}), encoding="utf-8")
        arguments: dict[str, Any] = dict(
            providers=[("openrouter", "fixture/model")],
            prompt_versions={"fixture": prompt},
            trials=1, timeout=30, temperature=None, reasoning=None, reasoning_effort=None,
            suite_path=suite, corpus_path=ROOT / "evaluator/fixtures/generation-corpus.json",
            protocol_path=protocol, output_dir=results, resume=False, run_kind="development",
            execution_seed=None, source_tag=None, cost_ceiling_usd=None, cost_ceiling_provider=None,
        )
        arguments.update(overrides)
        return preflight_run(**arguments)

    def test_development_run_passes_and_existing_directory_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self._development(Path(temporary))
            self.assertTrue(result["ok"], _failed(result))
            self.assertEqual(result["plan"]["planned_case_trials"], 3)
            (Path(temporary) / "results").mkdir()
            result = self._development(Path(temporary))
            self.assertTrue(_failed(result)["output_dir"])

    def test_resume_requires_a_running_compatible_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            results = Path(temporary) / "results"
            results.mkdir()
            (results / "manifest.json").write_text(json.dumps({"run_status": "complete"}), encoding="utf-8")
            result = self._development(Path(temporary), resume=True)
            self.assertTrue(_failed(result)["output_dir"])
            (results / "manifest.json").write_text(
                json.dumps({"run_status": "running", "completed_at": None, "trials_per_case": 9}), encoding="utf-8"
            )
            result = self._development(Path(temporary), resume=True)
            self.assertIn("immutable fields differ", [c["detail"] for c in result["checks"]
                                                      if c["name"] == "output_dir"][0])


class PreflightCliTest(unittest.TestCase):
    def test_cli_exit_code_follows_the_verdict_and_makes_no_call(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            suite, prompt, results = _resume_fixture(Path(temporary))
            protocol = Path(temporary) / "protocol.json"
            protocol.write_text(json.dumps({"protocol_id": "fixture"}), encoding="utf-8")
            argv = ["evaluator", "preflight", "--provider", "openrouter=fixture/model",
                    "--prompt", f"fixture={prompt}", "--suite", str(suite), "--protocol", str(protocol),
                    "--output-dir", str(results)]
            stdout = io.StringIO()
            with redirect_stdout(stdout), patch.object(sys, "argv", argv), \
                    patch("evaluator.adapters.ProductionParityAdapter.generate_structured",
                          side_effect=AssertionError("preflight must not call a provider")):
                self.assertEqual(evaluator_cli.main(), 0)
            self.assertTrue(json.loads(stdout.getvalue())["ok"])
            results.mkdir()
            with redirect_stdout(io.StringIO()), patch.object(sys, "argv", argv):
                self.assertEqual(evaluator_cli.main(), 1)

    def test_preflight_shares_the_run_options(self) -> None:
        stderr = io.StringIO()
        with patch.object(sys, "stderr", stderr), self.assertRaises(SystemExit) as raised:
            with patch.object(sys, "argv", ["evaluator", "run", "--before-tag"]):
                evaluator_cli.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("unrecognized arguments: --before-tag", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
