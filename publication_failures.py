"""Allowlisted public explanations; raw provider and model text stays private."""

from __future__ import annotations

from dataclasses import asdict, dataclass

MODEL_LABELS = {
    "tencent/hy3": "Tencent HY3",
    "deepseek/deepseek-v4-flash-0731": "DeepSeek V4 Flash",
    "google/gemini-3.7-flash": "Gemini 3.7 Flash",
}
FAILURE_MESSAGES = {
    "duplicate_story": "The generated briefing repeated a story and did not pass validation.",
    "validation_failed": "The generated briefing did not pass publication checks.",
    "empty_response": "The model provider returned no text.",
    "invalid_request": "The model provider rejected a request parameter (HTTP 400).",
    "rate_limited": "The model provider rate-limited the request.",
    "provider_unavailable": "The model provider was unavailable.",
    "generation_failed": "The model did not produce an accepted briefing.",
}


@dataclass(frozen=True)
class GenerationFailure:
    model: str
    reason: str

    def payload(self) -> dict[str, str]:
        return asdict(self)


def parse_generation_failures(raw: object) -> tuple[GenerationFailure, ...]:
    """Reject unexpected fields and free-form strings at the history boundary."""
    if not isinstance(raw, list) or len(raw) > len(MODEL_LABELS):
        raise ValueError("generation_failures must be a bounded array")
    failures: list[GenerationFailure] = []
    for row in raw:
        if (
            not isinstance(row, dict)
            or set(row) != {"model", "reason"}
            or not isinstance(row["model"], str)
            or row["model"] not in MODEL_LABELS
            or not isinstance(row["reason"], str)
            or row["reason"] not in FAILURE_MESSAGES
            or any(failure.model == row["model"] for failure in failures)
        ):
            raise ValueError("generation_failures contains an invalid model or reason")
        failures.append(GenerationFailure(row["model"], row["reason"]))
    return tuple(failures)


def summarize_failed_chain(raw: object) -> tuple[GenerationFailure, ...]:
    """Project a fully exhausted fallback log, including logs from older runs."""
    if not isinstance(raw, dict) or raw.get("status") != "failed":
        return ()
    chain, attempts = raw.get("model_chain"), raw.get("attempts")
    if (
        not isinstance(chain, list) or not chain or len(chain) > len(MODEL_LABELS)
        or any(not isinstance(model, str) or model not in MODEL_LABELS for model in chain)
        or len(set(chain)) != len(chain)
        or not isinstance(attempts, list) or len(attempts) != len(chain)
        or raw.get("selected_model") is not None
        or raw.get("selected_run_dir") is not None
    ):
        return ()
    failures = []
    for model, row in zip(chain, attempts, strict=True):
        if (
            not isinstance(row, dict) or row.get("model") != model
            or row.get("status") not in ("failed", "quarantined")
        ):
            return ()
        reason = row.get("failure_reason")
        code = "generation_failed"
        if isinstance(reason, str):
            if reason.startswith(("review_required:", "rejected:")):
                code = "duplicate_story" if "repeated_topic:" in reason else "validation_failed"
            elif reason.startswith("ProviderError: openrouter returned no text content"):
                code = "empty_response"
            elif reason.startswith("ProviderError: openrouter HTTP 400:"):
                code = "invalid_request"
            elif reason.startswith("ProviderError: openrouter HTTP 429:"):
                code = "rate_limited"
            elif reason.startswith(tuple(f"ProviderError: openrouter HTTP {n}:" for n in (500, 502, 503, 504))):
                code = "provider_unavailable"
        failures.append(GenerationFailure(model, code))
    return tuple(failures)
