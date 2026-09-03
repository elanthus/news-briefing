import http.server
import io
import json
import os
import subprocess
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from agent_runner.models import GenerationRequest, ProviderError
from agent_runner.providers import (
    ClaudeCodeProvider,
    CodexCliProvider,
    OpenAICompatibleProvider,
    OpenRouterProvider,
    _command_version,
    _grammar_compatible_schema,
    _run_cli,
    provider_for,
)

ROOT = Path(__file__).resolve().parents[1]

SCHEMA = {
    "type": "object",
    "properties": {"schema_version": {"type": "integer"}},
    "required": ["schema_version"],
    "additionalProperties": False,
}
REQUEST = GenerationRequest("prompt", SCHEMA, 30, "0" * 32)


class FakeResponse:
    def __init__(self, payload, headers=None):
        self.payload = json.dumps(payload).encode()
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.payload


class TimeoutResponse(FakeResponse):
    def read(self):
        raise TimeoutError("response read timed out")


class ProviderTests(unittest.TestCase):
    def test_grammar_schema_removes_unique_items_without_mutating_source(self):
        schema = {
            "type": "object",
            "const": {"uniqueItems": True},
            "properties": {
                "uniqueItems": {"type": "boolean"},
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                }
            },
            "$defs": {
                "nested": {
                    "type": "array",
                    "items": {"type": "string"},
                    "uniqueItems": True,
                }
            },
        }

        compatible = _grammar_compatible_schema(schema)

        self.assertNotIn("uniqueItems", compatible["properties"]["refs"])
        self.assertNotIn("uniqueItems", compatible["$defs"]["nested"])
        self.assertEqual(compatible["properties"]["uniqueItems"], {"type": "boolean"})
        self.assertEqual(compatible["const"], {"uniqueItems": True})
        self.assertTrue(schema["properties"]["refs"]["uniqueItems"])

    def test_openrouter_sends_schema_and_parses_usage(self):
        payload = {
            "id": "gen-1",
            "choices": [{"message": {"content": '{"schema_version":1}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.01},
        }
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", return_value=FakeResponse(payload)
        ) as opened:
            result = provider.generate(REQUEST)
        sent = json.loads(opened.call_args.args[0].data)
        self.assertEqual(sent["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(sent["provider"], {"require_parameters": True})
        self.assertEqual(sent["reasoning"], {"enabled": True})
        self.assertNotIn("tools", sent)
        self.assertEqual(result.structured_output, {"schema_version": 1})
        self.assertEqual(result.cost_usd, 0.01)

    def test_openrouter_strips_unique_items_only_for_incompatible_models(self):
        """hy3's backends cannot compile uniqueItems; every other model keeps it."""
        schema = {
            "type": "object",
            "properties": {
                "refs": {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
            },
            "required": ["refs"],
            "additionalProperties": False,
        }
        request = GenerationRequest("prompt", schema, 30, "0" * 32)

        stripped = OpenRouterProvider("tencent/hy3")._payload(request)
        kept = OpenRouterProvider("vendor/model")._payload(request)

        self.assertNotIn(
            "uniqueItems",
            stripped["response_format"]["json_schema"]["schema"]["properties"]["refs"],
        )
        self.assertTrue(
            kept["response_format"]["json_schema"]["schema"]["properties"]["refs"]["uniqueItems"]
        )
        self.assertTrue(schema["properties"]["refs"]["uniqueItems"])

    def test_openrouter_reasoning_can_be_explicitly_disabled(self):
        provider = OpenRouterProvider("vendor/model", reasoning_enabled=False)
        self.assertEqual(provider._payload(REQUEST)["reasoning"], {"enabled": False})

    def test_openrouter_rejects_tool_calls(self):
        payload = {
            "id": "gen-1",
            "choices": [{"message": {"content": "{}", "tool_calls": [{"id": "bad"}]}}],
        }
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", return_value=FakeResponse(payload)
        ), self.assertRaisesRegex(ProviderError, "empty tool policy"):
            provider.generate(REQUEST)

    def test_openrouter_ignores_malformed_optional_cost(self):
        payload = {
            "id": "gen-1",
            "choices": [{"message": {"content": '{"schema_version":1}'}}],
            "usage": {"cost": "not-a-number"},
        }
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", return_value=FakeResponse(payload)
        ):
            result = provider.generate(REQUEST)
        self.assertIsNone(result.cost_usd)

    def test_openrouter_retries_429_and_honors_retry_after(self):
        error = urllib.error.HTTPError(
            "https://example.invalid",
            429,
            "rate limited",
            {"Retry-After": "0"},
            io.BytesIO(b"slow down"),
        )
        success = FakeResponse({
            "id": "gen-2",
            "choices": [{"message": {"content": '{"schema_version":1}'}}],
            "usage": {},
        })
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", side_effect=[error, success]
        ) as opened:
            result = provider.generate(REQUEST)
        self.assertEqual(opened.call_count, 2)
        self.assertEqual(result.attempts, 2)

    def test_openrouter_redacts_user_id_from_provider_errors(self):
        error = urllib.error.HTTPError(
            "https://example.invalid",
            400,
            "bad request",
            {},
            io.BytesIO(
                b'{"error":{"message":"bad schema","User_ID":"nested_secret"},'
                b'"details":[{"user_id":"list_secret"}],'
                b'"user_id":"top_secret"}'
            ),
        )
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", side_effect=error
        ), self.assertRaises(ProviderError) as raised:
            provider.generate(REQUEST)
        self.assertNotIn("top_secret", str(raised.exception))
        self.assertNotIn("nested_secret", str(raised.exception))
        self.assertNotIn("list_secret", str(raised.exception))
        self.assertIn("[redacted]", str(raised.exception))

    def test_openrouter_records_404_for_model_catalog_check(self):
        error = urllib.error.HTTPError(
            "https://openrouter.ai/api/v1/chat/completions",
            404,
            "not found",
            {},
            io.BytesIO(b'{"error":{"message":"No endpoints found for vendor/model"}}'),
        )
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", side_effect=error
        ), self.assertRaises(ProviderError) as raised:
            provider.generate(REQUEST)
        self.assertTrue(raised.exception.openrouter_model_404)
        self.assertTrue(raised.exception.record()["openrouter_model_404"])
        self.assertEqual(raised.exception.status_code, 404)

    def test_openrouter_does_not_retry_ambiguous_response_timeout(self):
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", return_value=TimeoutResponse({})
        ) as opened, self.assertRaises(ProviderError) as raised:
            provider.generate(REQUEST)
        self.assertTrue(raised.exception.ambiguous_completion)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(opened.call_count, 1)

    def test_openai_compatible_defaults_to_ollama_and_supports_other_endpoints(self):
        default = OpenAICompatibleProvider("qwen3:32b")
        self.assertEqual(default.endpoint, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(default.info()["endpoint"], default.endpoint)
        custom = OpenAICompatibleProvider(
            "qwen3:32b", endpoint="http://127.0.0.1:8080/v1/chat/completions"
        )
        self.assertEqual(custom.info()["endpoint"], "http://127.0.0.1:8080/v1/chat/completions")
        self.assertNotIn("OPENROUTER_API_KEY", json.dumps(custom.info()))

    def test_provider_for_routes_openai_compatible_and_guards_endpoint(self):
        provider = provider_for(
            "openai-compatible",
            "qwen3:32b",
            temperature=0.3,
            reasoning_enabled=None,
            max_tokens=8000,
            endpoint="http://127.0.0.1:8080/v1/chat/completions",
        )
        self.assertIsInstance(provider, OpenAICompatibleProvider)
        self.assertEqual(provider.temperature, 0.3)
        self.assertEqual(provider.max_tokens, 8000)
        with self.assertRaisesRegex(ValueError, "openai-compatible provider only"):
            provider_for("openrouter", "vendor/model", endpoint="http://127.0.0.1:8080/v1")

    def test_openai_compatible_payload_omits_openrouter_fields_and_constrains_output(self):
        # Contract for OpenAICompatibleProvider._payload. The schema must reach
        # the server through response_format, and nothing OpenRouter-specific
        # may leak into a request that a local server might reject.
        request = GenerationRequest(
            "prompt",
            {"type": "object", "properties": {"refs": {"type": "array", "uniqueItems": True, "maxItems": 2}}},
            30,
            "0" * 32,
        )
        payload = OpenAICompatibleProvider("qwen3:32b", temperature=0.1, max_tokens=8000)._payload(request)
        self.assertEqual(payload["model"], "qwen3:32b")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "prompt"}])
        self.assertEqual(payload["temperature"], 0.1)
        self.assertEqual(payload["max_tokens"], 8000)
        self.assertEqual(payload["response_format"]["type"], "json_schema")
        sent_refs = payload["response_format"]["json_schema"]["schema"]["properties"]["refs"]
        self.assertNotIn("uniqueItems", sent_refs)
        self.assertEqual(sent_refs["maxItems"], 2)
        self.assertNotIn("provider", payload)
        self.assertNotIn("reasoning", payload)
        self.assertNotIn("max_tokens", OpenAICompatibleProvider("qwen3:32b")._payload(request))

    def test_openai_compatible_lean_profile_removes_array_bounds_and_keeps_enums(self):
        schema = {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "minItems": 0,
                    "maxItems": 5,
                    "items": {
                        "type": "object",
                        "properties": {
                            "refs": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["a", "b"]},
                                "minItems": 1,
                                "maxItems": 2,
                                "uniqueItems": True,
                            },
                            "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                        },
                    },
                }
            },
        }
        request = GenerationRequest("prompt", schema, 30, "0" * 32)
        full = OpenAICompatibleProvider("m")
        lean = OpenAICompatibleProvider("m", lean_schema=True)
        full_topics = full._payload(request)["response_format"]["json_schema"]["schema"]["properties"]["topics"]
        lean_topics = lean._payload(request)["response_format"]["json_schema"]["schema"]["properties"]["topics"]
        self.assertEqual(full_topics["items"]["properties"]["refs"]["maxItems"], 2)
        refs = lean_topics["items"]["properties"]["refs"]
        self.assertNotIn("maxItems", refs)
        self.assertNotIn("minItems", refs)
        self.assertNotIn("uniqueItems", refs)
        self.assertEqual(refs["items"]["enum"], ["a", "b"])
        self.assertNotIn("maxItems", lean_topics)
        self.assertNotIn("minItems", lean_topics)
        self.assertEqual(lean_topics["items"]["properties"]["summary"], {"type": "string"})
        self.assertEqual(full_topics["items"]["properties"]["summary"]["maxLength"], 300)
        self.assertEqual(schema["properties"]["topics"]["items"]["properties"]["refs"]["maxItems"], 2)
        self.assertEqual(lean._payload(request)["max_tokens"], OpenAICompatibleProvider.LEAN_DEFAULT_MAX_TOKENS)
        self.assertNotIn("max_tokens", full._payload(request))
        self.assertEqual(OpenAICompatibleProvider("m", lean_schema=True, max_tokens=900).max_tokens, 900)
        self.assertEqual(lean.info()["schema_profile"], "lean")
        self.assertEqual(full.info()["schema_profile"], "full")
        with self.assertRaisesRegex(ValueError, "lean_schema apply"):
            provider_for("openrouter", "vendor/model", lean_schema=True)

    def test_openai_compatible_timeout_hints_at_lean_schema_only_for_full_profile(self):
        request = GenerationRequest("prompt", SCHEMA, 30, "0" * 32)
        with patch("agent_runner.providers._urlopen", return_value=TimeoutResponse({})), self.assertRaises(
            ProviderError
        ) as raised:
            OpenAICompatibleProvider("m").generate(request)
        self.assertIn("--lean-schema", str(raised.exception))
        self.assertTrue(raised.exception.ambiguous_completion)
        self.assertTrue(raised.exception.transient)
        with patch("agent_runner.providers._urlopen", return_value=TimeoutResponse({})), self.assertRaises(
            ProviderError
        ) as raised:
            OpenAICompatibleProvider("m", lean_schema=True).generate(request)
        self.assertNotIn("--lean-schema", str(raised.exception))

    def test_openai_compatible_strips_think_blocks_and_code_fences(self):
        wrapped = '<think>\nweighing the stories\n</think>\n```json\n{"schema_version": 1}\n```'
        response = FakeResponse({"choices": [{"message": {"content": wrapped}}]})
        with patch("agent_runner.providers._urlopen", return_value=response):
            result = OpenAICompatibleProvider("qwen3:32b").generate(REQUEST)
        self.assertEqual(result.structured_output, {"schema_version": 1})
        self.assertEqual(result.raw_output, wrapped)
        unclosed = FakeResponse({"choices": [{"message": {"content": "<think>still going"}}]})
        with patch("agent_runner.providers._urlopen", return_value=unclosed), self.assertRaisesRegex(
            ProviderError, "invalid JSON"
        ):
            OpenAICompatibleProvider("qwen3:32b").generate(REQUEST)

    def test_openai_compatible_lean_profile_keeps_exact_count_arrays(self):
        schema = {
            "type": "object",
            "properties": {
                "topics": {
                    "type": "array",
                    "minItems": 3,
                    "maxItems": 3,
                    "items": {
                        "type": "object",
                        "properties": {
                            "refs": {
                                "type": "array",
                                "items": {"type": "string", "enum": ["a", "b", "c"]},
                                "minItems": 1,
                                "maxItems": 3,
                            },
                            "summary": {"type": "string", "minLength": 1, "maxLength": 300},
                        },
                    },
                }
            },
        }
        request = GenerationRequest("prompt", schema, 30, "0" * 32)
        lean = OpenAICompatibleProvider("m", lean_schema=True)
        topics = lean._payload(request)["response_format"]["json_schema"]["schema"]["properties"]["topics"]
        # The prose pass returns exactly one entry per frozen selection; that bound survives.
        self.assertEqual((topics["minItems"], topics["maxItems"]), (3, 3))
        # Ranged bounds and string lengths are still dropped.
        refs = topics["items"]["properties"]["refs"]
        self.assertNotIn("minItems", refs)
        self.assertNotIn("maxItems", refs)
        self.assertEqual(refs["items"]["enum"], ["a", "b", "c"])
        self.assertEqual(topics["items"]["properties"]["summary"], {"type": "string"})

    def test_openai_compatible_wrapper_stripping_is_linear_on_unclosed_fences(self):
        pathological = "```" + " " * 200_000
        response = FakeResponse({"choices": [{"message": {"content": pathological}}]})
        started = time.perf_counter()
        with patch("agent_runner.providers._urlopen", return_value=response), self.assertRaisesRegex(
            ProviderError, "invalid JSON"
        ):
            OpenAICompatibleProvider("qwen3:32b").generate(REQUEST)
        self.assertLess(time.perf_counter() - started, 1.0)
        # A fence without a newline after the opener is still unwrapped.
        compact = FakeResponse({"choices": [{"message": {"content": '```json{"schema_version": 1}```'}}]})
        with patch("agent_runner.providers._urlopen", return_value=compact):
            result = OpenAICompatibleProvider("qwen3:32b").generate(REQUEST)
        self.assertEqual(result.structured_output, {"schema_version": 1})

    def test_chat_completion_truncated_at_max_tokens_is_named_not_reported_as_invalid_json(self):
        truncated = {
            "id": "req-1",
            "choices": [{"finish_reason": "length", "message": {"content": '{"schema_version": 1, "sec'}}],
        }
        with patch("agent_runner.providers._urlopen", return_value=FakeResponse(truncated)), self.assertRaises(
            ProviderError
        ) as raised:
            OpenAICompatibleProvider("qwen3:32b", lean_schema=True).generate(REQUEST)
        self.assertIn("truncated at the max_tokens ceiling", str(raised.exception))
        self.assertTrue(raised.exception.output_truncated)
        self.assertFalse(raised.exception.transient)
        self.assertEqual(raised.exception.provider_request_id, "req-1")
        self.assertTrue(raised.exception.record()["output_truncated"])
        # The parser is shared, so OpenRouter reports the same cause.
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "agent_runner.providers._urlopen", return_value=FakeResponse(truncated)
        ), self.assertRaises(ProviderError) as raised:
            OpenRouterProvider("vendor/model").generate(REQUEST)
        self.assertTrue(raised.exception.output_truncated)

    def test_openai_compatible_manifest_omits_endpoint_userinfo(self):
        provider = OpenAICompatibleProvider("m", endpoint="https://user:token@gateway.example/v1/chat/completions")
        self.assertEqual(provider.info()["endpoint"], "https://gateway.example/v1/chat/completions")
        self.assertEqual(provider.endpoint, "https://user:token@gateway.example/v1/chat/completions")
        plain = OpenAICompatibleProvider("m", endpoint="http://127.0.0.1:8080/v1/chat/completions")
        self.assertEqual(plain.info()["endpoint"], "http://127.0.0.1:8080/v1/chat/completions")

    def test_openai_compatible_refuses_bearer_token_over_cleartext_to_remote_hosts(self):
        response = FakeResponse({"choices": [{"message": {"content": '{"schema_version": 1}'}}]})
        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "secret"}):
            with patch("agent_runner.providers._urlopen", return_value=response) as urlopen, self.assertRaises(
                ProviderError
            ) as raised:
                OpenAICompatibleProvider("m", endpoint="http://gateway.example/v1/chat/completions").generate(REQUEST)
            self.assertIn("cleartext", str(raised.exception))
            self.assertFalse(raised.exception.transient)
            urlopen.assert_not_called()
            # Loopback over http and any host over https are fine.
            for endpoint in (
                "http://127.0.0.1:11434/v1/chat/completions",
                "http://localhost:1234/v1/chat/completions",
                "http://[::1]:8080/v1/chat/completions",
                "https://gateway.example/v1/chat/completions",
            ):
                with self.subTest(endpoint=endpoint), patch(
                    "agent_runner.providers._urlopen", return_value=response
                ) as urlopen:
                    OpenAICompatibleProvider("m", endpoint=endpoint).generate(REQUEST)
                    sent = urlopen.call_args.args[0]
                    self.assertEqual(sent.get_header("Authorization"), "Bearer secret")

    def test_provider_transport_refuses_redirects_so_bearer_tokens_stay_home(self):
        seen: list[tuple[str, str | None]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802 - http.server naming
                seen.append((self.path, self.headers.get("Authorization")))
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                self.send_response(307)
                self.send_header("Location", "/elsewhere/v1/chat/completions")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
            with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "secret"}), self.assertRaises(
                ProviderError
            ) as raised:
                OpenAICompatibleProvider("m", endpoint=endpoint).generate(REQUEST)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(raised.exception.status_code, 307)
        self.assertFalse(raised.exception.transient)
        # Exactly one request reached the server; the redirect target was never contacted.
        self.assertEqual(seen, [("/v1/chat/completions", "Bearer secret")])

    def test_openai_compatible_sends_bearer_token_only_when_configured(self):
        provider = OpenAICompatibleProvider("qwen3:32b")
        response = FakeResponse(
            {
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": json.dumps({"schema_version": 1})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        )
        with patch.dict(os.environ, {}, clear=True), patch(
            "agent_runner.providers._urlopen", return_value=response
        ) as opened:
            result = provider.generate(REQUEST)
        sent = opened.call_args.args[0]
        self.assertFalse(sent.has_header("Authorization"))
        self.assertEqual(sent.full_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(result.structured_output, {"schema_version": 1})
        self.assertEqual(result.input_tokens, 10)
        self.assertIsNone(result.cost_usd)

        with patch.dict(os.environ, {"OPENAI_COMPATIBLE_API_KEY": "local-key"}), patch(
            "agent_runner.providers._urlopen", return_value=response
        ) as opened:
            provider.generate(REQUEST)
        self.assertEqual(opened.call_args.args[0].get_header("Authorization"), "Bearer local-key")

    def test_openai_compatible_rejects_tool_calls(self):
        response = FakeResponse(
            {"choices": [{"message": {"content": "{}", "tool_calls": [{"id": "call_1"}]}}]}
        )
        with patch("agent_runner.providers._urlopen", return_value=response), self.assertRaisesRegex(
            ProviderError, "openai-compatible violated the empty tool policy"
        ):
            OpenAICompatibleProvider("qwen3:32b").generate(REQUEST)

    def test_openai_compatible_does_not_flag_404_as_openrouter_removal(self):
        error = urllib.error.HTTPError(
            "http://127.0.0.1:11434/v1/chat/completions", 404, "Not Found", {}, io.BytesIO(b"no such model")
        )
        with patch("agent_runner.providers._urlopen", side_effect=error), self.assertRaises(ProviderError) as raised:
            OpenAICompatibleProvider("missing").generate(REQUEST)
        self.assertFalse(raised.exception.openrouter_model_404)
        self.assertIn("openai-compatible HTTP 404", str(raised.exception))

    def test_claude_allows_only_structured_output_tool(self):
        wrapper = {
            "result": '{"schema_version":1}',
            "structured_output": {"schema_version": 1},
            "usage": {"input_tokens": 2, "output_tokens": 1},
            "session_id": "session-1",
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(wrapper), "")
        with patch("shutil.which", return_value="/bin/claude"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 12.0, 1)
        ) as run:
            result = ClaudeCodeProvider("sonnet").generate(REQUEST)
        command = run.call_args.args[0]
        self.assertIn("--safe-mode", command)
        self.assertEqual(command[command.index("--tools") + 1], "StructuredOutput")
        self.assertEqual(command[command.index("--allowedTools") + 1], "StructuredOutput")
        self.assertNotIn("--disallowedTools", command)
        self.assertEqual(result.structured_output, {"schema_version": 1})

    def test_claude_ignores_malformed_optional_cost(self):
        wrapper = {
            "result": '{"schema_version":1}',
            "structured_output": {"schema_version": 1},
            "total_cost_usd": {"unexpected": "shape"},
        }
        completed = subprocess.CompletedProcess([], 0, json.dumps(wrapper), "")
        with patch("shutil.which", return_value="/bin/claude"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 12.0, 1)
        ):
            result = ClaudeCodeProvider("sonnet").generate(REQUEST)
        self.assertIsNone(result.cost_usd)

    def test_command_version_returns_none_when_probe_fails(self):
        for failure in (
            subprocess.TimeoutExpired(["claude", "--version"], 10),
            OSError("cannot execute"),
        ):
            with self.subTest(failure=type(failure).__name__), patch(
                "shutil.which", return_value="/bin/claude"
            ), patch("subprocess.run", side_effect=failure):
                self.assertIsNone(_command_version("claude"))

    def test_cli_retries_transient_failure_before_output(self):
        failed = subprocess.CompletedProcess([], 1, "", "service unavailable")
        succeeded = subprocess.CompletedProcess([], 0, "result", "")
        with patch("subprocess.run", side_effect=[failed, succeeded]) as run, patch("time.sleep"):
            completed, _latency, attempts = _run_cli(["model", "run"], "prompt", timeout=30)
        self.assertEqual(completed.stdout, "result")
        self.assertEqual(attempts, 2)
        self.assertEqual(run.call_count, 2)

    def test_cli_pipes_use_utf8_regardless_of_locale(self):
        succeeded = subprocess.CompletedProcess([], 0, "result", "")
        with patch("subprocess.run", return_value=succeeded) as run:
            _run_cli(["model", "run"], "prompt with hair space", timeout=30)
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")

    def test_cli_does_not_retry_ambiguous_failure_after_output(self):
        failed = subprocess.CompletedProcess([], 1, "partial output", "service unavailable")
        with patch("subprocess.run", return_value=failed) as run, self.assertRaises(
            ProviderError
        ) as raised:
            _run_cli(["model", "run"], "prompt", timeout=30)
        self.assertTrue(raised.exception.ambiguous_completion)
        self.assertEqual(run.call_count, 1)

    def test_codex_accepts_only_message_and_reasoning_events(self):
        events = [
            {"thread_id": "thread-1", "type": "thread.started"},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"schema_version":1}'},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}},
        ]
        completed = subprocess.CompletedProcess(
            [], 0, "\n\n" + "\n".join(map(json.dumps, events)) + "\n\n", ""
        )
        with patch("shutil.which", return_value="/bin/codex"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 20.0, 1)
        ) as run:
            result = CodexCliProvider("gpt").generate(REQUEST)
        self.assertEqual(result.provider_request_id, "thread-1")
        self.assertEqual(result.structured_output, {"schema_version": 1})
        command = run.call_args.args[0]
        pairs = [command[i : i + 2] for i in range(len(command) - 1)]
        self.assertIn(["--disable", "shell_tool"], pairs)
        self.assertIn(["--disable", "multi_agent"], pairs)
        self.assertIn(["--disable", "remote_plugin"], pairs)
        self.assertIn(["-c", "tools.web_search=false"], pairs)
        self.assertIn(["-c", "tools.view_image=false"], pairs)
        self.assertIn(["-c", 'model_reasoning_effort="medium"'], pairs)

    def test_briefing_prompt_does_not_tell_model_to_fetch_corpus(self):
        prompt = (ROOT / "briefing-prompt.md").read_text(encoding="utf-8")
        self.assertNotIn("python3 fetch_news.py", prompt)
        self.assertIn("runner has already fetched", prompt)

    def test_codex_rejects_any_tool_event(self):
        events = [
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "pwd"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"schema_version":1}'},
            },
        ]
        completed = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
        with patch("shutil.which", return_value="/bin/codex"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 20.0, 1)
        ), self.assertRaisesRegex(ProviderError, "empty tool policy"):
            CodexCliProvider("gpt").generate(REQUEST)

    def test_codex_rejects_malformed_item_without_string_type(self):
        events = [
            {
                "type": "item.completed",
                "item": {"command": "cat /secret"},
            },
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"schema_version":1}'},
            },
        ]
        completed = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
        with patch("shutil.which", return_value="/bin/codex"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 20.0, 1)
        ), self.assertRaisesRegex(ProviderError, "malformed item record"):
            CodexCliProvider("gpt").generate(REQUEST)

    def test_codex_rejects_unknown_lifecycle_event(self):
        completed = subprocess.CompletedProcess(
            [], 0, json.dumps({"type": "future.event", "payload": {}}), ""
        )
        with patch("shutil.which", return_value="/bin/codex"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 20.0, 1)
        ), self.assertRaisesRegex(ProviderError, "unsupported lifecycle event"):
            CodexCliProvider("gpt").generate(REQUEST)


if __name__ == "__main__":
    unittest.main()
