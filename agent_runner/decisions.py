"""Bounded, dependency-free OpenRouter Decisions API client for Jev."""

from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any

from agent_runner.models import ProviderError
from agent_runner.providers import _urlopen

JEV_MODEL = "typesafe/jev-1.13"
ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
MAX_REQUEST_BYTES = 24_000
MAX_RESPONSE_BYTES = 128_000


def probability(value: Any) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ProviderError("Jev returned an invalid probability", transient=False)
    return float(value)


class JevClient:
    """Use only Noul questions; never retry an ambiguously billed request."""

    def evaluate(self, state: dict[str, Any], questions: dict[str, Any],
                 *, timeout: int = 30) -> dict[str, Any]:
        if timeout <= 0:
            raise ValueError("Jev timeout must be positive")
        if not questions or len(questions) > 50:
            raise ValueError("Jev requires between one and fifty questions per batch")
        body = json.dumps({"model": JEV_MODEL, "state": state, "questions": questions},
                          ensure_ascii=True).encode("ascii")
        if len(body) > MAX_REQUEST_BYTES:
            raise ValueError("Jev request exceeds the bounded input budget")
        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            raise ProviderError("OPENROUTER_API_KEY is required for Jev", transient=False)
        request = urllib.request.Request(ENDPOINT, data=body, method="POST", headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-OpenRouter-Title": "news-briefing",
        })
        started = time.perf_counter()
        try:
            with _urlopen(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            # Never echo the remote body: it can contain credentials or evidence.
            raise ProviderError(f"Jev HTTP {status}", transient=False, status_code=status) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ProviderError("Jev transport failed; completion and billing may be ambiguous",
                                transient=False, ambiguous_completion=True) from exc
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ProviderError("Jev response exceeds the bounded output budget", transient=False)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ProviderError("Jev returned invalid JSON", transient=False) from exc
        if not isinstance(payload, dict) or set(payload) - {"model", "answers", "usage", "id", "provider"}:
            raise ProviderError("Jev returned an unexpected response envelope", transient=False)
        answers = payload.get("answers")
        if not isinstance(answers, dict) or set(answers) != set(questions):
            raise ProviderError("Jev answer IDs differ from the requested questions", transient=False)
        probabilities = {}
        for name, answer in answers.items():
            if not isinstance(answer, dict) or set(answer) != {"type", "noul"} or answer["type"] != "noul":
                raise ProviderError("Jev returned an unexpected answer type", transient=False)
            probabilities[name] = probability(answer["noul"])
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            raise ProviderError("Jev returned missing usage", transient=False)
        cost = usage.get("cost")
        if cost is not None and (type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0):
            raise ProviderError("Jev returned invalid cost", transient=False)
        tokens = {}
        for field in ("input_tokens", "output_tokens"):
            value = usage.get(field)
            if value is not None and (type(value) is not int or value < 0):
                raise ProviderError("Jev returned invalid token usage", transient=False)
            tokens[field] = value
        model = payload.get("model")
        if (not isinstance(model, str) or len(model) > 100
                or re.fullmatch(r"typesafe/jev-[A-Za-z0-9.-]+", model) is None):
            raise ProviderError("Jev returned an unexpected model", transient=False)
        return {"probabilities": probabilities, "model": model, "cost_usd": cost,
                **tokens, "latency_ms": (time.perf_counter() - started) * 1000}
