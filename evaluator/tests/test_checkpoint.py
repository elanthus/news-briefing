"""Evaluator checkpoint regression coverage."""
from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

import evaluator.__main__ as evaluator_cli
from evaluator.adapters import (
    Generation,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    _checkpoint,
    run_evaluation,
)
from evaluator.tests.support import (
    AlwaysFailAdapter,
    CostedFakeAdapter,
    FakeAdapter,
    RecordingFakeAdapter,
    StructuredFakeAdapter,
    _final_provenance,
    _resume_fixture,
)


class FailOnceAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary provider timeout")
        return super().generate(prompt)



class CheckpointTest(unittest.TestCase):
    def test_markdown_checkpoint_is_rejected_before_provider_calls(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            run_evaluation([FakeAdapter("fixture")], {"production": prompt}, output,
                           suite_path=suite, corpus_path=DEFAULT_CORPUS)
            path = output / "manifest.json"
            manifest = json.loads(path.read_text())
            manifest["generation_path"] = "markdown"
            manifest["run_status"] = "running"
            manifest.pop("completed_at", None)
            path.write_text(json.dumps(manifest))
            adapter = FakeAdapter("fixture")
            with patch.object(adapter, "generate_structured") as generate:
                with self.assertRaisesRegex(ValueError, "generation_path"):
                    run_evaluation([adapter], {"production": prompt}, output,
                                   suite_path=suite, corpus_path=DEFAULT_CORPUS, resume=True)
                generate.assert_not_called()


    def test_production_parity_resume_requires_structured_artifacts(self) -> None:
        class InterruptSecondStructuredCall(StructuredFakeAdapter):
            def generate_structured(
                self, prompt: str, output_schema: dict[str, Any], trace_id: str
            ) -> Generation:
                # A complete production-parity case makes selection and prose
                # calls. Interrupt the selection call for the second case.
                if len(self.requests) == 2:
                    raise KeyboardInterrupt("simulated structured interruption")
                return super().generate_structured(prompt, output_schema, trace_id)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=2)
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [InterruptSecondStructuredCall("fixture")],
                    {"production": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            first_artifact = output / manifest["results"][0]["artifact_dir"]
            (first_artifact / "first-structured.json").unlink()
            resumed = StructuredFakeAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "missing artifact files"):
                run_evaluation(
                    [resumed],
                    {"production": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                        resume=True,
                )
            self.assertEqual(resumed.requests, [])


    def test_development_resume_ignores_nonruntime_git_metadata_changes(self) -> None:
        class InterruptImmediately(FakeAdapter):
            def generate(self, prompt: str) -> Generation:
                raise KeyboardInterrupt("simulated interruption")

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            initial_code = {
                "commit": "initial",
                "tree": "initial-tree",
                "dirty": False,
                "tags": [],
                "runtime_source_sha256": {"evaluator/runner.py": "same-runtime"},
            }
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [InterruptImmediately("fixture")],
                    {"production": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    source_provenance=initial_code,
                )

            changed_runtime = {
                **initial_code,
                "runtime_source_sha256": {"evaluator/runner.py": "changed-runtime"},
            }
            blocked = RecordingFakeAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "immutable fields differ: code"):
                run_evaluation(
                    [blocked],
                    {"production": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                    source_provenance=changed_runtime,
                )
            self.assertEqual(blocked.requests, [])

            changed_metadata = {
                **initial_code,
                "commit": "docs-only-change",
                "tree": "docs-only-tree",
                "dirty": True,
                "tags": ["unrelated-tag"],
            }
            run_evaluation(
                [FakeAdapter("fixture")],
                {"production": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                resume=True,
                source_provenance=changed_metadata,
            )


    def test_final_resume_reuses_generated_execution_seed_when_omitted(self) -> None:
        class InterruptImmediately(FakeAdapter):
            def generate(self, prompt: str) -> Generation:
                raise KeyboardInterrupt("simulated process interruption")

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, production, output = _resume_fixture(temporary, case_count=1)
            candidate = temporary / "candidate.md"
            candidate.write_text("Produce the candidate briefing.", encoding="utf-8")
            prompts = {"production": production, "candidate": candidate}
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [InterruptImmediately("fixture")],
                    prompts,
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    run_kind="final",
                    source_provenance=_final_provenance(),
                )
            generated_seed = json.loads(
                (output / "manifest.json").read_text(encoding="utf-8")
            )["execution_seed"]

            run_evaluation(
                [FakeAdapter("fixture")],
                prompts,
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                run_kind="final",
                resume=True,
                source_provenance=_final_provenance(
                    ["portfolio-v2-source", "release-alias"]
                ),
            )
            resumed = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resumed["execution_seed"], generated_seed)
            self.assertEqual(len(resumed["results"]), 2)


    def test_interrupted_run_resumes_without_repeating_checkpointed_rows(self) -> None:
        class InterruptSecondCall(FakeAdapter):
            def __init__(self, model: str):
                super().__init__(model)
                self.calls = 0

            def generate(self, prompt: str) -> Generation:
                self.calls += 1
                if self.calls == 2:
                    raise KeyboardInterrupt("simulated process interruption")
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary)
            interrupted = InterruptSecondCall("fixture")
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [interrupted],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                )

            checkpoint = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["run_status"], "running")
            self.assertEqual(len(checkpoint["results"]), 1)
            first_row = copy.deepcopy(checkpoint["results"][0])
            interrupted_dir = output / "offline-fixture__fixture__v1__resume-1__1"
            self.assertTrue(interrupted_dir.is_dir())
            stale_names = (
                "error.json",
                "correction-error.json",
                "semantic-adjudication.json",
                "model-corpus.json",
                "citation-map.json",
                "first-structured.json",
                "final-structured.json",
            )
            for name in stale_names:
                (interrupted_dir / name).write_text("stale interrupted artifact", encoding="utf-8")

            resumed_adapter = RecordingFakeAdapter("fixture")
            report = run_evaluation(
                [resumed_adapter],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                resume=True,
            )

            resumed = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resumed["run_status"], "complete")
            self.assertEqual(len(resumed["results"]), 3)
            self.assertEqual(resumed["results"][0], first_row)
            self.assertEqual(len(resumed_adapter.requests), 2)
            self.assertEqual(len(resumed["resume_history"]), 1)
            self.assertEqual(report["operations"]["recorded_case_trials"], 3)
            for name in stale_names:
                if (interrupted_dir / name).exists():
                    self.assertNotEqual((interrupted_dir / name).read_text(), "stale interrupted artifact")


    def test_fully_recorded_running_checkpoint_finalizes_on_resume(self) -> None:
        def interrupt_after_final_row(
            manifest: dict[str, Any], output_dir: Path
        ) -> dict[str, Any]:
            report = _checkpoint(manifest, output_dir)
            if (
                manifest["run_status"] == "running"
                and len(manifest["results"]) == manifest["planned_case_trials"]
            ):
                raise KeyboardInterrupt("simulated interruption before terminal status")
            return report

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            with (
                patch("evaluator.runner._checkpoint", side_effect=interrupt_after_final_row),
                self.assertRaises(KeyboardInterrupt),
            ):
                run_evaluation(
                    [FakeAdapter("fixture")],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                )

            checkpoint = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["run_status"], "running")
            self.assertEqual(len(checkpoint["results"]), checkpoint["planned_case_trials"])
            final_row = copy.deepcopy(checkpoint["results"][0])

            resumed_adapter = RecordingFakeAdapter("fixture")
            progress_events: list[tuple[str, str, int, int, str]] = []

            def record_progress(
                provider: str, model: str, completed: int, total: int, status: str
            ) -> None:
                progress_events.append((provider, model, completed, total, status))

            original_read_bytes = Path.read_bytes
            prompt_reads = 0

            def count_prompt_reads(path: Path) -> bytes:
                nonlocal prompt_reads
                if path == prompt:
                    prompt_reads += 1
                return original_read_bytes(path)

            with patch.object(
                Path, "read_bytes", autospec=True, side_effect=count_prompt_reads
            ):
                report = run_evaluation(
                    [resumed_adapter],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    progress=record_progress,
                    resume=True,
                )

            resumed = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resumed["run_status"], "complete")
            self.assertIsNotNone(resumed["completed_at"])
            self.assertEqual(resumed["results"], [final_row])
            self.assertEqual(resumed_adapter.requests, [])
            self.assertEqual(progress_events, [])
            self.assertEqual(prompt_reads, 1)
            self.assertEqual(len(resumed["resume_history"]), 1)
            self.assertEqual(report["operations"]["recorded_case_trials"], 1)


    def test_resume_rejects_more_results_than_planned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=1)
            run_evaluation(
                [FakeAdapter("fixture")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["run_status"] = "running"
            manifest["completed_at"] = None
            manifest["results"].append(copy.deepcopy(manifest["results"][0]))
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "more results than planned"):
                run_evaluation(
                    [FakeAdapter("fixture")],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )


    def test_resume_reconstructs_circuit_breaker_state(self) -> None:
        class FailTwiceThenInterrupt(FakeAdapter):
            provider = "nvidia"

            def __init__(self, model: str):
                super().__init__(model)
                self.calls = 0

            def generate(self, prompt: str) -> Generation:
                self.calls += 1
                if self.calls == 3:
                    raise KeyboardInterrupt("simulated process interruption")
                raise TimeoutError("provider unavailable")

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary, case_count=5)
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [FailTwiceThenInterrupt("model")],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                )

            resumed_adapter = AlwaysFailAdapter("model")
            run_evaluation(
                [resumed_adapter],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                resume=True,
            )
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resumed_adapter.calls, 1)
            self.assertEqual(
                [row["status"] for row in manifest["results"]],
                ["provider_error"] * 3 + ["skipped_circuit_open"] * 2,
            )


    def test_resume_reconstructs_observed_cost_before_next_call(self) -> None:
        class CostOnceThenInterrupt(CostedFakeAdapter):
            def __init__(self, model: str):
                super().__init__(model)
                self.calls = 0

            def generate(self, prompt: str) -> Generation:
                self.calls += 1
                if self.calls == 2:
                    raise KeyboardInterrupt("simulated process interruption")
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, output = _resume_fixture(temporary)
            ceiling = 0.0015
            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [CostOnceThenInterrupt("model")],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    cost_ceiling_usd=ceiling,
                    cost_ceiling_provider="costed-fixture",
                )

            class CountingCostedAdapter(CostedFakeAdapter):
                def __init__(self, model: str):
                    super().__init__(model)
                    self.calls = 0

                def generate(self, request: str) -> Generation:
                    self.calls += 1
                    return super().generate(request)

            resumed_adapter = CountingCostedAdapter("model")
            run_evaluation(
                [resumed_adapter],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                cost_ceiling_usd=ceiling,
                cost_ceiling_provider="costed-fixture",
                resume=True,
            )
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(resumed_adapter.calls, 1)
            self.assertEqual(len(manifest["results"]), 2)
            self.assertEqual(manifest["run_status"], "stopped_cost_ceiling")
            self.assertEqual(manifest["observed_ceiling_cost_usd"], 0.002)


    def test_resume_refuses_completed_checkpoint_without_calls(self) -> None:
        class CountingAdapter(FakeAdapter):
            def __init__(self, model: str):
                super().__init__(model)
                self.calls = 0

            def generate(self, prompt: str) -> Generation:
                self.calls += 1
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite, prompt, complete_output = _resume_fixture(temporary, case_count=1)
            run_evaluation(
                [FakeAdapter("fixture")],
                {"v1": prompt},
                complete_output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            counter = CountingAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "interrupted manifest"):
                run_evaluation(
                    [counter],
                    {"v1": prompt},
                    complete_output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )
            self.assertEqual(counter.calls, 0)

    def test_resume_refuses_incompatible_and_corrupt_checkpoints_without_calls(self) -> None:
        class CountingAdapter(FakeAdapter):
            def __init__(self, model: str):
                super().__init__(model)
                self.calls = 0

            def generate(self, prompt: str) -> Generation:
                self.calls += 1
                return super().generate(prompt)

        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            interrupted_root = temporary / "interrupted"
            interrupted_root.mkdir()
            interrupted_suite, interrupted_prompt, interrupted_output = _resume_fixture(
                interrupted_root, case_count=2
            )
            interrupted_suite_data = json.loads(interrupted_suite.read_text(encoding="utf-8"))
            interrupted_suite_data["cases"][0]["must_convey"] = [{
                "url": "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                "propositions": ["The author built a patch for third-party model subagents."],
            }]
            interrupted_suite.write_text(
                json.dumps(interrupted_suite_data), encoding="utf-8"
            )

            class InterruptSecondCall(FakeAdapter):
                def __init__(self, model: str):
                    super().__init__(model)
                    self.calls = 0

                def generate(self, request: str) -> Generation:
                    self.calls += 1
                    if self.calls == 2:
                        raise KeyboardInterrupt
                    return super().generate(request)

            with self.assertRaises(KeyboardInterrupt):
                run_evaluation(
                    [InterruptSecondCall("fixture")],
                    {"v1": interrupted_prompt},
                    interrupted_output,
                    suite_path=interrupted_suite,
                    corpus_path=DEFAULT_CORPUS,
                )

            checkpoint_path = interrupted_output / "manifest.json"
            original_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertIsNotNone(original_checkpoint["results"][0]["semantic_adjudication"])
            identity_mutations = {
                "suite_sha256": "different-suite",
                "corpus_sha256": "different-corpus",
                "case_corpus_sha256": {},
                "config_sha256": {},
                "protocol_sha256": "different-protocol",
                "prompt_sha256": {},
                "prompt_order": ["different-prompt"],
                "trials_per_case": 99,
                "run_kind": "pilot",
                "execution_order": "different-order",
                "execution_seed": 99,
                "cost_ceiling_usd": 1.0,
                "cost_ceiling_provider": "offline-fixture",
                "circuit_breaker_threshold": 99,
                "generation_controls": [],
                "adapter_timeouts_seconds": [],
            }
            for field, changed_value in identity_mutations.items():
                with self.subTest(identity_field=field):
                    changed = copy.deepcopy(original_checkpoint)
                    changed[field] = changed_value
                    checkpoint_path.write_text(json.dumps(changed), encoding="utf-8")
                    counter = CountingAdapter("fixture")
                    with self.assertRaisesRegex(ValueError, "immutable fields differ"):
                        run_evaluation(
                            [counter],
                            {"v1": interrupted_prompt},
                            interrupted_output,
                            suite_path=interrupted_suite,
                            corpus_path=DEFAULT_CORPUS,
                            resume=True,
                        )
                    self.assertEqual(counter.calls, 0)
            checkpoint_path.write_text(json.dumps(original_checkpoint), encoding="utf-8")

            changed = copy.deepcopy(original_checkpoint)
            changed["results"][0]["source_failure_count"] = 99
            checkpoint_path.write_text(json.dumps(changed), encoding="utf-8")
            counter = CountingAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "result metadata differs"):
                run_evaluation(
                    [counter],
                    {"v1": interrupted_prompt},
                    interrupted_output,
                    suite_path=interrupted_suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )
            self.assertEqual(counter.calls, 0)

            changed = copy.deepcopy(original_checkpoint)
            changed["results"][0]["semantic_adjudication"] = None
            checkpoint_path.write_text(json.dumps(changed), encoding="utf-8")
            counter = CountingAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "semantic adjudication presence"):
                run_evaluation(
                    [counter],
                    {"v1": interrupted_prompt},
                    interrupted_output,
                    suite_path=interrupted_suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )
            self.assertEqual(counter.calls, 0)
            checkpoint_path.write_text(json.dumps(original_checkpoint), encoding="utf-8")

            counter = CountingAdapter("different-model")
            with self.assertRaisesRegex(ValueError, "immutable fields differ"):
                run_evaluation(
                    [counter],
                    {"v1": interrupted_prompt},
                    interrupted_output,
                    suite_path=interrupted_suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )
            self.assertEqual(counter.calls, 0)

            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            checkpoint["results"][0]["trial"] = 99
            checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
            counter = CountingAdapter("fixture")
            with self.assertRaisesRegex(ValueError, "exact execution-plan prefix"):
                run_evaluation(
                    [counter],
                    {"v1": interrupted_prompt},
                    interrupted_output,
                    suite_path=interrupted_suite,
                    corpus_path=DEFAULT_CORPUS,
                    resume=True,
                )
            self.assertEqual(counter.calls, 0)



    def test_run_cli_exposes_explicit_resume_flag(self) -> None:
        result = {
            "operations": {
                "provider_error_trials": 0,
                "circuit_open_skipped_trials": 0,
                "correction_error_trials": 0,
                "run_status": "complete",
            }
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "interrupted-run"
            argv = [
                "evaluator",
                "run",
                "--provider", "openrouter=fixture",
                "--output-dir", str(output),
                "--resume",
            ]
            with (
                patch.object(sys, "argv", argv),
                patch.object(evaluator_cli, "_preflight"),
                patch.object(evaluator_cli, "production_adapter_for", return_value=FakeAdapter("fixture")),
                patch.object(evaluator_cli, "run_evaluation", return_value=result) as run,
                patch("builtins.print"),
            ):
                self.assertEqual(evaluator_cli.main(), 0)
            self.assertTrue(run.call_args.kwargs["resume"])
            self.assertEqual(run.call_args.args[2], output)

        with (
            patch.object(sys, "argv", [
                "evaluator", "run", "--provider", "openrouter=fixture", "--resume",
            ]),
            patch("sys.stderr", new_callable=io.StringIO) as stderr,
            self.assertRaisesRegex(SystemExit, "2"),
        ):
            evaluator_cli.main()
        self.assertIn("--resume requires --output-dir", stderr.getvalue())


    def test_provider_failure_is_checkpointed_and_remaining_trials_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "case_count": 1,
                        "cases": [
                            {
                                "id": "flaky",
                                "kind": "utility",
                                "family": "valid_edge",
                                "config": "config.json",
                                "mutations": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            report = run_evaluation(
                [FailOnceAdapter("fixture-1")],
                {"v1": prompt},
                output,
                trials=2,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run_status"], "completed_with_errors")
            self.assertEqual(len(manifest["results"]), 2)
            self.assertEqual(manifest["results"][0]["status"], "provider_error")
            self.assertEqual(manifest["results"][0]["error"]["stage"], "first")
            self.assertEqual(manifest["results"][1]["status"], "completed")
            self.assertEqual(report["operations"]["provider_error_trials"], 1)
            self.assertEqual(report["operations"]["run_status"], "completed_with_errors")
            group = report["operations"]["groups"][0]
            self.assertEqual(group["case_trials"], 2)
            self.assertEqual(group["completed_case_trials"], 1)
            self.assertEqual(group["cost"]["reported_calls"], 3)
            self.assertEqual(group["cost"]["unreported_calls"], 1)
            self.assertEqual(group["cost"]["total_usd"], 0.001)
            utility = report["score_families"]["application_utility"]["groups"][0]
            self.assertEqual(utility["first_pass_contract_success"]["trials"], 1)

