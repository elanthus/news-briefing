from __future__ import annotations

import copy
import hashlib
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

import corpus_schema
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
from evaluator.quality import (
    QUALITY_AXES,
    _parse_judgment,
    _topics,
    matched_pairs,
    run_quality_judging,
)
from evaluator.runner import (
    DEFAULT_CORPUS,
    _attack_dimensions,
    _mutate,
    _oracle,
    _semantic_adjudication_template,
    _validate_generation_case,
    apply_adjudications,
    correction_request,
    markdown_report,
    run_evaluation,
    summarize,
)
from evaluator.semantic_review import _judgment_prompt as _semantic_judgment_prompt
from evaluator.semantic_review import _parse_judgment as _parse_semantic_judgment
from evaluator.semantic_review import run_semantic_judging


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
        self.assertEqual(suite["case_count"], 67)
        self.assertEqual(len(suite["cases"]), 67)
        cases = {case["id"]: case for case in suite["cases"]}
        self.assertEqual(len(cases), 67)
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
            "success_if_checks", "must_include_urls", "must_exclude_urls",
            "must_not_lead_urls", "url_sections", "separate_topic_urls", "must_convey",
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
        self.assertEqual(sum(bool(case.get("must_convey")) for case in decoys), 8)

        attack_dimensions = {
            _attack_dimensions(case["id"])
            for case in suite["cases"]
            if case["kind"] == "attack"
        }
        self.assertEqual({behavior for behavior, _ in attack_dimensions}, {
            "category-selection",
            "citation-alteration",
            "citation-fabrication",
            "duplicate-citations",
            "formatting",
            "health-reporting",
            "prose",
            "selection-promotion",
            "selection-suppression",
        })
        self.assertEqual({technique for _, technique in attack_dimensions}, {
            "combined",
            "context_ignore",
            "direct",
            "escape_character",
            "response_injection",
        })

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


class FakeAdapterVariant(Adapter):
    """A second, differently-worded model producing a topic on the same corpus URL as FakeAdapter."""

    provider = "offline-fixture-b"

    def generate(self, prompt: str) -> Generation:
        self.last_prompt = prompt
        return Generation(
            text=(
                "# Daily Briefing — August 11, 2026\n\n"
                "## AI Dev Tools\n\n"
                "**Subagents gain third-party model support** — A community patch lets subagents call "
                "other providers while billing stays on the subscription plan.\n"
                "🔗 https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/\n"
            ),
            latency_ms=10.0,
            input_tokens=90,
            output_tokens=25,
            cost_usd=0.001,
        )


class FakeJudgeAdapter(Adapter):
    """A judge with pure position bias: it always prefers whichever text is labeled Option A."""

    provider = "offline-judge"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        payload = dict.fromkeys((*QUALITY_AXES, "overall"), "a")
        payload["rationale"] = "fixture rationale: always prefers Option A"
        return Generation(text=json.dumps(payload), latency_ms=1.0)


class FakeContentAwareJudgeAdapter(Adapter):
    """A judge that tracks a content marker regardless of which slot it appears in."""

    provider = "offline-judge-content-aware"

    def __init__(self, model: str, prefers_marker: str):
        super().__init__(model)
        self.prefers_marker = prefers_marker
        self.calls = 0

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        option_a = prompt[prompt.index("OPTION A:"):prompt.index("OPTION B:")]
        winner = "a" if self.prefers_marker in option_a else "b"
        payload = dict.fromkeys((*QUALITY_AXES, "overall"), winner)
        payload["rationale"] = "fixture rationale: tracks a content marker"
        return Generation(text=json.dumps(payload), latency_ms=1.0)


class FakeSemanticJudgeAdapter(Adapter):
    provider = "offline-semantic-judge"

    def __init__(self, model: str):
        super().__init__(model)
        self.calls = 0
        self.last_prompt = ""

    def generate(self, prompt: str) -> Generation:
        self.calls += 1
        self.last_prompt = prompt
        return Generation(text=json.dumps({
            "judgment": "conveyed",
            "rationale": "The generated topic expresses the proposition as a paraphrase.",
        }), latency_ms=1.0)


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
    def test_report_score_families_use_disjoint_denominators(self) -> None:
        def row(
            case_id: str,
            kind: str,
            success: bool,
            *,
            family: str | None = None,
            source_failure_count: int = 0,
        ) -> dict[str, object]:
            attack_success = kind == "attack" and not success
            utility_failure = kind == "utility" and not success
            result = {
                "contract_success": success,
                "oracle": {
                    "attack_success": attack_success,
                    "utility_failure": utility_failure,
                },
                "generated_topics": 1,
                "grounding_error_topics": 0 if kind == "utility" else 1,
            }
            final = {
                **result,
                "human_grounding_reviewed_topics": 1 if kind == "utility" else 0,
                "human_grounding_error_topics": 0,
                "semantic_required_propositions": 1 if kind == "utility" else 0,
                "semantic_reviewed_propositions": 1 if kind == "utility" else 0,
                "semantic_conveyed_propositions": 1 if kind == "utility" else 0,
                "semantic_unclear_propositions": 0,
            }
            first = {
                **result,
                "latency_ms": 10.0,
                "cost_usd": 0.01,
            }
            return {
                "provider": "fixture",
                "model": "model",
                "prompt_version": "prompt",
                "case_id": case_id,
                "case_kind": kind,
                "case_family": family or ("valid_edge" if kind == "utility" else "citation"),
                "source_failure_count": source_failure_count,
                "status": "completed",
                "correction_attempted": False,
                "correction": None,
                "correction_error": None,
                "first": first,
                "final": final,
            }

        report = summarize(
            {
                "run_status": "complete",
                "planned_case_trials": 5,
                "grounding_measure": "fixture proxy",
                "results": [
                    row("utility-clean", "utility", True),
                    row(
                        "utility-degraded",
                        "utility",
                        True,
                        family="degraded",
                        source_failure_count=1,
                    ),
                    row(
                        "utility-partially-degraded",
                        "utility",
                        True,
                        family="partially_degraded",
                        source_failure_count=2,
                    ),
                    row(
                        "utility-over-refusal-health-reporting",
                        "utility",
                        True,
                        family="health",
                    ),
                    row("attack-citation-fabrication-escape", "attack", False),
                ],
            }
        )

        families = report["score_families"]
        utility = families["application_utility"]["groups"][0]
        security = families["security_robustness"]["groups"][0]
        editorial = families["editorial_quality"]["groups"][0]
        self.assertEqual(utility["end_to_end_success_final"]["successes"], 4)
        self.assertEqual(utility["end_to_end_success_final"]["trials"], 4)
        self.assertEqual(utility["over_refusal_success_final"]["trials"], 1)
        self.assertEqual(
            utility["degraded_source_health_reporting_success_final"]["trials"],
            2,
        )
        self.assertEqual(security["attack_success_final"]["successes"], 1)
        self.assertEqual(security["robustness_final"]["successes"], 0)
        self.assertEqual(security["by_behavior"][0]["behavior"], "citation-fabrication")
        self.assertEqual(security["by_technique"][0]["technique"], "escape_character")
        self.assertEqual(editorial["semantic_meaning_preservation"]["trials"], 4)
        self.assertEqual(editorial["grounding_error_topics_proxy_final"]["trials"], 4)
        self.assertEqual(report["operations"]["recorded_case_trials"], 5)

    def test_partial_deterministic_suite_renders_available_components(self) -> None:
        deterministic = run_deterministic_suite()
        deterministic["components"].pop("feed_parser")
        report = summarize({
            "run_status": "complete",
            "grounding_measure": "fixture proxy",
            "deterministic_summary": deterministic,
            "results": [],
        })

        checker = report["score_families"]["checker_capability"]
        self.assertIsNotNone(checker["checker"])
        self.assertIsNone(checker["feed_parser"])
        self.assertIn(
            "Feed-parser metrics: not present in this deterministic suite",
            markdown_report(report),
        )

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
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(), encoding="utf-8"
            )
            suite = temporary / "suite.json"
            suite.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "case_count": 1,
                        "cases": [
                            {
                                "id": "offline",
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
            with (
                patch("evaluator.runner.run_deterministic_suite", return_value=deterministic),
                patch(
                    "evaluator.runner.eval_briefing.parse_briefing",
                    wraps=eval_briefing.parse_briefing,
                ) as parse_briefing,
            ):
                report = run_evaluation(
                    [FakeAdapter("fixture-1")],
                    {"v1": prompt},
                    output,
                    suite_path=suite,
                    corpus_path=DEFAULT_CORPUS,
                )

            self.assertTrue((output / "manifest.json").is_file())
            self.assertTrue((output / "report.json").is_file())
            self.assertTrue((output / "report.md").is_file())
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 5)
            self.assertEqual(report["schema_version"], 6)
            families = report["score_families"]
            self.assertEqual(families["checker_capability"]["case_count"], 54)
            utility = families["application_utility"]["groups"][0]
            security = families["security_robustness"]["groups"][0]
            editorial = families["editorial_quality"]["groups"][0]
            operations = report["operations"]["groups"][0]
            self.assertEqual(utility["first_pass_contract_success"]["trials"], 1)
            self.assertEqual(utility["end_to_end_success_final"]["rate"], 1.0)
            self.assertEqual(security["attack_success_final"]["trials"], 0)
            self.assertEqual(editorial["grounding_error_topics_human"]["trials"], 0)
            self.assertEqual(operations["cost"]["total_usd"], 0.001)
            self.assertEqual(parse_briefing.call_count, 1)
            self.assertEqual(
                report["generation_controls"],
                [
                    {
                        "provider": "offline-fixture",
                        "model": "fixture-1",
                        "temperature": None,
                        "seed": None,
                        "disclosure": (
                            "This CLI exposes no evaluator control for temperature or seed; repeated trials are "
                            "stochastic and are not directly comparable to API runs made with temperature=0."
                        ),
                    }
                ],
            )
            rendered_report = (output / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Generation controls", rendered_report)
            for family in (
                "Checker capability",
                "Application utility",
                "Security robustness",
                "Editorial quality",
            ):
                self.assertIn(family, rendered_report)
            self.assertIn("Operations (not a score family)", rendered_report)
            self.assertIn("offline-fixture / fixture-1 | uncontrolled | uncontrolled", rendered_report)

            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            adjudication_path = output / manifest["results"][0]["grounding_adjudication"]
            adjudication = json.loads(adjudication_path.read_text(encoding="utf-8"))
            adjudication["topics"][0]["grounding_error"] = True
            adjudication_path.write_text(json.dumps(adjudication), encoding="utf-8")
            apply_adjudications(manifest, output)
            reviewed = summarize(manifest)["score_families"]["editorial_quality"]["groups"][0][
                "grounding_error_topics_human"
            ]
            self.assertEqual(reviewed["successes"], 1)
            self.assertEqual(reviewed["trials"], 1)

    def test_per_case_corpus_override_is_hashed_and_used_independently(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            default_corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
            other_corpus = copy.deepcopy(default_corpus)
            other_corpus["categories"]["dev_community"][0]["title"] = "A different top story"
            other_corpus_path = temporary / "other-corpus.json"
            other_corpus_path.write_text(json.dumps(other_corpus), encoding="utf-8")

            suite = temporary / "suite.json"
            suite.write_text(
                json.dumps(
                    {
                        "schema_version": 5,
                        "case_count": 2,
                        "cases": [
                            {
                                "id": "default-corpus",
                                "kind": "utility",
                                "family": "valid_edge",
                                "config": "config.json",
                                "mutations": [],
                            },
                            {
                                "id": "override-corpus",
                                "kind": "utility",
                                "family": "valid_edge",
                                "config": "config.json",
                                "corpus": "other-corpus.json",
                                "mutations": [],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            run_evaluation(
                [FakeAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            by_case = {row["case_id"]: row for row in manifest["results"]}
            self.assertEqual(
                by_case["default-corpus"]["corpus_sha256"],
                hashlib.sha256(DEFAULT_CORPUS.read_bytes()).hexdigest(),
            )
            self.assertEqual(
                by_case["override-corpus"]["corpus_sha256"],
                hashlib.sha256(other_corpus_path.read_bytes()).hexdigest(),
            )
            self.assertNotEqual(
                by_case["default-corpus"]["corpus_sha256"],
                by_case["override-corpus"]["corpus_sha256"],
            )

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
            utility = report["score_families"]["application_utility"]["groups"][0]
            self.assertEqual(utility["first_pass_contract_success"]["trials"], 1)

    def test_three_consecutive_failures_open_model_circuit_and_skip_remaining_trials(self) -> None:
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
                                "id": "unavailable-provider",
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
            self.assertEqual(manifest["run_status"], "completed_with_errors")
            self.assertEqual(report["operations"]["provider_error_trials"], 3)
            self.assertEqual(report["operations"]["circuit_open_skipped_trials"], 2)
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
            suite.write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "case_count": 1,
                        "cases": [
                            {
                                "id": "correction-failure",
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
                [FailCorrectionAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            row = manifest["results"][0]
            self.assertEqual(manifest["run_status"], "completed_with_errors")
            self.assertEqual(row["status"], "completed_with_correction_error")
            self.assertEqual(row["correction_error"]["stage"], "correction")
            self.assertIsNone(row["correction"])
            self.assertTrue((output / row["artifact_dir"] / "first.md").is_file())
            self.assertEqual(report["operations"]["correction_error_trials"], 1)
            utility = report["score_families"]["application_utility"]["groups"][0]
            self.assertEqual(utility["correction_success"]["successes"], 0)
            self.assertEqual(utility["correction_success"]["trials"], 1)

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
        with self.assertRaisesRegex(ValueError, "unknown behavior or technique"):
            _validate_generation_case({
                "id": "attack-citation-fabrication-rot13",
                "kind": "attack",
                "family": "citation",
                "config": "config.json",
                "mutations": [],
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
        with self.assertRaisesRegex(ValueError, "unknown fields: required_substrings"):
            _validate_generation_case({
                "id": "legacy-exact-match",
                "kind": "utility",
                "family": "valid_edge",
                "config": "config.json",
                "mutations": [],
                "required_substrings": ["exact words"],
            })
        with self.assertRaisesRegex(ValueError, r"must_convey\[0\]"):
            _validate_generation_case({
                "id": "malformed-semantic-requirement",
                "kind": "utility",
                "family": "valid_edge",
                "config": "config.json",
                "mutations": [],
                "must_convey": [{"url": "https://example.test/story", "propositions": []}],
            })

    def test_correction_prompt_does_not_reveal_hidden_case_assertions(self) -> None:
        prompt = correction_request(
            "Generate a briefing.",
            "First output.",
            [{"level": "ERROR", "check": "missing_section", "message": "section missing"}],
        )
        self.assertIn("Checker findings", prompt)
        self.assertNotIn("Case assertions", prompt)
        self.assertNotIn("must_include_urls", prompt)

    def test_hidden_oracle_failure_does_not_trigger_a_correction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            config = temporary / "config.json"
            config.write_text(
                (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
                encoding="utf-8",
            )
            suite = temporary / "suite.json"
            suite.write_text(json.dumps({
                "schema_version": 4,
                "case_count": 1,
                "cases": [{
                    "id": "hidden-oracle",
                    "kind": "utility",
                    "family": "selection",
                    "config": "config.json",
                    "mutations": [],
                    "must_include_urls": [
                        "https://news.ycombinator.com/item?id=90000001"
                    ],
                }],
            }), encoding="utf-8")
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")

            output = temporary / "results"
            run_evaluation(
                [FakeAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            row = json.loads((output / "manifest.json").read_text(encoding="utf-8"))["results"][0]
            self.assertTrue(row["first"]["contract_success"])
            self.assertTrue(row["first"]["oracle"]["utility_failure"])
            self.assertFalse(row["correction_attempted"])

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


class SemanticJudgeTest(unittest.TestCase):
    def test_parser_accepts_fenced_json_and_rejects_bad_labels(self) -> None:
        parsed = _parse_semantic_judgment(
            '```json\n{"judgment":"conveyed","rationale":"faithful paraphrase"}\n```'
        )
        self.assertEqual(parsed["judgment"], "conveyed")
        with self.assertRaisesRegex(ValueError, "must be 'conveyed'"):
            _parse_semantic_judgment(
                '{"judgment":"probably","rationale":"not a supported label"}'
            )

    def test_paraphrase_is_judged_separately_from_deterministic_routing(self) -> None:
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
                        "schema_version": 4,
                        "case_count": 1,
                        "cases": [
                            {
                                "id": "semantic-paraphrase",
                                "kind": "utility",
                                "family": "valid_edge",
                                "config": "config.json",
                                "mutations": [],
                                "must_include_urls": ["https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/"],
                                "must_convey": [
                                    {
                                        "url": "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                                        "propositions": [
                                            "The patch allows subagents to run using other model providers."
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            prompt = temporary / "prompt.md"
            prompt.write_text("Produce the briefing.", encoding="utf-8")
            output = temporary / "results"

            first_report = run_evaluation(
                [FakeAdapter("fixture-1")],
                {"v1": prompt},
                output,
                suite_path=suite,
                corpus_path=DEFAULT_CORPUS,
            )
            utility = first_report["score_families"]["application_utility"]["groups"][0]
            editorial = first_report["score_families"]["editorial_quality"]["groups"][0]
            self.assertEqual(utility["routing_success_final"]["rate"], 1.0)
            self.assertIsNone(editorial["semantic_meaning_preservation"]["rate"])
            self.assertEqual(editorial["semantic_unreviewed_propositions"], 1)

            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            semantic_path = output / manifest["results"][0]["semantic_adjudication"]
            semantic = json.loads(semantic_path.read_text(encoding="utf-8"))
            generated_prose = semantic["judgments"][0]["topics"][0]["prose"]
            self.assertNotIn("run using other model providers", generated_prose)

            judge = FakeSemanticJudgeAdapter("fixture-judge")
            result = run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")
            self.assertEqual(result["counts"]["conveyed"], 1)
            self.assertEqual(judge.calls, 1)
            self.assertNotIn("semantic-paraphrase", judge.last_prompt)
            self.assertNotIn("offline-fixture", judge.last_prompt)

            updated = json.loads((output / "report.json").read_text(encoding="utf-8"))
            metric = updated["score_families"]["editorial_quality"]["groups"][0]["semantic_meaning_preservation"]
            self.assertEqual(metric["successes"], 1)
            self.assertEqual(metric["trials"], 1)

            resumed = run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")
            self.assertEqual(resumed["model_calls"], 0)
            self.assertEqual(judge.calls, 1)

            identity_path = output / "semantic-judgments" / "semantic-judging-run.json"
            identity = json.loads(identity_path.read_text(encoding="utf-8"))
            self.assertEqual(identity["schema_version"], 2)
            identity["schema_version"] = 1
            identity_path.write_text(json.dumps(identity), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "different semantic-judge run"):
                run_semantic_judging(output / "manifest.json", judge, output / "semantic-judgments")

    def test_repeated_url_exposes_every_citing_topic_to_the_semantic_judge(self) -> None:
        url = "https://example.test/repeated"
        proposition = "The later topic contains the required meaning."
        payload = _semantic_adjudication_template(
            {
                "must_convey": [{"url": url, "propositions": [proposition]}],
            },
            {
                "AI Dev Tools": {
                    "topics": ["First mention", "Later mention"],
                    "topic_texts": [
                        "This topic omits the required meaning.",
                        "The later topic contains the required meaning.",
                    ],
                    "topic_links": [[url], [url]],
                },
            },
        )

        judgment = payload["judgments"][0]
        self.assertEqual(
            [topic["title"] for topic in judgment["topics"]],
            ["First mention", "Later mention"],
        )
        prompt = _semantic_judgment_prompt(
            "Supporting evidence.", judgment["topics"], proposition
        )
        self.assertIn("TOPIC 1:\nFirst mention", prompt)
        self.assertIn("TOPIC 2:\nLater mention", prompt)
        self.assertIn("whether at least one GENERATED TOPIC conveys", prompt)


class QualityJudgeTest(unittest.TestCase):
    def _minimal_run(self, directory: Path) -> Path:
        """Run two fake models against one case and return the manifest path."""
        config = directory / "config.json"
        config.write_text(
            (Path(__file__).parents[1] / "fixtures" / "generation-config-1.json").read_text(),
            encoding="utf-8",
        )
        suite = directory / "suite.json"
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
        prompt = directory / "prompt.md"
        prompt.write_text("Produce the briefing.", encoding="utf-8")
        output = directory / "results"
        run_evaluation(
            [FakeAdapter("fixture-1"), FakeAdapterVariant("fixture-1")],
            {"v1": prompt},
            output,
            suite_path=suite,
            corpus_path=DEFAULT_CORPUS,
        )
        return output / "manifest.json"

    def test_parse_judgment_accepts_fenced_json_and_rejects_bad_shapes(self) -> None:
        fenced = "```\n" + json.dumps({
            "faithfulness": "a", "salience": "b", "concision": "tie", "coherence": "a",
            "overall": "a", "rationale": "clear reason",
        }) + "\n```"
        parsed = _parse_judgment(fenced)
        self.assertEqual(parsed["salience"], "b")
        with self.assertRaisesRegex(ValueError, "must contain exactly"):
            _parse_judgment(json.dumps({"faithfulness": "a"}))
        with self.assertRaisesRegex(ValueError, "must be 'a', 'b', or 'tie'"):
            _parse_judgment(json.dumps({
                "faithfulness": "a", "salience": "a", "concision": "a", "coherence": "a",
                "overall": "definitely-a", "rationale": "reason",
            }))

    def test_matched_pairs_link_two_models_writing_about_the_same_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            configs = {"offline": load_config(temporary / "config.json")}
            pairs = matched_pairs(manifest, manifest_path.parent, configs)
            self.assertEqual(len(pairs), 1)
            self.assertEqual(pairs[0]["group_a"][0], "offline-fixture")
            self.assertEqual(pairs[0]["group_b"][0], "offline-fixture-b")
            self.assertIn("billing stays on the subscription plan", pairs[0]["topic_b"]["prose"])

    def test_multi_url_evidence_is_joined_in_canonical_url_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            case_dir = temporary / "case"
            case_dir.mkdir()
            corpus = json.loads(DEFAULT_CORPUS.read_text(encoding="utf-8"))
            urls = [
                "https://www.reddit.com/r/ClaudeAI/comments/1vjrap8/example/",
                "https://news.ycombinator.com/item?id=90000001",
            ]
            (case_dir / "corpus.json").write_text(json.dumps(corpus), encoding="utf-8")
            (case_dir / "final.md").write_text(
                "# Daily Briefing\n\n## AI Dev Tools\n\n"
                "**Combined topic** — A concise combined summary.\n"
                f"🔗 {urls[0]}\n🔗 {urls[1]}\n",
                encoding="utf-8",
            )
            config = load_config(
                Path(__file__).parents[1] / "fixtures" / "generation-config-1.json"
            )

            topics = _topics(temporary, {"artifact_dir": "case"}, config)

            evidence = eval_briefing.corpus_evidence(corpus)
            expected = " ".join(
                dict.fromkeys(
                    evidence[url]
                    for url in sorted(corpus_schema.canonicalize_url(url) for url in urls)
                )
            )
            self.assertEqual(topics[0]["evidence"], expected)

    def test_position_biased_judge_is_flagged_as_inconsistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")

            result = run_quality_judging(manifest_path, judge, temporary / "quality")

            self.assertEqual(result["pairs_judged"], 1)
            self.assertEqual(judge.calls, 2)  # one original-order call, one swapped
            for axis in (*QUALITY_AXES, "overall"):
                self.assertEqual(result["position_consistency"][axis]["rate"], 0.0)
            report = (temporary / "quality" / "quality-report.md").read_text(encoding="utf-8")
            self.assertIn("Position-bias consistency", report)

    def test_content_aware_judge_is_consistent_and_wins_are_attributed_correctly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeContentAwareJudgeAdapter(
                "fixture-judge", prefers_marker="billing stays on the subscription plan"
            )

            result = run_quality_judging(manifest_path, judge, manifest_path.parent / "quality-judgments")

            for axis in (*QUALITY_AXES, "overall"):
                self.assertEqual(result["position_consistency"][axis]["rate"], 1.0)
            winners = {row["provider"]: row for row in result["win_rates"] if row["axis"] == "overall"}
            self.assertEqual(winners["offline-fixture-b"]["win_rate_excluding_ties"]["rate"], 1.0)
            self.assertEqual(winners["offline-fixture"]["win_rate_excluding_ties"]["rate"], 0.0)
            main_report = json.loads((manifest_path.parent / "report.json").read_text(encoding="utf-8"))
            editorial = main_report["score_families"]["editorial_quality"]
            self.assertEqual(editorial["pairwise_judging"]["status"], "available")
            self.assertEqual(editorial["pairwise_judging"]["pairs_judged"], 1)
            self.assertEqual(editorial["groups"][1]["pairwise_prose_quality"]["status"], "available")

    def test_rerunning_with_a_different_judge_in_the_same_output_dir_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            run_quality_judging(manifest_path, FakeJudgeAdapter("judge-one"), temporary / "quality")

            with self.assertRaisesRegex(ValueError, "different judge-quality run"):
                run_quality_judging(manifest_path, FakeJudgeAdapter("judge-two"), temporary / "quality")

    def test_rerunning_after_the_suite_changes_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")
            run_quality_judging(manifest_path, judge, temporary / "quality")

            suite_path = temporary / "suite.json"
            suite = json.loads(suite_path.read_text(encoding="utf-8"))
            suite["description"] = "changed after checkpoints were recorded"
            suite_path.write_text(json.dumps(suite), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "different judge-quality run"):
                run_quality_judging(manifest_path, judge, temporary / "quality")

    def test_suite_override_missing_a_manifest_case_reports_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            mismatched_suite = temporary / "mismatched-suite.json"
            mismatched_suite.write_text(json.dumps({
                "schema_version": 2,
                "case_count": 1,
                "cases": [{
                    "id": "different-case",
                    "kind": "utility",
                    "family": "valid_edge",
                    "config": "config.json",
                    "mutations": [],
                }],
            }), encoding="utf-8")

            with self.assertRaisesRegex(
                ValueError, "case 'offline' is in the manifest but not in the suite"
            ):
                run_quality_judging(
                    manifest_path,
                    FakeJudgeAdapter("fixture-judge"),
                    temporary / "quality",
                    suite_path=mismatched_suite,
                )

    def test_resumed_run_reuses_checkpoints_without_a_second_paid_call(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            manifest_path = self._minimal_run(temporary)
            judge = FakeJudgeAdapter("fixture-judge")
            run_quality_judging(manifest_path, judge, temporary / "quality")
            self.assertEqual(judge.calls, 2)

            run_quality_judging(manifest_path, judge, temporary / "quality")
            self.assertEqual(judge.calls, 2)


if __name__ == "__main__":
    unittest.main()
