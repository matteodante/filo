"""Cost ledger with a hard spending cap for every evaluation model call."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import RequestUsage

DEFAULT_LEDGER = Path.home() / ".thera-evals" / "ledger.json"
DEFAULT_BUDGET_USD = 45.0
DEFAULT_RESERVE_USD = 3.0
MILLION = 1_000_000


@dataclass(frozen=True)
class Price:
    """US dollars per million tokens: input, 5-minute cache write, cache read, output."""

    input: float
    cache_write: float
    cache_read: float
    output: float


# Anthropic list prices verified 2026-10-16.
PRICES: dict[str, Price] = {
    "claude-haiku-5-5": Price(0.10, 0.125, 0.01, 0.50),
    "claude-sonnet-5-5": Price(2.0, 2.50, 0.10, 10.0),
    "claude-opus-5-5": Price(4.0, 5.0, 0.20, 20.0),
    "claude-opus-4-8": Price(5.0, 6.25, 0.50, 25.0),
    "claude-sonnet-4-6": Price(3.0, 3.75, 0.30, 15.0),
    "claude-haiku-4-5": Price(1.0, 1.25, 0.10, 5.0),
}
LONG_PROMPT_PRICES: dict[str, tuple[int, Price]] = {
    "claude-haiku-5-5": (100_000, Price(0.50, 0.625, 0.05, 2.50)),
}


class BudgetExceeded(RuntimeError):
    """Raised before a call or run that would spend beyond the configured cap."""


def price_for(model: str, prompt_tokens: int) -> Price:
    if model not in PRICES:
        raise BudgetExceeded(f"No verified price for {model}; refusing to call an unpriced model.")
    threshold, long_price = LONG_PROMPT_PRICES.get(model, (None, None))
    if threshold is not None and long_price is not None and prompt_tokens > threshold:
        return long_price
    return PRICES[model]


def usage_cost(model: str, usage: RequestUsage) -> float:
    """Price one request. PydanticAI's input_tokens already include both cache counts."""
    cache_read = usage.cache_read_tokens or 0
    cache_write = usage.cache_write_tokens or 0
    prompt = usage.input_tokens or 0
    uncached = max(prompt - cache_read - cache_write, 0)
    price = price_for(model, prompt)
    return (
        uncached * price.input
        + cache_write * price.cache_write
        + cache_read * price.cache_read
        + (usage.output_tokens or 0) * price.output
    ) / MILLION


def estimate_cost(model: str, calls: int, input_tokens: int, output_tokens: int) -> float:
    """Upper-leaning estimate that assumes no cache hits."""
    price = price_for(model, input_tokens)
    return calls * (input_tokens * price.input + output_tokens * price.output) / MILLION


class Ledger:
    """Append-only JSON spending record kept outside the repository."""

    def __init__(
        self,
        path: Path | None = None,
        budget_usd: float | None = None,
        reserve_usd: float | None = None,
    ) -> None:
        self.path = path or Path(os.getenv("FILO_EVAL_LEDGER", str(DEFAULT_LEDGER)))
        self.budget_usd = (
            budget_usd
            if budget_usd is not None
            else float(os.getenv("FILO_EVAL_BUDGET_USD", str(DEFAULT_BUDGET_USD)))
        )
        self.reserve_usd = (
            reserve_usd
            if reserve_usd is not None
            else float(os.getenv("FILO_EVAL_RESERVE_USD", str(DEFAULT_RESERVE_USD)))
        )
        self.run_id: str | None = None
        self._data = self._read()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"entries": [], "runs": []}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._data, indent=1) + "\n", encoding="utf-8")
        temporary.replace(self.path)

    @property
    def spent_usd(self) -> float:
        return sum(entry["usd"] for entry in self._data["entries"])

    @property
    def remaining_usd(self) -> float:
        return self.budget_usd - self.spent_usd

    def begin_run(self, command: str, estimate_usd: float) -> str:
        """Refuse a run whose estimate does not fit the remaining budget minus the reserve."""
        available = self.remaining_usd - self.reserve_usd
        if estimate_usd > available:
            raise BudgetExceeded(
                f"{command}: estimated ${estimate_usd:.2f} exceeds the available "
                f"${max(available, 0):.2f} (cap ${self.budget_usd:.2f}, spent "
                f"${self.spent_usd:.2f}, reserve ${self.reserve_usd:.2f})."
            )
        self.run_id = f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
        self._data["runs"].append(
            {
                "run_id": self.run_id,
                "command": command,
                "estimate_usd": round(estimate_usd, 4),
                "started_at": datetime.now(UTC).isoformat(),
            }
        )
        self._write()
        return self.run_id

    def check(self, worst_case_usd: float = 0.0) -> None:
        """Abort before a call once the ledger has reached, or would cross, the hard cap."""
        if self.spent_usd + worst_case_usd >= self.budget_usd:
            raise BudgetExceeded(
                f"Spending cap reached: spent ${self.spent_usd:.4f} of ${self.budget_usd:.2f}."
            )

    def record(self, model: str, role: str, usage: RequestUsage) -> float:
        usd = usage_cost(model, usage)
        self._data["entries"].append(
            {
                "at": datetime.now(UTC).isoformat(),
                "run_id": self.run_id,
                "role": role,
                "model": model,
                "input_tokens": usage.input_tokens or 0,
                "cache_read_tokens": usage.cache_read_tokens or 0,
                "cache_write_tokens": usage.cache_write_tokens or 0,
                "output_tokens": usage.output_tokens or 0,
                "usd": usd,
            }
        )
        self._write()
        return usd

    def summary(self, run_id: str | None = None) -> dict[str, dict[str, float]]:
        """Token and dollar totals per role:model, optionally for one run."""
        totals: dict[str, dict[str, float]] = {}
        for entry in self._data["entries"]:
            if run_id is not None and entry["run_id"] != run_id:
                continue
            bucket = totals.setdefault(
                f"{entry['role']}:{entry['model']}",
                {
                    "calls": 0,
                    "input_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_write_tokens": 0,
                    "output_tokens": 0,
                    "usd": 0.0,
                },
            )
            bucket["calls"] += 1
            for key in ("input_tokens", "cache_read_tokens", "cache_write_tokens", "output_tokens"):
                bucket[key] += entry[key]
            bucket["usd"] += entry["usd"]
        return totals


class MeteredModel(WrapperModel):
    """Checks the cap before every request and records the usage of every response."""

    def __init__(
        self,
        wrapped: Model,
        *,
        ledger: Ledger,
        price_key: str,
        role: str,
        max_output_tokens: int,
    ) -> None:
        super().__init__(wrapped)
        price_for(price_key, 0)
        self.ledger = ledger
        self.price_key = price_key
        self.role = role
        self.max_output_tokens = max_output_tokens

    def _guard(self) -> None:
        self.ledger.check(estimate_cost(self.price_key, 1, 0, self.max_output_tokens))

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        self._guard()
        response = await super().request(messages, model_settings, model_request_parameters)
        self.ledger.record(self.price_key, self.role, response.usage)
        return response

    @asynccontextmanager
    async def request_stream(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
        run_context: Any = None,
    ) -> AsyncIterator[StreamedResponse]:
        self._guard()
        async with super().request_stream(
            messages, model_settings, model_request_parameters, run_context
        ) as stream:
            try:
                yield stream
            finally:
                self.ledger.record(self.price_key, self.role, stream.usage)
