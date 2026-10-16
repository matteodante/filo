"""Wave 5: help between sessions (experiment follow-up, recap, wellbeing check)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic_ai import models
from pydantic_ai.models.function import FunctionModel

from filo.chat import CASE_CONTEXT_PREAMBLE, ChatSession
from filo.memory import InterventionState, MemoryStore
from filo.protocol import ProtocolPack

models.ALLOW_MODEL_REQUESTS = False
PACK = ProtocolPack.load(Path("protocols/transdiagnostic"))
T0 = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)


def _experiment(
    store: MemoryStore,
    *,
    when: datetime = T0,
    state: InterventionState = InterventionState.AGREED,
    description: str = "Make one two-minute call",
):
    session = store.start_session(when)
    message_id = store.save_turn(session, "yes, I will try that", "Good.", [], when)
    return store.create_intervention(
        skill="change-avoidance-behavior",
        description=description,
        state=state,
        linked_claim_ids=[],
        evidence_message_id=message_id,
        consent_quote="yes, I will try that",
        evidence_text="yes, I will try that",
        prediction="I think I will feel sick before it",
        planned_for="Monday before the team call",
        now=when,
    )


def _envelopes(store: MemoryStore, texts: list[str], now: datetime) -> list[dict[str, Any]]:
    """The case-data envelope the model received on each turn."""
    seen: list[dict[str, Any]] = []
    envelopes: list[dict[str, Any]] = []

    async def stream(messages: list[Any], _info: Any):
        for message in messages:
            for part in message.parts:
                content = str(getattr(part, "content", ""))
                if getattr(part, "part_kind", "") == "user-prompt" and content.startswith(
                    CASE_CONTEXT_PREAMBLE
                ):
                    seen.append(json.loads(content.removeprefix(CASE_CONTEXT_PREAMBLE)))
        yield "Thanks for telling me."

    session = ChatSession(FunctionModel(stream_function=stream), PACK, store, "en-US")
    for index, text in enumerate(texts):
        seen.clear()
        session.respond(text, now=now + timedelta(minutes=index))
        envelopes.append(seen[-1])
    return envelopes


def test_experiment_records_when_and_expected_outcome(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    record = _experiment(store)
    store.update_intervention(
        record.id,
        state=InterventionState.AGREED,
        evidence_message_id=1,
        consent_quote="yes",
        evidence_text="yes, Tuesday instead",
        planned_for="Tuesday instead",
        now=T0 + timedelta(hours=1),
    )

    (stored,) = store.list_interventions()
    assert stored.prediction == "I think I will feel sick before it"
    assert stored.planned_for == "Tuesday instead"
    context = store.retrieve_case_context("unrelated words", follow_up_before=_later())
    assert context.active_interventions[0].planned_for == "Tuesday instead"


def _later() -> str:
    return (T0 + timedelta(days=3)).isoformat()


def test_first_turn_of_a_later_session_lists_the_experiment_to_follow_up(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    record = _experiment(store)
    store.close_session(store.active_session(), end_reason=None, now=T0 + timedelta(hours=1))  # type: ignore[arg-type]

    first, second = _envelopes(store, ["Hi again", "Work was busy"], T0 + timedelta(days=3))

    assert first["follow_up_due"] == [record.id]
    assert first["active_interventions"][0]["planned_for"] == "Monday before the team call"
    assert second["follow_up_due"] == []
    assert "follow_up_due" in CASE_CONTEXT_PREAMBLE


def test_reported_or_new_experiments_are_not_followed_up(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    tried = _experiment(store)
    store.update_intervention(
        tried.id,
        state=InterventionState.TRIED,
        evidence_message_id=1,
        consent_quote="it went",
        evidence_text="it went fine",
        outcome="it went fine",
        now=T0 + timedelta(hours=1),
    )
    agreed_later = _experiment(store, when=T0 + timedelta(days=5), description="Send one email")

    context = store.retrieve_case_context("hello", follow_up_before=_later())

    assert context.follow_up_due == []
    assert agreed_later.id not in context.follow_up_due
    assert store.retrieve_case_context("hello").follow_up_due == []


def test_forgetting_an_experiment_clears_when_it_was_planned(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _experiment(store)

    store.delete_session(store.active_session().id)  # type: ignore[union-attr]

    (record,) = store.list_interventions()
    assert record.state is InterventionState.STOPPED
    assert record.planned_for is None
    assert record.prediction is None
