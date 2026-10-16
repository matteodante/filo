"""Retrieval engine, forgetting reach, and time continuity (W2.1, W2.2). Synthetic data only."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRequest, models
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from filo.chat import (
    CASE_CONTEXT_PREAMBLE,
    CONSOLIDATION_CHUNK_CHARS,
    ChatSession,
    _transcript_chunks,
)
from filo.memory import (
    MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL,
    ROUND_TEXT_CACHE_SIZE,
    ClaimCorrection,
    MemoryKind,
    MemoryMode,
    MemoryStore,
    RetentionPolicy,
    TimeExpression,
    TimePrecision,
    UserReport,
    time_expression_problem,
)
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False
ROOT = Path("protocols/transdiagnostic")
STORY = "my brother crashed the boat at the lake"


class CountingEmbedder:
    """Topic vectors: one axis per concept, and a shared axis for everything else."""

    concepts = (
        ("boat", "lake", "barca", "lago", "sailing"),
        ("sleep", "night", "sonno"),
        ("call", "phone", "telefon"),
    )

    def __init__(self) -> None:
        self.queries: list[str] = []
        self.documents = 0

    def _vector(self, text: str) -> list[float]:
        folded = text.casefold()
        for index, terms in enumerate(self.concepts):
            if any(term in folded for term in terms):
                return [float(position == index) for position in range(4)]
        return [0.0, 0.0, 0.0, 1.0]

    def embed_documents_sync(self, documents: list[str]) -> SimpleNamespace:
        self.documents += len(documents)
        return SimpleNamespace(embeddings=[self._vector(value) for value in documents])

    def embed_query_sync(self, query: str) -> SimpleNamespace:
        self.queries.append(query)
        return SimpleNamespace(embeddings=[self._vector(query)])


def _store(tmp_path, embedder: Any | None = None) -> MemoryStore:
    return MemoryStore(
        tmp_path,
        embedding_model="test:concept",
        embedder=embedder or CountingEmbedder(),
    )


def _pack() -> ProtocolPack:
    return ProtocolPack.load(ROOT)


def _tool(name: str, arguments: dict[str, Any]) -> dict[int, DeltaToolCall]:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(arguments), tool_call_id="call")}


def _has_return(messages: list[Any], tool: str) -> bool:
    return any(
        getattr(part, "part_kind", "") == "tool-return" and getattr(part, "tool_name", "") == tool
        for message in messages
        for part in message.parts
    )


def _visible(store: MemoryStore, query: str) -> str:
    """Everything a retrieval returns, as the model would receive it."""
    return store.retrieve_case_context(query).model_dump_json().casefold()


def _story_store(tmp_path, when: datetime) -> tuple[MemoryStore, str]:
    """A closed session whose story never became a claim, then one about something else."""
    store = _store(tmp_path)
    session = store.start_session(when)
    store.save_turn(
        session,
        f"Last summer {STORY} and nobody was hurt",
        "That sounds frightening. How was the boat afterwards?",
        [],
        when,
    )
    store.save_turn(
        session,
        "I keep thinking about the lake",
        f"You mentioned that {STORY}; it is still on your mind.",
        [],
        when + timedelta(minutes=5),
    )
    store.close_session(
        session,
        summary="A family accident during a holiday came up.",
        themes=["family accident"],
        now=when + timedelta(minutes=10),
    )
    return store, session.id


def test_rounds_beyond_the_last_thousand_user_messages_are_retrievable(tmp_path) -> None:
    embedder = CountingEmbedder()
    store = _store(tmp_path, embedder)
    start = datetime.now(UTC) - timedelta(days=900)
    session = store.start_session(start)
    with store.transaction():
        store.save_turn(
            session,
            "The greenhouse tomatoes finally ripened at night",
            "What did that mean to you?",
            [],
            start,
        )
        for index in range(1_050):
            store.save_turn(session, f"filler {index}", "ok", [], start + timedelta(minutes=index))
    # Indexing a large unindexed archive is spread across retrievals instead of one turn, and
    # the oldest round is found once indexing reaches it.
    for _ in range(1_051 // MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL + 1):
        before = embedder.documents
        context = store.retrieve_case_context("greenhouse tomatoes at night")
        assert embedder.documents - before <= MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL
    assert context.relevant_excerpts[0].quote == "The greenhouse tomatoes finally ripened at night"
    assert context.relevant_excerpts[0].assistant_reply == "What did that mean to you?"


def test_assistant_reply_is_part_of_the_indexed_round(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    store.save_turn(session, "Something happened today", "Was it on the lake again?", [])
    assert store.retrieve_case_context("sailing").relevant_excerpts[0].quote == (
        "Something happened today"
    )


def test_query_is_embedded_once_and_vectors_are_packed_and_cached(tmp_path) -> None:
    embedder = CountingEmbedder()
    store = _store(tmp_path, embedder)
    session = store.start_session()
    message_id = store.save_turn(session, "I sleep badly at night", "Since when?", [])
    store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.EVENT,
                content="I sleep badly at night",
                evidence_quote="I sleep badly at night",
            )
        ],
        message_id,
        "I sleep badly at night",
    )
    store.close_session(session, summary="Sleep came up.")
    store.retrieve_case_context("my night was bad")
    assert len(embedder.queries) == 1
    indexed = embedder.documents
    store.retrieve_case_context("my night was bad")
    assert embedder.documents == indexed
    # A fresh process rebuilds its cache from the packed float32 index, not the embedder.
    rebuilt = _store(tmp_path, embedder)
    rebuilt.retrieve_case_context("my night was bad")
    assert embedder.documents == indexed
    with store._connect() as database:
        rows = database.execute("SELECT dimensions, payload FROM semantic_index").fetchall()
    for dimensions, payload in rows:
        assert len(store._decrypt(payload)) == dimensions * 4


def test_an_unpacked_legacy_vector_is_rebuilt(tmp_path) -> None:
    embedder = CountingEmbedder()
    store = _store(tmp_path, embedder)
    store.save_turn(store.start_session(), "I sleep badly at night", "Since when?", [])
    store.retrieve_case_context("night")
    with store._connect() as database:
        database.execute(
            "UPDATE semantic_index SET payload = ?",
            (store._encrypt_json({"vector": [0.0, 1.0, 0.0, 0.0]}),),
        )
    before = embedder.documents
    assert _store(tmp_path, embedder).retrieve_case_context("night").relevant_excerpts
    assert embedder.documents == before + 1


def test_semantic_floor_filters_unrelated_items(tmp_path) -> None:
    class Fixed(CountingEmbedder):
        def _vector(self, text: str) -> list[float]:
            # Every document sits at cosine 0.3 from the query: above the old 0.20 floor.
            if "query" in text:
                return [1.0, 0.0]
            return [0.3, (1 - 0.09) ** 0.5]

    store = _store(tmp_path, Fixed())
    store.save_turn(store.start_session(), "I repainted the fence", "How did it go?", [])
    assert store.retrieve_case_context("query about something").relevant_excerpts == []


def test_envelope_query_includes_recent_user_turns(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    store.save_turn(session, "My sister Giulia called", "What did she say?", [])
    chat = ChatSession(FunctionModel(lambda *_: None), _pack(), store, "en-US")
    query = chat._retrieval_query(session, "And what about her?")
    assert query.endswith("And what about her?")
    assert "My sister Giulia called" in query
    assert chat._retrieval_query(session, "x" * 3_000) == "x" * 3_000


def test_forgetting_a_claim_reaches_every_round_and_summary_that_carried_it(tmp_path) -> None:
    when = datetime.now(UTC) - timedelta(days=30)
    store, session_id = _story_store(tmp_path, when)
    first = store.list_sessions()[0]
    message_id = store.export()["messages"][0]["id"]
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=STORY, evidence_quote=STORY)],
        message_id,
        f"Last summer {STORY} and nobody was hurt",
        when,
    )[0]
    assert first.summary
    store.forget_claim(claim.id)
    for query in (STORY, "boat lake", "the accident with my brother"):
        assert "boat" not in _visible(store, query)
    assert store.consolidation_rounds(session_id) == []
    closed = next(item for item in store.list_sessions() if item.id == session_id)
    assert closed.summary == "" and closed.themes == []
    with store._connect() as database:
        assert not database.execute(
            "SELECT 1 FROM semantic_index WHERE entity_type = 'round'"
        ).fetchall()


def test_correction_suppresses_later_rounds_repeating_the_superseded_wording(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    old = "I drink coffee at midnight every day"
    first = store.save_turn(session, old, "Noted.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.FACT, content=old, evidence_quote=old)], first, old
    )[0]
    store.save_turn(session, "Anyway", f"You said: {old}.", [])
    correction = "No, I only drink tea"
    second = store.save_turn(session, correction, "Thanks for correcting me.", [])
    store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id,
            correction_quote=correction,
            replacement_quote="I only drink tea",
        ),
        second,
        correction,
    )
    assert "midnight" not in _visible(store, "coffee at midnight")


def _forget_turn(store: MemoryStore, mode: MemoryMode, request: str, quote: str) -> str:
    async def stream(messages: list[Any], _info: Any):
        if _has_return(messages, "forget_content"):
            yield "Done. I will not bring that up again."
        else:
            yield _tool(
                "forget_content",
                {"request_quote": "please forget", "content_quotes": [quote]},
            )

    chat = ChatSession(
        FunctionModel(stream_function=stream), _pack(), store, "en-US", memory_mode=mode
    )
    return chat.respond(request).text


@pytest.mark.parametrize("mode", [MemoryMode.STANDARD, MemoryMode.TRANSCRIPT_ONLY])
def test_in_chat_forget_request_suppresses_content_that_never_became_a_claim(
    tmp_path, mode: MemoryMode
) -> None:
    when = datetime.now(UTC) - timedelta(days=3)
    store, session_id = _story_store(tmp_path, when)
    assert "boat" in _visible(store, "boat lake")
    _forget_turn(store, mode, "Please forget what I said about the boat.", STORY)
    for query in (STORY, "boat lake", "the accident with my brother", "hi"):
        assert "boat" not in _visible(store, query)
    closed = next(item for item in store.list_sessions() if item.id == session_id)
    assert closed.summary == ""
    active = store.active_session()
    assert active is not None
    assert all("boat" not in text for _, text, _ in store.consolidation_rounds(active.id))
    # Suppression, not deletion: the archive is kept until the user deletes it locally.
    assert any(STORY in item["content"] for item in store.export()["messages"])


def test_forget_content_archives_claims_built_on_the_quote(tmp_path) -> None:
    when = datetime.now(UTC) - timedelta(days=3)
    store, _ = _story_store(tmp_path, when)
    message_id = store.export()["messages"][0]["id"]
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=STORY, evidence_quote=STORY)],
        message_id,
        f"Last summer {STORY} and nobody was hurt",
    )[0]
    _forget_turn(store, MemoryMode.STANDARD, "Please forget the boat story.", STORY)
    assert claim.id not in {item.id for item in store.list_claims()}


def test_forget_content_rejects_a_refusal_an_unknown_quote_and_a_fragment(tmp_path) -> None:
    store, _ = _story_store(tmp_path, datetime.now(UTC) - timedelta(days=1))
    session = store.active_session() or store.start_session()
    text = "Do not forget it. Please forget the boat story"
    message_id = store.save_turn(session, text, "Okay.", [])
    with pytest.raises(ValueError, match="negation"):
        store.forget_content([STORY], "forget it", message_id, text)
    with pytest.raises(ValueError, match="earlier user message"):
        store.forget_content(["a story never told here"], "please forget", message_id, text)
    with pytest.raises(ValueError, match="at least"):
        store.forget_content(["the boat"], "please forget", message_id, text)


def test_long_gap_is_measured_from_the_latest_session_not_the_most_relevant(tmp_path) -> None:
    store = _store(tmp_path)
    old = datetime.now(UTC) - timedelta(days=90)
    first = store.start_session(old)
    store.save_turn(first, "Phone calls with my manager scare me", "Tell me more.", [], old)
    store.close_session(first, summary="Work phone calls were hard.", now=old)
    yesterday = datetime.now(UTC) - timedelta(days=1)
    second = store.start_session(yesterday)
    store.save_turn(second, "I baked bread", "Nice.", [], yesterday)
    store.close_session(second, summary="The user baked bread.", now=yesterday)
    captured: list[Any] = []

    async def stream(messages: list[Any], _info: Any):
        captured.extend(messages)
        yield "Good to hear from you."

    ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(
        "I have to call my manager today"
    )
    instructions = "\n".join(
        str(getattr(message, "instructions", "") or "")
        for message in captured
        if isinstance(message, ModelRequest)
    )
    assert "long gap" not in instructions
    envelope = next(
        json.loads(part.content.removeprefix(CASE_CONTEXT_PREAMBLE))
        for message in captured
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part.content, str) and part.content.startswith(CASE_CONTEXT_PREAMBLE)
    )
    assert envelope["latest_session"]["summary"] == "The user baked bread."
    assert envelope["elapsed_since_latest_session"] == "1 day"
    assert envelope["today"] == datetime.now(UTC).date().isoformat()


def test_claims_carry_observation_time_and_only_a_quoted_valid_from(tmp_path) -> None:
    store = _store(tmp_path)
    when = datetime(2026, 5, 4, 10, tzinfo=UTC)
    text = "Since March 2026 I wake at four"
    message_id = store.save_turn(store.start_session(when), text, "That is early.", [], when)
    claim = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.PATTERN,
                content="I wake at four",
                evidence_quote="Since March 2026 I wake at four",
                valid_from=TimeExpression(
                    quote="Since March 2026", precision=TimePrecision.MONTH, value="2026-03"
                ),
            )
        ],
        message_id,
        text,
        when,
    )[0]
    assert claim.observed_at == when.isoformat()
    assert claim.valid_from is not None and claim.valid_from.value == "2026-03"
    context = store.retrieve_case_context("I wake at four")
    assert context.user_reports[0].valid_from == claim.valid_from
    assert context.user_reports[0].observed_at == when.isoformat()
    with pytest.raises(ValueError, match="exact span"):
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.PATTERN,
                    content="I wake at four",
                    evidence_quote="I wake at four",
                    valid_from=TimeExpression(quote="lately", precision=TimePrecision.MONTH),
                )
            ],
            message_id,
            text,
        )
    correction = "Actually I wake at five"
    later = when + timedelta(days=2)
    second = store.save_turn(store.start_session(later), correction, "Thanks.", [], later)
    corrected = store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id,
            correction_quote=correction,
            replacement_quote="I wake at five",
        ),
        second,
        correction,
    )
    assert corrected.valid_from is None
    assert corrected.observed_at == later.isoformat()


@pytest.mark.parametrize(
    ("quote", "precision", "value"),
    [
        ("last spring", TimePrecision.SEASON, "2026-03-21"),
        ("in 2024", TimePrecision.YEAR, "2025"),
        ("on 2026-02-30", TimePrecision.DAY, "2026-02-30"),
    ],
)
def test_time_value_cannot_exceed_or_contradict_its_expression(
    quote: str, precision: TimePrecision, value: str
) -> None:
    with pytest.raises(ValidationError):
        TimeExpression(quote=quote, precision=precision, value=value)


def _risk_session(store: MemoryStore, when: datetime) -> str:
    text = "I thought about stepping off the bridge on Friday"
    session = store.start_session(when)
    message_id = store.save_turn(session, text, "I am glad you told me.", [], when)
    store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text)],
        message_id,
        text,
        when,
    )
    store.close_session(
        session,
        summary="Safety came up.",
        risk_notes=["stepping off the bridge on Friday; agreed to call a friend"],
        now=when,
    )
    return session.id


def _risk_notes(store: MemoryStore, session_id: str) -> list[str]:
    return next(item.risk_notes for item in store.list_sessions() if item.id == session_id)


def test_risk_notes_reach_the_envelope_and_follow_correction(tmp_path) -> None:
    store = _store(tmp_path)
    session_id = _risk_session(store, datetime.now(UTC) - timedelta(days=1))
    context = store.retrieve_case_context("hi")
    assert context.latest_session is not None
    assert context.latest_session.risk_notes
    claim = store.list_claims()[0]
    correction = "No, it was the river path, not the bridge"
    message_id = store.save_turn(store.start_session(), correction, "Thanks.", [])
    store.correct_claim(
        ClaimCorrection(memory_id=claim.id, correction_quote=correction),
        message_id,
        correction,
    )
    assert _risk_notes(store, session_id) == []


def test_risk_notes_follow_forgetting(tmp_path) -> None:
    store = _store(tmp_path)
    session_id = _risk_session(store, datetime.now(UTC) - timedelta(days=1))
    store.forget_claim(store.list_claims()[0].id)
    assert _risk_notes(store, session_id) == []


def test_risk_notes_follow_retention(tmp_path) -> None:
    store = _store(tmp_path)
    session_id = _risk_session(store, datetime.now(UTC) - timedelta(days=40))
    store.apply_retention(RetentionPolicy(session_summary_days=30))
    assert _risk_notes(store, session_id) == []


def test_consolidation_covers_the_whole_session_in_dated_parts(tmp_path) -> None:
    store = _store(tmp_path)
    start = datetime(2026, 9, 1, 9, tzinfo=UTC)
    session = store.start_session(start)
    store.save_turn(session, "The opening topic was the boat", "Go on.", [], start)
    padding = "x" * 1_000
    for index in range(40):
        store.save_turn(
            session, f"{padding} {index}", "ok", [], start + timedelta(minutes=index + 1)
        )
    prompts: list[str] = []
    case_data: list[dict[str, Any]] = []

    async def stream(messages: list[Any], _info: Any):
        for message in messages:
            for part in message.parts:
                content = getattr(part, "content", None)
                if not isinstance(content, str):
                    continue
                if content.startswith(CASE_CONTEXT_PREAMBLE):
                    case_data.append(json.loads(content.removeprefix(CASE_CONTEXT_PREAMBLE)))
                else:
                    prompts.append(content)
        yield _tool(
            "final_result",
            {"summary": f"Part {len(prompts)}", "risk_notes": ["none discussed"]},
        )

    chat = ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US")
    closed = chat.end(start + timedelta(hours=2))
    assert closed is not None
    assert len(prompts) >= 2
    assert "The opening topic was the boat" in prompts[0]
    assert "[2026-09-01 09:00 UTC]" in prompts[0]
    assert "reflection_so_far" not in case_data[0]
    assert case_data[1]["reflection_so_far"]["summary"] == "Part 1"
    assert closed.summary == f"Part {len(prompts)}"
    assert closed.risk_notes == ["none discussed"]


def test_transcript_chunks_keep_whole_rounds_and_a_bounded_count() -> None:
    rounds = [("2026-01-01T00:00:00+00:00", "u" * 5_000, "a" * 5_000)] * 100
    chunks = _transcript_chunks(rounds)
    assert len(chunks) <= 8
    assert all(chunk.count("user:") == chunk.count("assistant:") for chunk in chunks)
    assert sum(chunk.count("user:") for chunk in chunks) == 100
    small = _transcript_chunks([("2026-01-01T00:00:00+00:00", "hi", "hello")])
    assert len(small) == 1 and len(small[0]) < CONSOLIDATION_CHUNK_CHARS


def test_forgetting_in_the_open_session_withholds_its_reflection(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    store.save_turn(session, f"Last summer {STORY}", "That sounds frightening.", [])
    # Paraphrases the story without three quarters of its words, so no wording matches it.
    store.save_turn(session, "The family holiday went badly", "What happened?", [])
    calls: list[int] = []

    async def stream(messages: list[Any], info: Any):
        if info.output_tools:
            calls.append(1)
            yield _tool("final_result", {"summary": "A family holiday went badly."})
        elif _has_return(messages, "forget_content"):
            yield "Done."
        else:
            yield _tool(
                "forget_content", {"request_quote": "please forget", "content_quotes": [STORY]}
            )

    chat = ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US")
    chat.respond("Please forget the boat story.")
    closed = chat.end()
    assert closed is not None and closed.summary == "" and calls == []
    assert store.retrieve_case_context("hi").latest_session.summary == ""  # type: ignore[union-attr]


def test_correction_clears_the_reflection_of_sessions_holding_the_old_wording(tmp_path) -> None:
    store = _store(tmp_path)
    old = "I drink coffee at midnight every day"
    first_session = store.start_session(datetime.now(UTC) - timedelta(days=2))
    first = store.save_turn(first_session, old, "Noted.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.FACT, content=old, evidence_quote=old)], first, old
    )[0]
    store.close_session(first_session, summary="Late caffeine is a habit.", themes=["caffeine"])
    correction = "No, I only drink tea"
    second = store.save_turn(store.start_session(), correction, "Thanks.", [])
    store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id, correction_quote=correction, replacement_quote="I only drink tea"
        ),
        second,
        correction,
    )
    closed = next(item for item in store.list_sessions() if item.id == first_session.id)
    assert closed.summary == "" and closed.themes == []


def test_legacy_message_vectors_are_deleted_on_open(tmp_path) -> None:
    store = _store(tmp_path)
    with store._connect() as database:
        database.execute(
            "INSERT INTO semantic_index(entity_type, entity_id, model, content_hash, dimensions, "
            "payload) VALUES ('message', '1', 'test:concept', 'h', 1, ?)",
            (store._cipher.encrypt(b"x"),),
        )
    reopened = _store(tmp_path)
    with reopened._connect() as database:
        assert not database.execute(
            "SELECT 1 FROM semantic_index WHERE entity_type = 'message'"
        ).fetchall()


MESSAGE_TIME = datetime(2026, 10, 16, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("quote", "precision", "value", "problem"),
    [
        ("lately", TimePrecision.MONTH, None, "names no date"),
        ("last spring", TimePrecision.DAY, None, "finer than the quote"),
        ("last spring", TimePrecision.SEASON, "2019-spring", "message date"),
        ("last spring", TimePrecision.SEASON, "2026-summer", "season"),
        ("in March", TimePrecision.MONTH, "2026-04", "month"),
        ("yesterday", TimePrecision.DAY, "2026-10-10", "message date"),
        ("in December", TimePrecision.MONTH, "2026-12", "after the message"),
        ("two years ago", TimePrecision.YEAR, "2023", "names no date"),
        ("2 years ago", TimePrecision.YEAR, "2023", "message date"),
    ],
)
def test_time_expression_cannot_claim_more_than_its_quote(
    quote: str, precision: TimePrecision, value: str | None, problem: str
) -> None:
    expression = TimeExpression(quote=quote, precision=precision, value=value)
    found = time_expression_problem(expression, f"It started {quote}.", MESSAGE_TIME)
    assert found is not None and problem in found


@pytest.mark.parametrize(
    ("quote", "precision", "value"),
    [
        ("last spring", TimePrecision.SEASON, "2026-spring"),
        ("last spring", TimePrecision.YEAR, None),
        ("in March", TimePrecision.MONTH, "2026-03"),
        ("a marzo", TimePrecision.MONTH, "2025-03"),
        ("yesterday", TimePrecision.DAY, "2026-10-15"),
        ("since 2019", TimePrecision.YEAR, "2019"),
        ("2 years ago", TimePrecision.YEAR, "2024"),
        ("l'estate scorsa", TimePrecision.SEASON, "2026-summer"),
    ],
)
def test_time_expression_resolves_only_what_it_says(
    quote: str, precision: TimePrecision, value: str | None
) -> None:
    expression = TimeExpression(quote=quote, precision=precision, value=value)
    assert time_expression_problem(expression, f"It started {quote}.", MESSAGE_TIME) is None


def test_record_user_reports_retries_an_unusable_time_instead_of_losing_the_turn(
    tmp_path,
) -> None:
    text = "Lately I wake at four"
    attempts: list[dict[str, Any]] = []

    async def stream(messages: list[Any], _info: Any):
        if _has_return(messages, "record_user_reports"):
            yield "Noted."
            return
        report: dict[str, Any] = {
            "kind": "pattern",
            "content": "I wake at four",
            "evidence_quote": "Lately I wake at four",
        }
        if not attempts:
            report["valid_from"] = {"quote": "Lately", "precision": "month"}
        attempts.append(report)
        yield _tool("record_user_reports", {"reports": [report]})

    store = _store(tmp_path)
    ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(text)
    assert len(attempts) == 2
    [claim] = store.list_claims()
    assert claim.content == "I wake at four" and claim.valid_from is None


def test_a_rolled_back_round_is_never_served_from_the_cache(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    with pytest.raises(RuntimeError), store.transaction():
        store.save_turn(session, "I sleep in the boat at night", "Rolled back.", [])
        store.retrieve_case_context("boat at night")
        raise RuntimeError("abort")
    store.save_turn(session, "I sleep badly at night", "Since when?", [])
    excerpt = store.retrieve_case_context("sleep at night").relevant_excerpts[0]
    assert excerpt.quote == "I sleep badly at night"


def test_process_memory_stays_bounded_and_vectors_update_in_place(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session()
    with store.transaction():
        ids = [
            store.save_turn(session, f"night {index} sleep note", "ok", [])
            for index in range(ROUND_TEXT_CACHE_SIZE + 200)
        ]
    for _ in range(len(ids) // MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL + 1):
        store.retrieve_case_context("sleep at night")
    table = store._vector_tables["round"]
    assert len(table.ids) == len(ids)
    matrix = table.matrix
    assert len(store._round_cache) <= ROUND_TEXT_CACHE_SIZE
    store._suppress_rounds({ids[0]})
    store.retrieve_case_context("sleep at night")
    assert str(ids[0]) not in table.rows and len(table.ids) == len(ids) - 1
    # The suppressed row was swapped out of the same matrix, not rebuilt from scratch.
    assert table.matrix is matrix
