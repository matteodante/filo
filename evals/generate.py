"""Generate the replayed user turns once, so every memory arm sees identical input.

Usage: uv run python -m evals.generate [--personas MP01,MP02] [--force]
The output is committed to tests/cases/memory_user_turns.yaml.
"""

from __future__ import annotations

import argparse

from evals.costs import Ledger, estimate_cost
from evals.datasets import (
    USER_TURNS_PATH,
    UserTurnDataset,
    load_personas,
    load_user_turns,
    write_user_turns,
)
from evals.models import Roles, anthropic_model
from evals.simulator import generate_persona_turns

INPUT_TOKENS = 2_500
OUTPUT_TOKENS = 120


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.generate", description=__doc__)
    parser.add_argument("--personas", default="")
    parser.add_argument("--force", action="store_true", help="Regenerate existing personas.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    roles = Roles.from_env()
    dataset = load_personas()
    existing = (
        load_user_turns()
        if USER_TURNS_PATH.exists()
        else UserTurnDataset(
            name="memory_user_turns", synthetic_only=True, dataset_version=dataset.version
        )
    )
    if existing.dataset_version != dataset.version and not args.force:
        parser.error("user turns belong to another persona dataset version; use --force")
    wanted = set(filter(None, args.personas.split(",")))
    personas = [
        persona
        for persona in dataset.personas
        if (not wanted or persona.id in wanted)
        and (args.force or existing.for_persona(persona.id) is None)
    ]
    turns = sum(session.turns for persona in personas for session in persona.sessions)
    cost = estimate_cost(roles.simulator, turns, INPUT_TOKENS, OUTPUT_TOKENS)
    print(f"{len(personas)} personas, {turns} user turns, estimated ${cost:.2f}")
    if args.dry_run or not personas:
        return 0
    ledger = Ledger()
    ledger.begin_run("generate", cost)
    model = anthropic_model(roles.simulator, "simulator", ledger)
    generated = {item.persona_id: item for item in existing.personas}
    for persona in personas:
        generated[persona.id] = generate_persona_turns(model, persona, roles.simulator)
        ordered = [generated[p.id] for p in dataset.personas if p.id in generated]
        write_user_turns(
            UserTurnDataset(
                name="memory_user_turns",
                synthetic_only=True,
                dataset_version=dataset.version,
                personas=ordered,
            )
        )
        print(f"{persona.id}: done, spent ${ledger.spent_usd:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
