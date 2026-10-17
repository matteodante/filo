"""Bounded longitudinal retrieval scenarios using synthetic data only."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from filo.chat import MAX_CONTEXT_CHARS, _bounded_context_json
from filo.memory import (
    ClaimCorrection,
    ClaimFit,
    HypothesisReview,
    InterventionDecision,
    InterventionState,
    MemoryKind,
    MemoryStore,
    UserReport,
)


class ConceptEmbedder:
    @staticmethod
    def _vector(text: str) -> list[float]:
        text = text.casefold()
        concepts = (
            ("phone", "call", "telefon", "chiamat"),
            ("sleep", "night", "sonno", "notte"),
            ("support", "psychologist", "aiuto", "psicolog"),
        )
        for index, terms in enumerate(concepts):
            if any(term in text for term in terms):
                return [float(position == index) for position in range(4)]
        return [0.0, 0.0, 0.0, 1.0]

    def embed_documents_sync(self, documents: list[str]) -> SimpleNamespace:
        return SimpleNamespace(embeddings=[self._vector(value) for value in documents])

    def embed_query_sync(self, query: str) -> SimpleNamespace:
        return SimpleNamespace(embeddings=[self._vector(query)])


def _store(tmp_path) -> MemoryStore:
    return MemoryStore(
        tmp_path,
        embedding_model="test:concept",
        embedder=ConceptEmbedder(),  # type: ignore[arg-type]
    )


def _report(store: MemoryStore, text: str, when: datetime):
    session = store.active_session() or store.start_session(when)
    message_id = store.save_turn(session, text, "Noted.", [], when)
    return store.add_user_reports(
        [UserReport(kind=MemoryKind.PATTERN, content=text, evidence_quote=text)],
        message_id,
        text,
        when,
    )[0]


def test_old_relevant_claim_beats_recent_distractor(tmp_path) -> None:
    store = _store(tmp_path)
    old = datetime.now(UTC) - timedelta(days=300)
    relevant = _report(store, "Phone calls with my manager feel difficult", old)
    _report(store, "I bought a green notebook today", datetime.now(UTC))
    context = store.retrieve_case_context("I need to make a work call")
    assert context.user_reports[0].id == relevant.id


def test_accepted_focus_pending_intervention_and_process_preference_are_pinned(tmp_path) -> None:
    store = _store(tmp_path)
    now = datetime.now(UTC)
    claim = _report(store, "Work calls are difficult", now)
    store.set_focus(
        "Understand work-call avoidance",
        accepted=True,
        evidence_quote="Understand work-call avoidance",
        evidence_text="Understand work-call avoidance",
    )
    store.save_formulation_links({"presenting_concerns": [claim.id]})
    store.record_process_preference(
        "I want reflection before advice",
        "I want reflection before advice",
        4,
        "I want reflection before advice",
    )
    intervention = store.create_intervention(
        skill="take-a-small-step",
        description="One two-minute call",
        state=InterventionState.AGREED,
        linked_claim_ids=[claim.id],
        evidence_message_id=5,
        consent_quote="yes",
        evidence_text="yes, let us try that",
    )
    state = store.load_app_state()
    state.pending_intervention_id = intervention.id
    store.save_app_state(state)
    context = store.retrieve_case_context("something unrelated")
    assert context.accepted_focus == "Understand work-call avoidance"
    assert context.process_preferences[0].content == "I want reflection before advice"
    assert context.active_interventions[0].id == intervention.id


def test_unwanted_effect_and_support_choice_are_retrievable(tmp_path) -> None:
    store = _store(tmp_path)
    intervention = store.create_intervention(
        skill="increase-psychological-flexibility",
        description="Brief grounding",
        state=InterventionState.AGREED,
        linked_claim_ids=[],
        evidence_message_id=1,
        consent_quote="yes",
        evidence_text="yes, let us try that",
    )
    store.update_intervention(
        intervention.id,
        state=InterventionState.TRIED,
        evidence_message_id=2,
        unwanted_effects="Grounding increased panic",
        decision=InterventionDecision.STOP,
    )
    store.record_support_choice(
        "I want to speak with a psychologist",
        "I want to speak with a psychologist",
        3,
        "I want to speak with a psychologist",
    )
    context = store.retrieve_case_context("panic and support")
    assert context.active_interventions[0].unwanted_effects == "Grounding increased panic"
    assert context.support_choices[0].content == "I want to speak with a psychologist"


def test_correction_removes_superseded_wording_from_context(tmp_path) -> None:
    store = _store(tmp_path)
    now = datetime.now(UTC)
    item = _report(store, "I live in Rome", now)
    text = "That is wrong; I live in Turin"
    message_id = store.save_turn(store.active_session(), text, "Thanks", [], now)  # type: ignore[arg-type]
    store.correct_claim(
        ClaimCorrection(
            memory_id=item.id,
            correction_quote="That is wrong",
            replacement_quote="I live in Turin",
        ),
        message_id,
        text,
    )
    serialized = store.retrieve_case_context("where I live").model_dump_json()
    assert "I live in Turin" in serialized
    assert "I live in Rome" not in serialized


def test_forgotten_claim_is_absent_from_context_and_excerpts(tmp_path) -> None:
    store = _store(tmp_path)
    item = _report(store, "A forgotten private event", datetime.now(UTC))
    store.forget_claim(item.id)
    assert (
        "forgotten private event"
        not in store.retrieve_case_context("private event").model_dump_json()
    )


def test_stale_hypothesis_can_be_retrieved_but_is_marked_stale(tmp_path) -> None:
    store = _store(tmp_path)
    old = datetime.now(UTC) - timedelta(days=200)
    evidence = _report(store, "I fear work calls", old)
    item = store.add_hypothesis(
        "Calls may trigger fear of judgment",
        linked_claim_ids=[evidence.id],
        evidence_message_ids=[evidence.evidence[0].message_id],
        now=old,
    )
    context = store.retrieve_case_context("fear during calls")
    found = next(value for value in context.hypotheses if value.id == item.id)
    assert found.stale


def test_does_not_fit_hypothesis_is_not_retrieved(tmp_path) -> None:
    store = _store(tmp_path)
    evidence = _report(store, "I fear calls", datetime.now(UTC))
    item = store.add_hypothesis(
        "Calls may trigger fear",
        linked_claim_ids=[evidence.id],
        evidence_message_ids=[evidence.evidence[0].message_id],
    )
    text = "No, it does not fit"
    session = store.start_session()
    message_id = store.save_turn(session, text, "Understood", [])
    store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=item.id,
                fit=ClaimFit.DOES_NOT_FIT,
                evidence_quote="does not fit",
            )
        ],
        message_id,
        text,
    )
    assert item.id not in {
        value.id for value in store.retrieve_case_context("fear and calls").hypotheses
    }


def test_relevant_closed_session_is_retrieved_after_long_gap(tmp_path) -> None:
    store = _store(tmp_path)
    old = datetime.now(UTC) - timedelta(days=300)
    session = store.start_session(old)
    store.save_turn(session, "I struggled with phone calls", "We discussed that.", [], old)
    store.close_session(
        session,
        summary="The user discussed phone calls.",
        themes=["phone calls"],
        now=old,
    )
    recent_time = datetime.now(UTC) - timedelta(days=1)
    recent = store.start_session(recent_time)
    store.save_turn(recent, "I baked bread", "How did it turn out?", [], recent_time)
    store.close_session(recent, summary="The user baked bread.", now=recent_time)
    context = store.retrieve_case_context("I have another call")
    assert context.relevant_sessions[0].id == session.id
    # The most recent session is always present, whatever the message is about.
    assert context.latest_session is not None
    assert context.latest_session.id == recent.id
    assert context.elapsed_since_latest_session == "1 day"
    assert context.today == datetime.now(UTC).date().isoformat()


def test_bounded_result_is_valid_json_with_complete_records(tmp_path) -> None:
    store = _store(tmp_path)
    start = datetime.now(UTC) - timedelta(days=100)
    for index in range(60):
        _report(store, f"Phone call record {index}", start + timedelta(days=index))
    context = store.retrieve_case_context("phone call")
    assert len(context.user_reports) <= 20
    assert len(context.relevant_excerpts) <= 5
    assert context.model_validate_json(context.model_dump_json()) == context


def test_over_the_cap_claims_are_trimmed_before_conversation_rounds(tmp_path) -> None:
    # Since W2.5 concrete details live only in the rounds, so the envelope must not lose them to
    # a long tail of claims (W0.6 found every excerpt dropped at 50 sessions).
    store = _store(tmp_path)
    start = datetime.now(UTC) - timedelta(days=100)
    for index in range(20):
        _report(
            store,
            f"Phone call record {index} " + "about the long and difficult call " * 13,
            start + timedelta(days=index),
        )
    context = store.retrieve_case_context("phone call")
    assert len(context.model_dump_json()) > MAX_CONTEXT_CHARS
    assert context.relevant_excerpts

    bounded = json.loads(_bounded_context_json(context))

    assert len(bounded["relevant_excerpts"]) == len(context.relevant_excerpts)
    assert len(bounded["user_reports"]) < len(context.user_reports)
