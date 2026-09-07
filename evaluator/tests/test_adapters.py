"""Evaluator adapters regression coverage."""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from email.message import Message
from unittest.mock import patch

from evaluator.adapters import (
    API_MAX_ATTEMPTS,
    NvidiaAdapter,
    OpenAiCompatibleAdapter,
    OpenRouterAdapter,
    ProviderRequestError,
    _retry_after_seconds,
)
from evaluator.tests.support import (
    adapter_for,
)


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

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"})
    def test_openrouter_retries_connection_reset_then_succeeds(self) -> None:
        response = FakeHttpResponse({
            "id": "generation-1",
            "choices": [{"message": {"content": "review"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001},
        })
        with (
            patch(
                "evaluator.adapters.urllib.request.urlopen",
                side_effect=[ConnectionResetError("peer reset"), response],
            ) as urlopen,
            patch("evaluator.adapters.time.sleep") as sleep,
        ):
            generation = OpenRouterAdapter("review-model", timeout=30).generate("request")

        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once_with(1.0)
        self.assertEqual(generation.attempts, 2)
        self.assertEqual(generation.text, "review")

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"})
    def test_openrouter_rejects_success_envelope_without_text_content(self) -> None:
        response = FakeHttpResponse({
            "id": "generation-without-content",
            "choices": [{
                "finish_reason": "length",
                "message": {"content": None, "reasoning": "reasoning exhausted the budget"},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 8192, "cost": 0.01},
        })
        with patch("evaluator.adapters.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(
                RuntimeError, "returned no text content.*finish_reason='length'"
            ) as raised:
                OpenRouterAdapter("reasoning-model", timeout=30).generate("request")
        self.assertIsInstance(raised.exception, ProviderRequestError)
        assert isinstance(raised.exception, ProviderRequestError)
        self.assertEqual(raised.exception.cost_usd, 0.01)
        self.assertEqual(raised.exception.output_tokens, 8192)

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



class AdaptersTest(unittest.TestCase):
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
            reasoning_enabled=False,
        )
        assert isinstance(adapter, OpenAiCompatibleAdapter)

        self.assertEqual(
            adapter._payload("request"),
            {
                "model": "nvidia/nemotron-3-super-120b-a12b",
                "messages": [{"role": "user", "content": "request"}],
                "temperature": 0.2,
                "seed": 42,
                "reasoning": {"enabled": False},
                "max_tokens": 100000,
            },
        )
        controls = adapter.generation_controls()
        self.assertEqual(controls["reasoning_enabled"], False)


    def test_api_sampling_control_defaults_remain_optional(self) -> None:
        adapter = adapter_for("openrouter", "openai/gpt-5.6-terra")
        assert isinstance(adapter, OpenAiCompatibleAdapter)

        payload = adapter._payload("request")
        self.assertEqual(payload["temperature"], 0)
        self.assertNotIn("seed", payload)
        self.assertNotIn("reasoning", payload)


    def test_api_reasoning_effort_implies_enabled_reasoning_budget(self) -> None:
        adapter = adapter_for(
            "openrouter",
            "deepseek/deepseek-v4-flash-0731",
            reasoning_enabled=True,
            reasoning_effort="low",
        )
        assert isinstance(adapter, OpenAiCompatibleAdapter)

        self.assertEqual(adapter._payload("request")["reasoning"], {"effort": "low"})
        controls = adapter.generation_controls()
        self.assertEqual(controls["reasoning_enabled"], True)
        self.assertEqual(controls["reasoning_effort"], "low")


    def test_sampling_controls_do_not_change_cli_adapters(self) -> None:
        adapter = adapter_for(
            "codex-cli",
            "gpt-5.6-terra",
            temperature=0.2,
            seed=42,
        )

        self.assertFalse(hasattr(adapter, "temperature"))
        self.assertFalse(hasattr(adapter, "seed"))

