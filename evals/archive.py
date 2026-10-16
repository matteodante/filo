"""Copy evaluation results into the repository so they are versioned with the code.

Everything is synthetic. The encrypted stores (SQLite files and their test keys) stay outside:
they are binary, unreadable in a diff, and not needed to reproduce any reported number.

Usage: uv run python -m evals.archive [--into docs/evidence/runs-2026-10]
"""

from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path

from evals.costs import DEFAULT_LEDGER
from evals.runs import DEFAULT_RUNS


def archive(runs: Path, ledger: Path, into: Path) -> list[str]:
    copied = []
    for run in sorted(path for path in runs.iterdir() if path.is_dir()):
        shutil.copytree(
            run, into / run.name, ignore=shutil.ignore_patterns("stores"), dirs_exist_ok=True
        )
        copied.append(run.name)
    if ledger.exists():
        into.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ledger, into / "ledger.json")
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--into", type=Path, default=Path("docs/evidence/runs-2026-10"))
    args = parser.parse_args()
    runs = Path(os.getenv("FILO_EVAL_RUNS", str(DEFAULT_RUNS)))
    ledger = Path(os.getenv("FILO_EVAL_LEDGER", str(DEFAULT_LEDGER)))
    copied = archive(runs, ledger, args.into)
    print(f"archived {len(copied)} runs into {args.into}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
