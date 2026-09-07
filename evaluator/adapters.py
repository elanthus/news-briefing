"""Provider adapters for the two agent CLIs and two OpenAI-compatible APIs."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from agent_runner.models import GenerationRequest, ModelProvider, ProviderError
from agent_runner.providers import provider_for as runner_provider_for

API_MAX_ATTEMPTS = 3
RETRYABLE_HTTP_STATUSES = {408, 425, 429}


@dataclass(frozen=True)
class Generation:
    text: str
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    cost_note: str | None = None
    provider_request_id: str | None = None
    usage: dict[str, Any] | None = None
    attempts: int = 1
    structured_output: dict[str, Any] | None = None

    def record(self) -> dict[str, Any]:
        record = asdict(self)
        record.pop("structured_output")
        return record


class Adapter:
    provider: str

    def __init__(self, model: str, timeout: int = 300):
        self.model = model
        self.timeout = timeout

    def generate(self, prompt: str) -> Generation:
        raise NotImplementedError

    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        raise NotImplementedError(
            f"{self.provider} does not implement the production structured-output transport"
        )

    def generation_controls(self) -> dict[str, Any]:
        return {
            "temperature": None,
            "seed": None,
            "disclosure": (
                "This CLI exposes no evaluator control for temperature or seed; repeated trials are "
                "stochastic and are not directly comparable to API runs made with temperature=0."
            ),
        }


class ProviderRequestError(RuntimeError):
    """A provider failure with enough structure for retry and circuit-breaker policy."""

    def __init__(
        self,
        message: str,
        *,
        transient: bool,
        attempts: int = 1,
        status_code: int | None = None,
        retry_after: float | None = None,
        cost_usd: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        provider_request_id: str | None = None,
    ):
        super().__init__(message)
        self.transient = transient
        self.attempts = attempts
        self.status_code = status_code
        self.retry_after = retry_after
        self.cost_usd = cost_usd
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.provider_request_id = provider_request_id


def is_transient_provider_error(exc: Exception) -> bool:
    if isinstance(exc, ProviderRequestError):
        return exc.transient
    return isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))


def _retry_after_seconds(value: str | None, now: datetime | None = None) -> float | None:
    """Parse Retry-After delta-seconds or an HTTP date, returning a nonnegative delay."""
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
    current = now or datetime.now(UTC)
    return max(0.0, (target - current).total_seconds())


def _run(
    command: list[str], prompt: str, timeout: int, cwd: str | None = None
) -> tuple[subprocess.CompletedProcess[str], float]:
    started = time.perf_counter()
    completed = subprocess.run(
        command,
        input=prompt,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
        cwd=cwd,
    )
    latency_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0:
        detail = completed.stderr.strip() or f"exit status {completed.returncode}"
        raise RuntimeError(f"{' '.join(command[:2])} failed: {detail}")
    return completed, latency_ms


class CodexCliAdapter(Adapter):
    provider = "codex-cli"

    def generate(self, prompt: str) -> Generation:
        # An empty temporary working directory plus read-only sandboxing keeps the
        # corpus in stdin and removes the repository from the agent's context.
        with tempfile.TemporaryDirectory(prefix="news-briefing-codex-eval-") as directory:
            command = [
                "codex", "exec", "--ephemeral", "--ignore-user-config",
                "--ignore-rules", "--skip-git-repo-check", "--sandbox", "read-only",
                "--color", "never", "--json", "--model", self.model, "-",
            ]
            completed, latency_ms = _run(command, prompt, self.timeout, directory)
        text = ""
        usage: dict[str, Any] = {}
        request_id = None
        for line in completed.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            request_id = request_id or event.get("thread_id")
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    text = item.get("text", text)
            if event.get("type") == "turn.completed":
                usage = event.get("usage", usage)
        if not text:
            raise RuntimeError("codex CLI returned no final agent message")
        return Generation(
            text=text,
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_note="Codex CLI does not report a billed per-run USD amount.",
            provider_request_id=request_id,
            usage=usage,
        )


class ClaudeCodeCliAdapter(Adapter):
    provider = "claude-code-cli"

    def generate(self, prompt: str) -> Generation:
        command = [
            "claude", "--print", "--output-format", "json", "--model", self.model,
            "--tools", "", "--disable-slash-commands", "--no-session-persistence",
        ]
        completed, latency_ms = _run(command, prompt, self.timeout)
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError("claude-code CLI returned invalid JSON") from exc
        if payload.get("is_error"):
            raise RuntimeError(f"claude-code CLI failed: {payload.get('result', 'unknown error')}")
        usage = payload.get("usage") or {}
        total_cost = payload.get("total_cost_usd")
        return Generation(
            text=payload.get("result", ""),
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=total_cost,
            cost_note=(
                None if total_cost is not None
                else "Claude Code did not report total_cost_usd for this call."
            ),
            provider_request_id=payload.get("session_id"),
            usage=usage,
        )


class OpenAiCompatibleAdapter(Adapter):
    endpoint: str
    api_key_env: str

    def __init__(
        self,
        model: str,
        timeout: int = 300,
        endpoint: str | None = None,
        *,
        temperature: float = 0,
        seed: int | None = None,
        reasoning_enabled: bool | None = None,
        reasoning_effort: str | None = None,
    ):
        super().__init__(model, timeout)
        if endpoint:
            self.endpoint = endpoint
        if reasoning_effort is not None and reasoning_enabled is False:
            raise ValueError("reasoning effort cannot be combined with disabled reasoning")
        self.temperature = temperature
        self.seed = seed
        self.reasoning_enabled = True if reasoning_effort is not None else reasoning_enabled
        self.reasoning_effort = reasoning_effort

    def _headers(self) -> dict[str, str]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} is required for {self.provider}")
        return {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    def _payload(self, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
            "max_tokens": int(os.environ.get("EVALUATOR_MAX_TOKENS", "100000")),
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}
        elif self.reasoning_enabled is not None:
            payload["reasoning"] = {"enabled": self.reasoning_enabled}
        return payload

    def generation_controls(self) -> dict[str, Any]:
        seed_disclosure = "no seed" if self.seed is None else f"seed={self.seed}"
        return {
            "temperature": self.temperature,
            "seed": self.seed,
            "reasoning_enabled": self.reasoning_enabled,
            "reasoning_effort": self.reasoning_effort,
            "disclosure": (
                f"The evaluator sends temperature={self.temperature} and {seed_disclosure}; "
                f"reasoning is {'provider-default' if self.reasoning_enabled is None else self.reasoning_enabled}"
                f"{'' if self.reasoning_effort is None else f' at {self.reasoning_effort} effort'}; "
                "exact reproducibility is not guaranteed, "
                "and these runs are not directly comparable to CLI runs without temperature control."
            ),
        }

    def generate(self, prompt: str) -> Generation:
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(self._payload(prompt)).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        started = time.perf_counter()
        deadline = started + self.timeout
        attempt = 0
        while True:
            attempt += 1
            remaining = deadline - time.perf_counter()
            if remaining <= 0:
                raise ProviderRequestError(
                    f"{self.provider} timed out after {self.timeout}s across {attempt - 1} attempt(s)",
                    transient=True,
                    attempts=attempt - 1,
                )
            failure: ProviderRequestError | None = None
            cause: Exception | None = None
            try:
                with urllib.request.urlopen(request, timeout=remaining) as response:
                    response_body = response.read()
                    request_id = response.headers.get("x-request-id")
                break
            except urllib.error.HTTPError as exc:
                retry_after = _retry_after_seconds(exc.headers.get("Retry-After"))
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                finally:
                    exc.close()
                transient = exc.code in RETRYABLE_HTTP_STATUSES or 500 <= exc.code <= 599
                failure = ProviderRequestError(
                    f"{self.provider} HTTP {exc.code}: {detail[:500]}",
                    transient=transient,
                    attempts=attempt,
                    status_code=exc.code,
                    retry_after=retry_after,
                )
                cause = exc
            except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
                failure = ProviderRequestError(
                    f"{self.provider} request failed: {exc}",
                    transient=True,
                    attempts=attempt,
                )
                cause = exc

            if not failure.transient or attempt >= API_MAX_ATTEMPTS:
                raise failure from cause
            delay = failure.retry_after if failure.retry_after is not None else float(2 ** (attempt - 1))
            remaining = max(0.0, deadline - time.perf_counter())
            if delay >= remaining:
                raise ProviderRequestError(
                    f"{failure}; retry delay {delay:g}s exceeds the remaining "
                    f"{remaining:g}s call timeout budget",
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
            text = payload["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"{self.provider} returned an unexpected response") from exc
        usage = payload.get("usage") or {}
        cost = usage.get("cost")
        cost_usd = float(cost) if cost is not None else self._estimated_cost(usage)
        if not isinstance(text, str):
            finish_reason = payload.get("choices", [{}])[0].get("finish_reason")
            raise ProviderRequestError(
                f"{self.provider} returned no text content"
                f" (finish_reason={finish_reason!r})",
                transient=False,
                attempts=attempt,
                cost_usd=cost_usd,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                provider_request_id=payload.get("id") or request_id,
            )
        return Generation(
            text=text,
            latency_ms=latency_ms,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            cost_usd=cost_usd,
            cost_note=None if cost is not None else self._cost_note(usage),
            provider_request_id=payload.get("id") or request_id,
            usage=usage,
            attempts=attempt,
        )

    def _estimated_cost(self, usage: dict[str, Any]) -> float | None:
        prefix = self.provider.upper().replace("-", "_")
        try:
            input_rate = float(os.environ[f"{prefix}_INPUT_USD_PER_MTOK"])
            output_rate = float(os.environ[f"{prefix}_OUTPUT_USD_PER_MTOK"])
            return (
                int(usage.get("prompt_tokens", 0)) * input_rate
                + int(usage.get("completion_tokens", 0)) * output_rate
            ) / 1_000_000
        except (KeyError, TypeError, ValueError):
            return None

    def _cost_note(self, usage: dict[str, Any]) -> str | None:
        if self._estimated_cost(usage) is not None:
            return "Estimated from configured per-million-token rates."
        return "Provider did not return cost; configure per-million-token rates to estimate it."


class OpenRouterAdapter(OpenAiCompatibleAdapter):
    provider = "openrouter"
    endpoint = "https://openrouter.ai/api/v1/chat/completions"
    api_key_env = "OPENROUTER_API_KEY"

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if os.environ.get("OPENROUTER_HTTP_REFERER"):
            headers["HTTP-Referer"] = os.environ["OPENROUTER_HTTP_REFERER"]
        headers["X-OpenRouter-Title"] = "news-briefing evaluator"
        return headers


class NvidiaAdapter(OpenAiCompatibleAdapter):
    provider = "nvidia"
    endpoint = "https://integrate.api.nvidia.com/v1/chat/completions"
    api_key_env = "NVIDIA_API_KEY"


class ProductionParityAdapter(Adapter):
    """Expose the real structured runner transport through the evaluator API."""

    def __init__(
        self,
        provider: ModelProvider,
        timeout: int,
        controls: dict[str, Any],
    ):
        super().__init__(provider.model, timeout)
        self.provider = provider.name
        self._runner_provider = provider
        self._controls = controls

    def generate(self, prompt: str) -> Generation:
        raise NotImplementedError(
            "production-parity adapters require evaluator generation_path='production-parity'"
        )

    def generate_structured(
        self, prompt: str, output_schema: dict[str, Any], trace_id: str
    ) -> Generation:
        try:
            response = self._runner_provider.generate(GenerationRequest(
                prompt=prompt,
                output_schema=output_schema,
                timeout_seconds=self.timeout,
                trace_id=trace_id,
            ))
        except ProviderError as exc:
            raise ProviderRequestError(
                str(exc),
                transient=exc.transient,
                attempts=exc.attempts,
                status_code=exc.status_code,
                retry_after=exc.retry_after,
                provider_request_id=exc.provider_request_id,
            ) from exc
        return Generation(
            text=response.raw_output,
            latency_ms=response.latency_ms,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            cost_usd=response.cost_usd,
            cost_note=(
                "Codex CLI does not report a billed per-run USD amount."
                if self.provider == "codex-cli" and response.cost_usd is None
                else None
            ),
            provider_request_id=response.provider_request_id,
            usage=response.usage,
            attempts=response.attempts,
            structured_output=response.structured_output,
        )

    def generation_controls(self) -> dict[str, Any]:
        return self._controls


def production_adapter_for(
    provider: str,
    model: str,
    timeout: int = 300,
    temperature: float | None = None,
    seed: int | None = None,
    reasoning_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> ProductionParityAdapter:
    """Build an evaluator adapter around the production runner's provider."""
    if seed is not None:
        raise ValueError(
            "--seed is unavailable for production-parity runs because the production transports "
            "do not send one"
        )
    if provider not in {"codex-cli", "claude-code-cli", "openrouter"}:
        raise ValueError(
            "production-parity runs support codex-cli, claude-code-cli, and openrouter"
        )
    resolved_temperature = 0 if temperature is None else temperature
    max_tokens = int(os.environ.get("EVALUATOR_MAX_TOKENS", "100000"))
    runner_provider = runner_provider_for(
        provider,
        model,
        temperature=resolved_temperature,
        reasoning_enabled=reasoning_enabled,
        reasoning_effort=reasoning_effort,
        max_tokens=max_tokens,
    )
    info = runner_provider.info()
    effective_reasoning_enabled = (
        True
        if provider == "codex-cli" or (provider == "openrouter" and reasoning_effort is not None)
        else reasoning_enabled if provider == "openrouter" else None
    )
    effective_reasoning_effort = (
        "medium"
        if provider == "codex-cli"
        else reasoning_effort if provider == "openrouter" else None
    )
    controls = {
        "temperature": resolved_temperature if provider == "openrouter" else None,
        "seed": None,
        "reasoning_enabled": effective_reasoning_enabled,
        "reasoning_effort": effective_reasoning_effort,
        "tool_policy": info.get("tool_policy"),
        "disclosure": (
            "Uses the production structured-output transport, empty application-tool policy, "
            "projected corpus, schema validator, and Markdown renderer."
        ),
    }
    return ProductionParityAdapter(runner_provider, timeout, controls)


def adapter_for(
    provider: str,
    model: str,
    timeout: int = 300,
    temperature: float | None = None,
    seed: int | None = None,
    reasoning_enabled: bool | None = None,
    reasoning_effort: str | None = None,
) -> Adapter:
    adapters: dict[str, type[Adapter]] = {
        "codex-cli": CodexCliAdapter,
        "claude-code-cli": ClaudeCodeCliAdapter,
        "openrouter": OpenRouterAdapter,
        "nvidia": NvidiaAdapter,
    }
    try:
        adapter_type = adapters[provider]
    except KeyError as exc:
        raise ValueError(f"unknown provider {provider!r}; choose {', '.join(adapters)}") from exc
    if issubclass(adapter_type, OpenAiCompatibleAdapter):
        return adapter_type(
            model,
            timeout,
            temperature=0 if temperature is None else temperature,
            seed=seed,
            reasoning_enabled=reasoning_enabled,
            reasoning_effort=reasoning_effort,
        )
    return adapter_type(model, timeout)


def load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE entries without adding python-dotenv to runtime."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)
