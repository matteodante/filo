"""One tiny metered call per configured model, to verify access and pricing.

Usage: uv run python -m evals.smoke
"""

from __future__ import annotations

import json

from pydantic_ai import Agent

from evals.costs import PRICES, Ledger, estimate_cost
from evals.models import anthropic_model

SMOKE_MODELS = [
    "claude-haiku-5-5",
    "claude-sonnet-5-5",
    "claude-opus-4-8",
    "claude-sonnet-4-6",
    "claude-opus-5-5",
    "claude-haiku-4-5",
]


def main() -> int:
    ledger = Ledger()
    cost = sum(estimate_cost(name, 1, 50, 5) for name in SMOKE_MODELS)
    run_id = ledger.begin_run("smoke", cost)
    failures = 0
    for name in SMOKE_MODELS:
        assert name in PRICES
        model = anthropic_model(name, "smoke", ledger, max_tokens=5)
        try:
            Agent(model, output_type=str).run_sync("Reply with the single word: ok")
            print(f"{name}: ok")
        except Exception as error:  # report every model, then fail
            failures += 1
            print(f"{name}: {type(error).__name__}")
    print(json.dumps(ledger.summary(run_id), indent=1))
    print(f"total spent ${ledger.spent_usd:.4f} of ${ledger.budget_usd:.2f}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
