"""Provider-neutral model request and response contracts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class GenerationRequest:
    prompt: str
    output_schema: dict[str, Any]
    timeout_seconds: int
    trace_id: str


@dataclass(frozen=True)
class ModelResponse:
    raw_output: str
    structured_output: dict[str, Any]
    latency_ms: float
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    provider_request_id: str | None = None
    usage: dict[str, Any] | None = None
    attempts: int = 1
    provider_events: tuple[dict[str, Any], ...] = ()

    def record(self) -> dict[str, Any]:
        record = asdict(self)
        record.pop("raw_output")
        record.pop("structured_output")
        record.pop("provider_events")
        return record


class ProviderError(RuntimeError):
    """A typed provider failure suitable for tracing and retry policy."""

    def __init__(
        self,
        message: str,
        *,
        transient: bool,
        attempts: int = 1,
        status_code: int | None = None,
        retry_after: float | None = None,
        provider_request_id: str | None = None,
        ambiguous_completion: bool = False,
        model_removed_from_openrouter: bool = False,
    ):
        super().__init__(message)
        self.transient = transient
        self.attempts = attempts
        self.status_code = status_code
        self.retry_after = retry_after
        self.provider_request_id = provider_request_id
        self.ambiguous_completion = ambiguous_completion
        self.model_removed_from_openrouter = model_removed_from_openrouter

    def record(self) -> dict[str, Any]:
        return {
            "type": type(self).__name__,
            "message": str(self),
            "transient": self.transient,
            "attempts": self.attempts,
            "status_code": self.status_code,
            "retry_after": self.retry_after,
            "provider_request_id": self.provider_request_id,
            "ambiguous_completion": self.ambiguous_completion,
            "model_removed_from_openrouter": self.model_removed_from_openrouter,
        }


class ModelProvider(Protocol):
    name: str
    model: str

    def generate(self, request: GenerationRequest) -> ModelResponse:
        """Generate one schema-constrained response without application tools."""

    def info(self) -> dict[str, Any]:
        """Return non-secret provider and runtime provenance."""
