import io
import json
import os
import subprocess
import unittest
import urllib.error
from unittest.mock import patch

from agent_runner.models import GenerationRequest, ProviderError
from agent_runner.providers import ClaudeCodeProvider, CodexCliProvider, OpenRouterProvider, _run_cli

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


class ProviderTests(unittest.TestCase):
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
        self.assertNotIn("tools", sent)
        self.assertEqual(result.structured_output, {"schema_version": 1})
        self.assertEqual(result.cost_usd, 0.01)

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

    def test_claude_uses_explicit_empty_tool_policy(self):
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
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--disallowedTools") + 1], "*")
        self.assertEqual(result.structured_output, {"schema_version": 1})

    def test_cli_retries_transient_failure_before_output(self):
        failed = subprocess.CompletedProcess([], 1, "", "service unavailable")
        succeeded = subprocess.CompletedProcess([], 0, "result", "")
        with patch("subprocess.run", side_effect=[failed, succeeded]) as run, patch("time.sleep"):
            completed, _latency, attempts = _run_cli(["model", "run"], "prompt", timeout=30)
        self.assertEqual(completed.stdout, "result")
        self.assertEqual(attempts, 2)
        self.assertEqual(run.call_count, 2)

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
            {
                "type": "item.completed",
                "item": {"type": "agent_message", "text": '{"schema_version":1}'},
            },
            {"type": "turn.completed", "usage": {"input_tokens": 4, "output_tokens": 2}},
        ]
        completed = subprocess.CompletedProcess([], 0, "\n".join(map(json.dumps, events)), "")
        with patch("shutil.which", return_value="/bin/codex"), patch(
            "agent_runner.providers._run_cli", return_value=(completed, 20.0, 1)
        ):
            result = CodexCliProvider("gpt").generate(REQUEST)
        self.assertEqual(result.provider_request_id, "thread-1")
        self.assertEqual(result.structured_output, {"schema_version": 1})

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


if __name__ == "__main__":
    unittest.main()
