import io
import json
import os
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from agent_runner.models import GenerationRequest, ProviderError
from agent_runner.providers import (
    ClaudeCodeProvider,
    CodexCliProvider,
    OpenRouterProvider,
    _codex_compatible_schema,
    _command_version,
    _run_cli,
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
    def test_codex_schema_removes_unique_items_without_mutating_source(self):
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

        compatible = _codex_compatible_schema(schema)

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
            "urllib.request.urlopen", return_value=FakeResponse(payload)
        ) as opened:
            result = provider.generate(REQUEST)
        sent = json.loads(opened.call_args.args[0].data)
        self.assertEqual(sent["response_format"]["json_schema"]["schema"], SCHEMA)
        self.assertEqual(sent["provider"], {"require_parameters": True})
        self.assertEqual(sent["reasoning"], {"enabled": True})
        self.assertNotIn("tools", sent)
        self.assertEqual(result.structured_output, {"schema_version": 1})
        self.assertEqual(result.cost_usd, 0.01)

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
            "urllib.request.urlopen", return_value=FakeResponse(payload)
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
            "urllib.request.urlopen", return_value=FakeResponse(payload)
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
            "urllib.request.urlopen", side_effect=[error, success]
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
            "urllib.request.urlopen", side_effect=error
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
            "urllib.request.urlopen", side_effect=error
        ), self.assertRaises(ProviderError) as raised:
            provider.generate(REQUEST)
        self.assertTrue(raised.exception.openrouter_model_404)
        self.assertTrue(raised.exception.record()["openrouter_model_404"])
        self.assertEqual(raised.exception.status_code, 404)

    def test_openrouter_does_not_retry_ambiguous_response_timeout(self):
        provider = OpenRouterProvider("vendor/model")
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "secret"}), patch(
            "urllib.request.urlopen", return_value=TimeoutResponse({})
        ) as opened, self.assertRaises(ProviderError) as raised:
            provider.generate(REQUEST)
        self.assertTrue(raised.exception.ambiguous_completion)
        self.assertEqual(raised.exception.attempts, 1)
        self.assertEqual(opened.call_count, 1)

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
