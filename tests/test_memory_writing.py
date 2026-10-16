"""Memory writing: the conversation agent records patterns and preferences inline."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRequest, models
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from filo.chat import ChatSession
from filo.memory import MemoryKind, MemoryStore, UserReport
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False
ROOT = Path("protocols/transdiagnostic")


def _call(name: str, arguments: dict[str, Any], call_id: str = "call") -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=call_id)}


def _report(content: str, quote: str | None = None, kind: str = "pattern") -> dict[str, str]:
    return {"kind": kind, "content": content, "evidence_quote": quote or content}


def _retries(messages: list[Any]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


def _active(store: MemoryStore) -> list[str]:
    return [item.content for item in store.list_claims()]


def test_a_thread_never_joins_another_threads_transaction(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.save_turn(store.start_session(), "text", "reply", [])
    inside = threading.Event()
    release = threading.Event()
    errors: list[BaseException] = []

    def hold() -> None:
        with store.transaction():
            store.list_claims()
            inside.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    inside.wait(5)

    def read() -> None:
        try:
            store.list_sessions()
        except BaseException as error:
            errors.append(error)

    reader = threading.Thread(target=read)
    reader.start()
    reader.join(5)
    release.set()
    holder.join(5)
    assert errors == []


def test_conversation_agent_has_the_report_tool_and_no_quote_mechanics(tmp_path) -> None:
    tools: list[set[str]] = []
    instructions: list[str] = []

    async def stream(messages: list[Any], info: Any):
        tools.append({tool.name for tool in info.function_tools})
        instructions.extend(
            message.instructions
            for message in messages
            if isinstance(message, ModelRequest) and message.instructions
        )
        yield "I hear you."

    store = MemoryStore(tmp_path)
    ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    ).respond("Hello.")
    assert "record_user_reports" in tools[0]
    assert "record_user_under_18" not in tools[0]
    for mechanic in ("verbatim", "grounded_paraphrase", "replacement clause", "dropping words"):
        assert mechanic not in instructions[0]
    assert "Record durable state when it would matter in a later conversation" in instructions[0]
    assert "concrete details of the user's life need no record" in instructions[0]


@pytest.mark.parametrize("kind", ["fact", "event", "hypothesis"])
def test_a_user_report_is_only_a_pattern_or_a_preference(kind) -> None:
    with pytest.raises(ValidationError):
        UserReport.model_validate(_report("I wake at four", kind=kind))


def test_the_store_rejects_any_other_report_kind(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    text = "I wake at four most nights."
    message_id = store.save_turn(store.start_session(), text, "reply", [])
    report = UserReport.model_construct(
        kind=MemoryKind.HYPOTHESIS,
        content="I wake at four most nights",
        evidence_quote="I wake at four most nights",
        aliases=[],
        merge_into_id=None,
        valid_from=None,
        about_others=False,
    )
    with pytest.raises(ValueError, match="preference or a pattern"):
        store.add_user_reports([report], message_id, text)
    assert _active(store) == []


def test_inline_report_rejection_says_exactly_what_to_change(tmp_path) -> None:
    seen: list[str] = []

    async def stream(messages: list[Any], _info: Any):
        retries = _retries(messages)
        seen.extend(retries)
        if retries and any(
            getattr(part, "part_kind", "") == "tool-return" for m in messages for part in m.parts
        ):
            yield "Noted."
        elif retries:
            yield _call("record_user_reports", {"reports": [_report("I wake at four")]}, "2")
        else:
            yield _call(
                "record_user_reports", {"reports": [_report("at four I wake", "I wake at four")]}
            )

    store = MemoryStore(tmp_path)
    ChatSession(
        FunctionModel(stream_function=stream), ProtocolPack.load(ROOT), store, "en-US"
    ).respond("Most nights I wake at four.")
    assert any("content may only drop words from evidence_quote" in item for item in seen)
    assert _active(store) == ["I wake at four"]
