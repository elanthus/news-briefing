"""One conservative allowance shared by generation calls, fallbacks and days."""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_runner.checkpoint import write_json_atomic
from agent_runner.models import ProviderError

DEFAULT_GENERATION_SECONDS = 1800
DEFAULT_GENERATION_USD = 25.0
# Deliberate allowances, not fetched provider prices or a promised billing cap.
INPUT_USD_PER_MILLION = 20.0
OUTPUT_USD_PER_MILLION = 60.0
DEFAULT_OUTPUT_ALLOWANCE = 100_000


class GenerationBudgetExceeded(ProviderError):
    def __init__(self, reason: str):
        super().__init__(f"generation budget stopped: {reason}", transient=False)


def _amount(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


@dataclass
class GenerationBudget:
    limit_usd: float = DEFAULT_GENERATION_USD
    deadline_epoch: float = field(default_factory=lambda: time.time() + DEFAULT_GENERATION_SECONDS)
    known_spend_usd: float = 0.0
    unknown_reserved_usd: float = 0.0
    unknown_cost_calls: int = 0
    calls: int = 0
    in_flight_usd: float = 0.0
    stop_reason: str | None = None
    path: Path | None = None

    def __post_init__(self) -> None:
        if not all(_amount(value) for value in (
            self.limit_usd, self.deadline_epoch, self.known_spend_usd,
            self.unknown_reserved_usd, self.in_flight_usd,
        )) or self.limit_usd <= 0:
            raise ValueError("invalid generation budget amounts")
        if any(type(value) is not int or value < 0 for value in (self.calls, self.unknown_cost_calls)):
            raise ValueError("invalid generation budget call counts")
        if self.stop_reason is not None and not isinstance(self.stop_reason, str):
            raise ValueError("invalid generation budget stop reason")

    @classmethod
    def open(cls, path: Path, *, limit_usd: float = DEFAULT_GENERATION_USD,
             seconds: int = DEFAULT_GENERATION_SECONDS) -> GenerationBudget:
        """Reuse a serialized allowance; never reset it for the next model/day."""
        if path.is_symlink():
            raise ValueError("generation budget path cannot be a symlink")
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            fields = {"limit_usd", "deadline_epoch", "known_spend_usd", "unknown_reserved_usd",
                      "unknown_cost_calls", "calls", "in_flight_usd", "stop_reason"}
            if not isinstance(raw, dict) or set(raw) != fields | {"schema_version"} or raw["schema_version"] != 1:
                raise ValueError("invalid generation budget state")
            budget = cls(**{key: raw[key] for key in fields}, path=path)
            if budget.in_flight_usd:
                budget.stop_reason = "ambiguous_completion"
                budget._persist()
            return budget
        if type(seconds) is not int or seconds <= 0:
            raise ValueError("generation seconds must be positive")
        budget = cls(limit_usd=limit_usd, deadline_epoch=time.time() + seconds, path=path)
        budget._persist()
        return budget

    def record(self) -> dict[str, Any]:
        return {
            "schema_version": 1, "limit_usd": self.limit_usd,
            "deadline_epoch": self.deadline_epoch,
            "known_spend_usd": self.known_spend_usd,
            "unknown_reserved_usd": self.unknown_reserved_usd,
            "unknown_cost_calls": self.unknown_cost_calls,
            "calls": self.calls, "in_flight_usd": self.in_flight_usd,
            "stop_reason": self.stop_reason,
        }

    def _persist(self) -> None:
        if self.path is not None:
            try:
                write_json_atomic(self.path, self.record())
            except Exception:
                self.stop_reason = "budget_state_write_failed"
                raise

    def available(self) -> bool:
        if self.stop_reason is None:
            if self.in_flight_usd:
                self.stop_reason = "ambiguous_completion"
            elif self.deadline_epoch - time.time() < 1:
                self.stop_reason = "elapsed_time"
            elif self.known_spend_usd + self.unknown_reserved_usd >= self.limit_usd:
                self.stop_reason = "spending_allowance"
            if self.stop_reason is not None:
                self._persist()
        return self.stop_reason is None

    def reserve(self, prompt: str, schema: dict[str, Any], max_tokens: Any,
                timeout_seconds: int) -> int:
        """Charge the full input/output allowance before starting provider IO."""
        if not self.available():
            raise GenerationBudgetExceeded(self.stop_reason or "unavailable")
        if type(max_tokens) is not int or max_tokens <= 0:
            max_tokens = DEFAULT_OUTPUT_ALLOWANCE
        input_bytes = len(prompt.encode("utf-8")) + len(json.dumps(schema, ensure_ascii=False).encode("utf-8"))
        reservation = (input_bytes * INPUT_USD_PER_MILLION + max_tokens * OUTPUT_USD_PER_MILLION) / 1_000_000
        if self.known_spend_usd + self.unknown_reserved_usd + reservation > self.limit_usd:
            self.stop_reason = "spending_allowance"
            self._persist()
            raise GenerationBudgetExceeded(self.stop_reason)
        self.in_flight_usd = reservation
        self.calls += 1
        self._persist()
        return min(timeout_seconds, max(1, int(self.deadline_epoch - time.time())))

    def settle(self, cost_usd: Any, *, ambiguous: bool = False) -> None:
        reservation = self.in_flight_usd
        self.in_flight_usd = 0.0
        if _amount(cost_usd):
            self.known_spend_usd += float(cost_usd)
        else:
            self.unknown_cost_calls += 1
            self.unknown_reserved_usd += reservation
        if ambiguous:
            self.stop_reason = "ambiguous_completion"
        self._persist()
        self.available()
