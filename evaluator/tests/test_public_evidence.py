"""Evaluator public evidence regression coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from evaluator.publication import export_public_run, verify_public_run
from evaluator.runner import (
    ROOT,
)


class PublicRunTest(unittest.TestCase):
    def test_export_removes_provider_ids_and_rebuilds_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            manifest = {
                "schema_version": 9,
                "generation_path": "markdown",
                "run_kind": "final",
                "run_status": "complete",
                "planned_case_trials": 1,
                "trials_per_case": 1,
                "suite": str(ROOT / "evaluator/fixtures/generation-cases.json"),
                "protocol": str(ROOT / "evaluator/protocols/portfolio-v1.json"),
                "code": {
                    "commit": "abc",
                    "tree": "def",
                    "dirty": False,
                    "source_tag": "portfolio-test-source",
                    "runtime_source_sha256": {"evaluator/runner.py": "123"},
                },
                "generation_controls": [],
                "grounding_measure": "test",
                "deterministic_summary": None,
                "results": [{
                    "provider": "openrouter",
                    "model": "model",
                    "prompt_version": "prompt",
                    "prompt_sha256": "prompt-sha",
                    "case_id": "case",
                    "case_family": "utility",
                    "case_kind": "utility",
                    "trial": 1,
                    "status": "completed",
                    "artifact_dir": "row",
                    "correction_attempted": False,
                    "correction": None,
                    "correction_error": None,
                    "error": None,
                    "first": {
                        "text": "generated output",
                        "provider_request_id": "secret-id",
                        "contract_success": True,
                        "latency_ms": 1,
                        "cost_usd": 0.01,
                        "findings": [],
                        "grounding_error_topics": 0,
                        "generated_topics": 1,
                        "oracle": {},
                    },
                    "final": {
                        "contract_success": True,
                        "findings": [],
                        "grounding_error_topics": 0,
                        "generated_topics": 1,
                        "oracle": {},
                    },
                }],
            }
            manifest_path = source / "manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "public"
            ledger = root / "ledger.json"
            export_public_run(manifest_path, output, ledger_output=ledger)

            published = (output / "manifest.json").read_text(encoding="utf-8")
            self.assertIn("generated output", published)
            self.assertNotIn("secret-id", published)
            public_ledger = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertNotIn("text", public_ledger["results"][0]["first"])
            self.assertEqual(verify_public_run(output)["rows"], 1)
            metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
            self.assertIn("<external-path-redacted>", metadata["regeneration_command"])

            report_markdown = output / "report.md"
            report_markdown.write_text(
                report_markdown.read_text(encoding="utf-8") + "\nStale summary.\n",
                encoding="utf-8",
            )
            metadata["files"]["report.md"] = {
                "bytes": report_markdown.stat().st_size,
                "sha256": hashlib.sha256(report_markdown.read_bytes()).hexdigest(),
            }
            (output / "metadata.json").write_text(
                json.dumps(metadata, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            sums = "".join(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
                for path in sorted(output.iterdir())
                if path.is_file() and path.name != "SHA256SUMS"
            )
            (output / "SHA256SUMS").write_text(sums, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "report.md"):
                verify_public_run(output)

    def test_export_combines_whole_adapter_split_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            suite_path = root / "suite.json"
            suite_path.write_text(
                json.dumps({"cases": [{"id": "case", "matched_pair": False}]}),
                encoding="utf-8",
            )
            common = {
                "schema_version": 9,
                "generation_path": "markdown",
                "run_kind": "final",
                "execution_order": "prompt_interleaved_randomized",
                "execution_seed": 123,
                "cost_ceiling_provider": "openrouter",
                "circuit_breaker_threshold": 3,
                "suite": str(suite_path),
                "suite_sha256": hashlib.sha256(suite_path.read_bytes()).hexdigest(),
                "corpus_sha256": "corpus-sha",
                "case_corpus_sha256": {"case": "corpus-sha"},
                "config_sha256": {"config.json": "config-sha"},
                "protocol": str(ROOT / "evaluator/protocols/portfolio-v1.json"),
                "protocol_sha256": "protocol-sha",
                "prompt_sha256": {
                    "prompt": "prompt-sha",
                    "prompt-2": "prompt-2-sha",
                },
                "prompt_order": ["prompt", "prompt-2"],
                "trials_per_case": 1,
                "matched_pair_case_ids": [],
                "planned_matched_pair_trials": 0,
                "grounding_measure": "test",
                "deterministic_summary": None,
                "code": {
                    "commit": "abc",
                    "tree": "def",
                    "dirty": False,
                    "source_tag": "portfolio-test-source",
                    "runtime_source_sha256": {"evaluator/runner.py": "123"},
                },
            }

            def row(model: str, cost: float, prompt: str = "prompt") -> dict[str, Any]:
                return {
                    "provider": "openrouter",
                    "model": model,
                    "prompt_version": prompt,
                    "prompt_sha256": f"{prompt}-sha",
                    "case_id": "case",
                    "case_family": "utility",
                    "case_kind": "utility",
                    "trial": 1,
                    "status": "completed",
                    "artifact_dir": model,
                    "correction_attempted": False,
                    "correction": None,
                    "correction_error": None,
                    "error": None,
                    "first": {
                        "text": f"output from {model}",
                        "provider_request_id": f"secret-{model}",
                        "contract_success": True,
                        "latency_ms": 1,
                        "cost_usd": cost,
                        "findings": [],
                        "grounding_error_topics": 0,
                        "generated_topics": 1,
                        "oracle": {},
                    },
                    "final": {
                        "contract_success": True,
                        "findings": [],
                        "grounding_error_topics": 0,
                        "generated_topics": 1,
                        "oracle": {},
                    },
                }

            controls = [
                {
                    "provider": "openrouter",
                    "model": "model-a",
                    "temperature": 0,
                    "seed": 123,
                    "reasoning_enabled": False,
                    "reasoning_effort": None,
                    "disclosure": "test",
                },
                {
                    "provider": "openrouter",
                    "model": "model-b",
                    "temperature": 0,
                    "seed": 123,
                    "reasoning_enabled": False,
                    "reasoning_effort": None,
                    "disclosure": "test",
                },
            ]
            primary = root / "primary"
            primary.mkdir()
            primary_manifest = {
                **common,
                "run_status": "running",
                "planned_case_trials": 4,
                "generation_controls": controls,
                "adapter_timeouts_seconds": [
                    {"provider": "openrouter", "model": "model-a", "timeout_seconds": 300},
                    {"provider": "openrouter", "model": "model-b", "timeout_seconds": 300},
                ],
                "observed_ceiling_cost_usd": 0.01,
                "cost_ceiling_usd": 0.05,
                "completed_at": None,
                "checkpointed_at": "2026-01-01T00:00:00+00:00",
                "results": [
                    row("model-a", 0.01),
                    row("model-a", 0.01, "prompt-2"),
                    # This interrupted prefix is superseded by supplement's
                    # exact whole adapter matrix and must not be published.
                    row("model-b", 0.01),
                ],
            }
            primary_path = primary / "manifest.json"
            primary_path.write_text(json.dumps(primary_manifest), encoding="utf-8")

            supplement = root / "supplement"
            supplement.mkdir()
            supplement_manifest: dict[str, Any] = {
                **common,
                "run_status": "complete",
                "planned_case_trials": 2,
                "generation_controls": [controls[1]],
                "adapter_timeouts_seconds": [
                    {"provider": "openrouter", "model": "model-b", "timeout_seconds": 300},
                ],
                "observed_ceiling_cost_usd": 0.02,
                "cost_ceiling_usd": 0.04,
                "completed_at": "2026-01-01T01:00:00+00:00",
                "checkpointed_at": "2026-01-01T01:00:00+00:00",
                "results": [
                    row("model-b", 0.02),
                    row("model-b", 0.02, "prompt-2"),
                ],
            }
            supplement_path = supplement / "manifest.json"
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")

            output = root / "public"
            export_public_run([primary_path, supplement_path], output)
            published = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(published["run_status"], "complete")
            self.assertEqual(published["planned_case_trials"], 4)
            self.assertEqual(len(published["results"]), 4)
            self.assertAlmostEqual(published["observed_ceiling_cost_usd"], 0.03)
            self.assertEqual(len(published["split_run_components"]), 2)
            primary_component = next(
                component
                for component in published["split_run_components"]
                if component["name"] == "primary"
            )
            self.assertEqual(primary_component["selected_rows"], 2)
            self.assertEqual(primary_component["excluded_partial_rows"], 1)
            self.assertEqual(
                {component["cost_ceiling_usd"] for component in published["split_run_components"]},
                {0.04, 0.05},
            )
            self.assertEqual(verify_public_run(output)["rows"], 4)

            # A recorded provider failure is published, not a reason to drop the
            # whole component: the row keeps its place in the adapter matrix,
            # carries its error, scores nothing, and is disclosed in the bundle.
            failed = {
                **row("model-b", 0.02),
                "status": "provider_error",
                "first": None,
                "final": None,
                "error": {"type": "ProviderRequestError", "message": "invalid JSON"},
            }
            supplement_manifest["results"] = [
                failed,
                row("model-b", 0.02, "prompt-2"),
            ]
            supplement_manifest["run_status"] = "completed_with_errors"
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            with_error = root / "with-error"
            export_public_run([primary_path, supplement_path], with_error)
            published_error = json.loads((with_error / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(published_error["run_status"], "completed_with_errors")
            self.assertEqual(len(published_error["results"]), 4)
            self.assertEqual(
                sum(c["error_rows"] for c in published_error["split_run_components"]), 1
            )
            self.assertEqual(verify_public_run(with_error)["status"], "verified")
            preserved = next(
                r for r in published_error["results"] if r["status"] == "provider_error"
            )
            self.assertEqual(preserved["error"]["type"], "ProviderRequestError")
            self.assertIsNone(preserved["first"])
            self.assertIsNone(preserved["final"])

            # A run that recorded failures but whose rows do not carry them is
            # rejected, in the single-manifest path as well as the split one.
            malformed = {**failed, "error": None}
            supplement_manifest["results"] = [
                malformed,
                row("model-b", 0.02, "prompt-2"),
            ]
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "recorded provider failure"):
                export_public_run([supplement_path], root / "malformed-single")
            with self.assertRaisesRegex(ValueError, "recorded provider failure"):
                export_public_run([primary_path, supplement_path], root / "malformed-split")
            supplement_manifest["results"] = [
                row("model-b", 0.02),
                row("model-b", 0.02, "prompt-2"),
            ]
            supplement_manifest["run_status"] = "complete"
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")

            supplement_manifest["circuit_breaker_threshold"] = 4
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "circuit_breaker_threshold"):
                export_public_run([primary_path, supplement_path], root / "different-limits")
            supplement_manifest["circuit_breaker_threshold"] = 3

            supplement_manifest["suite_sha256"] = "different"
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "suite_sha256"):
                export_public_run([primary_path, supplement_path], root / "incompatible")

            supplement_manifest["suite_sha256"] = common["suite_sha256"]
            supplement_manifest["results"][0]["case_id"] = "substituted-case"
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "exact whole adapter matrices"):
                export_public_run([primary_path, supplement_path], root / "wrong-matrix")

