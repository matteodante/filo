"""W2.4 memory guards: relevance-ranked preferences, third-party attribution, relevant formulation
with rejected hypotheses, and the superseded-at time axis."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel
from test_chat import _has_return, _pack, _tool
from test_longitudinal_memory import _store

from filo.chat import CASE_CONTEXT_PREAMBLE, ChatSession
from filo.memory import (
    ClaimCorrection,
    ClaimFit,
    ClaimLifecycle,
    HypothesisReview,
    MemoryKind,
    MemoryStore,
    UserReport,
)

models.ALLOW_MODEL_REQUESTS = False


def _turn(store: MemoryStore, text: str, when: datetime) -> int:
    session = store.active_session() or store.start_session(when)
    return store.save_turn(session, text, "Noted.", [], when)


def _report(store: MemoryStore, text: str, when: datetime, **fields: Any):
    message_id = _turn(store, text, when)
    return store.add_user_reports(
        [UserReport(kind=MemoryKind.PATTERN, content=text, evidence_quote=text, **fields)],
        message_id,
        text,
        when,
    )[0]


def _reject(store: MemoryStore, hypothesis_id: str, when: datetime) -> None:
    text = "No, that does not fit at all"
    store.review_hypotheses(
        [HypothesisReview(memory_id=hypothesis_id, fit=ClaimFit.DOES_NOT_FIT, evidence_quote=text)],
        _turn(store, text, when),
        text,
    )


def test_process_preferences_rank_by_relevance(tmp_path) -> None:
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
    _reject(store, rejected.id, now)
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


def test_rejected_hypothesis_reaches_the_model_and_stays_reviewable(tmp_path) -> None:
    store = _store(tmp_path)
    now = datetime.now(UTC) - timedelta(hours=1)
    calls = _report(store, "I avoid phone calls with my manager", now)
    rejected = store.add_hypothesis(
        "Phone calls may feel like an exam",
        linked_claim_ids=[calls.id],
        evidence_message_ids=[calls.evidence[0].message_id],
    )
    _reject(store, rejected.id, now)
    text = "Maybe the phone call thing does fit a bit"
    seen: list[Any] = []

    async def stream(messages: list[Any], _info: Any):
        seen.append(messages)
        if _has_return(messages, "review_hypotheses"):
            yield "Thanks for coming back to it."
            return
        review = {"memory_id": rejected.id, "fit": "partly_fits", "evidence_quote": text}
        yield _tool("review_hypotheses", {"reviews": [review]})

    ChatSession(FunctionModel(stream_function=stream), _pack(), store, "en-US").respond(text)

    case_data = next(
        part.content
        for message in seen[0]
        for part in message.parts
        if isinstance(getattr(part, "content", None), str)
        and part.content.startswith(CASE_CONTEXT_PREAMBLE)
    )
    payload = json.loads(case_data[len(CASE_CONTEXT_PREAMBLE) :])
    assert [item["id"] for item in payload["rejected_hypotheses"]] == [rejected.id]
    assert next(item for item in store.list_claims() if item.id == rejected.id).fit is (
        ClaimFit.PARTLY_FITS
    )


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
