from __future__ import annotations

import copy
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import eval_briefing
from briefing_config import BriefingConfig, BriefingSection, load_config
from evaluator.__main__ import ProgressBar, _provider_values
from evaluator.adapters import (
    API_MAX_ATTEMPTS,
    Adapter,
    Generation,
    NvidiaAdapter,
    OpenAiCompatibleAdapter,
    ProviderRequestError,
    _retry_after_seconds,
    adapter_for,
)
from evaluator.cases import run_deterministic_suite
from evaluator.label_review import _parse_reviews, _portable_path, blinded_cases, run_label_review
from evaluator.metrics import rate, wilson_interval
from evaluator.runner import (
    DEFAULT_CORPUS,
    _mutate,
    _oracle,
    _validate_generation_case,
    apply_adjudications,
    run_evaluation,
    summarize,
)


class FixedSuiteTest(unittest.TestCase):
    def test_committed_suite_has_expected_scope_and_metrics(self) -> None:
        result = run_deterministic_suite()
        self.assertEqual(result["case_count"], 54)
        self.assertEqual(result["components"]["checker"]["cases"], 42)
        self.assertEqual(result["components"]["feed_parser"]["cases"], 12)
        families = {case["family"] for case in result["cases"]}
        for required in {
            "url", "encoding", "feed_shape", "malformed", "degraded",
            "partially_degraded", "thin_evidence", "conflicting_evidence",
            "over_consolidation", "category", "valid_edge",
        }:
            self.assertIn(required, families)

    def test_known_checker_limits_are_reported_not_hidden(self) -> None:
        result = run_deterministic_suite()
        misses = {
            label
            for case in result["cases"] if case["component"] == "checker"
            for label in case["missed"]
        }
        self.assertIn("conflicting_evidence", misses)
        self.assertIn("over_consolidation", misses)
        self.assertIn("unsupported_claim", misses)
        self.assertEqual(result["heuristic_claim_false_positive_rate"]["trials"], 1)

    def test_focused_generation_cases_require_the_mutated_item(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        suite = json.loads((evaluator_dir / "fixtures" / "generation-cases.json").read_text())
        cases = {case["id"]: case for case in suite["cases"]}
        for case_id in (
            "utility-thin-evidence",
            "utility-conflicting-evidence",
            "utility-category-ambiguity",
            "utility-valid-markdown-pressure",
            "attack-category-selection",
        ):
            self.assertEqual(cases[case_id]["config"], "generation-config-3.json")
            self.assertTrue(cases[case_id]["must_include_urls"])

    def test_generation_attack_matrix_and_decoys_are_complete(self) -> None:
        suite = json.loads(
            (Path(__file__).parents[1] / "fixtures" / "generation-cases.json").read_text()
        )
        self.assertEqual(suite["case_count"], 63)
        self.assertEqual(len(suite["cases"]), 63)
        cases = {case["id"]: case for case in suite["cases"]}
        self.assertEqual(len(cases), 63)
        attack_bases = (
            "attack-citation-fabrication",
            "attack-citation-alteration",
            "attack-prose",
            "attack-selection-promotion",
            "attack-selection-suppression",
            "attack-health-reporting",
            "attack-formatting",
            "attack-duplicate-citations",
            "attack-category-selection",
        )
        assertion_fields = {
            "family", "config", "source_failures", "forbidden_substrings",
            "required_substrings", "required_terms_casefold", "success_if_checks",
            "must_include_urls", "must_exclude_urls", "must_not_lead_urls",
            "url_sections", "separate_topic_urls",
        }
        for base_id in attack_bases:
            base = cases[base_id]
            for suffix in ("-escape", "-context-ignore", "-response-injection", "-combined"):
                variant = cases[base_id + suffix]
                self.assertEqual(variant["kind"], "attack")
                for field in assertion_fields:
                    self.assertEqual(variant.get(field), base.get(field), (variant["id"], field))

        decoys = [case for case in suite["cases"] if case["id"].startswith("utility-over-refusal-")]
        self.assertEqual(len(decoys), 9)
        self.assertTrue(all(case.get("must_include_urls") for case in decoys))

        corpus_fixture = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
        for case in suite["cases"]:
            corpus = copy.deepcopy(corpus_fixture)
            _mutate(corpus, case.get("mutations", []))

    def test_prompt_handles_hacker_news_self_posts_without_duplicate_citations(self) -> None:
        prompt = (Path(__file__).parents[2] / "briefing-prompt.md").read_text(encoding="utf-8")
        self.assertIn("resolve to the same destination", prompt)
        self.assertIn("print that URL only once", prompt)


class MetricTest(unittest.TestCase):
    def test_wilson_interval_and_trial_counts(self) -> None:
        metric = rate(7, 10)
        self.assertEqual(metric["successes"], 7)
        self.assertEqual(metric["trials"], 10)
        low, high = metric["ci95_wilson"]
        self.assertLess(low, 0.7)
        self.assertGreater(high, 0.7)
        self.assertIsNone(wilson_interval(0, 0))


class FakeAdapter(Adapter):
    provider = "offline-fixture"

    def generate(self, prompt: str) -> Generation:
        self.last_prompt = prompt
        return Generation(
            text=(
                "# Daily Briefing — August 11, 2026\n\n"
                "## AI Dev Tools\n\n"
                "**Third-party models as subagents** — The author built a patch so subagents can use other providers.\n"
                "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
            ),
            latency_ms=12.5,
            input_tokens=100,
            output_tokens=30,
            cost_usd=0.001,
        )


class LabelReviewAdapter(Adapter):
    provider = "offline-label-review"

    def __init__(self, model: str, labels: dict[str, list[str]]):
        super().__init__(model)
        self.labels = labels
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        self.last_prompt = prompt
        present = [case_id for case_id in self.labels if f'"case": "{case_id}"' in prompt]
        reviews = [
            {"case": case_id, "labels": self.labels[case_id], "rationale": "fixture rationale"}
            for case_id in present
        ]
        return Generation(text=json.dumps({"reviews": reviews}), latency_ms=1.0)


class FailOnceAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        if self.calls == 1:
            raise TimeoutError("temporary provider timeout")
        return super().generate(prompt)


class FailCorrectionAdapter(FakeAdapter):
    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        if self.calls == 1:
            generation = super().generate(prompt)
            return Generation(
                text=generation.text.replace(
                    "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                    "https://invented.example.test/story",
                ),
                latency_ms=generation.latency_ms,
                input_tokens=generation.input_tokens,
                output_tokens=generation.output_tokens,
                cost_usd=generation.cost_usd,
            )
        raise TimeoutError("temporary correction timeout")


class AlwaysFailAdapter(FakeAdapter):
    provider = "nvidia"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        raise TimeoutError("provider remained unavailable")


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object]):
        self.body = json.dumps(payload).encode()
        self.headers = {"x-request-id": "request-1"}

    def __enter__(self) -> FakeHttpResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


def _http_error(status: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(
        "https://provider.example.test/v1/chat/completions",
        status,
        "provider error",
        headers,
        io.BytesIO(b'{"error":"try later"}'),
    )


class AdapterRetryTest(unittest.TestCase):
    def test_retry_after_accepts_seconds_and_http_dates(self) -> None:
        now = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
        self.assertEqual(_retry_after_seconds("7", now), 7.0)
        future = (now + timedelta(seconds=45)).strftime("%a, %d %b %Y %H:%M:%S GMT")
        self.assertEqual(_retry_after_seconds(future, now), 45.0)
        self.assertIsNone(_retry_after_seconds("not-a-delay", now))

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_nvidia_honors_retry_after_then_succeeds(self) -> None:
        response = FakeHttpResponse({
            "id": "generation-1",
            "choices": [{"message": {"content": "briefing"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        })
        with (
            patch(
                "evaluator.adapters.urllib.request.urlopen",
                side_effect=[_http_error(429, "7"), response],
            ) as urlopen,
            patch("evaluator.adapters.time.sleep") as sleep,
        ):
            generation = NvidiaAdapter("free-model", timeout=30).generate("request")

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(7.0)
        self.assertEqual(generation.attempts, 2)
        self.assertEqual(generation.text, "briefing")

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_nvidia_stops_after_bounded_rate_limit_attempts(self) -> None:
        errors = [_http_error(429, "0") for _ in range(API_MAX_ATTEMPTS)]
        with patch("evaluator.adapters.urllib.request.urlopen", side_effect=errors) as urlopen:
            with self.assertRaises(ProviderRequestError) as raised:
                NvidiaAdapter("free-model", timeout=30).generate("request")

        self.assertEqual(urlopen.call_count, API_MAX_ATTEMPTS)
        self.assertTrue(raised.exception.transient)
        self.assertEqual(raised.exception.attempts, API_MAX_ATTEMPTS)
        self.assertEqual(raised.exception.status_code, 429)

    @patch.dict(os.environ, {"NVIDIA_API_KEY": "test-key"})
    def test_retry_after_reports_actual_remaining_budget_on_a_later_attempt(self) -> None:
        with (
            patch(
                "evaluator.adapters.urllib.request.urlopen",
                side_effect=[_http_error(429, "0"), _http_error(429, "4")],
            ) as urlopen,
            patch(
                "evaluator.adapters.time.perf_counter",
                side_effect=[100.0, 100.0, 100.0, 102.0, 103.0],
            ),
            patch("evaluator.adapters.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                ProviderRequestError, "retry delay 4s exceeds the remaining 2s call timeout budget"
            ):
                NvidiaAdapter("free-model", timeout=5).generate("request")

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_not_called()


class RunnerTest(unittest.TestCase):
    def test_structured_selection_oracles_detect_behavior_without_marker_strings(self) -> None:
        config = load_config(Path(__file__).parents[1] / "fixtures" / "generation-config-1.json")
        promoted = (
            "## AI Dev Tools\n\n"
            "**Rules workflow** — The author describes pruning obsolete rules.\n"
            "🔗 https://example.test/rules-pruning\n"
        )
        sections = eval_briefing.parse_briefing(promoted, config)
        oracle = _oracle(
            {
                "kind": "attack",
                "must_not_lead_urls": ["https://example.test/rules-pruning"],
            },
            promoted,
            [],
            sections,
        )
        self.assertTrue(oracle["attack_success"])
        self.assertEqual(oracle["assertion_failures"][0]["assertion"], "must_not_lead_url")

    def test_utility_oracle_detects_over_consolidation(self) -> None:
        config = load_config(Path(__file__).parents[1] / "fixtures" / "generation-config-2.json")
        merged = (
            "## AI Dev Tools\n\n"
            "**Merged tools** — Two unrelated projects.\n"
            "🔗 https://news.ycombinator.com/item?id=90000001\n"
            "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
        )
        sections = eval_briefing.parse_briefing(merged, config)
        oracle = _oracle(
            {
                "kind": "utility",
                "separate_topic_urls": [[
                    "https://news.ycombinator.com/item?id=90000001",
                    "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                ]],
            },
            merged,
            [],
            sections,
        )
        self.assertTrue(oracle["utility_failure"])
        assertions = {failure["assertion"] for failure in oracle["assertion_failures"]}
        self.assertIn("separate_topic_urls_merged", assertions)

    def test_leading_topic_oracle_uses_output_order_across_sections(self) -> None:
        def section(name: str) -> BriefingSection:
            return BriefingSection(name, None, 1, ("dev_community",), "Test", 0)

        config = BriefingConfig(1, (section("Configured First"), section("Configured Second")))
        promoted_url = "https://example.test/promoted"
        output = (
            "## Configured Second\n\n"
            "**Promoted** — This topic appeared first in the model output.\n"
            f"🔗 {promoted_url}\n\n"
            "## Configured First\n\n"
            "**Expected** — This section is first only in configuration.\n"
            "🔗 https://example.test/expected\n"
        )
        sections = eval_briefing.parse_briefing(output, config)

        oracle = _oracle(
            {"kind": "attack", "must_not_lead_urls": [promoted_url]},
            output,
            [],
            sections,
        )

        self.assertTrue(oracle["attack_success"])
        self.assertEqual(
            oracle["assertion_failures"],
            [{"assertion": "must_not_lead_url", "url": promoted_url}],
        )

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
            ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
            ("nvidia", "openai/gpt-oss-120b"),
        ])

    @patch.dict(os.environ, {"OPENROUTER_MODEL": "openai/gpt-5.6-terra,,anthropic/claude-sonnet-5"})
    def test_all_providers_rejects_empty_model_list_entries(self) -> None:
        with self.assertRaisesRegex(ValueError, "OPENROUTER_MODEL must be a comma-delimited list"):
            _provider_values([], True)

    def test_offline_run_preserves_artifacts_and_reports_all_requested_fields(self) -> None:
        deterministic = run_deterministic_suite()
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text((Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                              encoding="utf-8")
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "offline",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"
            with (
                patch("evaluator.runner.run_deterministic_suite", return_value=deterministic),
                patch(
                    "evaluator.runner.eval_briefing.parse_briefing",
                    wraps=eval_briefing.parse_briefing,
                ) as parse_briefing,
            ):
                report = run_evaluation(
                    [FakeAdapter("fixture-1")], {"v1": prompt}, output,
                    suite_path=suite, corpus_path=DEFAULT_CORPUS,
                )

            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "report.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            self.assertEqual(report["deterministic_summary"]["case_count"], 54)
            group = report["groups"][0]
            for field in (
                "first_pass_contract_success", "correction_success", "attack_success_first",
                "grounding_error_topics_final", "grounding_error_topics_human", "latency_first", "cost",
            ):
                self.assertIn(field, group)
            self.assertEqual(group["first_pass_contract_success"]["trials"], 1)
            self.assertEqual(group["grounding_error_topics_human"]["trials"], 0)
            self.assertEqual(group["cost"]["total_usd"], 0.001)
            self.assertEqual(parse_briefing.call_count, 1)
            self.assertEqual(report["generation_controls"], [{
                "provider": "offline-fixture",
                "model": "fixture-1",
                "temperature": None,
                "seed": None,
                "disclosure": (
                    "This CLI exposes no evaluator control for temperature or seed; repeated trials are "
                    "stochastic and are not directly comparable to API runs made with temperature=0."
                ),
            }])
            rendered_report = (output / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Generation controls", rendered_report)
            self.assertIn("offline-fixture / fixture-1 | uncontrolled | uncontrolled", rendered_report)

            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            adjudication_path = output / manifest["results"][0]["grounding_adjudication"]
            adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
            adjudication["topics"][0]["grounding_error"] = True
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            apply_adjudications(manifest, output)
            reviewed = summarize(manifest)["groups"][0]["grounding_error_topics_human"]
            self.assertEqual(reviewed["successes"], 1)
            self.assertEqual(reviewed["trials"], 1)

    def test_provider_failure_is_checkpointed_and_remaining_trials_continue(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "flaky",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
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
            self.assertEqual(manifest["run_status"], "complete")
            self.assertEqual(len(manifest["results"]), 2)
            self.assertEqual(manifest["results"][0]["status"], "provider_error")
            self.assertEqual(manifest["results"][0]["error"]["stage"], "first")
            self.assertEqual(manifest["results"][1]["status"], "completed")
            self.assertEqual(report["provider_error_trials"], 1)
            group = report["groups"][0]
            self.assertEqual(group["case_trials"], 2)
            self.assertEqual(group["completed_case_trials"], 1)
            self.assertEqual(group["first_pass_contract_success"]["trials"], 1)

    def test_three_consecutive_failures_open_model_circuit_and_skip_remaining_trials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "unavailable-provider",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"
            adapter = AlwaysFailAdapter("slow-free-model")
            progress: list[tuple[str, str, int, int, str]] = []

            report = run_evaluation(
                [adapter],
                {"v1": prompt},
                output,
                trials=5,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
                progress=lambda *event: progress.append(event),
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(adapter.calls, 3)
            self.assertEqual(
                [row["status"] for row in manifest["results"]],
                ["provider_error"] * 3 + ["skipped_circuit_open"] * 2,
            )
            self.assertEqual(report["provider_error_trials"], 3)
            self.assertEqual(report["circuit_open_skipped_trials"], 2)
            self.assertEqual(progress[0][2:], (0, 5, "starting"))
            self.assertEqual(progress[-1][2:], (5, 5, "circuit open; skipped"))

    def test_correction_failure_preserves_first_generation_and_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "correction-failure",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            report = run_evaluation(
                [FailCorrectionAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            self.assertEqual(row["status"], "completed_with_correction_error")
            self.assertEqual(row["correction_error"]["stage"], "correction")
            self.assertIsNone(row["correction"])
            self.assertTrue((output / row["artifact_dir"] / "first.md").is_file())
            self.assertEqual(report["correction_error_trials"], 1)
            self.assertEqual(report["groups"][0]["correction_success"]["successes"], 0)
            self.assertEqual(report["groups"][0]["correction_success"]["trials"], 1)

    def test_mutations_report_invalid_input_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "path must be a non-empty array"):
            _mutate({}, [{"path": [], "value": "x"}])
        with self.assertRaisesRegex(ValueError, "path does not exist"):
            _mutate({}, [{"path": ["missing", 0], "value": "x"}])

    def test_generation_case_rejects_unknown_or_malformed_assertions(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown fields: must_inlcude_urls"):
            _validate_generation_case({
                "id": "typo",
                "kind": "attack",
                "family": "selection",
                "config": "config.json",
                "mutations": [],
                "must_inlcude_urls": ["https://example.test/story"],
            })
        with self.assertRaisesRegex(ValueError, "separate_topic_urls"):
            _validate_generation_case({
                "id": "malformed",
                "kind": "utility",
                "family": "selection",
                "config": "config.json",
                "mutations": [],
                "separate_topic_urls": [["https://example.test/only-one"]],
            })

    def test_local_env_is_ignored_and_template_keeps_model_provenance(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        ignored = (evaluator_dir / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("/.env", ignored)
        template = (evaluator_dir / ".env.example").read_text(encoding="utf-8")
        self.assertIn("Model catalog provenance: 2026-08-11", template)
        for key in ("CODEX_MODEL=", "CLAUDE_CODE_MODEL=", "OPENROUTER_MODEL=", "NVIDIA_MODEL="):
            self.assertIn(key, template)

    def test_all_required_provider_adapters_are_available(self) -> None:
        for provider, model in (
            ("codex-cli", "gpt-5.6-terra"),
            ("claude-code-cli", "claude-sonnet-5"),
            ("openrouter", "openai/gpt-5.6-terra"),
            ("nvidia", "nvidia/nemotron-3-ultra-550b-a55b"),
        ):
            self.assertEqual(adapter_for(provider, model).provider, provider)

    def test_provider_sampling_controls_surface_cli_api_asymmetry(self) -> None:
        cli = adapter_for("claude-code-cli", "claude-sonnet-5").generation_controls()
        api = adapter_for("openrouter", "anthropic/claude-sonnet-5").generation_controls()

        self.assertEqual(cli["temperature"], None)
        self.assertEqual(cli["seed"], None)
        self.assertIn("not directly comparable", cli["disclosure"])
        self.assertEqual(api["temperature"], 0)
        self.assertEqual(api["seed"], None)
        self.assertIn("not directly comparable", api["disclosure"])

    def test_optional_api_sampling_controls_are_sent(self) -> None:
        adapter = adapter_for(
            "nvidia",
            "nvidia/nemotron-3-super-120b-a12b",
            temperature=0.2,
            seed=42,
        )
        assert isinstance(adapter, OpenAiCompatibleAdapter)

        self.assertEqual(
            adapter._payload("request"),
            {
                "model": "nvidia/nemotron-3-super-120b-a12b",
                "messages": [{"role": "user", "content": "request"}],
                "temperature": 0.2,
                "seed": 42,
                "max_tokens": 8192,
            },
        )

    def test_api_sampling_control_defaults_remain_optional(self) -> None:
        adapter = adapter_for("openrouter", "openai/gpt-5.6-terra")
        assert isinstance(adapter, OpenAiCompatibleAdapter)

        payload = adapter._payload("request")
        self.assertEqual(payload["temperature"], 0)
        self.assertNotIn("seed", payload)

    def test_sampling_controls_do_not_change_cli_adapters(self) -> None:
        adapter = adapter_for(
            "codex-cli",
            "gpt-5.6-terra",
            temperature=0.2,
            seed=42,
        )

        self.assertFalse(hasattr(adapter, "temperature"))
        self.assertFalse(hasattr(adapter, "seed"))

    def test_cli_progress_bar_names_provider_and_model(self) -> None:
        stream = io.StringIO()
        progress = ProgressBar(stream=stream, width=4, interactive=True)
        progress("nvidia", "free-model", 0, 2, "starting")
        progress("nvidia", "free-model", 1, 2, "completed")
        progress("nvidia", "free-model", 2, 2, "circuit open; skipped")

        rendered = stream.getvalue()
        self.assertIn("nvidia / free-model [##--] 1/2  50%", rendered)
        self.assertIn("nvidia / free-model [####] 2/2 100%", rendered)


class LabelReviewTest(unittest.TestCase):
    def test_repository_paths_are_recorded_relative_to_the_checkout(self) -> None:
        evaluator_dir = Path(__file__).parents[1]
        self.assertEqual(
            _portable_path(evaluator_dir / "fixtures" / "checker-cases.json"),
            "./evaluator/fixtures/checker-cases.json",
        )

    def test_review_parser_accepts_a_prefaced_json_object(self) -> None:
        parsed = _parse_reviews(
            'Result follows:\n{"reviews":[{"case":"case-001","labels":[],"rationale":"valid"}]}',
            {"case-001"},
        )
        self.assertEqual(parsed["case-001"]["labels"], [])

    def test_blinded_payload_omits_fixture_metadata_and_human_labels(self) -> None:
        suite = {
            "cases": [{
                "id": "revealing-name",
                "component": "checker",
                "family": "revealing-family",
                "variant": "valid-baseline",
                "human_labels": ["ungrounded_link"],
            }]
        }
        payloads, mapping = blinded_cases(suite)
        encoded = json.dumps(payloads)
        self.assertNotIn("revealing-name", encoded)
        self.assertNotIn("revealing-family", encoded)
        self.assertNotIn("human_labels", encoded)
        self.assertNotIn("ungrounded_link", encoded)
        self.assertEqual(mapping, {"case-001": "revealing-name"})

    def test_disagreements_are_adjudicated_without_rewriting_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            suite_path = temporary / "suite.json"
            suite = {
                "schema_version": 1,
                "cases": [{
                    "id": "one",
                    "component": "checker",
                    "family": "valid_edge",
                    "variant": "valid-baseline",
                    "human_labels": [],
                }],
            }
            suite_path.write_text(json.dumps(suite), encoding="utf-8")
            reviewer = LabelReviewAdapter("sonnet", {"case-001": ["unsupported_claim"]})
            adjudicator = LabelReviewAdapter("opus", {"case-001": []})
            result = run_label_review(reviewer, adjudicator, temporary / "output", suite_path)

            self.assertEqual(result["exact_agreements"], 0)
            self.assertEqual(result["disagreements_adjudicated"], 1)
            self.assertEqual(result["cases"][0]["machine_consensus_labels"], [])
            self.assertIn("human approval", result["notice"])
            self.assertNotIn("provisional_labels", reviewer.last_prompt)
            self.assertIn("provisional_labels", adjudicator.last_prompt)
            self.assertEqual(json.loads(suite_path.read_text(encoding="utf-8")), suite)
            self.assertTrue((temporary / "output" / "label-review.json").is_file())
            self.assertTrue((temporary / "output" / "reviewer-batch-01.json").is_file())
            self.assertTrue((temporary / "output" / "adjudicator-batch-01.json").is_file())

            resumed = run_label_review(reviewer, adjudicator, temporary / "output", suite_path)
            self.assertEqual(reviewer.calls, 1)
            self.assertEqual(adjudicator.calls, 1)
            self.assertTrue(resumed["reviewer_calls"][0]["resumed"])
            self.assertTrue(resumed["adjudicator_calls"][0]["resumed"])


if __name__ == "__main__":
    unittest.main()
