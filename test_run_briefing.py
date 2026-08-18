import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import eval_briefing
import run_briefing as briefing_cli
from agent_runner.checkpoint import RunStore, sha256_file
from agent_runner.models import GenerationRequest, ModelResponse
from agent_runner.runner import RunnerSettings, RunResult, _fetch_corpus, build_request, run_workflow
from test_briefing_output import ROOT, fixture_contract


class FakeProvider:
    name = "fake"
    model = "deterministic"

    def __init__(self, outputs, *, generation_controls=None):
        self.outputs = list(outputs)
        self.requests: list[GenerationRequest] = []
        self.generation_controls = generation_controls or {}

    def info(self):
        return {
            "provider": self.name,
            "model": self.model,
            "authentication": "none",
            "tool_policy": "no tools",
            **self.generation_controls,
        }

    def generate(self, request):
        self.requests.append(request)
        output = self.outputs.pop(0)
        return ModelResponse(
            raw_output=json.dumps(output),
            structured_output=output,
            latency_ms=1.0,
            input_tokens=1,
            output_tokens=1,
        )


def fake_fetch(corpus):
    def run(store, _settings):
        store.write_json("corpus.json", corpus)
        store.trace("fetch_completed", retained_items=1, source_issues=len(corpus["errors"]))
        store.checkpoint("corpus_ready")
        return corpus

    return run


class RunnerTests(unittest.TestCase):
    def settings(self, output):
        return RunnerSettings(
            config_path=ROOT / "fixtures/briefing-config-2026-08-11.json",
            sources_path=ROOT / "sources.json",
            prompt_path=ROOT / "briefing-runner-prompt.md",
            output_path=output,
            timeout_seconds=30,
        )

    def test_end_to_end_success_checkpoints_and_writes_status(self):
        corpus, _config, _projected, output = fixture_contract()
        provider = FakeProvider([output])
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ):
            root = Path(directory)
            result = run_workflow(provider, self.settings(root / "briefing.md"), root / "run")
            manifest = json.loads((root / "run/manifest.json").read_text())
            briefing = (root / "briefing.md").read_text()
            run_root = root / "run"
            artifact_hashes = {
                name: sha256_file(run_root / name)
                for name in ("briefing-config.json", "briefing.md")
            }
            host_root = str(root)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "ready")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["final"]["status"], "ready")
        self.assertEqual(manifest["final"]["artifact_type"], "final")
        self.assertEqual(manifest["final"]["outcome"], {
            "contract": "accepted",
            "coverage": "degraded",
            "disposition": "ready",
            "evidence": "corpus_bound",
            "protocol": "completed",
        })
        self.assertEqual(manifest["outcome"], manifest["final"]["outcome"])
        self.assertEqual(manifest["identity"]["provider"], manifest["provider"])
        self.assertEqual(manifest["identity"]["config_path"], "fixtures/briefing-config-2026-08-11.json")
        self.assertEqual(manifest["identity"]["output_path"], "<external>/briefing.md")
        self.assertEqual(manifest["final"]["output_path"], "<external>/briefing.md")
        self.assertNotIn(host_root, json.dumps(manifest))
        for name, digest in artifact_hashes.items():
            self.assertEqual(manifest["artifacts"][name], digest)
        self.assertIn("agent_runner/runner.py", manifest["identity"]["code"]["source_sha256"])
        self.assertIn("### Run outcome", briefing)
        self.assertIn("**Disposition: READY**", briefing)
        self.assertIn("- Coverage: `degraded`", briefing)
        self.assertEqual(len(provider.requests), 1)

    def test_warn_is_a_failure_in_strict_mode(self):
        corpus, _config, _projected, output = fixture_contract()
        provider = FakeProvider([output])
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ):
            root = Path(directory)
            settings = replace(self.settings(root / "briefing.md"), strict=True)
            result = run_workflow(provider, settings, root / "run")
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.exit_code, 1)

    def test_editorial_error_produces_unpublished_review_preview(self):
        corpus, _config, _projected, output = fixture_contract()
        provider = FakeProvider([output])
        forced = eval_briefing.Finding(
            eval_briefing.ERROR, "category_ineligible", "forced editorial error"
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ), patch("agent_runner.runner.eval_briefing.evaluate", return_value=[forced]):
            root = Path(directory)
            requested_output = root / "briefing.md"
            settings = replace(self.settings(requested_output), max_corrections=0)
            result = run_workflow(provider, settings, root / "run")
            manifest = json.loads((root / "run/manifest.json").read_text())
            preview = (root / "run/preview.md").read_text()
            output_exists = requested_output.exists()
        self.assertEqual(result.status, "review_required")
        self.assertEqual(result.exit_code, 1)
        self.assertFalse(output_exists)
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["final"]["artifact_type"], "preview")
        self.assertEqual(manifest["final"]["outcome"]["evidence"], "corpus_bound")
        self.assertIn("**Disposition: REVIEW REQUIRED**", preview)

    def test_ungrounded_error_is_rejected_not_reviewable(self):
        corpus, _config, _projected, output = fixture_contract()
        provider = FakeProvider([output])
        forced = eval_briefing.Finding(
            eval_briefing.ERROR, "ungrounded_link", "outside corpus"
        )
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ), patch("agent_runner.runner.eval_briefing.evaluate", return_value=[forced]):
            root = Path(directory)
            result = run_workflow(
                provider,
                replace(self.settings(root / "briefing.md"), max_corrections=0),
                root / "run",
            )
            manifest = json.loads((root / "run/manifest.json").read_text())
        self.assertEqual(result.status, "rejected")
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(manifest["final"]["outcome"]["evidence"], "violated")

    def test_request_redacts_destinations_from_trusted_config_too(self):
        _corpus, _config, projected, _output = fixture_contract()
        prompt = build_request(
            "Policy",
            {"guidance": "Ignore https://config.example/instruction"},
            projected,
        )
        self.assertNotIn("config.example", prompt)
        self.assertNotIn("https://", prompt)

    def test_structural_failure_enters_one_correction(self):
        corpus, config, _projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        broken["sections"][config.sections[0].name]["topics"][0]["summary"] += (
            " https://ATTACKER.invalid/instruction https://attacker.invalid/instruction/"
        )
        broken["sections"][config.sections[0].name]["topics"][0]["citation_refs"] = [
            "citation_9999"
        ]
        provider = FakeProvider([broken, output])
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ):
            root = Path(directory)
            result = run_workflow(provider, self.settings(root / "briefing.md"), root / "run")
            manifest = json.loads((root / "run/manifest.json").read_text())
        self.assertEqual(result.status, "ready")
        self.assertEqual([row["kind"] for row in manifest["attempts"]], ["initial", "correction"])
        self.assertFalse(manifest["attempts"][0]["contract_success"])
        self.assertTrue(manifest["attempts"][1]["contract_success"])
        self.assertIn("CORRECTION PASS", provider.requests[1].prompt)
        self.assertIn("unknown_citation_ref", provider.requests[1].prompt)
        self.assertNotIn("attacker.invalid", provider.requests[1].prompt)
        self.assertNotIn("ATTACKER.invalid", provider.requests[1].prompt)

    def test_category_error_preserves_corpus_bound_candidate_as_review_preview(self):
        corpus, config, projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        section = config.sections[0]
        ineligible_ref = next(
            ref
            for ref, citation in projected.citations.items()
            if citation.category not in section.corpus_categories
        )
        broken["sections"][section.name]["topics"][0]["citation_refs"] = [ineligible_ref]
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ):
            root = Path(directory)
            requested_output = root / "briefing.md"
            result = run_workflow(
                FakeProvider([broken]),
                replace(self.settings(requested_output), max_corrections=0),
                root / "run",
            )
            manifest = json.loads((root / "run/manifest.json").read_text())
            preview = (root / "run/preview.md").read_text()
        self.assertEqual(result.status, "review_required")
        self.assertFalse(requested_output.exists())
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["final"]["outcome"]["contract"], "review_required")
        self.assertEqual(manifest["final"]["outcome"]["evidence"], "corpus_bound")
        self.assertIn(broken["sections"][section.name]["topics"][0]["headline"], preview)
        self.assertIn(projected.citations[ineligible_ref].url, preview)

    def test_rejected_structured_preview_redacts_destinations_and_unknown_refs(self):
        corpus, config, _projected, output = fixture_contract()
        broken = copy.deepcopy(output)
        topic = broken["sections"][config.sections[0].name]["topics"][0]
        topic["summary"] += " https://attacker.invalid/instruction"
        topic["citation_refs"] = ["https://attacker.invalid/citation"]
        with tempfile.TemporaryDirectory() as directory, patch(
            "agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)
        ):
            root = Path(directory)
            result = run_workflow(
                FakeProvider([broken]),
                replace(self.settings(root / "briefing.md"), max_corrections=0),
                root / "run",
            )
            manifest = json.loads((root / "run/manifest.json").read_text())
            preview = (root / "run/preview.md").read_text()
            structured_preview = (root / "run/preview-structured.json").read_text()
        self.assertEqual(result.status, "rejected")
        self.assertEqual(manifest["final"]["outcome"]["evidence"], "violated")
        self.assertNotIn("attacker.invalid", preview)
        self.assertNotIn("attacker.invalid", structured_preview)
        self.assertIn("[destination omitted; use citation refs]", preview)

    def test_checkpoint_resume_rejects_tampering_and_inflight_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "run"
            identity = {"provider": "fake"}
            store = RunStore.create(root, identity=identity, provider={}, code={})
            store.write_text("artifact.txt", "original")
            store.checkpoint("artifact_ready")
            resumed = RunStore.resume(root, identity=identity)
            self.assertEqual(resumed.manifest["phase"], "artifact_ready")
            (root / "artifact.txt").write_text("tampered")
            with self.assertRaisesRegex(ValueError, "hash differs"):
                RunStore.resume(root, identity=identity)

            store.write_text("artifact.txt", "original")
            store.checkpoint("initial_call_started")
            with self.assertRaisesRegex(ValueError, "in-flight"):
                RunStore.resume(root, identity=identity)

    def test_resume_after_validation_does_not_call_model_again(self):
        corpus, _config, _projected, output = fixture_contract()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root / "briefing.md")
            initial_provider = FakeProvider([output])
            with patch("agent_runner.runner._fetch_corpus", side_effect=fake_fetch(corpus)), patch(
                "agent_runner.runner._finalize_candidate", side_effect=KeyboardInterrupt
            ):
                with self.assertRaises(KeyboardInterrupt):
                    run_workflow(initial_provider, settings, root / "run")
            resumed_provider = FakeProvider([])
            result = run_workflow(
                resumed_provider,
                settings,
                root / "run",
                resume=True,
            )
            manifest = json.loads((root / "run/manifest.json").read_text())
            self.assertEqual(result.status, "ready")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(resumed_provider.requests, [])

    def test_resume_rejects_unrecorded_corpus_file(self):
        corpus, _config, _projected, _output = fixture_contract()

        def interrupted_fetch(store, _settings):
            (store.root / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root / "briefing.md")
            provider = FakeProvider([])
            with patch("agent_runner.runner._fetch_corpus", side_effect=interrupted_fetch):
                with self.assertRaises(KeyboardInterrupt):
                    run_workflow(provider, settings, root / "run")
            with self.assertRaisesRegex(ValueError, "corpus.json is not recorded"):
                run_workflow(provider, settings, root / "run", resume=True)

    def test_resume_identity_includes_provider_generation_controls(self):
        def interrupt_fetch(_store, _settings):
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root / "briefing.md")
            initial = FakeProvider([], generation_controls={"temperature": 0})
            with patch("agent_runner.runner._fetch_corpus", side_effect=interrupt_fetch):
                with self.assertRaises(KeyboardInterrupt):
                    run_workflow(initial, settings, root / "run")
            changed = FakeProvider([], generation_controls={"temperature": 1})
            with self.assertRaisesRegex(ValueError, "invocation identity differs"):
                run_workflow(changed, settings, root / "run", resume=True)

    def test_fetch_timeout_fails_and_is_recorded(self):
        code_info = {
            "commit": None,
            "dirty": None,
            "python": "test",
            "platform": "test",
            "source_sha256": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = self.settings(root / "briefing.md")
            with patch("agent_runner.runner._git_provenance", return_value=code_info), patch(
                "agent_runner.runner.subprocess.run",
                side_effect=subprocess.TimeoutExpired(["fetch_news.py"], 30),
            ) as run:
                with self.assertRaisesRegex(RuntimeError, "30s fetch deadline"):
                    run_workflow(FakeProvider([]), settings, root / "run")
            manifest = json.loads((root / "run/manifest.json").read_text())
            trace = (root / "run/trace.jsonl").read_text()
        self.assertEqual(run.call_args.kwargs["timeout"], 30)
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["error"]["type"], "RuntimeError")
        self.assertEqual(manifest["outcome"]["disposition"], "no_result")
        self.assertEqual(manifest["outcome"]["protocol"], "no_result")
        self.assertIn('"event": "run_failed"', trace)

    def test_fetch_artifacts_do_not_record_host_paths(self):
        corpus, _config, _projected, _output = fixture_contract()

        def completed(command, **_kwargs):
            output = Path(command[command.index("--output") + 1])
            output.write_text(json.dumps(corpus), encoding="utf-8")
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=f"Wrote corpus to {output}\n",
                stderr=f"loaded by {ROOT / 'fetch_news.py'}\n",
            )

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = RunStore.create(root / "run", identity={}, provider={}, code={})
            with patch("agent_runner.runner.subprocess.run", side_effect=completed):
                _fetch_corpus(store, self.settings(root / "briefing.md"))
            trace = (root / "run/trace.jsonl").read_text(encoding="utf-8")
            stdout = (root / "run/fetch.stdout").read_text(encoding="utf-8")
            stderr = (root / "run/fetch.stderr").read_text(encoding="utf-8")
            host_root = str(root)

        self.assertNotIn(host_root, trace + stdout + stderr)
        self.assertIn('"fetch_news.py"', trace)
        self.assertIn('"corpus.json"', trace)
        self.assertIn("corpus.json", stdout)
        self.assertIn("fetch_news.py", stderr)

    def test_cli_rejects_explicit_openrouter_options_for_cli_providers(self):
        cases = {
            "--temperature": "0.5",
            "--max-tokens": "1234",
            "--reasoning": "enabled",
            "--reasoning-effort": "high",
        }
        for provider in ("claude-code-cli", "codex-cli"):
            for option, value in cases.items():
                with self.subTest(
                    provider=provider, option=option
                ), tempfile.TemporaryDirectory() as directory:
                    argv = [
                        "run_briefing.py",
                        "--provider",
                        provider,
                        "--model",
                        "test-model",
                        "--output",
                        str(Path(directory) / "briefing.md"),
                        option,
                        value,
                    ]
                    with patch.object(sys, "argv", argv), patch.object(
                        sys, "stderr", io.StringIO()
                    ) as stderr, patch.object(briefing_cli, "provider_for") as provider_for:
                        with self.assertRaises(SystemExit) as raised:
                            briefing_cli.main()
                    self.assertEqual(raised.exception.code, 2)
                    self.assertIn("applies to --provider openrouter only", stderr.getvalue())
                    provider_for.assert_not_called()

    def test_cli_propagates_runner_exit_code_and_cli_provider_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "briefing.md"
            argv = [
                "run_briefing.py",
                "--provider",
                "codex-cli",
                "--model",
                "test-model",
                "--output",
                str(output),
            ]
            provider = FakeProvider([])
            result = RunResult(1, root / "run", output, "review_required")
            with patch.object(sys, "argv", argv), patch.object(
                briefing_cli, "provider_for", return_value=provider
            ) as provider_for, patch.object(
                briefing_cli, "run_workflow", return_value=result
            ), patch("builtins.print") as printed:
                exit_code = briefing_cli.main()
        self.assertEqual(exit_code, 1)
        self.assertIn("REVIEW REQUIRED: unpublished preview", printed.call_args.args[0])
        provider_for.assert_called_once_with(
            "codex-cli",
            "test-model",
            temperature=0,
            reasoning_enabled=None,
            reasoning_effort=None,
            max_tokens=100_000,
        )


if __name__ == "__main__":
    unittest.main()
