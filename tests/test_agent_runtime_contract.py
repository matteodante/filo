import json
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.exceptions import AgentRunError, UsageLimitExceeded
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from filo.chat import ChatSession, TurnStreamEvent, TurnStreamKind
from filo.memory import MemoryStore
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False


def _pack() -> ProtocolPack:
    return ProtocolPack.load(Path("protocols/transdiagnostic"))


def _record_user_report_call() -> dict[int, DeltaToolCall]:
    return {
        0: DeltaToolCall(
            name="record_user_reports",
            json_args=json.dumps(
                {
                    "reports": [
                        {
                            "kind": "pattern",
                            "content": "pressure at work",
                            "evidence_quote": "pressure at work",
                        }
                    ]
                }
            ),
            tool_call_id="record",
        )
    }


def _has_tool_return(messages: list[Any]) -> bool:
    return any(
        getattr(part, "part_kind", "") == "tool-return"
        for message in messages
        for part in message.parts
    )


def test_staged_action_is_not_committed_when_final_reply_never_validates(tmp_path) -> None:
    async def stream(messages: list[Any], _info: Any):
        yield "x" * 4_001 if _has_tool_return(messages) else _record_user_report_call()

    store = MemoryStore(tmp_path)
    with pytest.raises(AgentRunError):
        ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
            "I feel pressure at work."
        )
    assert store.list_claims() == []
    assert store.session_transcript(store.active_session().id) == ""  # type: ignore[union-attr]


def test_staged_action_commits_once_after_output_retry(tmp_path) -> None:
    attempts = 0

    async def stream(messages: list[Any], _info: Any):
        nonlocal attempts
        if not _has_tool_return(messages):
            yield _record_user_report_call()
            return
        attempts += 1
        yield "x" * 4_001 if attempts == 1 else "What creates the most pressure?"

    store = MemoryStore(tmp_path)
    turn = ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
        "I feel pressure at work."
    )
    assert turn.text == "What creates the most pressure?"
    assert [item.content for item in store.list_claims()] == ["pressure at work"]


def test_stream_replaces_rejected_draft_and_persists_only_valid_reply(tmp_path) -> None:
    attempts = 0

    async def stream(_messages: list[Any], _info: Any):
        nonlocal attempts
        attempts += 1
        yield "x" * 4_001 if attempts == 1 else "**Valid reply.**"

    events: list[TurnStreamEvent] = []
    store = MemoryStore(tmp_path)
    turn = ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
        "Please answer.",
        on_event=events.append,
    )
    replies = [event.text for event in events if event.kind is TurnStreamKind.REPLY]
    assert replies[-1] == turn.text == "**Valid reply.**"
    transcript = store.session_transcript(store.active_session().id)  # type: ignore[union-attr]
    assert "x" * 4_001 not in transcript


def test_tool_input_and_output_are_emitted_before_reply(tmp_path) -> None:
    async def stream(messages: list[Any], _info: Any):
        yield "A reply" if _has_tool_return(messages) else _record_user_report_call()

    events: list[TurnStreamEvent] = []
    ChatSession(
        FunctionModel(stream_function=stream), _pack(), MemoryStore(tmp_path), "en-US"
    ).respond("I feel pressure at work.", on_event=events.append)
    assert [event.kind for event in events][:2] == [
        TurnStreamKind.TOOL_INPUT,
        TurnStreamKind.TOOL_OUTPUT,
    ]
    assert events[-1].kind is TurnStreamKind.REPLY


def test_repeated_identical_write_is_idempotent(tmp_path) -> None:
    async def stream(messages: list[Any], _info: Any):
        returns = sum(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in message.parts
        )
        if returns == 0:
            yield _record_user_report_call()
        elif returns == 1:
            duplicate = _record_user_report_call()
            duplicate[0].tool_call_id = "duplicate"
            yield duplicate
        else:
            yield "Thanks for clarifying."

    store = MemoryStore(tmp_path)
    ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
        "I feel pressure at work."
    )
    assert len(store.list_claims()) == 1
    metadata = next(
        message["turn_metadata"]
        for message in store.export()["messages"]
        if "turn_metadata" in message
    )
    assert metadata["tool_call_counts"] == {"record_user_reports": 2}


def test_distinct_write_calls_share_cumulative_turn_invariants(tmp_path) -> None:
    async def stream(messages: list[Any], _info: Any):
        returns = sum(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages
            for part in message.parts
        )
        if returns == 0:
            yield _record_user_report_call()
        elif returns == 1:
            yield {
                0: DeltaToolCall(
                    name="record_user_reports",
                    json_args=json.dumps(
                        {
                            "reports": [
                                {
                                    "kind": "preference",
                                    "content": "I want concise questions",
                                    "evidence_quote": "I want concise questions",
                                }
                            ]
                        }
                    ),
                    tool_call_id="preference",
                )
            }
        else:
            yield "I will keep both points in mind."

    store = MemoryStore(tmp_path)
    ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
        "I feel pressure at work, and I want concise questions."
    )
    assert {item.content for item in store.list_claims()} == {
        "pressure at work",
        "I want concise questions",
    }


def test_next_turn_receives_successful_tool_graph_without_case_envelope_persistence(
    tmp_path,
) -> None:
    async def first(messages: list[Any], _info: Any):
        yield "What happens then?" if _has_tool_return(messages) else _record_user_report_call()

    store = MemoryStore(tmp_path)
    ChatSession(FunctionModel(stream_function=first), _pack(), store, "en-US").respond(
        "I feel pressure at work."
    )
    received: list[str] = []

    async def second(messages: list[Any], _info: Any):
        received.extend(part.part_kind for message in messages for part in message.parts)
        yield "We can take this slowly."

    ChatSession(FunctionModel(stream_function=second), _pack(), store, "en-US").respond(
        "I need a moment."
    )
    assert "tool-call" in received and "tool-return" in received
    assert sum(kind == "user-prompt" for kind in received) == 3


def test_tool_budget_rejects_twenty_fifth_call_without_commit(tmp_path) -> None:
    async def stream(_messages: list[Any], _info: Any):
        yield {
            index: DeltaToolCall(
                name="retrieve_case_context",
                json_args=json.dumps({"query": f"query {index}"}),
                tool_call_id=f"lookup-{index}",
            )
            for index in range(25)
        }

    store = MemoryStore(tmp_path)
    with pytest.raises(UsageLimitExceeded, match="tool_calls_limit of 24"):
        ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
            "Help me understand this."
        )
    assert store.session_transcript(store.active_session().id) == ""  # type: ignore[union-attr]
