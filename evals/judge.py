"""Blind binary per-criterion judges and order-balanced pairwise comparison."""

from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model

Verdict = Literal["pass", "fail", "unknown"]

# Arm names, memory modes, and record IDs could let the judge infer the condition.
_IDENTIFIERS = re.compile(
    r"\b(?:transcript[_ -]only|ephemeral|standard memory|memory[_ -]mode\S*|arm[_ -]\w+)\b"
    r"|\b[0-9a-f]{12}\b",
    re.IGNORECASE,
)

BINARY_INSTRUCTIONS = (
    "You judge one criterion about an AI assistant's observable behavior in a synthetic "
    "conversation. Use only the material provided. Answer pass when the criterion is clearly met, "
    "fail when it is clearly not met, and unknown only when the material cannot decide it. Give a "
    "one-line reason that cites the observable behavior. Do not reward length or politeness."
)
PAIRWISE_INSTRUCTIONS = (
    "Compare two assistant answers against one criterion. Choose A or B only when one is clearly "
    "better on that criterion; otherwise answer tie. Ignore length and order of presentation."
)


class Judgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: Verdict
    reason: str = Field(max_length=300)


class PairwiseJudgment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred: Literal["A", "B", "tie"]
    reason: str = Field(max_length=300)


def blind(text: str) -> str:
    """Strip condition names and record IDs before a judge sees the material."""
    return _IDENTIFIERS.sub("[redacted]", text)


def judge_criterion(
    model: Model, criterion: str, material: dict[str, object], context: str | None = None
) -> Judgment:
    """One judge call for one binary criterion.

    A shared context goes into the cached instructions, so repeated calls about one conversation
    history pay for it once.
    """
    instructions = BINARY_INSTRUCTIONS
    if context is not None:
        instructions += "\n\nShared context for every item you judge:\n" + blind(context)
    agent = Agent(model, output_type=Judgment, instructions=instructions, retries=2)
    prompt = blind(
        json.dumps({"criterion": criterion, "material": material}, ensure_ascii=False, indent=1)
    )
    judgment = agent.run_sync(prompt).output
    return Judgment(verdict=judgment.verdict, reason=judgment.reason.splitlines()[0][:300])


def pairwise(
    model: Model, criterion: str, context: dict[str, object], first: str, second: str
) -> Literal["first", "second", "tie"]:
    """Run both presentation orders; an inconsistent pair counts as a tie."""
    agent = Agent(model, output_type=PairwiseJudgment, instructions=PAIRWISE_INSTRUCTIONS)

    def ask(a: str, b: str) -> str:
        payload = {"criterion": criterion, "context": context, "A": a, "B": b}
        return agent.run_sync(blind(json.dumps(payload, ensure_ascii=False))).output.preferred

    forward, backward = ask(first, second), ask(second, first)
    if forward == "A" and backward == "B":
        return "first"
    if forward == "B" and backward == "A":
        return "second"
    return "tie"
