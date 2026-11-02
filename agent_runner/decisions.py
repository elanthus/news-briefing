"""Bounded, dependency-free OpenRouter Decisions API client for Jev and Decider."""

from __future__ import annotations

import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Protocol

from agent_runner.http_transport import ResponseLimitError, bounded_request
from agent_runner.models import ProviderError
from agent_runner.providers import open_validated_url, validated_metadata

# Pin dated releases, not the ~latest aliases, so a judge change is a reviewed commit.
JEV_MODEL = "typesafe/jev-1.14"
# A different vendor's model answers isolated confirmations, so one judge's error is not repeated.
DECIDER_MODEL = "perplexity/pplx-decider-v1.2"
MODEL_LABELS = {JEV_MODEL: "Jev", DECIDER_MODEL: "Decider"}
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MAX_REQUEST_BYTES = 24_000
MAX_RESPONSE_BYTES = 128_000


def probability(value: Any, label: str = "Jev") -> float:
    if type(value) not in (int, float) or not 0 <= value <= 1:
        raise ProviderError(f"{label} returned an invalid probability", transient=False)
    return float(value)


class DecisionsJudge(Protocol):
    """What the review stage needs from a judge; test fakes satisfy it without network access."""

    model: str

    def evaluate(self, state: dict[str, Any], questions: dict[str, Any],
                 *, timeout: int = 30) -> dict[str, Any]: ...


class DecisionsClient:
    """Use only Noul questions; never retry an ambiguously billed request."""

    model = JEV_MODEL
    label = MODEL_LABELS[JEV_MODEL]

    def __init__(self, model: str = JEV_MODEL) -> None:
        if model not in MODEL_LABELS:
            raise ValueError("unsupported Decisions model")
        self.model = model
        self.label = MODEL_LABELS[model]

    def evaluate(self, state: dict[str, Any], questions: dict[str, Any],
                 *, timeout: int = 30) -> dict[str, Any]:
        label = self.label
        if timeout <= 0:
            raise ValueError(f"{label} timeout must be positive")
        if not questions or len(questions) > 50:
            raise ValueError(f"{label} requires between one and fifty questions per batch")
        body = json.dumps({"model": self.model, "state": state, "questions": questions},
                          ensure_ascii=True).encode("ascii")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError(f"{label} request exceeds the bounded input budget")
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ProviderError(f"OPENROUTER_API_KEY is required for {label}", transient=False)
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-OpenRouter-Title": "news-briefing",
        })
        started = time.perf_counter()
        try:
            raw, _ = bounded_request(request, deadline=started + timeout, opener=open_validated_url,
                                     max_bytes=MAX_RESPONSE_BYTES)
        except ResponseLimitError as exc:
            raise ProviderError(f"{label} {exc}", transient=False, status_code=exc.status,
                                provider_request_id=exc.request_id) from exc
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            # Never echo the remote body: it can contain credentials or evidence.
            raise ProviderError(f"{label} HTTP {status}", transient=False, status_code=status) from exc
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            raise ProviderError(f"{label} transport failed; completion and billing may be ambiguous",
                                transient=False, ambiguous_completion=True) from exc
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProviderError(f"{label} returned invalid JSON", transient=False) from exc
        if not isinstance(payload, dict) or set(payload) - {"model", "answers", "usage", "id", "provider"}:
            raise ProviderError(f"{label} returned an unexpected response envelope", transient=False)
        answers = payload.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ProviderError(f"{label} answer IDs differ from the requested questions", transient=False)
        probabilities = {}
        for name, answer in answers.items():
            if not isinstance(answer, dict) or set(answer) != {"type", "noul"} or answer["type"] != "noul":
                raise ProviderError(f"{label} returned an unexpected answer type", transient=False)
            probabilities[name] = probability(answer["noul"], label)
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError(f"{label} returned missing usage", transient=False)
        usage, request_id, cost = validated_metadata(
            label, usage, payload.get("id"), input_field="input_tokens", output_field="output_tokens",
            cost=usage.get("cost"), latency_ms=(time.perf_counter() - started) * 1000,
        )
        tokens = {field: usage.get(field) for field in ("input_tokens", "output_tokens")}
        model = payload.get("model")
        # OpenRouter echoes the requested slug plus the dated release it served.
        if (not isinstance(model, str) or len(model) > 100
                or re.fullmatch(re.escape(self.model) + r"(?:-[0-9]{8})?", model) is None):
            raise ProviderError(f"{label} returned an unexpected model", transient=False)
        return {"probabilities": probabilities, "model": model, "cost_usd": cost,
                "provider_request_id": request_id,
                **tokens, "latency_ms": (time.perf_counter() - started) * 1000}
