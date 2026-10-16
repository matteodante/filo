"""Reduced W1.3 safety suite: adaptive multi-turn scenarios judged per binary criterion.

Usage: uv run python -m evals.safety [--repeat 3] [--scenarios SV2-01,SV2-14]
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic_ai.embeddings import Embedder
from pydantic_ai.models import Model

from evals.costs import Ledger, estimate_cost
from evals.datasets import SafetyScenario, load_safety
from evals.judge import judge_criterion
from evals.models import Roles, anthropic_model
from evals.runs import failed_reply, read_jsonl, run_directory, write_jsonl
from evals.simulator import adaptive_user_turn
from evals.stats import pass_hat_k, wilson
from evals.style import aggregate
from filo.chat import ChatSession
from filo.memory import MemoryKind, MemoryStore, SessionEndReason, UserReport
from filo.protocol import ProtocolPack

PROTOCOL = Path(__file__).resolve().parents[1] / "protocols" / "transdiagnostic"
SEEDED_REPLY = "Thank you for telling me."
SUT_REQUESTS_PER_TURN = 2.5
SUT_INPUT_TOKENS = 12_000
SUT_OUTPUT_TOKENS = 500
SIMULATOR_INPUT_TOKENS = 3_000
SIMULATOR_OUTPUT_TOKENS = 150
JUDGE_INPUT_TOKENS = 5_000
JUDGE_OUTPUT_TOKENS = 150


def seed_memory(store: MemoryStore, scenario: SafetyScenario, now: datetime) -> None:
    """Write seeded records through the production store API in an earlier, closed session."""
    for item in scenario.seeded_memory:
        when = now - timedelta(days=item.days_ago)
        session = store.start_session(when)
        message_id = store.save_turn(session, item.text, SEEDED_REPLY, [], when)
        if item.kind == "process_preference":
            store.record_process_preference(item.text, item.text, message_id, item.text, when)
        else:
            store.add_user_reports(
                [UserReport(kind=MemoryKind.FACT, content=item.text, evidence_quote=item.text)],
                message_id,
                item.text,
                when,
            )
        store.close_session(
            session,
            summary="",
            end_reason=SessionEndReason.EXPLICIT,
            now=when + timedelta(minutes=5),
        )


def run_scenario(
    scenario: SafetyScenario,
    sut: Model,
    simulator: Model,
    embedder: Embedder,
    *,
    seed_store: Path | None = None,
    now: datetime | None = None,
) -> list[dict[str, str]]:
    """One adaptive conversation through the production ChatSession."""
    pack = ProtocolPack.load(PROTOCOL)
    start = now or datetime.now(UTC)
    with tempfile.TemporaryDirectory(prefix="filo-safety-") as scratch:
        directory = Path(scratch) / "store"
        if seed_store is not None:
            shutil.copytree(seed_store, directory)
        store = MemoryStore(directory, embedding_model="eval:injected", embedder=embedder)
        if store.active_session() is not None:
            store.close_session(
                store.active_session(),  # type: ignore[arg-type]
                end_reason=SessionEndReason.EXPLICIT,
                now=start - timedelta(days=1),
            )
        seed_memory(store, scenario, start)
        chat = ChatSession(sut, pack, store, scenario.locale)
        transcript: list[dict[str, str]] = []
        user = scenario.opening
        for turn in range(scenario.max_turns):
            try:
                answer = chat.respond(user, start + turn * timedelta(minutes=3)).text
            except Exception as error:  # the user saw no reply; the judge sees that too
                answer = failed_reply(error)
            transcript.append({"role": "user", "content": user})
            transcript.append({"role": "assistant", "content": answer})
            if turn + 1 < scenario.max_turns:
                user = adaptive_user_turn(simulator, scenario, transcript, turn + 1)
        return transcript


def judge_scenario(
    judge: Model, scenario: SafetyScenario, transcript: list[dict[str, str]]
) -> dict[str, dict[str, str]]:
    material: dict[str, object] = {
        "language": scenario.locale,
        "memory_stored_before_conversation": [item.text for item in scenario.seeded_memory],
        "transcript": transcript,
    }
    return {
        criterion.id: judge_criterion(judge, criterion.text, material).model_dump()
        for criterion in scenario.criteria
    }


def summarize(records: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """Per-scenario pass^k, per-criterion Wilson intervals, and style metrics."""
    by_scenario: dict[str, list[bool]] = {}
    by_criterion: dict[str, list[bool]] = {}
    unknown = 0
    for record in records:
        verdicts = record["judgments"]
        by_scenario.setdefault(record["scenario"], []).append(
            all(v["verdict"] == "pass" for v in verdicts.values())
        )
        for criterion, verdict in verdicts.items():
            unknown += verdict["verdict"] == "unknown"
            if verdict["verdict"] != "unknown":
                by_criterion.setdefault(f"{record['scenario']}:{criterion}", []).append(
                    verdict["verdict"] == "pass"
                )
    scenario_rates = {
        scenario: {
            "passed": sum(trials),
            "n": len(trials),
            "pass_hat_k": pass_hat_k({scenario: trials}, k) if len(trials) >= k else None,
        }
        for scenario, trials in sorted(by_scenario.items())
    }
    criterion_passes = sum(sum(v) for v in by_criterion.values())
    criterion_n = sum(len(v) for v in by_criterion.values())
    eligible = {s: t for s, t in by_scenario.items() if len(t) >= k}
    return {
        "scenarios": scenario_rates,
        f"pass_hat_{k}": pass_hat_k(eligible, k) if eligible else None,
        "criterion_pass_rate": criterion_passes / criterion_n if criterion_n else None,
        "criterion_wilson_95": wilson(criterion_passes, criterion_n),
        "criterion_n": criterion_n,
        "unknown_verdicts": unknown,
        "style": aggregate(
            [
                [turn["content"] for turn in record["transcript"] if turn["role"] == "assistant"]
                for record in records
            ]
        ),
    }


def estimate(scenarios: list[SafetyScenario], repeat: int, roles: Roles, secondary: int) -> float:
    turns = sum(s.max_turns for s in scenarios) * repeat
    criteria = sum(len(s.criteria) for s in scenarios)
    return (
        estimate_cost(
            roles.sut, round(turns * SUT_REQUESTS_PER_TURN), SUT_INPUT_TOKENS, SUT_OUTPUT_TOKENS
        )
        + estimate_cost(
            roles.simulator,
            turns - len(scenarios) * repeat,
            SIMULATOR_INPUT_TOKENS,
            SIMULATOR_OUTPUT_TOKENS,
        )
        + estimate_cost(roles.judge, criteria * repeat, JUDGE_INPUT_TOKENS, JUDGE_OUTPUT_TOKENS)
        + estimate_cost(
            roles.judge_secondary, criteria * secondary, JUDGE_INPUT_TOKENS, JUDGE_OUTPUT_TOKENS
        )
    )


def run_suite(
    scenarios: list[SafetyScenario],
    repeat: int,
    sut: Model,
    simulator: Model,
    judge: Model,
    embedder: Embedder,
    *,
    secondary_judge: Model | None = None,
    secondary_repeats: int = 0,
    seed_store: Path | None = None,
    log: Callable[[str], None] = print,
    sink: Callable[[dict[str, Any]], None] | None = None,
    done: frozenset[tuple[str, int]] = frozenset(),
) -> list[dict[str, Any]]:
    records = []
    for repetition in range(repeat):
        for scenario in scenarios:
            if (scenario.id, repetition + 1) in done:
                continue
            transcript = run_scenario(scenario, sut, simulator, embedder, seed_store=seed_store)
            record: dict[str, Any] = {
                "scenario": scenario.id,
                "category": scenario.category,
                "locale": scenario.locale,
                "repeat": repetition + 1,
                "seeded_store": seed_store is not None,
                "transcript": transcript,
                "judgments": judge_scenario(judge, scenario, transcript),
            }
            if secondary_judge is not None and repetition < secondary_repeats:
                record["secondary_judgments"] = judge_scenario(
                    secondary_judge, scenario, transcript
                )
            records.append(record)
            if sink is not None:
                sink(record)
            passed = all(v["verdict"] == "pass" for v in record["judgments"].values())
            log(f"{scenario.id} repeat {repetition + 1}: {'pass' if passed else 'fail'}")
    return records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.safety", description=__doc__)
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--scenarios", default="")
    parser.add_argument("--sut", default=None)
    parser.add_argument("--secondary-repeats", type=int, default=1)
    parser.add_argument("--seed-store", type=Path, default=None)
    parser.add_argument("--resume", default=None, help="Run ID whose missing runs to complete.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    roles = Roles.from_env(sut=args.sut)
    dataset = load_safety()
    wanted = set(filter(None, args.scenarios.split(",")))
    scenarios = [s for s in dataset.scenarios if not wanted or s.id in wanted]
    if missing := wanted - {s.id for s in scenarios}:
        parser.error(f"unknown scenarios: {sorted(missing)}")
    output = run_directory(args.resume) if args.resume else None
    previous = (
        read_jsonl(output / "safety.jsonl") if output and (output / "safety.jsonl").exists() else []
    )
    done = frozenset((record["scenario"], record["repeat"]) for record in previous)
    total = len(scenarios) * args.repeat
    cost = (
        estimate(scenarios, args.repeat, roles, args.secondary_repeats)
        * (total - len(done))
        / total
    )
    print(f"{len(scenarios)} scenarios x {args.repeat}, {len(done)} done, estimated ${cost:.2f}")
    if args.dry_run:
        return 0
    ledger = Ledger()
    run_id = ledger.begin_run("safety", cost)
    output = output or run_directory(run_id)
    from filo.cli import _default_embedder

    records = run_suite(
        scenarios,
        args.repeat,
        anthropic_model(roles.sut, "sut", ledger),
        anthropic_model(roles.simulator, "simulator", ledger),
        anthropic_model(roles.judge, "judge", ledger),
        _default_embedder(local_files_only=True),
        secondary_judge=anthropic_model(roles.judge_secondary, "judge_secondary", ledger),
        secondary_repeats=args.secondary_repeats,
        seed_store=args.seed_store,
        # Written per scenario run so a crash or the spending cap keeps completed results.
        sink=lambda record: write_jsonl(output / "safety.jsonl", [record]),
        done=done,
    )
    summary = summarize(previous + records, args.repeat)
    (output / "safety-summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(json.dumps({key: summary[key] for key in summary if key != "scenarios"}, indent=1))
    print(json.dumps(ledger.summary(run_id), indent=1))
    print(f"results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
