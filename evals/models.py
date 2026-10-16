"""Anthropic model construction for evaluation roles; the key never leaves the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic_ai.models import Model

from evals.costs import Ledger, MeteredModel

ANTHROPIC_API_URL = "https://api.anthropic.com"


@dataclass(frozen=True)
class Roles:
    """Which model plays each role. The simulator must differ from the system under test."""

    sut: str = "claude-haiku-5-5"
    judge: str = "claude-opus-4-8"
    judge_secondary: str = "claude-sonnet-4-6"
    simulator: str = "claude-sonnet-4-6"

    @classmethod
    def from_env(cls, **overrides: str | None) -> Roles:
        defaults = cls()
        values = {
            name: overrides.get(name)
            or os.getenv(f"THERA_EVAL_{name.upper()}_MODEL")
            or getattr(defaults, name)
            for name in ("sut", "judge", "judge_secondary", "simulator")
        }
        roles = cls(**values)
        if roles.simulator == roles.sut:
            raise ValueError("The user simulator must use a different model than the system.")
        return roles


# Output caps per role. The system under test also stays inside ChatSession's run limits.
MAX_TOKENS = {"sut": 2_000, "judge": 400, "judge_secondary": 400, "simulator": 600}


def anthropic_model(name: str, role: str, ledger: Ledger, max_tokens: int | None = None) -> Model:
    """Build a metered Anthropic model against the public API, ignoring ANTHROPIC_BASE_URL."""
    from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
    from pydantic_ai.providers.anthropic import AnthropicProvider

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")
    limit = max_tokens or MAX_TOKENS.get(role, 1_000)
    settings = AnthropicModelSettings(
        max_tokens=limit,
        anthropic_cache_instructions=True,
        anthropic_cache_tool_definitions=True,
    )
    provider = AnthropicProvider(api_key=api_key, base_url=ANTHROPIC_API_URL)
    return MeteredModel(
        AnthropicModel(name, provider=provider, settings=settings),
        ledger=ledger,
        price_key=name,
        role=role,
        max_output_tokens=limit,
    )
