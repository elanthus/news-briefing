"""Dependency-free adapters for OpenRouter, OpenAI-compatible servers, Claude Code, and Codex CLI."""

from __future__ import annotations

import ipaddress
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
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


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect so ``Authorization`` never follows a ``Location`` header.

    urllib's default handler copies the request headers, bearer token
    included, onto whatever host the redirect names. The provider endpoints
    are fixed URLs, so a redirect is a misconfiguration or a hostile
    intermediary either way; it surfaces as a non-transient HTTP 3xx error.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _urlopen(request: urllib.request.Request, *, timeout: float) -> Any:
    """Open a provider request through the no-redirect opener."""
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


def _post_chat_completion(
    provider: str,
    endpoint: str,
    headers: dict[str, str],
    body: bytes,
    *,
    timeout: int,
    flag_model_404: bool = False,
) -> tuple[bytes, str | None, float, int]:
    """POST one chat-completions request under the shared retry policy.

    Returns the response body, the ``x-request-id`` header when present, the
    wall-clock latency in milliseconds, and the number of attempts made. Only
    explicit transient failures are retried, and a timeout after transmission
    may have begun is never retried because completion is ambiguous.
    """
    http_request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    started = time.perf_counter()
    deadline = started + timeout
    attempt = 0
    request_id: str | None = None
    while True:
        attempt += 1
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            raise ProviderError(
                f"{provider} timed out after {attempt - 1} attempt(s)",
                transient=True,
                attempts=attempt - 1,
            )
        failure: ProviderError | None = None
        cause: Exception | None = None
        try:
            with _urlopen(http_request, timeout=remaining) as response:
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
                f"{provider} HTTP {exc.code}: {detail[:500]}",
                transient=transient,
                attempts=attempt,
                status_code=exc.code,
                retry_after=retry_after,
                openrouter_model_404=flag_model_404 and exc.code == 404,
            )
            cause = exc
        except TimeoutError as exc:
            raise ProviderError(
                f"{provider} request timed out after transmission may have begun; completion is ambiguous",
                transient=True,
                attempts=attempt,
                ambiguous_completion=True,
            ) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderError(
                    f"{provider} request timed out after transmission may have begun; completion is ambiguous",
                    transient=True,
                    attempts=attempt,
                    ambiguous_completion=True,
                ) from exc
            failure = ProviderError(
                f"{provider} request failed: {exc}", transient=True, attempts=attempt
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
    return response_body, request_id, (time.perf_counter() - started) * 1000, attempt


def _parse_chat_completion(
    provider: str,
    response_body: bytes,
    request_id: str | None,
    latency_ms: float,
    attempt: int,
    normalize: Callable[[str], str] | None = None,
) -> ModelResponse:
    """Turn a chat-completions response into a ModelResponse, enforcing the empty tool policy.

    ``normalize`` may rewrite the text content before JSON parsing; the raw
    content is preserved in the response either way.
    """
    try:
        payload = json.loads(response_body)
        choice = payload["choices"][0]
        message = choice["message"]
        content = message["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError(f"{provider} returned an unexpected response", transient=False) from exc
    if message.get("tool_calls") or message.get("function_call"):
        raise ProviderError(
            f"{provider} violated the empty tool policy by returning a tool call",
            transient=False,
            attempts=attempt,
            provider_request_id=payload.get("id") or request_id,
        )
    if not isinstance(content, str):
        raise ProviderError(
            f"{provider} returned no text content",
            transient=False,
            attempts=attempt,
            provider_request_id=payload.get("id") or request_id,
        )
    if isinstance(choice, dict) and choice.get("finish_reason") == "length":
        # The body is a prefix of the intended output, so it would fail as
        # invalid JSON with a misleading cause. Name the real one: the output
        # ceiling, which is the only bound on array sizes under a lean schema.
        raise ProviderError(
            f"{provider} output was truncated at the max_tokens ceiling (finish_reason=length)",
            transient=False,
            attempts=attempt,
            provider_request_id=payload.get("id") or request_id,
            output_truncated=True,
        )
    structured = _parse_json_object(normalize(content) if normalize else content, provider)
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
        max_tokens: int | None = None,
        endpoint: str | None = None,
    ):
        if reasoning_effort is not None and reasoning_enabled is False:
            raise ValueError("reasoning effort cannot be combined with disabled reasoning")
        self.model = model
        self.temperature = temperature
        self.reasoning_enabled = True if reasoning_effort is not None else reasoning_enabled
        self.reasoning_effort = reasoning_effort
        self.max_tokens = 100_000 if max_tokens is None else max_tokens
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
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-OpenRouter-Title": "news-briefing",
        }
        response_body, request_id, latency_ms, attempt = _post_chat_completion(
            self.name,
            self.endpoint,
            headers,
            body,
            timeout=request.timeout_seconds,
            flag_model_404=True,
        )
        return _parse_chat_completion(self.name, response_body, request_id, latency_ms, attempt)


_LEAN_STRIPPED_KEYWORDS = frozenset({"minItems", "maxItems", "minLength", "maxLength"})
#: Keywords whose value is a map from user-chosen names to subschemas.
_SCHEMA_MAP_KEYWORDS = frozenset({"properties", "$defs", "definitions"})


def _lean_local_schema(value: Any) -> Any:
    """Return a copy without string-length bounds or ranged array-size bounds.

    An array whose ``minItems`` equals its ``maxItems`` keeps both. Exact
    counts compile to a fixed repetition, which is cheap for every engine,
    and the prose schema relies on them: each section must return exactly as
    many entries as its frozen selection, in order, so that code can attach
    the frozen citations positionally. Dropping that bound turns a short
    answer into a ``frozen_selection_count`` error that no deterministic
    repair can fix. The expensive case in the measurements below is the
    ranged bound, above all the citation arrays sized to their eligible set.

    Constrained-decoding engines that expand bounded repetition into explicit
    states (LM Studio's MLX engine is the observed case) take minutes to
    compile the selection schema. Measured on Qwen3 30B-A3B with a 235-item
    corpus: citation arrays bounded at their eligible-set size did not
    compile in 25 minutes; capped at 10 they took about 10 minutes; with the
    citation bounds removed but the five-topic section bounds kept, 106
    seconds; with no array bounds at all, 20 seconds. The prose schema's
    ``maxLength`` of up to 1,500 characters per string hangs the same engine
    the same way. Enums are kept intact, so the grammar still limits every
    citation to an eligible handle. Sizes and lengths are then bounded only
    by the provider's ``max_tokens``, and section sizes, text lengths,
    duplicate references, and ineligible references are all still rejected by
    the code-owned validator.
    """
    if isinstance(value, dict):
        exact_count = (
            "minItems" in value and "maxItems" in value and value["minItems"] == value["maxItems"]
        )
        kept = {"minItems", "maxItems"} if exact_count else set()
        return {
            key: (
                # Member names under a schema map are user data (a section can
                # be called anything), not keywords; only their values are schemas.
                {name: _lean_local_schema(member) for name, member in nested.items()}
                if key in _SCHEMA_MAP_KEYWORDS and isinstance(nested, dict)
                else _lean_local_schema(nested)
            )
            for key, nested in value.items()
            if key not in _LEAN_STRIPPED_KEYWORDS or key in kept
        }
    if isinstance(value, list):
        return [_lean_local_schema(item) for item in value]
    return value


_THINK_OPEN = "<think>"
_THINK_CLOSE = "</think>"
_FENCE = "```"


def _strip_local_wrappers(content: str) -> str:
    """Remove a leading ``<think>`` block and a surrounding code fence.

    A schema grammar constrains the JSON itself, but reasoning models served
    by llama.cpp or LM Studio can emit their thinking before the grammar
    engages, and some chat templates wrap constrained output in a Markdown
    fence. Both are transport noise around the same JSON object, so they are
    stripped before parsing. Anything else still fails as invalid JSON.

    This is plain string handling rather than a regex on purpose: the
    response is unbounded model output, and a pattern with nested optional
    whitespace around a lazy body backtracks super-linearly on an unclosed
    fence followed by whitespace, which a degenerate local model can emit for
    thousands of tokens after the HTTP timeout has already been satisfied.
    """
    text = content.lstrip()
    if text.startswith(_THINK_OPEN):
        close = text.find(_THINK_CLOSE)
        if close == -1:
            return text
        text = text[close + len(_THINK_CLOSE):].lstrip()
    if text.startswith(_FENCE):
        inner = text[len(_FENCE):]
        if inner.startswith("json"):
            inner = inner[len("json"):]
        inner = inner.strip()
        if inner.endswith(_FENCE):
            return inner[: -len(_FENCE)].rstrip()
    return text


def _redact_endpoint(url: str) -> str:
    """Drop any ``user:password@`` userinfo so the run manifest never records it."""
    parts = urllib.parse.urlsplit(url)
    if "@" not in parts.netloc:
        return url
    host = parts.netloc.rsplit("@", 1)[1]
    return urllib.parse.urlunsplit(parts._replace(netloc=host))


def _is_loopback_endpoint(url: str) -> bool:
    hostname = urllib.parse.urlsplit(url).hostname
    if hostname is None:
        return False
    if hostname == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


class OpenAICompatibleProvider(ModelProvider):
    """Any server that speaks the OpenAI chat-completions API.

    Ollama, llama.cpp server, LM Studio, and vLLM all expose
    ``/v1/chat/completions`` locally, so one adapter covers them. It shares
    the transport, retry, and tool-policy code with :class:`OpenRouterProvider`
    but sends none of OpenRouter's routing or reasoning fields. The endpoint
    defaults to Ollama's; pass ``endpoint`` for anything else.

    Authentication is optional: local servers usually ignore it, hosted
    OpenAI-compatible gateways usually require it. When ``OPENAI_COMPATIBLE_API_KEY``
    is set it is sent as a bearer token.
    """

    name = "openai-compatible"
    DEFAULT_ENDPOINT = "http://127.0.0.1:11434/v1/chat/completions"
    API_KEY_ENV = "OPENAI_COMPATIBLE_API_KEY"
    #: Output ceiling applied when the lean profile removes the citation
    #: array bounds and the caller set none. Real briefings complete in about
    #: 5,000 tokens; this stops a repeating model from filling the context.
    LEAN_DEFAULT_MAX_TOKENS = 16_000

    def __init__(
        self,
        model: str,
        *,
        endpoint: str | None = None,
        temperature: float = 0,
        max_tokens: int | None = None,
        lean_schema: bool = False,
    ):
        self.model = model
        self.endpoint = endpoint or self.DEFAULT_ENDPOINT
        self.temperature = temperature
        self.lean_schema = lean_schema
        if lean_schema and max_tokens is None:
            max_tokens = self.LEAN_DEFAULT_MAX_TOKENS
        self.max_tokens = max_tokens

    def info(self) -> dict[str, Any]:
        return {
            "provider": self.name,
            "model": self.model,
            "transport": "OpenAI-compatible chat completions API",
            "endpoint": _redact_endpoint(self.endpoint),
            "authentication": f"{self.API_KEY_ENV} (optional; sent as a bearer token when set)",
            "tool_policy": "No tools are included; any returned tool_calls are rejected.",
            "schema_profile": "lean" if self.lean_schema else "full",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

    def _sent_schema(self, output_schema: dict[str, Any]) -> dict[str, Any]:
        schema = _grammar_compatible_schema(output_schema)
        return _lean_local_schema(schema) if self.lean_schema else schema

    def _payload(self, request: GenerationRequest) -> dict[str, Any]:
        """Build the chat-completions request body for a local server.

        Only fields every common local server accepts are sent:

        * ``response_format`` in ``json_schema`` form is how Ollama, llama.cpp
          server, LM Studio, and vLLM constrain output to the schema. It is
          the same mechanism OpenRouter uses; ``strict`` is ignored where
          unsupported.
        * The schema is sent without ``uniqueItems``. llama.cpp's grammar
          converter, which Ollama and LM Studio's GGUF engine reuse, drops
          keywords it cannot compile without reporting it, and the MLX and
          vLLM engines do not implement it either. Stripping it makes every
          backend behave the same way; ``maxItems`` keeps the array bounded
          and the code-owned validator rejects duplicate citations.
        * ``max_tokens`` is sent only when the caller set one. It bounds
          generation, not context, and a value above the server's window is
          a hard error on vLLM.
        * OpenRouter's ``provider`` and ``reasoning`` fields are omitted.
          Servers that validate the request body reject unknown keys.
        * With ``lean_schema`` array-size and string-length bounds are also
          removed; see ``_lean_local_schema`` for the engines that need it.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": self.temperature,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "news_briefing",
                    "strict": True,
                    "schema": self._sent_schema(request.output_schema),
                },
            },
        }
        if self.max_tokens is not None:
            payload["max_tokens"] = self.max_tokens
        return payload

    def generate(self, request: GenerationRequest) -> ModelResponse:
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get(self.API_KEY_ENV)
        if api_key:
            scheme = urllib.parse.urlsplit(self.endpoint).scheme
            if scheme != "https" and not _is_loopback_endpoint(self.endpoint):
                raise ProviderError(
                    f"{self.name} refuses to send {self.API_KEY_ENV} over cleartext to a non-loopback "
                    f"endpoint: {_redact_endpoint(self.endpoint)}",
                    transient=False,
                )
            headers["Authorization"] = f"Bearer {api_key}"
        body = json.dumps(self._payload(request)).encode("utf-8")
        try:
            response_body, request_id, latency_ms, attempt = _post_chat_completion(
                self.name, self.endpoint, headers, body, timeout=request.timeout_seconds
            )
        except ProviderError as exc:
            if self.lean_schema or not exc.ambiguous_completion:
                raise
            # A local server that never answers is usually still compiling the
            # schema, not generating; say so instead of leaving a bare timeout.
            raise ProviderError(
                f"{exc}. If the server compiles the schema slowly (LM Studio's MLX engine "
                "is one), retry with --lean-schema",
                transient=exc.transient,
                attempts=exc.attempts,
                status_code=exc.status_code,
                retry_after=exc.retry_after,
                provider_request_id=exc.provider_request_id,
                ambiguous_completion=True,
            ) from exc
        return _parse_chat_completion(
            self.name, response_body, request_id, latency_ms, attempt, normalize=_strip_local_wrappers
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
    max_tokens: int | None = None,
    endpoint: str | None = None,
    lean_schema: bool = False,
) -> ModelProvider:
    if (endpoint is not None or lean_schema) and name != "openai-compatible":
        raise ValueError("endpoint and lean_schema apply to the openai-compatible provider only")
    if name == "openrouter":
        return OpenRouterProvider(
            model,
            temperature=temperature,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=reasoning_effort,
            max_tokens=max_tokens,
        )
    if name == "openai-compatible":
        return OpenAICompatibleProvider(
            model,
            endpoint=endpoint,
            temperature=temperature,
            max_tokens=max_tokens,
            lean_schema=lean_schema,
        )
    if name == "claude-code-cli":
        return ClaudeCodeProvider(model)
    if name == "codex-cli":
        return CodexCliProvider(model)
    raise ValueError(f"unknown provider {name!r}")
