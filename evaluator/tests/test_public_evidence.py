"""Evaluator public evidence regression coverage."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from evaluator.evidence_assets import IN_TREE_FILES, Asset, Config, fetch, pack_bytes, read_sums
from evaluator.publication import (
    _recorded_verification_command,
    export_public_run,
    verify_public_run,
)
from evaluator.runner import ROOT


def _refresh_sums(output: Path) -> None:
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted(output.iterdir()) if path.name != "SHA256SUMS"
    ), encoding="utf-8")


def _final_manifest() -> dict[str, Any]:
    """Return a minimal complete final-run manifest with one scored row."""
    return {
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


class PublicRunTest(unittest.TestCase):
    def test_export_removes_provider_ids_and_rebuilds_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            manifest = _final_manifest()
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
            self.assertEqual(
                metadata["regeneration_command"],
                "python3 -S -m evaluator verify-public-run <external-path-redacted>",
            )

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
            with patch("evaluator.publication.summarize", side_effect=AssertionError("current reporter used")), patch(
                "evaluator.publication.markdown_report", side_effect=AssertionError("current rendering used")
            ):
                self.assertEqual(verify_public_run(output)["status"], "verified")

            # Matching rewritten checksums cannot make contradictory metadata or
            # changed score primitives agree with the independently rebuilt ledger.
            metadata["source_tag"] = "other-tag"
            (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            _refresh_sums(output)
            with self.assertRaisesRegex(ValueError, "source_tag mismatch"):
                verify_public_run(output)
            metadata["source_tag"] = manifest["code"]["source_tag"]
            metadata["files"]["report.md"]["bytes"] += 1
            (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            _refresh_sums(output)
            with self.assertRaisesRegex(ValueError, r"metadata.json \(report.md\)"):
                verify_public_run(output)
            metadata["files"]["report.md"]["bytes"] -= 1
            public_manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            public_manifest["results"][0]["first"]["cost_usd"] = 99
            (output / "manifest.json").write_text(json.dumps(public_manifest), encoding="utf-8")
            metadata["files"]["manifest.json"] = {
                "bytes": (output / "manifest.json").stat().st_size,
                "sha256": hashlib.sha256((output / "manifest.json").read_bytes()).hexdigest(),
            }
            (output / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
            _refresh_sums(output)
            with self.assertRaisesRegex(ValueError, "row primitive mismatch"):
                verify_public_run(output)

    def test_export_combines_complete_disjoint_adapter_components(self) -> None:
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
            primary_manifest: dict[str, Any] = {
                **common,
                "run_status": "complete",
                "planned_case_trials": 2,
                "generation_controls": [controls[0]],
                "adapter_timeouts_seconds": [
                    {"provider": "openrouter", "model": "model-a", "timeout_seconds": 300},
                ],
                "observed_ceiling_cost_usd": 0.01,
                "cost_ceiling_usd": 0.05,
                "completed_at": "2026-01-01T00:00:00+00:00",
                "checkpointed_at": "2026-01-01T00:00:00+00:00",
                "results": [
                    row("model-a", 0.01),
                    row("model-a", 0.01, "prompt-2"),
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

            primary_manifest["run_status"] = "running"
            primary_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "complete components"):
                export_public_run([primary_path, supplement_path], root / "interrupted")
            primary_manifest["run_status"] = "complete"
            primary_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "disjoint adapter sets"):
                export_public_run([primary_path, primary_path], root / "overlap")
            primary_manifest["results"].append(primary_manifest["results"][0])
            primary_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "duplicate rows"):
                export_public_run([primary_path, supplement_path], root / "duplicate")
            primary_manifest["results"].pop()
            primary_path.write_text(json.dumps(primary_manifest), encoding="utf-8")
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
            self.assertEqual(primary_component["rows"], 2)
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

            # A scored correction failure alone must also keep the merged error status.
            correction_failed = row("model-b", 0.02)
            correction_failed.update({
                "status": "completed_with_correction_error",
                "correction_error": {"type": "ProviderRequestError", "message": "invalid JSON"},
            })
            supplement_manifest["results"][0] = correction_failed
            supplement_path.write_text(json.dumps(supplement_manifest), encoding="utf-8")
            correction_output = root / "with-correction-error"
            export_public_run([primary_path, supplement_path], correction_output)
            correction_public = json.loads((correction_output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(correction_public["run_status"], "completed_with_errors")
            self.assertEqual(sum(c["error_rows"] for c in correction_public["split_run_components"]), 0)
            self.assertEqual(sum(c["correction_error_rows"] for c in correction_public["split_run_components"]), 1)
            self.assertEqual(verify_public_run(correction_output)["status"], "verified")

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



class RecordedCommandTest(unittest.TestCase):
    """`regeneration_command` must name a directory that holds the complete bundle."""

    FETCH = "python3 -S -m evaluator.evidence_assets fetch"
    VERIFY = "python3 -S -m evaluator verify-public-run"

    def test_in_tree_bundle_records_fetch_and_the_assembled_path(self) -> None:
        assets = Config("o/r", "t", ROOT / "docs/results", ROOT / ".news-briefing/evidence", ())
        for output_dir in (ROOT / "docs/results/example-evidence", ROOT / "docs/results/example-evidence/"):
            self.assertEqual(
                _recorded_verification_command(output_dir, assets),
                f"{self.FETCH} && {self.VERIFY} .news-briefing/evidence/example-evidence",
            )

    def test_bundle_outside_the_source_root_records_its_own_path(self) -> None:
        assets = Config("o/r", "t", ROOT / "docs/results", ROOT / ".news-briefing/evidence", ())
        self.assertEqual(
            _recorded_verification_command(ROOT / "docs/results/example-evidence/nested", assets),
            f"{self.VERIFY} docs/results/example-evidence/nested",
        )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                _recorded_verification_command(Path(directory) / "public", assets),
                f"{self.VERIFY} <external-path-redacted>",
            )

    def test_recorded_steps_verify_a_bundle_whose_export_directory_does_not(self) -> None:
        bundle = "example-evidence"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            tree, assembled = root / "tree", root / "assembled"
            manifest_path = root / "source" / "manifest.json"
            manifest_path.parent.mkdir()
            manifest_path.write_text(json.dumps(_final_manifest()), encoding="utf-8")
            tree.mkdir()
            with patch(
                "evaluator.publication.load_config",
                return_value=Config("o/r", "t", tree, assembled, ()),
            ):
                metadata = export_public_run(manifest_path, tree / bundle)
            # Both roots are outside the repository here, so the assembled path is redacted.
            self.assertEqual(
                metadata["regeneration_command"],
                f"{self.FETCH} && {self.VERIFY} <external-path-redacted>",
            )

            # Publish as the repository does: large files leave the tree for a release archive.
            large = sorted(set(read_sums(tree / bundle)) - set(IN_TREE_FILES))
            archive = pack_bytes(bundle, [(name, (tree / bundle / name).read_bytes()) for name in large])
            (root / "assets").mkdir()
            (root / "assets" / f"{bundle}.tar.gz").write_bytes(archive)
            for name in large:
                (tree / bundle / name).unlink()
            asset = Asset(bundle, f"{bundle}.tar.gz", hashlib.sha256(archive).hexdigest(), tuple(large))

            with self.assertRaisesRegex(ValueError, "public evidence verification failed"):
                verify_public_run(tree / bundle)
            fetch(Config("o/r", "t", tree, assembled, (asset,)), root / "assets")
            self.assertEqual(verify_public_run(assembled / bundle)["status"], "verified")


class CorrectionErrorRowTest(unittest.TestCase):
    def _manifest(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": 9, "generation_path": "production-parity", "run_kind": "final",
            "run_status": "completed_with_errors", "planned_case_trials": 1, "trials_per_case": 1,
            "suite": str(ROOT / "evaluator/fixtures/generation-cases.json"),
            "protocol": str(ROOT / "evaluator/protocols/parity-v3.json"),
            "code": {"commit": "abc", "tree": "def", "dirty": False, "source_tag": "parity-test-source",
                     "runtime_source_sha256": {"evaluator/runner.py": "123"}},
            "generation_controls": [], "grounding_measure": "test", "deterministic_summary": None,
            "results": [row],
        }

    def test_scored_row_with_disclosed_correction_error_is_published(self) -> None:
        scored = {"contract_success": True, "latency_ms": 1, "cost_usd": 0.01, "findings": [],
                  "grounding_error_topics": 0, "generated_topics": 1, "oracle": {}}
        row = {
            "provider": "openrouter", "model": "model", "prompt_version": "prompt", "prompt_sha256": "prompt-sha",
            "case_id": "case", "case_family": "utility", "case_kind": "utility", "trial": 1,
            "status": "completed_with_correction_error", "artifact_dir": "row", "correction_attempted": True,
            "correction": None, "error": None,
            "correction_error": {"stage": "correction", "type": "ProviderRequestError", "cost_usd": 0.0,
                                 "message": "openrouter returned invalid JSON"},
            "first": {"text": "generated output", "provider_request_id": "secret-id", **scored},
            "final": dict(scored),
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "source").mkdir()
            (root / "source" / "manifest.json").write_text(json.dumps(self._manifest(row)), encoding="utf-8")
            export_public_run(root / "source" / "manifest.json", root / "public", ledger_output=root / "ledger.json")
            self.assertEqual(verify_public_run(root / "public")["rows"], 1)
            metadata = json.loads((root / "public" / "metadata.json").read_text(encoding="utf-8"))
            component = metadata["component_manifests"][0]
            self.assertEqual((component["error_rows"], component["correction_error_rows"]), (0, 1))
            self.assertNotIn("secret-id", (root / "public" / "manifest.json").read_text(encoding="utf-8"))
            # Without a scored result or a recorded correction error the row is not publishable.
            broken_rows: list[dict[str, Any]] = [{"final": None}, {"correction_error": None}]
            for field, changed in (
                ("contract_success", False), ("findings", [{"code": "mismatch"}]),
                ("deterministic_repairs", ["mismatch"]), ("oracle", {"mismatch": True}),
                ("generated_topics", 2), ("grounding_error_topics", 1),
            ):
                broken_rows.append({"final": {**scored, field: changed}})
            for broken in broken_rows:
                (root / "source" / "manifest.json").write_text(json.dumps(self._manifest({**row, **broken})),
                                                               encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "every recorded row"):
                    export_public_run(root / "source" / "manifest.json", root / f"public-{list(broken)[0]}")
