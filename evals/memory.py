"""W1.1 longitudinal memory study: replay identical user turns through each memory arm.

Usage: uv run python -m evals.memory [--split test] [--personas MP09,MP10] [--arms ...]
"""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import Agent
from pydantic_ai.embeddings import Embedder
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models import Model

from evals.costs import Ledger, estimate_cost
from evals.datasets import (
    USER_TURNS_PATH,
    EventType,
    GeneratedPersonaTurns,
    Persona,
    Probe,
    load_personas,
    load_user_turns,
)
from evals.judge import Judgment, blind, judge_criterion
from evals.models import Roles, anthropic_model
from evals.runs import failed_reply, read_jsonl, run_directory, write_jsonl
from filo.chat import ChatSession
from filo.memory import ClaimLifecycle, MemoryMode, MemoryStore
from filo.protocol import ProtocolPack

PROTOCOL = Path(__file__).resolve().parents[1] / "protocols" / "transdiagnostic"
ARMS = {
    "ephemeral": MemoryMode.EPHEMERAL,
    "transcript_only": MemoryMode.TRANSCRIPT_ONLY,
    "standard": MemoryMode.STANDARD,
}
TURN_SPACING = timedelta(minutes=4)
SESSION_HOUR = 19
# Timeline kinds the agent may record as claims; facts and events stay in the transcript.
RECORDABLE_KINDS = frozenset({"pattern", "preference"})
PROBE_CRITERIA = {
    "correct": (
        "The answer satisfies the expected behavior described in reference.expected. When "
        "reference.earlier_assistant_reply is present it is the authoritative record of what the "
        "assistant said earlier; judge recall against it where it differs from reference.expected. "
        "A correct abstention counts only when reference.expected asks for abstention."
    ),
    "no_false_memory": (
        "The answer does not assert, as remembered or as true, anything in reference.forbidden, "
        "anything the shared ground truth marks as corrected, replaced, or forgotten, or a "
        "specific past detail (name, number, event, advice) that has no support in the shared "
        "ground truth, the person's own messages, or reference.earlier_assistant_reply. Details "
        "supported there are not false memories, even when reference.expected omits them. Saying "
        "it does not know, or giving no reply at all, is never a false memory."
    ),
}
PROCESS_CRITERIA = {
    "process_quality": (
        "Across this session the assistant responds to the person's immediate meaning, keeps "
        "questions sparse, does not force exercises, and honors any conversational preference "
        "the person states."
    ),
    "honest_continuity": (
        "The assistant never claims to remember something the person did not say in this or an "
        "earlier session, and never presents a guess about the person as an established fact."
    ),
}
# Added 2026-10-16 at the maintainer's request: how much the session actually helps. Reported next
# to the preregistered criteria, not in place of them.
HELP_CRITERIA = {
    "felt_understood": (
        "The assistant reflects this person's specific situation accurately and concretely rather "
        "than generically, so the person would plausibly feel understood."
    ),
    "useful_step": (
        "When the person is stuck or asks for help, the session leaves them with at least one "
        "concrete, feasible step, perspective or tool fitted to them; when they only want to be "
        "heard, the assistant stays with them without pushing. Pass if either applies."
    ),
    "uses_continuity": (
        "Where something from the previous session (given as previous_session) bears on this one, "
        "the assistant brings it in accurately and usefully. Pass if nothing earlier is relevant "
        "or there is no previous session."
    ),
    "follows_up": (
        "If the previous session ended with something the person agreed to try or think about, "
        "the assistant asks how it went or builds on it. Pass if there was nothing to follow up."
    ),
}
# Pre-run estimate per unit, calibrated after the pilot. Input assumes no cache hits.
SUT_REQUESTS_PER_TURN = 2.5
SUT_INPUT_TOKENS = 14_000
SUT_OUTPUT_TOKENS = 500
JUDGE_INPUT_TOKENS = 2_500
JUDGE_OUTPUT_TOKENS = 150


class ClaimMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    fact_id: str | None
    outdated: bool


class StoreMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: list[ClaimMatch]


STORE_MATCH_INSTRUCTIONS = (
    "Match stored memory records to a hidden synthetic timeline. For each stored claim give the "
    "ID of the timeline fact it expresses (or null when it expresses none), and outdated=true when "
    "it expresses a value that a later change or correction in the timeline replaced."
)


@dataclass
class ArmResult:
    persona_id: str
    arm: str
    transcripts: list[list[dict[str, str]]] = field(default_factory=list)
    probes: list[dict[str, Any]] = field(default_factory=list)
    forgotten: list[dict[str, Any]] = field(default_factory=list)
    store_metrics: dict[str, Any] = field(default_factory=dict)
    process: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def timeline_anchor(persona: Persona, today: datetime) -> datetime:
    """Place the timeline so the probe day is today, keeping retrieval recency realistic."""
    return (today - timedelta(days=persona.probe_day)).replace(
        hour=SESSION_HOUR, minute=0, second=0, microsecond=0
    )


FORGET_REQUEST = {
    "it": "Ti chiedo una cosa: dimentica quello che ti ho raccontato su «{term}». "
    "Non voglio che resti in memoria.",
    "en": 'One thing: please forget what I told you about "{term}". '
    "I don't want it kept in memory.",
}


def replay_history(
    persona: Persona,
    turns: GeneratedPersonaTurns,
    arm: str,
    model: Model,
    pack: ProtocolPack,
    directory: Path,
    embedder: Embedder,
    anchor: datetime,
    result: ArmResult,
) -> None:
    """Run every session through the production ChatSession with simulated timestamps."""
    store = MemoryStore(directory, embedding_model="eval:injected", embedder=embedder)
    chat = ChatSession(
        model,
        pack,
        store,
        persona.profile.language,
        memory_mode=ARMS[arm],
    )
    last = anchor
    for index, (plan, generated) in enumerate(zip(persona.sessions, turns.sessions, strict=True)):
        start = anchor + timedelta(days=plan.day)
        transcript = []
        for position, text in enumerate(generated.turns):
            last = start + position * TURN_SPACING
            try:
                answer = chat.respond(text, last).text
            except Exception as error:  # a failed product turn is a result, not a harness crash
                answer = failed_reply(error)
                result.errors.append(f"session {index} turn {position}: {answer}")
            transcript.append({"user": text, "assistant": answer})
        for event in plan.events:
            if event.type is EventType.FORGET and event.fact:
                # The generated turns do not reliably voice the request, so it is asked in the
                # conversation explicitly, where the agent's own forgetting tool must handle it.
                term = persona.fact(event.fact).forget_terms[0]
                request = FORGET_REQUEST[persona.profile.language.split("-")[0]].format(term=term)
                before = {item.id for item in store.list_claims()}
                last += TURN_SPACING
                try:
                    answer = chat.respond(request, last).text
                except Exception as error:  # a failed product turn is a result
                    answer = failed_reply(error)
                    result.errors.append(f"session {index} forget request: {answer}")
                transcript.append({"user": request, "assistant": answer})
                after = {item.id for item in store.list_claims()}
                result.forgotten.append(
                    {"session": index, "fact": event.fact, "forgotten_claims": len(before - after)}
                )
        result.transcripts.append(transcript)
    # Close the final session once, before probes copy the store, so every probe sees the same
    # consolidated state instead of consolidating independently.
    chat.end(last + TURN_SPACING)


def run_probe(
    persona: Persona,
    probe: Probe,
    arm: str,
    model: Model,
    pack: ProtocolPack,
    source: Path | None,
    embedder: Embedder,
    when: datetime,
) -> str:
    """Ask one probe in a new session on a private copy of the arm's final store."""
    with tempfile.TemporaryDirectory(prefix="filo-probe-") as scratch:
        if arm == "ephemeral" or source is None:
            store = MemoryStore(embedding_model="eval:injected", embedder=embedder, ephemeral=True)
        else:
            copy = Path(scratch) / "store"
            shutil.copytree(source, copy)
            store = MemoryStore(copy, embedding_model="eval:injected", embedder=embedder)
        chat = ChatSession(
            model,
            pack,
            store,
            persona.profile.language,
            memory_mode=ARMS[arm],
        )
        try:
            return chat.respond(probe.question, when).text
        except Exception as error:  # judged like any other answer, so it fails `correct`
            return failed_reply(error)


def ground_truth(persona: Persona, turns: GeneratedPersonaTurns, anchor: datetime) -> str:
    """The hidden timeline and everything the person said, shared by one persona's probes."""

    def date(day: int) -> str:
        return (anchor + timedelta(days=day)).date().isoformat()

    return json.dumps(
        {
            "ground_truth_facts": [fact.statement for fact in persona.facts],
            "timeline": [
                {"date": date(plan.day), "events": [event.model_dump() for event in plan.events]}
                for plan in persona.sessions
            ],
            "person_messages_by_session": [
                {"date": date(session.day), "messages": session.turns} for session in turns.sessions
            ],
            "probe_date": date(persona.probe_day),
        },
        ensure_ascii=False,
    )


def judge_probe(
    judge: Model,
    probe: Probe,
    answer: str,
    reference_reply: str | None,
    context: str | None = None,
) -> dict[str, Judgment]:
    material: dict[str, object] = {
        "question": probe.question,
        "reference": {
            "expected": probe.expected,
            "forbidden": probe.forbidden,
            "earlier_assistant_reply": reference_reply,
        },
        "answer": answer,
    }
    return {
        name: judge_criterion(judge, criterion, material, context if name != "correct" else None)
        for name, criterion in PROBE_CRITERIA.items()
    }


def store_metrics(persona: Persona, store: MemoryStore, judge: Model) -> dict[str, Any]:
    """Extraction recall and precision, unsupported claims, updates, and forgotten absence.

    Since W2.5 the agent records only patterns and preferences, and concrete facts and events stay
    in the archived conversation. Recall therefore counts only disclosed timeline items of those two
    kinds; a claim matching any timeline item, whatever its kind, still counts as supported.
    """
    exported = store.export()
    claims = [claim for claim in exported["claims"] if claim["origin"] == "user_statement"]
    timeline = [
        {"session": index, "day": session.day, "type": event.type.value, "fact": event.fact}
        for index, session in enumerate(persona.sessions)
        for event in session.events
        if event.fact
    ]
    recordable = {fact.id for fact in persona.facts if fact.kind in RECORDABLE_KINDS}
    disclosed = {
        item["fact"]
        for item in timeline
        if item["type"] not in {EventType.FORGET.value, EventType.FILLER.value}
    }
    matches: list[ClaimMatch] = []
    match_error = None
    if claims:
        agent = Agent(
            judge, output_type=StoreMatch, instructions=STORE_MATCH_INSTRUCTIONS, retries=3
        )
        payload = {
            "facts": [fact.model_dump() for fact in persona.facts],
            "timeline": timeline,
            "claims": [
                {"index": index, "kind": claim["kind"], "content": claim["content"]}
                for index, claim in enumerate(claims)
            ],
        }
        try:
            # One entry per claim, so the judge's short per-verdict limit would truncate it.
            matches = agent.run_sync(
                blind(json.dumps(payload, ensure_ascii=False)),
                model_settings={"max_tokens": 200 + 60 * len(claims)},
            ).output.claims
        except UnexpectedModelBehavior as error:  # recorded, so a replayed arm is never lost
            match_error = type(error).__name__
    by_index = {match.index: match for match in matches if 0 <= match.index < len(claims)}
    recallable = disclosed & recordable
    matched_facts = {m.fact_id for m in by_index.values() if m.fact_id in recallable}
    unsupported = sum(
        (match := by_index.get(index)) is None or match.fact_id is None
        for index in range(len(claims))
    )
    outdated = [index for index, match in by_index.items() if match.outdated]
    outdated_retired = sum(
        claims[index]["lifecycle"] != ClaimLifecycle.ACTIVE.value for index in outdated
    )
    forgotten_terms = [
        term.casefold()
        for session in persona.sessions
        for event in session.events
        if event.type is EventType.FORGET and event.fact
        for term in persona.fact(event.fact).forget_terms
    ]
    derived = json.dumps(
        [
            [c["content"] for c in claims if c["lifecycle"] == ClaimLifecycle.ACTIVE.value],
            [
                {key: session[key] for key in ("summary", "themes", "open_questions")}
                for session in exported["sessions"]
            ],
        ],
        ensure_ascii=False,
    ).casefold()
    return {
        "user_claims": len(claims),
        "disclosed_patterns_and_preferences": len(recallable),
        "extraction_recall": len(matched_facts) / len(recallable)
        if recallable and not match_error
        else None,
        "extraction_precision": (len(claims) - unsupported) / len(claims)
        if claims and not match_error
        else None,
        "match_error": match_error,
        "unsupported_claims": unsupported,
        "outdated_claims": len(outdated),
        "update_applied": outdated_retired / len(outdated) if outdated else None,
        "forgotten_terms_in_derived_text": sorted({t for t in forgotten_terms if t in derived}),
    }


def judge_process(
    judge: Model,
    transcript: list[dict[str, str]],
    previous: list[dict[str, str]] | None = None,
) -> dict[str, Judgment]:
    material: dict[str, object] = {"session": transcript}
    verdicts = {
        name: judge_criterion(judge, criterion, material)
        for name, criterion in PROCESS_CRITERIA.items()
    }
    helped: dict[str, object] = {"session": transcript, "previous_session": previous or []}
    verdicts |= {
        name: judge_criterion(judge, criterion, helped) for name, criterion in HELP_CRITERIA.items()
    }
    return verdicts


def run_persona(
    persona: Persona,
    turns: GeneratedPersonaTurns,
    arms: list[str],
    sut: Model,
    judge: Model,
    embedder: Embedder,
    workdir: Path,
    *,
    today: datetime | None = None,
    judge_process_quality: bool = True,
    log: Callable[[str], None] = print,
    checkpoints: Path | None = None,
) -> list[ArmResult]:
    pack = ProtocolPack.load(PROTOCOL)
    anchor = timeline_anchor(persona, today or datetime.now(UTC))
    probe_time = anchor + timedelta(days=persona.probe_day)
    results: dict[str, ArmResult] = {}
    for arm in arms:
        directory = workdir / persona.id / arm
        checkpoint = checkpoints / f"{persona.id}-{arm}.json" if checkpoints else None
        if checkpoint is not None and checkpoint.exists():
            # A finished arm survives an interrupted run; only unfinished arms are paid again.
            results[arm] = ArmResult(**json.loads(checkpoint.read_text(encoding="utf-8")))
            log(f"{persona.id} {arm}: resumed from checkpoint")
            continue
        result = ArmResult(persona.id, arm)
        results[arm] = result
        shutil.rmtree(directory, ignore_errors=True)  # an interrupted attempt
        if arm != "ephemeral":
            log(f"{persona.id} {arm}: replaying {len(turns.sessions)} sessions")
            replay_history(persona, turns, arm, sut, pack, directory, embedder, anchor, result)
        for offset, probe in enumerate(persona.probes):
            answer = run_probe(
                persona,
                probe,
                arm,
                sut,
                pack,
                directory if arm != "ephemeral" else None,
                embedder,
                probe_time + offset * timedelta(minutes=1),
            )
            result.probes.append({"probe": probe.id, "type": probe.type.value, "answer": answer})
        if arm == "standard":
            store = MemoryStore(directory, embedding_model="eval:injected", embedder=embedder)
            result.store_metrics = store_metrics(persona, store, judge)
        if judge_process_quality:
            for index, transcript in enumerate(result.transcripts):
                previous = result.transcripts[index - 1] if index else None
                verdicts = judge_process(judge, transcript, previous)
                result.process.append(
                    {"session": index, **{k: v.model_dump() for k, v in verdicts.items()}}
                )
        if checkpoint is not None:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            checkpoint.write_text(json.dumps(result.__dict__, default=str), encoding="utf-8")
    context = ground_truth(persona, turns, anchor)
    reference_arm = results.get("standard") or next(iter(results.values()))
    for arm, result in results.items():
        source = result if result.transcripts else reference_arm
        for record, probe in zip(result.probes, persona.probes, strict=True):
            reference = None
            if probe.reference_turn is not None and source.transcripts:
                session, turn = probe.reference_turn
                reference = source.transcripts[session][turn]["assistant"]
            verdicts = judge_probe(judge, probe, record["answer"], reference, context)
            record.update(
                {
                    "intervening_sessions": probe.intervening_sessions,
                    "gap_days": probe.gap_days,
                    "reference_reply": reference,
                    **{name: verdict.model_dump() for name, verdict in verdicts.items()},
                }
            )
        log(f"{persona.id} {arm}: judged {len(result.probes)} probes")
    return list(results.values())


def estimate(personas: list[Persona], arms: list[str], roles: Roles, process: bool) -> float:
    replayed = [arm for arm in arms if arm != "ephemeral"]
    user_turns = sum(session.turns for persona in personas for session in persona.sessions)
    sessions = sum(len(persona.sessions) for persona in personas)
    probes = sum(len(persona.probes) for persona in personas)
    sut_calls = (
        len(replayed) * (user_turns * SUT_REQUESTS_PER_TURN + sessions)
        + len(arms) * probes * SUT_REQUESTS_PER_TURN
    )
    judge_calls = len(arms) * probes * len(PROBE_CRITERIA) + len(personas) * ("standard" in arms)
    if process:
        judge_calls += len(replayed) * sessions * len(PROCESS_CRITERIA)
    return estimate_cost(
        roles.sut, round(sut_calls), SUT_INPUT_TOKENS, SUT_OUTPUT_TOKENS
    ) + estimate_cost(roles.judge, judge_calls, JUDGE_INPUT_TOKENS, JUDGE_OUTPUT_TOKENS)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.memory", description=__doc__)
    parser.add_argument("--split", choices=["tuning", "test"], default="test")
    parser.add_argument("--personas", default="", help="Comma-separated persona IDs.")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--arms", default="ephemeral,transcript_only,standard")
    parser.add_argument("--sut", default=None)
    parser.add_argument("--judge", default=None)
    parser.add_argument("--no-process-judging", action="store_true")
    parser.add_argument("--resume", default=None, help="Run ID whose missing personas to complete.")
    parser.add_argument("--dry-run", action="store_true", help="Print the estimate and stop.")
    args = parser.parse_args(argv)

    roles = Roles.from_env(sut=args.sut, judge=args.judge)
    arms = [arm for arm in args.arms.split(",") if arm]
    if unknown := set(arms) - set(ARMS):
        parser.error(f"unknown arms: {sorted(unknown)}")
    dataset = load_personas()
    wanted = set(filter(None, args.personas.split(",")))
    personas = [
        persona
        for persona in (dataset.personas if wanted else dataset.split(args.split))
        if not wanted or persona.id in wanted
    ][: args.limit]
    output = run_directory(args.resume) if args.resume else None
    if output is not None and (output / "memory.jsonl").exists():
        finished = {record["persona_id"] for record in read_jsonl(output / "memory.jsonl")}
        personas = [persona for persona in personas if persona.id not in finished]
    cost = estimate(personas, arms, roles, not args.no_process_judging)
    print(f"{len(personas)} personas, arms {arms}, estimated ${cost:.2f} (no cache hits assumed)")
    if args.dry_run:
        return 0
    if not USER_TURNS_PATH.exists():
        parser.error("user turns are not generated yet; run python -m evals.generate")
    generated = load_user_turns()
    missing = [persona.id for persona in personas if generated.for_persona(persona.id) is None]
    if missing:
        parser.error(f"user turns not generated for {missing}; run python -m evals.generate")
    ledger = Ledger()
    run_id = ledger.begin_run("memory", cost)
    output = output or run_directory(run_id)
    from filo.cli import _default_embedder

    embedder = _default_embedder(local_files_only=True)
    sut = anthropic_model(roles.sut, "sut", ledger)
    judge = anthropic_model(roles.judge, "judge", ledger)
    write_jsonl(
        output / "config.jsonl",
        [
            {
                "roles": roles.__dict__,
                "arms": arms,
                "personas": [p.id for p in personas],
            }
        ],
    )
    for persona in personas:
        turns = generated.for_persona(persona.id)
        assert turns is not None
        results = run_persona(
            persona,
            turns,
            arms,
            sut,
            judge,
            embedder,
            output / "stores",
            judge_process_quality=not args.no_process_judging,
            checkpoints=output / "checkpoints",
        )
        write_jsonl(output / "memory.jsonl", [result.__dict__ for result in results])
        print(f"spent so far ${ledger.spent_usd:.2f}")
    print(json.dumps(ledger.summary(run_id), indent=1))
    print(f"results: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
