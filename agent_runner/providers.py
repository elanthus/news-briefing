"""Dependency-free adapters for OpenRouter, Claude Code, and Codex CLI."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from copy import deepcopy
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from agent_runner.models import GenerationRequest, ModelProvider, ModelResponse, ProviderError

MAX_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = {408, 425, 429}
_TRANSIENT_CLI_MARKERS = (
    "rate limit",
    "temporarily unavailable",
    "service unavailable",
    "connection reset",
    "connection refused",
    "timed out",
    "timeout",
)
_SCHEMA_MAP_KEYWORDS = frozenset(
    {
        "$defs",
        "definitions",
        "dependencies",
        "dependentSchemas",
        "patternProperties",
        "properties",
    }
)
_SCHEMA_VALUE_KEYWORDS = frozenset(
    {
        "additionalItems",
        "additionalProperties",
        "allOf",
        "anyOf",
        "contains",
        "contentSchema",
        "else",
        "if",
        "items",
        "not",
        "oneOf",
        "prefixItems",
        "propertyNames",
        "then",
        "unevaluatedItems",
        "unevaluatedProperties",
    }
)


def _parse_json_object(text: str, provider: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderError(
            f"{provider} returned invalid JSON: {exc}", transient=False
        ) from exc
    if not isinstance(value, dict):
        raise ProviderError(f"{provider} returned a non-object JSON value", transient=False)
    return value


#: OpenRouter models whose backends cannot compile ``uniqueItems`` into a
#: sampling grammar. ``tencent/hy3`` is served by DeepInfra and AtlasCloud, both
#: of which answer a schema carrying the keyword with
#: ``Grammar error: Unimplemented keys: ["uniqueItems"]`` instead of a
#: completion. Models are listed individually rather than stripped globally:
#: dropping the keyword measurably degrades output, so it stays in the schema
#: for every model that can compile it.
_UNIQUE_ITEMS_INCOMPATIBLE_MODELS = frozenset({"tencent/hy3"})


def _grammar_compatible_schema(value: Any) -> Any:
    """Return a copy without ``uniqueItems``, for backends that reject it.

    Codex structured outputs reject the keyword outright, and the OpenRouter
    models in ``_UNIQUE_ITEMS_INCOMPATIBLE_MODELS`` cannot compile it. The
    code-owned validator still rejects a duplicate citation after the response
    is returned, but removing the keyword is not free: paired with the ``enum``,
    distinctness also bounded the array's length, so a stripped schema depends
    on ``citation_refs``'s explicit ``maxItems`` to stay terminating.
    """
    if isinstance(value, dict):
        compatible: dict[str, Any] = {}
        for key, nested in value.items():
            if key == "uniqueItems":
                continue
            if key in _SCHEMA_MAP_KEYWORDS and isinstance(nested, dict):
                compatible[key] = {
                    name: _grammar_compatible_schema(schema)
                    for name, schema in nested.items()
                }
            elif key in _SCHEMA_VALUE_KEYWORDS:
                compatible[key] = _grammar_compatible_schema(nested)
            else:
                compatible[key] = deepcopy(nested)
        return compatible
    if isinstance(value, list):
        return [_grammar_compatible_schema(item) for item in value]
    return value


def _command_version(command: str) -> str | None:
    if shutil.which(command) is None:
        return None
    try:
        completed = subprocess.run(
            [command, "--version"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    rendered = completed.stdout.strip() or completed.stderr.strip()
    return rendered.splitlines()[0] if rendered else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _safe_openrouter_error_detail(detail: str) -> str:
    """Remove account-scoped identifiers before provider errors become artifacts."""
    try:
        payload = json.loads(detail)
    except json.JSONDecodeError:
        return detail

    def redact(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: "[redacted]" if key.casefold() == "user_id" else redact(nested)
                for key, nested in value.items()
            }
        if isinstance(value, list):
            return [redact(item) for item in value]
        return value

    payload = redact(payload)
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False)


def _retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value.strip()))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    return max(0.0, (target - (now or datetime.now(UTC))).total_seconds())


def _run_cli(
    command: list[str],
    prompt: str,
    *,
    timeout: int,
    cwd: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], float, int]:
    started = time.perf_counter()
    deadline = started + timeout
    for attempt in range(1, MAX_ATTEMPTS + 1):
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise ProviderError(
                f"{' '.join(command[:2])} timed out after {attempt - 1} attempt(s)",
                transient=True,
                attempts=attempt - 1,
            )
        try:
            completed = subprocess.run(
                command,
                input=prompt,
                text=True,
                # CLI children speak UTF-8; the Windows locale codec cannot
                # round-trip corpus text (e.g. U+200A from scraped articles).
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=remaining,
                check=False,
                cwd=cwd,
            )
        except subprocess.TimeoutExpired as exc:
            raise ProviderError(
                f"{' '.join(command[:2])} exceeded the {timeout}s call deadline",
                transient=True,
                attempts=attempt,
                ambiguous_completion=True,
            ) from exc
        if completed.returncode == 0:
            return completed, (time.perf_counter() - started) * 1000, attempt
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        emitted_output = bool(completed.stdout.strip())
        transient = any(marker in detail.casefold() for marker in _TRANSIENT_CLI_MARKERS)
        if emitted_output or not transient or attempt >= MAX_ATTEMPTS:
            raise ProviderError(
                f"{' '.join(command[:2])} failed: {detail[:1000]}",
                transient=transient,
                attempts=attempt,
                ambiguous_completion=emitted_output,
            )
        delay = float(2 ** (attempt - 1))
        if delay >= deadline - time.perf_counter():
            raise ProviderError(
                f"{' '.join(command[:2])} failed and the retry delay exceeds the call deadline",
                transient=True,
                attempts=attempt,
            )
        time.sleep(delay)
    raise AssertionError("unreachable CLI retry state")


class OpenRouterProvider(ModelProvider):
    name = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(
        self,
        model: str,
        *,
        temperature: float = 0,
        reasoning_enabled: bool | None = True,
        reasoning_effort: str | None = None,
        max_tokens: int = 100_000,
        endpoint: str | None = None,
    ):
        if reasoning_effort is not None and reasoning_enabled is False:
            raise ValueError("reasoning effort cannot be combined with disabled reasoning")
        self.model = model
        self.temperature = temperature
        self.reasoning_enabled = True if reasoning_effort is not None else reasoning_enabled
        self.reasoning_effort = reasoning_effort
        self.max_tokens = max_tokens
        if endpoint is not None:
            self.endpoint = endpoint

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "transport": "OpenRouter chat completions API",
            "endpoint": self.endpoint,
            "authentication": "OPENROUTER_API_KEY",
            "tool_policy": "No tools are included; any returned tool_calls are rejected.",
            "temperature": self.temperature,
            "reasoning_enabled": self.reasoning_enabled,
            "reasoning_effort": self.reasoning_effort,
            "max_tokens": self.max_tokens,
        }

    def _sent_schema(self, output_schema: dict[str, Any]) -> dict[str, Any]:
        """Strip ``uniqueItems`` only for models whose backends cannot compile it."""
        if self.model in _UNIQUE_ITEMS_INCOMPATIBLE_MODELS:
            return _grammar_compatible_schema(output_schema)
        return output_schema

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "news_briefing",
                    "strict": True,
                    "schema": self._sent_schema(request.output_schema),
                },
            },
            "provider": {"require_parameters": True},
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        elif self.reasoning_enabled is not None:
            payload["reasoning"] = {"enabled": self.reasoning_enabled}
        return payload

    def generate(self, request: GenerationRequest) -> ModelResponse:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise ProviderError("OPENROUTER_API_KEY is required for openrouter", transient=False)
        body = json.dumps(self._payload(request)).encode("utf-8")
        http_request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "X-OpenRouter-Title": "news-briefing",
            },
            method="POST",
        )
        started = time.perf_counter()
        deadline = started + request.timeout_seconds
        attempt = 0
        request_id: str | None = None
        while True:
            attempt += 1
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise ProviderError(
                    f"openrouter timed out after {attempt - 1} attempt(s)",
                    transient=True,
                    attempts=attempt - 1,
                )
            failure: ProviderError | None = None
            cause: Exception | None = None
            try:
                with urllib.request.urlopen(http_request, timeout=remaining) as response:
                    response_body = response.read()
                    request_id = response.headers.get("x-request-id")
                break
            except urllib.error.HTTPError as exc:
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                finally:
                    exc.close()
                detail = _safe_openrouter_error_detail(detail)
                transient = exc.code in RETRYABLE_HTTP_STATUSES or 500 <= exc.code <= 599
                failure = ProviderError(
                    f"openrouter HTTP {exc.code}: {detail[:500]}",
                    transient=transient,
                    attempts=attempt,
                    status_code=exc.code,
                    retry_after=retry_after,
                    openrouter_model_404=exc.code == 404,
                )
                cause = exc
            except TimeoutError as exc:
                raise ProviderError(
                    "openrouter request timed out after transmission may have begun; completion is ambiguous",
                    transient=True,
                    attempts=attempt,
                    ambiguous_completion=True,
                ) from exc
            except urllib.error.URLError as exc:
                if isinstance(exc.reason, TimeoutError):
                    raise ProviderError(
                        "openrouter request timed out after transmission may have begun; completion is ambiguous",
                        transient=True,
                        attempts=attempt,
                        ambiguous_completion=True,
                    ) from exc
                failure = ProviderError(
                    f"openrouter request failed: {exc}", transient=True, attempts=attempt
                )
                cause = exc
            if not failure.transient or attempt >= MAX_ATTEMPTS:
                raise failure from cause
            delay = failure.retry_after if failure.retry_after is not None else float(2 ** (attempt - 1))
            remaining = max(0.0, deadline - time.perf_counter())
            if delay >= remaining:
                raise ProviderError(
                    f"{failure}; retry delay {delay:g}s exceeds the remaining deadline",
                    transient=True,
                    attempts=attempt,
                    status_code=failure.status_code,
                    retry_after=delay,
                ) from cause
            if delay:
                time.sleep(delay)

        latency_ms = (time.perf_counter() - started) * 1000
        try:
            payload = json.loads(response_body)
            message = payload["choices"][0]["message"]
            content = message["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("openrouter returned an unexpected response", transient=False) from exc
        if message.get("tool_calls") or message.get("function_call"):
            raise ProviderError(
                "openrouter violated the empty tool policy by returning a tool call",
                transient=False,
                attempts=attempt,
                provider_request_id=payload.get("id") or request_id,
            )
        if not isinstance(content, str):
            raise ProviderError(
                "openrouter returned no text content",
                transient=False,
                attempts=attempt,
                provider_request_id=payload.get("id") or request_id,
            )
        structured = _parse_json_object(content, self.name)
        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        cost = usage.get("cost")
        return ModelResponse(
            raw_output=content,
            structured_output=structured,
            latency_ms=latency_ms,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=_optional_float(cost),
            provider_request_id=payload.get("id") or request_id,
            usage=usage,
            attempts=attempt,
        )


class ClaudeCodeProvider(ModelProvider):
    name = "claude-code-cli"

    def __init__(self, model: str):
        self.model = model

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "command": "claude",
            "version": _command_version("claude"),
            "authentication": "Claude Code cached login (subscription or Console)",
            "tool_policy": (
                "--safe-mode disables customizations; --tools StructuredOutput plus "
                "--allowedTools StructuredOutput exposes and permits only the internal "
                "schema-output tool."
            ),
        }

    def generate(self, request: GenerationRequest) -> ModelResponse:
        if shutil.which("claude") is None:
            raise ProviderError("claude command is required for claude-code-cli", transient=False)
        command = [
            "claude",
            "--print",
            "--safe-mode",
            "--output-format",
            "json",
            "--model",
            self.model,
            "--tools",
            "StructuredOutput",
            "--allowedTools",
            "StructuredOutput",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--json-schema",
            json.dumps(request.output_schema, separators=(",", ":")),
        ]
        completed, latency_ms, attempts = _run_cli(
            command, request.prompt, timeout=request.timeout_seconds
        )
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise ProviderError("claude-code-cli returned invalid wrapper JSON", transient=False) from exc
        if not isinstance(payload, dict):
            raise ProviderError("claude-code-cli returned a non-object wrapper", transient=False)
        if payload.get("is_error"):
            raise ProviderError(
                f"claude-code-cli failed: {payload.get('result', 'unknown error')}",
                transient=False,
                attempts=attempts,
                provider_request_id=payload.get("session_id"),
            )
        structured = payload.get("structured_output")
        raw = payload.get("result", "")
        if not isinstance(structured, dict):
            if not isinstance(raw, str):
                raise ProviderError("claude-code-cli returned no structured output", transient=False)
            structured = _parse_json_object(raw, self.name)
        raw_usage = payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, dict) else {}
        total_cost = payload.get("total_cost_usd")
        return ModelResponse(
            raw_output=raw if isinstance(raw, str) else json.dumps(structured, ensure_ascii=False),
            structured_output=structured,
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=_optional_float(total_cost),
            provider_request_id=payload.get("session_id"),
            usage=usage,
            attempts=attempts,
        )


class CodexCliProvider(ModelProvider):
    name = "codex-cli"
    _ALLOWED_ITEM_TYPES = {"agent_message", "reasoning"}
    _ALLOWED_EVENT_TYPES = {
        "thread.started",
        "turn.started",
        "item.started",
        "item.updated",
        "item.completed",
        "turn.completed",
    }
    _ITEM_EVENT_TYPES = {"item.started", "item.updated", "item.completed"}

    def __init__(self, model: str):
        self.model = model

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "command": "codex",
            "version": _command_version("codex"),
            "authentication": "Codex cached login (ChatGPT subscription or API key)",
            "tool_policy": (
                "Codex shell, multi-agent, remote-plugin, web-search, and image tools are explicitly "
                "disabled. It also runs in an empty read-only sandbox, and any non-message/reasoning "
                "item in its JSON trace fails the run."
            ),
        }

    def generate(self, request: GenerationRequest) -> ModelResponse:
        if shutil.which("codex") is None:
            raise ProviderError("codex command is required for codex-cli", transient=False)
        with tempfile.TemporaryDirectory(prefix="news-briefing-codex-") as directory:
            schema_path = Path(directory) / "output-schema.json"
            schema_path.write_text(
                json.dumps(_grammar_compatible_schema(request.output_schema)),
                encoding="utf-8",
            )
            command = [
                "codex",
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--ignore-rules",
                "--skip-git-repo-check",
                "--disable",
                "shell_tool",
                "--disable",
                "multi_agent",
                "--disable",
                "remote_plugin",
                "-c",
                "tools.web_search=false",
                "-c",
                "tools.view_image=false",
                "-c",
                'model_reasoning_effort="medium"',
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--json",
                "--output-schema",
                str(schema_path),
                "--model",
                self.model,
                "-",
            ]
            completed, latency_ms, attempts = _run_cli(
                command, request.prompt, timeout=request.timeout_seconds, cwd=directory
            )
        events: list[dict[str, Any]] = []
        text = ""
        usage: dict[str, Any] = {}
        request_id: str | None = None
        tool_items: list[str] = []
        for line in completed.stdout.splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ProviderError("codex-cli returned invalid JSONL", transient=False) from exc
            if not isinstance(event, dict):
                raise ProviderError("codex-cli returned a non-object JSONL event", transient=False)
            event_type = event.get("type")
            if not isinstance(event_type, str) or event_type not in self._ALLOWED_EVENT_TYPES:
                raise ProviderError(
                    f"codex-cli returned an unsupported lifecycle event: {event_type!r}",
                    transient=False,
                )
            events.append(event)
            thread_id = event.get("thread_id")
            if request_id is None and isinstance(thread_id, str):
                request_id = thread_id
            item = event.get("item")
            if event_type in self._ITEM_EVENT_TYPES:
                if not isinstance(item, dict):
                    raise ProviderError("codex-cli returned a malformed item record", transient=False)
                item_type = item.get("type")
                if not isinstance(item_type, str):
                    raise ProviderError("codex-cli returned a malformed item record", transient=False)
                if item_type not in self._ALLOWED_ITEM_TYPES:
                    tool_items.append(item_type)
                if event_type == "item.completed" and item_type == "agent_message":
                    candidate = item.get("text")
                    if isinstance(candidate, str):
                        text = candidate
            elif item is not None:
                raise ProviderError("codex-cli returned an item on a non-item event", transient=False)
            if event_type == "turn.completed" and isinstance(event.get("usage"), dict):
                usage = event["usage"]
        if tool_items:
            rendered = ", ".join(sorted(set(tool_items)))
            raise ProviderError(
                f"codex-cli violated the empty tool policy: {rendered}",
                transient=False,
                attempts=attempts,
                provider_request_id=request_id,
            )
        if not text:
            raise ProviderError("codex-cli returned no final agent message", transient=False)
        return ModelResponse(
            raw_output=text,
            structured_output=_parse_json_object(text, self.name),
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            provider_request_id=request_id,
            usage=usage,
            attempts=attempts,
            provider_events=tuple(events),
        )


def provider_for(
    name: str,
    model: str,
    *,
    temperature: float = 0,
    reasoning_enabled: bool | None = True,
    reasoning_effort: str | None = None,
    max_tokens: int = 100_000,
) -> ModelProvider:
    if name == "openrouter":
        return OpenRouterProvider(
            model,
            temperature=temperature,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
    if name == "claude-code-cli":
        return ClaudeCodeProvider(model)
    if name == "codex-cli":
        return CodexCliProvider(model)
    raise ValueError(f"unknown provider {name!r}")
