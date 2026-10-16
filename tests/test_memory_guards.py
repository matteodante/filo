"""W2.4 memory guards: relevance-ranked preferences, third-party attribution, relevant formulation,
the consented safety record with its full lifecycle, and the superseded-at time axis."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel
from test_chat import _pack, _tool
from test_longitudinal_memory import _store

from filo.chat import ChatSession, MemoryWriting
from filo.cli import _chat_command, main
from filo.memory import (
    PRECEDENCE_NOTE,
    ClaimCorrection,
    ClaimFit,
    ClaimLifecycle,
    HypothesisReview,
    MemoryKind,
    MemoryStore,
    RetentionPolicy,
    UserReport,
)

models.ALLOW_MODEL_REQUESTS = False

DANGER = "My partner hit me last night. I will stay with my sister Anna tonight."
CONSENT = "Yes, you can keep a note of that."
EPISODE = {
    "protective_action": "I will stay with my sister Anna tonight",
    "people_named": ["My partner", "my sister Anna"],
    "consent_quote": "you can keep a note of that",
}


def _turn(store: MemoryStore, text: str, when: datetime) -> int:
    session = store.active_session() or store.start_session(when)
    return store.save_turn(session, text, "Noted.", [], when)


def _report(store: MemoryStore, text: str, when: datetime, **fields: Any):
    message_id = _turn(store, text, when)
    return store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text, **fields)],
        message_id,
        text,
        when,
    )[0]


def _respond(store: MemoryStore, text: str, calls: list[tuple[str, dict]], **kwargs: Any):
    seen: list[Any] = []

    async def stream(messages: list[Any], _info: Any):
        seen.append(messages)
        # Only this turn's calls count; earlier turns of the session sit in the history too.
        start = max(
            index
            for index, message in enumerate(messages)
            for part in message.parts
            if getattr(part, "content", None) == text
        )
        answered = {
            getattr(part, "tool_name", None)
            for message in messages[start:]
            for part in message.parts
            if getattr(part, "part_kind", "") in {"tool-return", "retry-prompt"}
        }
        for index, (name, arguments) in enumerate(calls):
            if name not in answered:
                yield _tool(name, arguments, f"call-{index}")
                return
        yield "I'm here."

    session = ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US", **kwargs)
    session.respond(text)
    return session, seen


def _keep_episode(store: MemoryStore) -> str:
    _respond(store, DANGER, [])
    _respond(store, CONSENT, [("record_safety_episode", {"episode": EPISODE})])
    [episode] = store.list_safety_episodes()
    return episode.id


def _retries(seen: list[Any]) -> list[str]:
    return [
        str(part.content)
        for part in seen[-1][-1].parts
        if getattr(part, "part_kind", "") == "retry-prompt"
    ]


def _context_payload(messages: list[Any]) -> dict[str, Any]:
    for message in messages:
        for part in message.parts:
            content = getattr(part, "content", None)
            if isinstance(content, str) and '"precedence"' in content:
                return json.loads(content[content.index("{") :])
    raise AssertionError("No case-data message.")


def test_process_preferences_rank_by_relevance_and_yield_to_safety(tmp_path) -> None:
    store = _store(tmp_path)
    start = datetime.now(UTC) - timedelta(days=3)
    for offset, text in enumerate(
        (
            "When we talk about my sleep, keep it short",
            "Use plain words when we talk about work",
        )
    ):
        message_id = _turn(store, text, start + timedelta(hours=offset))
        store.record_process_preference(
            text, text, message_id, text, start + timedelta(hours=offset)
        )

    context = store.retrieve_case_context("I could not sleep again last night")

    # The newer preference would come first by recency; the relevant one comes first now.
    assert context.process_preferences[0].content.startswith("When we talk")
    assert context.precedence == PRECEDENCE_NOTE
    assert "outranks process_preferences" in context.precedence


def test_report_about_another_person_stays_attributed_to_the_user(tmp_path) -> None:
    store = _store(tmp_path)
    claim = _report(
        store, "My neighbour follows me home at night", datetime.now(UTC), about_others=True
    )
    store.save_formulation_links({"presenting_concerns": [claim.id]})

    assert store.load_formulation().presenting_concerns == [
        "User's account: My neighbour follows me home at night"
    ]
    context = store.retrieve_case_context("I am afraid to walk home at night")
    [found] = context.user_reports
    assert found.about_others is True
    assert found.formulation_fields == ["presenting_concerns"]
    assert "about_others=true" in context.precedence


def test_formulation_and_rejected_hypotheses_arrive_only_when_relevant(tmp_path) -> None:
    store = _store(tmp_path)
    now = datetime.now(UTC)
    calls = _report(store, "I avoid phone calls with my manager", now - timedelta(days=2))
    sleep = _report(store, "I wake at night and cannot fall asleep", now - timedelta(days=1))
    rejected = store.add_hypothesis(
        "Phone calls may feel like an exam",
        linked_claim_ids=[calls.id],
        evidence_message_ids=[calls.evidence[0].message_id],
    )
    review_id = _turn(store, "No, that does not fit at all", now)
    store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=rejected.id,
                fit=ClaimFit.DOES_NOT_FIT,
                evidence_quote="No, that does not fit at all",
            )
        ],
        review_id,
        "No, that does not fit at all",
    )
    store.save_formulation_links(
        {"situations_and_triggers": [calls.id], "emotions_and_body": [sleep.id]}
    )

    about_calls = store.retrieve_case_context("Tomorrow I have a phone call")
    assert [item.id for item in about_calls.rejected_hypotheses] == [rejected.id]
    assert rejected.id not in {item.id for item in about_calls.hypotheses}
    fields = {item.id: item.formulation_fields for item in about_calls.user_reports}
    assert fields == {calls.id: ["situations_and_triggers"]}

    about_sleep = store.retrieve_case_context("Another night without sleep")
    assert about_sleep.rejected_hypotheses == []
    assert {item.id for item in about_sleep.user_reports} == {sleep.id}


def test_consented_safety_record_prompts_one_check_in_next_session(tmp_path) -> None:
    store = _store(tmp_path)
    episode_id = _keep_episode(store)
    [episode] = store.list_safety_episodes()
    assert episode.protective_action == EPISODE["protective_action"]
    assert episode.people_named == EPISODE["people_named"]
    # The details came from the danger turn, the agreement from the turn after it.
    assert len({episode.consent.message_id, *(ref.message_id for ref in episode.evidence)}) == 2
    assert episode.check_in_pending

    # The same session never checks in on its own record.
    _, seen = _respond(store, "Thanks.", [])
    assert _context_payload(seen[0])["safety_check_in"] is None

    store.close_session(store.active_session())  # type: ignore[arg-type]
    _, seen = _respond(store, "Hi again.", [])
    payload = _context_payload(seen[0])
    assert payload["safety_check_in"]["id"] == episode_id
    assert "safety_check_in" in seen[0][-1].instructions
    assert not store.list_safety_episodes()[0].check_in_pending

    _, seen = _respond(store, "Something else today.", [])
    assert _context_payload(seen[0])["safety_check_in"] is None
    store.close_session(store.active_session())  # type: ignore[arg-type]
    _, seen = _respond(store, "A new week.", [])
    assert _context_payload(seen[0])["safety_check_in"] is None


def test_safety_record_requires_current_consent_and_session_wording(tmp_path) -> None:
    store = _store(tmp_path)
    _respond(store, DANGER, [])
    _, seen = _respond(
        store,
        "I do not want you to keep a note of that.",
        [
            (
                "record_safety_episode",
                {"episode": {**EPISODE, "consent_quote": "you to keep a note of that"}},
            )
        ],
    )
    assert "a refusal is not consent" in _retries(seen)[0]
    _, seen = _respond(
        store,
        CONSENT,
        [("record_safety_episode", {"episode": {**EPISODE, "people_named": ["my brother"]}})],
    )
    assert "'my brother' is not exact wording" in _retries(seen)[0]
    assert store.list_safety_episodes() == []


def test_no_safety_record_for_a_user_who_said_they_are_under_18(tmp_path) -> None:
    store = _store(tmp_path)
    split = {"memory_writing": MemoryWriting.SPLIT}
    _respond(store, DANGER, [])
    _respond(store, "I am 15, by the way.", [("record_user_under_18", {})], **split)
    _, seen = _respond(store, CONSENT, [("record_safety_episode", {"episode": EPISODE})], **split)
    assert "under 18" in _retries(seen)[0]
    assert store.list_safety_episodes() == []

    # A record kept before the user gave their age is removed once they do.
    other = _store(tmp_path / "other")
    _keep_episode(other)
    other.withhold_user_reports(other.list_safety_episodes()[0].session_id)
    assert other.list_safety_episodes() == []


def test_safety_record_can_be_inspected_corrected_and_forgotten(tmp_path, capsys) -> None:
    store = _store(tmp_path)
    episode_id = _keep_episode(store)
    data = ["--data-dir", str(tmp_path)]

    assert main([*data, "memory", "safety"]) == 0
    assert EPISODE["protective_action"] in capsys.readouterr().out
    assert _chat_command("/safety", None, store) is True  # type: ignore[arg-type]
    assert episode_id in capsys.readouterr().out

    assert main([*data, "memory", "correct", episode_id, "I went to my friend Marta"]) == 0
    [corrected] = store.list_safety_episodes()
    assert corrected.protective_action == "I went to my friend Marta"
    assert EPISODE["protective_action"] not in json.dumps(corrected.model_dump())

    assert main([*data, "memory", "forget", episode_id]) == 0
    assert store.list_safety_episodes() == []
    store.start_session()
    assert store.safety_check_in("another-session") is None


def test_forgetting_in_conversation_removes_the_safety_record(tmp_path) -> None:
    store = _store(tmp_path)
    _keep_episode(store)
    _respond(
        store,
        "Please forget what I said about my partner.",
        [
            (
                "forget_content",
                {
                    "request_quote": "Please forget what I said",
                    "content_quotes": ["My partner hit me last night"],
                },
            )
        ],
    )
    assert store.list_safety_episodes() == []


def test_safety_record_is_exported_and_removed_by_every_deletion_path(tmp_path) -> None:
    store = _store(tmp_path)
    _keep_episode(store)
    export = store.export()
    assert export["safety_episodes"][0]["protective_action"] == EPISODE["protective_action"]
    assert EPISODE["protective_action"].encode() not in store.database_path.read_bytes()

    store.delete_all()
    assert store.list_safety_episodes() == []

    store = _store(tmp_path / "session")
    _keep_episode(store)
    assert store.delete_session(store.list_safety_episodes()[0].session_id)
    assert store.list_safety_episodes() == []

    store = _store(tmp_path / "retention")
    _keep_episode(store)
    store.apply_retention(
        RetentionPolicy(raw_message_days=1), now=datetime.now(UTC) + timedelta(days=2)
    )
    assert store.list_safety_episodes() == []


def test_correction_records_when_a_claim_stopped_holding(tmp_path) -> None:
    store = _store(tmp_path)
    start = datetime.now(UTC) - timedelta(days=30)
    moved = _report(store, "I live in Turin", start)
    job = _report(store, "I work at the bakery", start)
    corrected_at = start + timedelta(days=10)
    message_id = _turn(store, "Actually I live in Milan.", corrected_at)
    store.correct_claim(
        ClaimCorrection(
            memory_id=moved.id,
            correction_quote="Actually I live in Milan.",
            replacement_quote="I live in Milan.",
        ),
        message_id,
        "Actually I live in Milan.",
        corrected_at,
    )
    quit_at = start + timedelta(days=20)
    message_id = _turn(store, "I no longer work there.", quit_at)
    store.correct_claim(
        ClaimCorrection(memory_id=job.id, correction_quote="I no longer work there."),
        message_id,
        "I no longer work there.",
        quit_at,
    )

    replaced = next(item for item in store.list_claims() if item.id == moved.id)
    assert replaced.superseded_at == corrected_at.isoformat()
    ended = next(item for item in store.list_claims(include_inactive=True) if item.id == job.id)
    assert ended.lifecycle is ClaimLifecycle.SUPERSEDED
    assert ended.superseded_at == quit_at.isoformat()

    def held(moment: datetime) -> set[str]:
        return {item.content for item in store.claims_as_of(moment)}

    assert held(start + timedelta(days=5)) == {"I work at the bakery"}
    assert held(start + timedelta(days=15)) == {"I live in Milan.", "I work at the bakery"}
    assert held(start + timedelta(days=25)) == {"I live in Milan."}


def test_new_table_reaches_an_existing_store(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    with store._connect() as database:
        database.execute("DROP TABLE safety_episodes")
    assert MemoryStore(tmp_path).list_safety_episodes() == []
