"""Split memory writing: a post-turn extractor records direct user reports from durable markers."""

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import ModelRequest, models
from pydantic_ai.messages import RetryPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from filo.chat import CASE_CONTEXT_PREAMBLE, MAX_EXTRACTION_ATTEMPTS, ChatSession, MemoryWriting
from filo.memory import MemoryKind, MemoryMode, MemoryStore, UserReport
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False
ROOT = Path("protocols/transdiagnostic")


def _call(name: str, arguments: dict[str, Any], call_id: str = "call") -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id=call_id)}


def _report(content: str, quote: str | None = None, kind: str = "fact") -> dict[str, str]:
    return {"kind": kind, "content": content, "evidence_quote": quote or content}


def _retries(messages: list[Any]) -> list[str]:
    return [
        str(part.content)
        for message in messages
        for part in message.parts
        if isinstance(part, RetryPromptPart)
    ]


class Model:
    """One FunctionModel serving both the conversation agent and the extractor.

    The extractor is the run with an output tool; the conversation agent answers in text.
    """

    def __init__(
        self, *extractions: dict[str, Any] | Exception, reply: str = "I hear you."
    ) -> None:
        self.extractions = list(extractions)
        self.reply = reply
        self.conversation_tools: list[set[str]] = []
        self.conversation_messages: list[list[Any]] = []
        self.extractor_calls = 0
        self.extractor_messages: list[list[Any]] = []

    def __call__(self) -> FunctionModel:
        return FunctionModel(stream_function=self._stream)

    async def _stream(self, messages: list[Any], info: Any):
        if info.output_tools and "under_18" not in json.dumps(
            info.output_tools[0].parameters_json_schema
        ):
            yield _call("final_result", {"summary": "A synthetic session."}, "reflection")
            return
        if info.output_tools:
            self.extractor_calls += 1
            self.extractor_messages.append(messages)
            outcome = self.extractions.pop(0) if self.extractions else {"reports": []}
            if isinstance(outcome, Exception):
                raise outcome
            yield _call("final_result", outcome, f"x{self.extractor_calls}")
            return
        self.conversation_tools.append({tool.name for tool in info.function_tools})
        self.conversation_messages.append(messages)
        yield self.reply


def _session(store: MemoryStore, model: Model, **options: Any) -> ChatSession:
    options.setdefault("memory_writing", MemoryWriting.SPLIT)
    return ChatSession(model(), ProtocolPack.load(ROOT), store, "en-US", **options)


def _markers(store: MemoryStore) -> list[int]:
    return [message_id for message_id, _session, _attempts in store.pending_extractions()]


def _active(store: MemoryStore) -> list[str]:
    return [item.content for item in store.list_claims()]


def test_split_turn_commits_marker_and_extractor_records_after_delivery(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"reports": [_report("I moved to Turin in May")]})
    chat = _session(store, model)
    chat.respond("I moved to Turin in May and I miss the sea.")
    assert "record_user_reports" not in model.conversation_tools[0]
    assert {"record_hypothesis", "correct_claim", "record_intervention"} <= (
        model.conversation_tools[0]
    )
    assert model.extractor_calls == 0
    assert len(_markers(store)) == 1
    assert store.list_claims() == []

    assert chat.extract_pending() == 1
    assert _active(store) == ["I moved to Turin in May"]
    assert _markers(store) == []
    data = json.loads(str(model.extractor_messages[0][0].parts[0].content).split("\n", 1)[1])
    assert data == {"previous_assistant_reply": None, "corrected_this_turn": []}


def test_extractor_sees_previous_assistant_reply_and_records_at_message_time(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"reports": []}, {"reports": [_report("my sister lives in Bari")]})
    chat = _session(store, model, memory_writing=MemoryWriting.SPLIT)
    chat.respond("Hello.")
    chat.respond("Yes, my sister lives in Bari.")
    chat.extract_pending()
    data = json.loads(str(model.extractor_messages[1][0].parts[0].content).split("\n", 1)[1])
    assert data["previous_assistant_reply"] == "I hear you."
    claim = store.list_claims()[0]
    user_message = next(
        item for item in store.export()["messages"] if item["content"].startswith("Yes")
    )
    assert claim.first_seen_at == user_message["created_at"]
    assert claim.evidence[0].message_id == user_message["id"]


def test_extractor_writes_pass_the_same_validation_and_retry_with_guidance(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model(
        {"reports": [_report("I have chronic insomnia", "I slept badly")]},
        {"reports": [_report("I slept badly")]},
    )
    chat = _session(store, model)
    chat.respond("I slept badly last night.")
    chat.extract_pending()
    retries = _retries(model.extractor_messages[1])
    assert len(retries) == 1
    assert "set content to exactly the evidence_quote text" in retries[0]
    assert _active(store) == ["I slept badly"]


def test_extractor_rejects_quote_mined_out_of_its_negation(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    mined = {"reports": [_report("è insonnia cronica")]}
    model = Model(mined, mined, mined)
    chat = _session(store, model)
    chat.respond("Non è insonnia cronica, è solo stress.")
    assert chat.extract_pending() == 0
    assert "negation" in _retries(model.extractor_messages[1])[0]
    assert store.list_claims() == []
    assert len(_markers(store)) == 1


def test_crash_between_turn_commit_and_extraction_is_retried_once(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    _session(store, Model()).respond("I started running on Mondays.")
    # The process ends here, before the transport calls extract_pending.

    restarted = MemoryStore(tmp_path)
    model = Model({"reports": [_report("I started running on Mondays")]})
    chat = _session(restarted, model)
    assert chat.extract_pending() == 1
    assert chat.extract_pending() == 0
    assert model.extractor_calls == 1
    claims = restarted.list_claims()
    assert [item.content for item in claims] == ["I started running on Mondays"]
    assert len(claims[0].evidence) == 1


def test_failed_commit_keeps_marker_and_retry_writes_once(tmp_path, monkeypatch) -> None:
    store = MemoryStore(tmp_path)
    report = {"reports": [_report("I quit my job")]}
    model = Model(report, report)
    chat = _session(store, model)
    chat.respond("I quit my job today.")

    original = store.add_user_reports

    def crash(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise OSError("disk full")

    monkeypatch.setattr(store, "add_user_reports", crash)
    assert chat.extract_pending() == 0
    assert store.list_claims() == []
    monkeypatch.setattr(store, "add_user_reports", original)
    assert chat.extract_pending() == 1
    claims = store.list_claims()
    assert [item.content for item in claims] == ["I quit my job"]
    assert len(claims[0].evidence) == 1


def test_a_claimed_marker_cannot_be_claimed_again(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = store.save_turn(store.start_session(), "text", "reply", [])
    store.add_pending_extraction(message_id)
    with store.transaction():
        assert store.claim_pending_extraction(message_id)
    with store.transaction():
        assert not store.claim_pending_extraction(message_id)


def test_provider_failure_keeps_marker_and_stops_after_bounded_attempts(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model(*(RuntimeError("provider down") for _ in range(MAX_EXTRACTION_ATTEMPTS)))
    chat = _session(store, model)
    chat.respond("I adopted a dog.")
    for _ in range(MAX_EXTRACTION_ATTEMPTS + 2):
        assert chat.extract_pending() == 0
    assert model.extractor_calls == MAX_EXTRACTION_ATTEMPTS
    assert store.list_claims() == []
    # The marker stays, so the turn is never archived without its memory or a marker.
    assert _markers(store) != []


def test_pending_marker_is_drained_before_the_next_retrieval(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"reports": [_report("my brother is called Luca")]})
    chat = _session(store, model)
    chat.respond("Yes, my brother is called Luca.")
    chat.respond("Luca called me today, my brother.")
    envelope = next(
        json.loads(str(part.content).split("\n", 1)[1])
        for message in model.conversation_messages[1]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if str(part.content).startswith(CASE_CONTEXT_PREAMBLE)
    )
    assert [item["content"] for item in envelope["user_reports"]] == ["my brother is called Luca"]


def test_explicit_end_extracts_before_consolidation(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"reports": [_report("I live alone")]})
    chat = _session(store, model)
    chat.respond("I live alone.")
    chat.end()
    assert _active(store) == ["I live alone"]
    assert _markers(store) == []


@pytest.mark.parametrize("mode", [MemoryMode.TRANSCRIPT_ONLY, MemoryMode.EPHEMERAL])
def test_non_standard_modes_create_no_markers_and_no_claims(tmp_path, mode) -> None:
    store = MemoryStore(ephemeral=True) if mode is MemoryMode.EPHEMERAL else MemoryStore(tmp_path)
    model = Model({"reports": [_report("I live alone")]})
    chat = _session(store, model, memory_mode=mode)
    chat.respond("I live alone.")
    assert chat.extract_pending() == 0
    assert _markers(store) == []
    assert store.list_claims() == []
    assert model.extractor_calls == 0


def test_under_18_disclosure_withholds_reports_for_the_rest_of_the_session(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model(
        {"under_18": True, "reports": [_report("I am 15")]},
        {"reports": [_report("I play the violin")]},
    )
    chat = _session(store, model)
    chat.respond("I am 15 and my parents fight a lot.")
    chat.extract_pending()
    chat.respond("I play the violin.")
    chat.extract_pending()
    assert store.list_claims() == []
    assert model.extractor_calls == 1
    assert _markers(store) == []
    assert store.active_session().user_reports_withheld  # type: ignore[union-attr]


def test_under_18_is_recorded_even_when_the_same_message_is_about_risk(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"under_18": True, "risk": True}, {"reports": [_report("I play the violin")]})
    chat = _session(store, model)
    chat.respond("I'm 15 and I keep thinking about hurting myself.")
    chat.extract_pending()
    chat.respond("I play the violin.")
    chat.extract_pending()
    assert store.list_claims() == []
    assert model.extractor_calls == 1
    assert store.active_session().user_reports_withheld  # type: ignore[union-attr]


def test_conversation_agent_can_flag_a_declared_age_for_the_session(tmp_path) -> None:
    calls: list[str] = []

    async def stream(messages: list[Any], info: Any):
        if info.output_tools:
            calls.append("extractor")
            yield _call("final_result", {"reports": [_report("I play the violin")]})
        elif (
            any(
                getattr(part, "part_kind", "") == "tool-return"
                for m in messages
                for part in m.parts
            )
            or calls
        ):
            yield "Thanks for telling me."
        else:
            calls.append("flag")
            yield _call("record_user_under_18", {})

    store = MemoryStore(tmp_path)
    chat = ChatSession(
        FunctionModel(stream_function=stream),
        ProtocolPack.load(ROOT),
        store,
        "en-US",
        memory_writing=MemoryWriting.SPLIT,
    )
    chat.respond("As I said, I'm in year nine.")
    chat.respond("I play the violin.")
    chat.extract_pending()
    assert calls == ["flag"]
    assert store.list_claims() == []
    assert _markers(store) == []


def test_a_later_turn_is_never_extracted_before_an_unresolved_earlier_one(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    failures = [RuntimeError("provider down") for _ in range(MAX_EXTRACTION_ATTEMPTS)]
    model = Model(*failures, {"reports": [_report("I play the violin")]})
    chat = _session(store, model)
    chat.respond("I'm 15.")
    first = _markers(store)[0]
    # The extraction after delivery fails, so the next turn's drain retries it first.
    chat.extract_pending()
    chat.respond("I play the violin.")
    for _ in range(MAX_EXTRACTION_ATTEMPTS + 1):
        chat.extract_pending()
    assert model.extractor_calls == MAX_EXTRACTION_ATTEMPTS
    assert store.list_claims() == []
    assert _markers(store)[0] == first
    assert len(_markers(store)) == 2


def test_another_session_is_not_blocked_by_an_unresolved_marker(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    failures = [RuntimeError("provider down") for _ in range(MAX_EXTRACTION_ATTEMPTS)]
    model = Model(*failures, {"reports": [_report("I play the violin")]})
    chat = _session(store, model)
    chat.respond("Something.")
    for _ in range(MAX_EXTRACTION_ATTEMPTS):
        chat.extract_pending()
    chat.end()
    chat.respond("I play the violin.")
    chat.extract_pending()
    assert _active(store) == ["I play the violin"]


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


def test_risk_turn_writes_nothing_and_later_turns_still_record(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model(
        {"risk": True, "reports": [_report("my neighbours are poisoning my water")]},
        {"reports": [_report("I walked by the river")]},
    )
    chat = _session(store, model)
    chat.respond("I know my neighbours are poisoning my water.")
    chat.extract_pending()
    assert store.list_claims() == []
    chat.respond("Today I walked by the river.")
    chat.extract_pending()
    assert _active(store) == ["I walked by the river"]


def test_wording_corrected_inline_is_not_recorded_again_by_the_extractor(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = store.save_turn(store.start_session(), "I live in Rome", "Noted", [])
    item = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.FACT, content="I live in Rome", evidence_quote="I live in Rome"
            )
        ],
        message_id,
        "I live in Rome",
    )[0]
    model = Model({"reports": [_report("That is wrong"), _report("I live in Turin")]})

    async def stream(messages: list[Any], info: Any):
        if info.output_tools:
            async for chunk in model._stream(messages, info):
                yield chunk
        elif any(
            getattr(part, "part_kind", "") == "tool-return" for m in messages for part in m.parts
        ):
            yield "Thanks for correcting that."
        else:
            yield _call(
                "correct_claim",
                {
                    "correction": {
                        "memory_id": item.id,
                        "correction_quote": "That is wrong",
                        "replacement_quote": "I live in Turin",
                    }
                },
            )

    chat = ChatSession(
        FunctionModel(stream_function=stream),
        ProtocolPack.load(ROOT),
        store,
        "en-US",
        memory_writing=MemoryWriting.SPLIT,
    )
    chat.respond("That is wrong; I live in Turin")
    chat.extract_pending()
    data = json.loads(str(model.extractor_messages[0][0].parts[0].content).split("\n", 1)[1])
    assert {"That is wrong", "I live in Turin", "I live in Rome"} <= set(
        data["corrected_this_turn"]
    )
    claims = store.list_claims()
    assert [(claim.id, claim.content) for claim in claims] == [(item.id, "I live in Turin")]


def test_deleting_the_turn_removes_its_marker(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    chat = _session(store, Model())
    chat.respond("Something private.")
    session_id = store.active_session().id  # type: ignore[union-attr]
    assert store.delete_session(session_id)
    assert _markers(store) == []
    chat.respond("Again.")
    store.delete_all()
    assert _markers(store) == []


def test_inline_arm_keeps_the_report_tool_and_creates_no_marker(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model()
    _session(store, model, memory_writing=MemoryWriting.INLINE).respond("Hello.")
    assert "record_user_reports" in model.conversation_tools[0]
    assert _markers(store) == []


@pytest.mark.parametrize("writing", list(MemoryWriting))
def test_conversation_instructions_carry_no_quote_mechanics(tmp_path, writing) -> None:
    store = MemoryStore(tmp_path)
    model = Model()
    _session(store, model, memory_writing=writing).respond("Hello.")
    instructions = next(
        message.instructions
        for message in model.conversation_messages[0]
        if isinstance(message, ModelRequest) and message.instructions
    )
    for mechanic in ("verbatim", "grounded_paraphrase", "replacement clause", "dropping words"):
        assert mechanic not in instructions
    assert "Record durable state when it would matter in a later conversation" in instructions


def test_inline_report_rejection_says_exactly_what_to_change(tmp_path) -> None:
    async def stream(messages: list[Any], _info: Any):
        if _retries(messages) and any(
            getattr(part, "part_kind", "") == "tool-return" for m in messages for part in m.parts
        ):
            yield "Noted."
        elif _retries(messages):
            yield _call("record_user_reports", {"reports": [_report("I slept badly")]}, "2")
        else:
            yield _call(
                "record_user_reports", {"reports": [_report("I sleep poorly", "I slept badly")]}
            )

    store = MemoryStore(tmp_path)
    seen: list[str] = []

    async def recording(messages: list[Any], info: Any):
        seen.extend(_retries(messages))
        async for chunk in stream(messages, info):
            yield chunk

    ChatSession(
        FunctionModel(stream_function=recording), ProtocolPack.load(ROOT), store, "en-US"
    ).respond("I slept badly last night.")
    assert any("set content to exactly the evidence_quote text" in item for item in seen)
    assert _active(store) == ["I slept badly"]


STORY = "my brother crashed the boat at the lake"


def test_forgetting_removes_a_pending_extraction_marker(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model(RuntimeError("provider down"), {"reports": [_report(STORY)]})
    chat = _session(store, model)
    chat.respond(f"Last summer {STORY}.")
    chat.extract_pending()
    [story_id] = _markers(store)
    request = "Please forget the boat story."
    request_id = store.save_turn(store.active_session(), request, "Done.", [])  # type: ignore[arg-type]
    store.forget_content([STORY], "Please forget", request_id, request)
    assert story_id not in _markers(store)
    chat.extract_pending()
    assert _active(store) == []


def test_extractor_records_nothing_from_a_suppressed_round(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    model = Model({"reports": [_report(STORY)]})
    chat = _session(store, model)
    chat.respond(f"Last summer {STORY}.")
    [story_id] = _markers(store)
    store._suppress_rounds({story_id})
    # A marker written after the suppression, as a concurrent turn could.
    store.add_pending_extraction(story_id)
    chat.extract_pending()
    assert _active(store) == []
    assert _markers(store) == []


def test_extractor_retries_an_unusable_time_expression(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    dated = {
        **_report("Lately I wake at four"),
        "valid_from": {"quote": "Lately", "precision": "day"},
    }
    model = Model({"reports": [dated]}, {"reports": [_report("Lately I wake at four")]})
    chat = _session(store, model)
    chat.respond("Lately I wake at four.")
    assert chat.extract_pending() == 1
    assert "names no date" in _retries(model.extractor_messages[1])[0]
    [claim] = store.list_claims()
    assert claim.valid_from is None
