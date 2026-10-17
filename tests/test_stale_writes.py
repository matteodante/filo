"""Writes from a stale snapshot never undo what another process forgot or corrected meanwhile.

The CLI and the Telegram listener share one store, so a turn or a consolidation can hold a session
or formulation read before another process forgot, corrected, or deleted something.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from evals.costs import BudgetExceeded
from filo.chat import ChatSession
from filo.memory import ClaimCorrection, MemoryKind, MemoryStore, UserReport
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False
ROOT = Path("protocols/transdiagnostic")
STORY = "my brother crashed the boat at the lake"


def _reflection(summary: str) -> dict[int, DeltaToolCall]:
    arguments = json.dumps({"summary": summary})
    return {0: DeltaToolCall(name="final_result", json_args=arguments, tool_call_id="r")}


def _forget_story_elsewhere(path: Path) -> None:
    """Another process forgets the story while this one holds an older snapshot."""
    other = MemoryStore(path)
    session = other.active_session() or other.start_session()
    request = "Please forget the boat story"
    request_id = other.save_turn(session, request, "Done.", [])
    other.forget_content([STORY], "Please forget", request_id, request)


def test_a_stale_turn_keeps_a_reflection_withheld_by_another_process(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(session, f"Last summer {STORY}", "That sounds frightening.", [])
    _forget_story_elsewhere(tmp_path)
    store.save_turn(session, "Anyway, work was fine", "Good to hear.", [])
    assert store.reflection_withheld(session.id)
    closed = store.close_session(session, summary="A boat accident upset the user.")
    assert closed.summary == ""


def test_a_forget_during_consolidation_drops_the_reflection(tmp_path) -> None:
    store = MemoryStore(tmp_path)

    async def stream(_messages: list[Any], info: Any):
        if info.output_tools:
            _forget_story_elsewhere(tmp_path)
            yield _reflection("The user's brother crashed the boat at the lake.")
        else:
            yield "That sounds frightening."

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    chat.respond(f"Last summer {STORY}.")
    closed = chat.end()
    assert closed is not None and closed.summary == "" and closed.themes == []


def test_a_correction_during_consolidation_drops_the_reflection(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = "I drink coffee at midnight every day"
    session = store.start_session()
    first = store.save_turn(session, old, "Noted.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.PATTERN, content=old, evidence_quote=old)], first, old
    )[0]

    async def stream(_messages: list[Any], _info: Any):
        other = MemoryStore(tmp_path)
        correction = "No, I only drink tea"
        second = other.save_turn(other.start_session(), correction, "Thanks.", [])
        other.correct_claim(
            ClaimCorrection(
                memory_id=claim.id,
                correction_quote=correction,
                replacement_quote="I only drink tea",
            ),
            second,
            correction,
        )
        yield _reflection("The user drinks coffee at midnight every day.")

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    closed = chat.end()
    assert closed is not None and closed.summary == ""


@pytest.mark.parametrize(
    "failure",
    [UnexpectedModelBehavior("bad output"), RuntimeError("login required")],
    ids=["agent-run-error", "any-other-error"],
)
def test_a_failed_consolidation_keeps_the_transcript_and_restores_no_forgotten_claim(
    tmp_path, failure: Exception
) -> None:
    store = MemoryStore(tmp_path)
    text = "I always put off the weekly report until Thursday night"
    session = store.start_session()
    message_id = store.save_turn(session, text, "Noted.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.PATTERN, content=text, evidence_quote=text)], message_id, text
    )[0]
    store.save_formulation_links({"actions_and_coping": [claim.id]})

    async def stream(_messages: list[Any], _info: Any):
        MemoryStore(tmp_path).forget_claim(claim.id)
        raise failure
        yield  # pragma: no cover - makes this an async generator

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    closed = chat.end()
    assert closed is not None and closed.consolidation_error == type(failure).__name__
    assert claim.id not in json.dumps(store.load_formulation().evidence)
    assert store.list_claims() == []
    assert next(item["content"] for item in store.export()["messages"]) == text


def test_consolidation_records_nothing_after_an_under_18_disclosure(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    instructions: list[str] = []

    async def stream(messages: list[Any], info: Any):
        if info.output_tools:
            instructions.extend(
                message.instructions for message in messages if getattr(message, "instructions", "")
            )
            yield _reflection("Not recorded.")
        else:
            yield "Filo is for adults only. A trusted adult can help."

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    chat.respond("I'm 15 and my parents keep fighting.")
    closed = chat.end()
    assert any("under 18, record nothing" in item for item in instructions)
    assert closed is not None and closed.summary == "Not recorded." and closed.themes == []
    assert store.load_formulation().evidence == {}


def test_correcting_an_earlier_sessions_claim_keeps_this_sessions_reflection(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = "I drink coffee at midnight every day"
    earlier = store.start_session()
    first = store.save_turn(earlier, old, "Noted.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.PATTERN, content=old, evidence_quote=old)], first, old
    )[0]
    store.close_session(earlier, summary="Late caffeine came up.")
    correction = "No, I only drink tea now"
    session = store.start_session()
    second = store.save_turn(session, correction, "Thanks for correcting me.", [])
    store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id, correction_quote=correction, replacement_quote="I only drink tea"
        ),
        second,
        correction,
    )

    async def stream(_messages: list[Any], _info: Any):
        yield _reflection("The user corrected an earlier habit: tea only.")

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    closed = chat.end()
    assert closed is not None and closed.summary == "The user corrected an earlier habit: tea only."


def test_a_fatal_error_during_consolidation_is_not_swallowed(tmp_path) -> None:
    class Fatal(RuntimeError):
        fatal = True

    store = MemoryStore(tmp_path)
    store.save_turn(store.start_session(), "Busy week at work", "Sounds full.", [])

    async def stream(_messages: list[Any], _info: Any):
        raise Fatal("budget cap")
        yield  # pragma: no cover - makes this an async generator

    chat = ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    )
    with pytest.raises(Fatal):
        chat.end()
    assert BudgetExceeded.fatal is True


def test_a_stale_turn_never_reopens_a_session_another_process_closed(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(session, "Busy week at work", "Sounds full.", [])
    other = MemoryStore(tmp_path)
    stored = other.active_session()
    assert stored is not None
    other.close_session(stored, summary="Work was busy.")
    store.save_turn(session, "Anyway", "Okay.", [])
    [reloaded] = [item for item in store.list_sessions() if item.id == session.id]
    assert reloaded.ended_at is not None and reloaded.summary == "Work was busy."
    assert store.active_session() is None
