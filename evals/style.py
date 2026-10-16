"""Deterministic style metrics over assistant replies; no model is involved."""

from __future__ import annotations

import re
import statistics
from collections import Counter
from collections.abc import Sequence

APOLOGY = re.compile(
    r"\b(?:sorry|i apologi[sz]e|my apologies|scusa(?:mi)?|chiedo scusa|mi scuso)\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"[\w']+", re.UNICODE)
_TRAILING_NOISE = " \t\n*_)]\"'»”"


def opener(reply: str, words: int = 3) -> str:
    return " ".join(_WORD.findall(reply.casefold())[:words])


def question_count(reply: str) -> int:
    return len(re.findall(r"\?+", reply))


def conversation_metrics(replies: Sequence[str]) -> dict[str, float]:
    """Metrics for one conversation's assistant replies."""
    if not replies:
        raise ValueError("No replies to measure.")
    openers = Counter(opener(reply) for reply in replies)
    lengths = [len(reply) for reply in replies]
    mean_length = statistics.fmean(lengths)
    return {
        "replies": len(replies),
        "questions_per_turn": statistics.fmean(question_count(r) for r in replies),
        "opener_repetition": sum(openers[opener(r)] > 1 for r in replies) / len(replies),
        "trailing_question_rate": sum(r.rstrip(_TRAILING_NOISE).endswith("?") for r in replies)
        / len(replies),
        "apology_rate": sum(bool(APOLOGY.search(r)) for r in replies) / len(replies),
        "mean_length": mean_length,
        "length_cv": statistics.pstdev(lengths) / mean_length if mean_length else 0.0,
    }


def aggregate(conversations: Sequence[Sequence[str]]) -> dict[str, float]:
    """Reply-weighted means across conversations, plus the conversation count."""
    measured = [conversation_metrics(replies) for replies in conversations if replies]
    if not measured:
        return {}
    total = sum(item["replies"] for item in measured)
    keys = [key for key in measured[0] if key != "replies"]
    result = {key: sum(item[key] * item["replies"] for item in measured) / total for key in keys}
    result["conversations"] = len(measured)
    result["replies"] = total
    return result
