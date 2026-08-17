import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_runner.checkpoint import RunStore
from agent_runner.models import GenerationRequest, ModelResponse
from agent_runner.runner import RunnerSettings, build_request, run_workflow
from test_briefing_output import ROOT, fixture_contract


class FakeProvider:
    name = "fake"
    model = "deterministic"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests: list[GenerationRequest] = []

    def info(self):
        return {
            "provider": self.name,
            "model": self.model,
            "authentication": "none",
            "tool_policy": "no tools",
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
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.status, "WARN")
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["final"]["status"], "WARN")
        self.assertIn("### Validation status", briefing)
        self.assertIn("**Status: WARN**", briefing)
        self.assertEqual(len(provider.requests), 1)

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
            " https://attacker.invalid/instruction"
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
        self.assertEqual(result.status, "WARN")
        self.assertEqual([row["kind"] for row in manifest["attempts"]], ["initial", "correction"])
        self.assertFalse(manifest["attempts"][0]["contract_success"])
        self.assertTrue(manifest["attempts"][1]["contract_success"])
        self.assertIn("CORRECTION PASS", provider.requests[1].prompt)
        self.assertIn("unknown_citation_ref", provider.requests[1].prompt)
        self.assertNotIn("attacker.invalid", provider.requests[1].prompt)

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
            self.assertEqual(result.status, "WARN")
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(resumed_provider.requests, [])


if __name__ == "__main__":
    unittest.main()
