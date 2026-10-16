"""Run output locations. Results stay outside the repository; reports summarize them."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

DEFAULT_RUNS = Path.home() / ".thera-evals" / "runs"


def failed_reply(error: Exception) -> str:
    """Placeholder for a turn the product failed to answer; the spending cap still propagates."""
    from evals.costs import BudgetExceeded

    if isinstance(error, BudgetExceeded):
        raise error
    return f"[no reply: {type(error).__name__}]"


def run_directory(run_id: str) -> Path:
    path = Path(os.getenv("FILO_EVAL_RUNS", str(DEFAULT_RUNS))) / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_jsonl(path: Path, records: Iterable[dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
