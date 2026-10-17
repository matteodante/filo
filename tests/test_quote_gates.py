"""Direct tests of the quote gates, written to kill the mutants W0.6 found surviving.

The conversation tools validate quotes before the store does, and most earlier tests reach both
through whole turns, where the store's backstop or a sibling check hid a weakened gate. These
call each gate on its own, on both sides of every condition. The mutation scores are in
docs/evidence/offline-instruments-2026-10.md.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic_ai import ModelRetry
from test_longitudinal_memory import _store

from filo.chat import (
    HypothesisAction,
    InterventionAction,
    ProcessFeedbackAction,
    SupportChoiceAction,
    _check_accepted_focus,
    _check_aliases,
    _check_consent,
    _check_correction,
    _check_hypothesis,
    _check_process_feedback,
    _check_review,
    _check_support_choice,
    _quote_in_text,
    _validate_user_reports,
)
from filo.memory import (
    ClaimCorrection,
    ClaimFit,
    ClaimLifecycle,
    ClaimOrigin,
    ContextClaim,
    HypothesisReview,
    InterventionState,
    MemoryKind,
    MemoryStore,
    UserReport,
    _carries,
    _claims_conflict,
    _has_broad_negation,
    _near_duplicate,
    _replace_derived,
    _require_consent_quote,
    _require_quoted_aliases,
    _supported_quote,
    is_grounded_paraphrase,
    quote_keeps_clause_polarity,
)

TEXT = "Non è insonnia cronica, è solo stress. Proverò a uscire prima dal lavoro."
MINED = "è insonnia cronica"
NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def _claim(claim_id: str, origin: ClaimOrigin, kind: MemoryKind = MemoryKind.PATTERN):
    return ContextClaim(
        id=claim_id,
        kind=kind,
        content="stress at work",
        origin=origin,
        fit=ClaimFit.NOT_REVIEWED,
        lifecycle=ClaimLifecycle.ACTIVE,
        evidence=[],
        first_seen_at=NOW.isoformat(),
        last_seen_at=NOW.isoformat(),
        last_reviewed_at=None,
        conflict_ids=[],
    )


CLAIMS = {
    "user": _claim("user", ClaimOrigin.USER_STATEMENT),
    "hyp": _claim("hyp", ClaimOrigin.AGENT_HYPOTHESIS),
}


def _rejects(check, *args) -> None:
    with pytest.raises(ModelRetry):
        check(*args)


def test_quote_presence_is_exact_and_never_empty() -> None:
    assert _quote_in_text("solo  STRESS", TEXT)
    assert not _quote_in_text("", TEXT)
    assert not _quote_in_text(None, TEXT)
    assert not _supported_quote("", TEXT)
    assert _supported_quote("è solo stress", TEXT)


def test_user_reports_are_capped_and_merge_only_into_a_compatible_user_report() -> None:
    quote = "Proverò a uscire prima dal lavoro"
    report = UserReport(kind=MemoryKind.PATTERN, content=quote, evidence_quote=quote)
    _validate_user_reports([report, report], TEXT, CLAIMS, NOW)
    _rejects(_validate_user_reports, [report, report, report], TEXT, CLAIMS, NOW)
    _validate_user_reports([report.model_copy(update={"merge_into_id": "user"})], TEXT, CLAIMS, NOW)
    for target, kind in (("missing", MemoryKind.PATTERN), ("hyp", MemoryKind.PATTERN)):
        merged = report.model_copy(update={"merge_into_id": target, "kind": kind})
        _rejects(_validate_user_reports, [merged], TEXT, CLAIMS, NOW)
    preference = report.model_copy(update={"merge_into_id": "user", "kind": MemoryKind.PREFERENCE})
    _rejects(_validate_user_reports, [preference], TEXT, CLAIMS, NOW)
    _rejects(
        _validate_user_reports, [report.model_copy(update={"aliases": [MINED]})], TEXT, CLAIMS, NOW
    )


def test_aliases_must_be_exact_and_keep_their_negation() -> None:
    _check_aliases(["è solo stress"], TEXT, "a report")
    _rejects(_check_aliases, ["solo ansia"], TEXT, "a report")
    _rejects(_check_aliases, [MINED], TEXT, "a report")
    _require_quoted_aliases(["è solo stress"], TEXT)
    for aliases, evidence in ((["solo ansia"], TEXT), ([MINED], TEXT), (["è solo stress"], None)):
        with pytest.raises(ValueError):
            _require_quoted_aliases(aliases, evidence)


def test_hypothesis_needs_available_links_or_an_unmined_quote() -> None:
    _check_hypothesis(HypothesisAction(content="x", linked_claim_ids=["user"]), TEXT, CLAIMS)
    _check_hypothesis(HypothesisAction(content="x", evidence_quote="è solo stress"), TEXT, CLAIMS)
    for action in (
        HypothesisAction(content="x", linked_claim_ids=["missing"]),
        HypothesisAction(content="x"),
        HypothesisAction(content="x", evidence_quote="solo ansia"),
        HypothesisAction(content="x", linked_claim_ids=["user"], evidence_quote=MINED),
        HypothesisAction(content="x", linked_claim_ids=["user"], aliases=[MINED]),
    ):
        _rejects(_check_hypothesis, action, TEXT, CLAIMS)


def test_correction_quotes_are_exact_unmined_and_never_recast_a_hypothesis() -> None:
    good = ClaimCorrection(memory_id="user", correction_quote="Non è insonnia cronica")
    _check_correction(good, TEXT, CLAIMS)
    _check_correction(good.model_copy(update={"replacement_quote": "è solo stress"}), TEXT, CLAIMS)
    _check_correction(good.model_copy(update={"memory_id": "hyp"}), TEXT, CLAIMS)
    for update in (
        {"memory_id": "missing"},
        {"memory_id": "hyp", "replacement_quote": "è solo stress"},
        {"correction_quote": "solo ansia"},
        {"replacement_quote": "solo ansia"},
        {"correction_quote": MINED},
        {"replacement_quote": MINED},
    ):
        _rejects(_check_correction, good.model_copy(update=update), TEXT, CLAIMS)


def test_review_targets_a_hypothesis_with_explicit_fit_and_unmined_quotes() -> None:
    good = HypothesisReview(memory_id="hyp", fit=ClaimFit.FITS, evidence_quote="è solo stress")
    _check_review(good, TEXT, CLAIMS)
    _check_review(good.model_copy(update={"accepted_wording_quote": "è solo stress"}), TEXT, CLAIMS)
    for update in (
        {"memory_id": "user"},
        {"memory_id": "missing"},
        {"fit": ClaimFit.NOT_REVIEWED},
        {"fit": ClaimFit.NOT_APPLICABLE},
        {"evidence_quote": "solo ansia"},
        {"evidence_quote": MINED},
        {"accepted_wording_quote": "solo ansia"},
        {"accepted_wording_quote": MINED},
    ):
        _rejects(_check_review, good.model_copy(update=update), TEXT, CLAIMS)


def test_accepted_focus_and_its_evidence_are_both_exact_and_unmined() -> None:
    _check_accepted_focus("uscire prima dal lavoro", "Proverò a uscire prima", TEXT)
    for focus, evidence in (
        ("uscire prima dal lavoro", None),
        ("uscire prima dal lavoro", "solo ansia"),
        ("restare in ufficio", "Proverò a uscire prima"),
        (MINED, "Proverò a uscire prima"),
        ("uscire prima dal lavoro", MINED),
    ):
        _rejects(_check_accepted_focus, focus, evidence, TEXT)


def test_process_feedback_and_support_choices_quote_and_restate_faithfully() -> None:
    quote = "Proverò a uscire prima dal lavoro"
    _check_process_feedback(
        ProcessFeedbackAction(
            content="uscire prima dal lavoro", evidence_quote=quote, reusable=True
        ),
        TEXT,
    )
    for content, evidence in (("uscire prima", "solo ansia"), ("lavoro uscire", quote)):
        feedback = ProcessFeedbackAction(content=content, evidence_quote=evidence, reusable=True)
        _rejects(_check_process_feedback, feedback, TEXT)

    good = SupportChoiceAction(
        content="uscire prima dal lavoro",
        evidence_quote=quote,
        barrier="è solo stress",
        preference="uscire prima",
    )
    _check_support_choice(good, TEXT)
    for update in (
        {"evidence_quote": "solo ansia"},
        {"content": "lavoro prima"},
        {"barrier": "solo ansia"},
        {"preference": "solo ansia"},
        {"barrier": MINED},
        {"preference": MINED},
    ):
        _rejects(_check_support_choice, good.model_copy(update=update), TEXT)


def test_agreement_and_every_update_need_an_unmined_consent_quote() -> None:
    offered = InterventionAction(skill="s", description="d", state=InterventionState.OFFERED)
    _check_consent(offered, TEXT)
    for action in (
        offered.model_copy(update={"state": InterventionState.AGREED}),
        offered.model_copy(update={"record_id": "r1", "state": InterventionState.TRIED}),
    ):
        _rejects(_check_consent, action, TEXT)
        _check_consent(action.model_copy(update={"consent_evidence_quote": "Proverò a"}), TEXT)
    for quote in ("solo ansia", MINED):
        _rejects(_check_consent, offered.model_copy(update={"consent_evidence_quote": quote}), TEXT)
    _require_consent_quote("Proverò a", TEXT)
    for quote, evidence in ((MINED, TEXT), ("solo ansia", TEXT), ("Proverò a", None)):
        with pytest.raises(ValueError):
            _require_consent_quote(quote, evidence)


def test_clause_polarity_checks_every_fragment_and_tolerates_unknown_ones() -> None:
    evidence = "non è insonnia cronica, è solo stress"
    assert not quote_keeps_clause_polarity(", è insonnia cronica", evidence)
    assert quote_keeps_clause_polarity("words from elsewhere", evidence)


def test_restatement_counts_weak_negation_and_refuses_mixed_polarity() -> None:
    # One plain and one weak marker: dropping both must not balance out to zero.
    assert not is_grounded_paraphrase("bevo alcol la sera", "non bevo mai alcol la sera")
    assert not is_grounded_paraphrase("non voglio lavorare", "non voglio dormire, voglio lavorare")
    # As many words as the quote, differing only in punctuation, is still a restatement.
    assert is_grounded_paraphrase("I always skip breakfast", "I always skip breakfast.")
    assert _has_broad_negation("nessuno mi chiama")


def test_near_duplicates_keep_negation_and_names_apart() -> None:
    left = "I usually feel anxious before the weekly team meeting on Monday morning"
    assert _near_duplicate(left, left + ".")
    assert not _near_duplicate(left, left.replace("I usually", "I never"))
    assert not _near_duplicate(left, left.replace("team", "Giulia"))
    assert not _near_duplicate(left, "I enjoy painting landscapes on quiet Sunday afternoons")


def test_claims_conflict_on_numbers_or_negation_alone() -> None:
    assert _claims_conflict("I sleep 5 hours a night", "I sleep 7 hours a night")
    assert _claims_conflict("I sleep well at night", "I do not sleep well at night")
    assert not _claims_conflict("I sleep well at night", "I sleep well at night lately")


def test_replace_derived_rewrites_case_insensitively_without_duplicating() -> None:
    old, new = "drink coffee late", "do not drink coffee late"
    assert _replace_derived("I Drink Coffee Late.", old, new) == "I do not drink coffee late."
    assert (
        _replace_derived("I do not drink coffee late.", old, new) == "I do not drink coffee late."
    )
    assert _replace_derived("Drink coffee late, then sleep.", old, "") == ", then sleep."
    # Two shared words are enough to discard paraphrased stale text; one is not.
    assert _replace_derived("Coffee taken late is the issue", old, "") == ""
    assert _replace_derived("Coffee is fine", old, "") == "Coffee is fine"
    assert _replace_derived("A do not drink coffee late note", old, new) == (
        "A do not drink coffee late note"
    )


def test_carrying_needs_three_content_words_and_three_quarters_of_them() -> None:
    assert _carries("missed the early train", "Yesterday I missed my early train again")
    assert not _carries("missed the early train", "Yesterday I missed my bus")
    assert not _carries("missed the train", "I missed my train")
    # Exactly three of four content words is enough; two of four is not.
    assert _carries("missed the early train home", "I missed the train home")
    assert not _carries("missed the early train home", "I missed the bus home")


def _turn(store: MemoryStore, text: str, when: datetime) -> int:
    session = store.active_session() or store.start_session(when)
    return store.save_turn(session, text, "Noted.", [], when)


def _report(store: MemoryStore, content: str, quote: str, when: datetime):
    message_id = _turn(store, quote, when)
    report = UserReport(kind=MemoryKind.PATTERN, content=content, evidence_quote=quote)
    return store.add_user_reports([report], message_id, quote, when)[0]


def _semantic_rows(store: MemoryStore, entity_type: str, entity_id: str) -> int:
    with store._connect() as database:
        (count,) = database.execute(
            "SELECT COUNT(*) FROM semantic_index WHERE entity_type = ? AND entity_id = ?",
            (entity_type, entity_id),
        ).fetchone()
    return count


def test_correction_records_its_quotes_and_drops_stale_index_rows(tmp_path) -> None:
    store = _store(tmp_path)
    start = NOW - timedelta(days=3)
    item = _report(store, "I drink coffee late", "every single night I drink coffee late", start)
    store.retrieve_case_context("coffee late at night", now=start)
    evidence_round = str(item.evidence[0].message_id)
    assert _semantic_rows(store, "claim", item.id) == _semantic_rows(store, "round", evidence_round)
    assert _semantic_rows(store, "claim", item.id) == 1
    intervention = store.create_intervention(
        skill="behavioral-step",
        description="Switch to tea after lunch",
        state=InterventionState.OFFERED,
        linked_claim_ids=[item.id],
        evidence_message_id=item.evidence[0].message_id,
        context="User said every single night I drink coffee late",
    )
    text = "That is wrong.   I stopped drinking coffee after lunch"
    corrected = store.correct_claim(
        ClaimCorrection(
            memory_id=item.id,
            correction_quote="That is wrong.",
            replacement_quote="I stopped drinking coffee after lunch",
        ),
        _turn(store, text, NOW),
        text,
        NOW,
    )

    assert corrected.last_reviewed_at == corrected.superseded_at
    assert corrected.last_reviewed_at is not None
    assert [ref.quote for ref in corrected.evidence[-2:]] == [
        "That is wrong.",
        "I stopped drinking coffee after lunch",
    ]
    assert _semantic_rows(store, "claim", item.id) == 0
    assert _semantic_rows(store, "round", evidence_round) == 0
    # Longest wording first: the whole quote is replaced, not only the restated part of it.
    (stored,) = store.list_interventions()
    assert stored.id == intervention.id
    assert stored.context == "User said I stopped drinking coffee after lunch"


def test_forgetting_a_claim_tombstones_and_stops_interventions_built_on_it(tmp_path) -> None:
    store = _store(tmp_path)
    item = _report(
        store, "I avoid phone calls with my manager", "I avoid phone calls with my manager", NOW
    )
    other = _report(store, "I walk after dinner", "I walk after dinner", NOW)
    message_id = item.evidence[0].message_id
    built = store.create_intervention(
        skill="behavioral-step",
        description="Make the phone calls with my manager shorter",
        state=InterventionState.OFFERED,
        linked_claim_ids=[item.id, other.id],
        evidence_message_id=message_id,
        prediction="I avoid phone calls with my manager less often",
    )
    unrelated = store.create_intervention(
        skill="behavioral-step",
        description="Take a walk after dinner",
        state=InterventionState.OFFERED,
        linked_claim_ids=[other.id],
        evidence_message_id=message_id,
        prediction="Sleep better",
    )
    state = store.load_app_state()
    state.pending_intervention_id = built.id
    store.save_app_state(state)

    store.forget_claim(item.id)

    records = {record.id: record for record in store.list_interventions()}
    assert records[built.id].description == "Content removed by user request."
    assert records[built.id].state is InterventionState.STOPPED
    assert records[built.id].prediction == "less often"
    assert records[built.id].linked_claim_ids == [other.id]
    assert records[unrelated.id].description == "Take a walk after dinner"
    assert records[unrelated.id].state is InterventionState.OFFERED
    assert records[unrelated.id].prediction == "Sleep better"
    assert records[unrelated.id].linked_claim_ids == [other.id]
    assert store.load_app_state().pending_intervention_id is None


def test_forgetting_keeps_an_unrelated_pending_intervention(tmp_path) -> None:
    store = _store(tmp_path)
    item = _report(
        store, "I avoid phone calls with my boss", "I avoid phone calls with my boss", NOW
    )
    kept = store.create_intervention(
        skill="behavioral-step",
        description="Take a walk after dinner",
        state=InterventionState.OFFERED,
        linked_claim_ids=[],
        evidence_message_id=item.evidence[0].message_id,
    )
    store.create_intervention(
        skill="behavioral-step",
        description="Shorter phone calls with my boss",
        state=InterventionState.OFFERED,
        linked_claim_ids=[item.id],
        evidence_message_id=item.evidence[0].message_id,
    )
    state = store.load_app_state()
    state.pending_intervention_id = kept.id
    store.save_app_state(state)

    store.forget_claim(item.id)

    assert store.load_app_state().pending_intervention_id == kept.id


def test_claims_as_of_includes_the_observation_instant_and_excludes_the_supersession(
    tmp_path,
) -> None:
    store = _store(tmp_path)
    observed = NOW - timedelta(days=2)
    item = _report(store, "I skip breakfast", "I skip breakfast", observed)
    assert [claim.id for claim in store.claims_as_of(observed)] == [item.id]
    text = "That is wrong"
    store.correct_claim(
        ClaimCorrection(memory_id=item.id, correction_quote=text),
        _turn(store, text, NOW),
        text,
        NOW,
    )
    superseded = store.list_claims(include_inactive=True)[0].superseded_at
    assert superseded is not None
    moment = datetime.fromisoformat(superseded)
    assert store.claims_as_of(moment - timedelta(microseconds=1))
    assert store.claims_as_of(moment) == []


def test_correction_suppresses_later_rounds_repeating_a_three_word_wording(tmp_path) -> None:
    store = _store(tmp_path)
    start = NOW - timedelta(days=5)
    item = _report(store, "hate Monday mornings", "I really hate Monday mornings", start)
    repeat = _turn(store, "As usual, hate Monday mornings", start + timedelta(days=1))
    short = _turn(store, "Mornings again", start + timedelta(days=2))
    text = "That is wrong"
    store.correct_claim(
        ClaimCorrection(memory_id=item.id, correction_quote=text),
        _turn(store, text, NOW),
        text,
        NOW,
    )
    suppressed = store._suppressed_round_ids()
    assert repeat in suppressed
    assert short not in suppressed
