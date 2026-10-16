"""Deterministic tests for the clean-break epistemic memory model."""

import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from filo.memory import (
    AppState,
    CaseFormulation,
    ClaimCorrection,
    ClaimFit,
    ClaimLifecycle,
    ClaimOrigin,
    EvidenceQuality,
    HypothesisReview,
    InterventionDecision,
    InterventionState,
    MemoryError,
    MemoryKind,
    MemoryMode,
    MemoryStore,
    RetentionPolicy,
    SessionEndReason,
    UserReport,
)


def _message(store: MemoryStore, text: str, now: datetime | None = None) -> int:
    session = store.active_session() or store.start_session(now)
    return store.save_turn(session, text, "acknowledged", [], now)


def _report(
    store: MemoryStore,
    text: str,
    kind: MemoryKind = MemoryKind.FACT,
    now: datetime | None = None,
):
    message_id = _message(store, text, now)
    return store.add_user_reports(
        [UserReport(kind=kind, content=text, evidence_quote=text)],
        message_id,
        text,
        now,
    )[0]


def _hypothesis(store: MemoryStore, text: str, now: datetime | None = None):
    evidence = _report(store, f"Evidence for {text}", MemoryKind.EVENT, now)
    return store.add_hypothesis(
        text,
        linked_claim_ids=[evidence.id],
        evidence_message_ids=[evidence.evidence[0].message_id],
        now=now,
    )


def test_app_state_defaults_to_standard_memory_and_indefinite_retention() -> None:
    state = AppState()
    assert state.default_memory_mode is MemoryMode.STANDARD
    assert state.retention_policy == RetentionPolicy()


def test_retention_policy_rejects_zero_days() -> None:
    with pytest.raises(ValidationError):
        RetentionPolicy(raw_message_days=0)


def test_user_statement_is_a_report_not_external_truth(tmp_path) -> None:
    item = _report(MemoryStore(tmp_path), "I live in Rome")
    assert item.kind is MemoryKind.FACT
    assert item.origin is ClaimOrigin.USER_STATEMENT
    assert item.fit is ClaimFit.NOT_APPLICABLE
    assert item.evidence[0].quality is EvidenceQuality.EXACT_QUOTE


def test_user_report_requires_exact_current_message_quote(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = _message(store, "I live in Rome")
    with pytest.raises(ValueError, match="exact"):
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.FACT,
                    content="I live in Milan",
                    evidence_quote="I live in Milan",
                )
            ],
            message_id,
            "I live in Rome",
        )


def test_user_report_marks_paraphrase_while_keeping_the_users_wording(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = _message(store, "I prefer shorter answers")
    [verbatim, paraphrased] = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.FACT,
                content="I prefer shorter answers",
                evidence_quote="I prefer shorter answers",
            ),
            UserReport(
                kind=MemoryKind.PREFERENCE,
                content="prefer shorter answers",
                evidence_quote="I prefer shorter answers",
            ),
        ],
        message_id,
        "I prefer shorter answers",
    )

    assert verbatim.evidence[0].quality is EvidenceQuality.EXACT_QUOTE
    assert paraphrased.content == "prefer shorter answers"
    assert paraphrased.evidence[0].quality is EvidenceQuality.GROUNDED_PARAPHRASE
    # The restatement never replaces the user's own words: they stay on the evidence.
    assert paraphrased.evidence[0].quote == "I prefer shorter answers"


def test_restatement_may_drop_words_but_never_substitute_vocabulary(tmp_path) -> None:
    message = "vorrei evitare il tema famiglia e preferisco scrivere la sera"
    store = MemoryStore(tmp_path)
    message_id = _message(store, message)

    # Dropping filler and keeping the user's own words across a long sentence is the point.
    [item] = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.PREFERENCE,
                content="evitare il tema famiglia",
                evidence_quote=message,
            )
        ],
        message_id,
        message,
    )
    assert item.evidence[0].quality is EvidenceQuality.GROUNDED_PARAPHRASE

    # Substituting clinical vocabulary the user never used is what the gate exists to stop.
    with pytest.raises(ValueError, match="restatement"):
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.PATTERN,
                    content="evitamento fobico familiare",
                    evidence_quote=message,
                )
            ],
            message_id,
            message,
        )


def test_user_report_refuses_to_paraphrase_a_fact(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = _message(store, "I slept badly again last night")
    with pytest.raises(ValueError, match="exact wording"):
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.FACT,
                    content="The user has chronic insomnia",
                    evidence_quote="I slept badly again last night",
                )
            ],
            message_id,
            "I slept badly again last night",
        )


@pytest.mark.parametrize(
    ("content", "quote", "message"),
    [
        # Expands beyond the words it claims to compress.
        (
            "I find long detailed answers hard to follow",
            "I dislike long answers",
            "I dislike long answers",
        ),
        # Introduces a number the user never gave.
        ("I wake up 4 times", "I keep waking up", "I keep waking up"),
        # Flips the polarity of what was said.
        ("I do not enjoy running", "I enjoy running these days", "I enjoy running these days"),
        # Introduces a name absent from the quote.
        ("Marco makes it worse", "my colleague makes it worse", "my colleague makes it worse"),
        # The quote is too slight to support any claim.
        ("The user avoids conflict", "I do", "I do that a lot"),
        # Reordering the user's own words can invert exactly what they denied.
        (
            "non è solo stress, è insonnia cronica",
            "non è insonnia cronica, è solo stress",
            "non è insonnia cronica, è solo stress",
        ),
        # Escalates a reported bad night into a recorded condition.
        (
            "insonnia cronica grave",
            "ultimamente dormo poco e mi sveglio spesso",
            "ultimamente dormo poco e mi sveglio spesso",
        ),
        # Dropping a word is enough to reverse the meaning of the quote.
        (
            "una volta che mi senta bene",
            "mai una volta che mi senta bene",
            "mai una volta che mi senta bene",
        ),
        # Dropping one of two negations inverts a conjunct while the clause stays negative.
        (
            "bevo alcol e non fumo",
            "non bevo alcol e non fumo da mesi ormai",
            "non bevo alcol e non fumo da mesi ormai",
        ),
    ],
)
def test_user_report_rejects_an_ungrounded_restatement(tmp_path, content, quote, message) -> None:
    store = MemoryStore(tmp_path)
    message_id = _message(store, message)
    with pytest.raises(ValueError, match="restatement"):
        store.add_user_reports(
            [UserReport(kind=MemoryKind.PATTERN, content=content, evidence_quote=quote)],
            message_id,
            message,
        )


def test_correcting_a_restated_claim_retires_the_wording_it_restated(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    first = _message(store, "I dislike long answers")
    [claim] = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.PREFERENCE,
                content="dislike long answers",
                evidence_quote="I dislike long answers",
            )
        ],
        first,
        "I dislike long answers",
    )
    assert claim.evidence[0].quality is EvidenceQuality.GROUNDED_PARAPHRASE

    second = _message(store, "actually I like detail now")
    corrected = store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id,
            correction_quote="actually I like detail now",
            replacement_quote="I like detail now",
        ),
        second,
        "actually I like detail now",
    )

    # Both the stored restatement and the user's original wording must be retired, or the
    # superseded phrasing returns through retrieval.
    assert "dislike long answers" in corrected.superseded_content
    assert "I dislike long answers" in corrected.superseded_content
    context = store.retrieve_case_context("answers")
    quotes = [reference.quote for item in context.user_reports for reference in item.evidence]
    assert "I dislike long answers" not in quotes


@pytest.mark.parametrize(
    ("quote", "message"),
    [
        ("è insonnia cronica", "non è insonnia cronica, è solo stress"),
        ("I want to die", "I would never say I want to die"),
        # Spanning a clause boundary must not launder the qualifier off the leading fragment.
        ("I want to die, I want to live", "I would never say I want to die, I want to live"),
        # A conjunction divides clauses too: this conjunct was negative in the message.
        ("bevo alcol e non fumo", "non bevo alcol e non fumo da mesi ormai"),
        # English contractions must count as negation, including with a typographic apostrophe.
        ("drink wine", "I don't drink wine and don't smoke these days"),
        ("drink wine", "I don\u2019t drink wine and don\u2019t smoke these days"),
    ],
)
def test_user_report_refuses_a_quote_mined_out_of_its_negation(tmp_path, quote, message) -> None:
    # Being an exact substring is not enough: the clause that qualified it has to come along.
    store = MemoryStore(tmp_path)
    message_id = _message(store, message)
    with pytest.raises(ValueError, match="negation"):
        store.add_user_reports(
            [UserReport(kind=MemoryKind.FACT, content=quote, evidence_quote=quote)],
            message_id,
            message,
        )


def test_correction_whose_replacement_contains_the_old_wording_is_not_duplicated(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message = "vorrei evitare il tema famiglia"
    message_id = _message(store, message)
    [claim] = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.PREFERENCE,
                content="evitare il tema famiglia",
                evidence_quote=message,
            )
        ],
        message_id,
        message,
    )
    store.set_focus(
        "evitare il tema famiglia",
        accepted=True,
        evidence_quote=message,
        evidence_text=message,
    )

    correction = "in realtà non voglio evitare il tema famiglia"
    second = _message(store, correction)
    store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id,
            correction_quote=correction,
            replacement_quote="non voglio evitare il tema famiglia",
        ),
        second,
        correction,
    )

    # A replacement that contains the wording it replaces must not be rewritten inside itself.
    focus = store.load_formulation().accepted_focus
    assert focus is None or "non voglio non voglio" not in focus


def test_store_accepts_only_an_exact_current_user_focus(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message = "voglio capire meglio le chiamate"

    formulation = store.set_focus(
        "capire meglio le chiamate",
        accepted=True,
        evidence_quote="capire meglio le chiamate",
        evidence_text=message,
    )

    assert formulation.accepted_focus == "capire meglio le chiamate"
    assert formulation.proposed_focus is None


def test_store_rejects_focus_mined_out_of_a_refusal(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    refusal = "non voglio capire meglio le chiamate"

    with pytest.raises(ValueError, match="negation"):
        store.set_focus(
            "voglio capire meglio le chiamate",
            accepted=True,
            evidence_quote="voglio capire meglio le chiamate",
            evidence_text=refusal,
        )


def test_generic_formulation_writes_cannot_bypass_focus_evidence(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    formulation = store.load_formulation()
    formulation.accepted_focus = "an unsupported focus"

    with pytest.raises(ValueError, match="set_focus"):
        store.save_formulation(formulation)

    store.set_focus(
        "understand the calls",
        accepted=True,
        evidence_quote="understand the calls",
        evidence_text="I want to understand the calls",
    )
    formulation = store.load_formulation()
    formulation.accepted_focus = None

    with pytest.raises(ValueError, match="set_focus"):
        store.save_formulation(formulation)


def test_formulation_link_updates_preserve_validated_focus(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store.set_focus(
        "understand the calls",
        accepted=True,
        evidence_quote="understand the calls",
        evidence_text="I want to understand the calls",
    )

    formulation = store.save_formulation_links({})

    assert formulation.accepted_focus == "understand the calls"


def test_correcting_a_verbatim_claim_by_negation_updates_derived_text(tmp_path) -> None:
    # The commonest correction there is. Derived text must not keep asserting what was just
    # denied, and must not end up containing the replacement twice.
    store = MemoryStore(tmp_path)
    message = "bevo caffè la sera"
    message_id = _message(store, message)
    [claim] = store.add_user_reports(
        [UserReport(kind=MemoryKind.FACT, content=message, evidence_quote=message)],
        message_id,
        message,
    )
    store.set_focus(
        message,
        accepted=True,
        evidence_quote=message,
        evidence_text=message,
    )

    correction = "in realtà non bevo caffè la sera"
    second = _message(store, correction)
    store.correct_claim(
        ClaimCorrection(
            memory_id=claim.id,
            correction_quote=correction,
            replacement_quote="non bevo caffè la sera",
        ),
        second,
        correction,
    )

    focus = store.load_formulation().accepted_focus
    assert focus != message
    if focus is not None:
        assert focus.count("non bevo caffè la sera") == 1
        assert "non non" not in focus


def test_intervention_consent_cannot_be_mined_out_of_a_refusal(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    refusal = "non sono d'accordo a provare l'esercizio stasera"
    with pytest.raises(ValueError, match="negation"):
        store.create_intervention(
            skill="take-a-small-step",
            description="One small step tonight",
            state=InterventionState.AGREED,
            linked_claim_ids=[],
            evidence_message_id=1,
            consent_quote="sono d'accordo a provare l'esercizio stasera",
            evidence_text=refusal,
        )


def test_intervention_consent_must_come_from_the_message(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError, match="current message"):
        store.create_intervention(
            skill="take-a-small-step",
            description="One small step tonight",
            state=InterventionState.AGREED,
            linked_claim_ids=[],
            evidence_message_id=1,
            consent_quote="sure, let us do it",
            evidence_text="I am not sure about this",
        )


def test_correction_refuses_a_replacement_mined_out_of_its_negation(tmp_path) -> None:
    # The inversion the whole gate exists to stop, attempted on the path meant to fix claims.
    store = MemoryStore(tmp_path)
    original = "bevo caffè la sera"
    first = _message(store, original)
    [claim] = store.add_user_reports(
        [UserReport(kind=MemoryKind.FACT, content=original, evidence_quote=original)],
        first,
        original,
    )

    correction = "in realtà non bevo caffè la sera, era mio fratello"
    second = _message(store, correction)
    with pytest.raises(ValueError, match="negation"):
        store.correct_claim(
            ClaimCorrection(
                memory_id=claim.id,
                correction_quote=correction,
                replacement_quote="bevo caffè la sera",
            ),
            second,
            correction,
        )


def test_forgetting_a_restated_claim_also_clears_the_users_own_wording(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message = "I dislike long answers"
    message_id = _message(store, message)
    [claim] = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.PREFERENCE,
                content="dislike long answers",
                evidence_quote=message,
            )
        ],
        message_id,
        message,
    )
    # Derived text quotes the user, not the model's shortened content.
    store.set_focus(message, accepted=True, evidence_quote=message, evidence_text=message)

    store.forget_claim(claim.id)

    assert store.load_formulation().accepted_focus is None


def test_user_report_paraphrase_still_requires_a_quote_from_the_message(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    message_id = _message(store, "I prefer shorter answers")
    with pytest.raises(ValueError, match="exact quote"):
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.PREFERENCE,
                    content="The user values brevity",
                    evidence_quote="I want you to be brief",
                )
            ],
            message_id,
            "I prefer shorter answers",
        )


def test_user_report_limit_is_two(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    with pytest.raises(ValueError, match="two"):
        store.add_user_reports(
            [
                UserReport(kind=MemoryKind.FACT, content=value, evidence_quote=value)
                for value in ("one", "two", "three")
            ],
            1,
            "one two three",
        )


def test_hypothesis_starts_unreviewed_and_remains_hypothesis_after_fits(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "Avoidance may reduce distress briefly")
    message_id = _message(store, "Yes, that fits")
    reviewed, reports = store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=item.id,
                fit=ClaimFit.FITS,
                evidence_quote="that fits",
            )
        ],
        message_id,
        "Yes, that fits",
    )
    assert reviewed[0].origin is ClaimOrigin.AGENT_HYPOTHESIS
    assert reviewed[0].fit is ClaimFit.FITS
    assert reports == []


@pytest.mark.parametrize(
    "fit",
    [ClaimFit.PARTLY_FITS, ClaimFit.DOES_NOT_FIT, ClaimFit.UNSURE],
)
def test_hypothesis_fit_variants_are_preserved(tmp_path, fit: ClaimFit) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "A tentative pattern")
    message_id = _message(store, "This is my review")
    reviewed, _ = store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=item.id,
                fit=fit,
                evidence_quote="This is my review",
            )
        ],
        message_id,
        "This is my review",
    )
    assert reviewed[0].fit is fit
    assert reviewed[0].origin is ClaimOrigin.AGENT_HYPOTHESIS


def test_partly_fits_can_create_distinct_exact_user_statement(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    hypothesis = _hypothesis(store, "I avoid every difficult conversation")
    text = "Partly: I avoid work calls"
    message_id = _message(store, text)
    _, reports = store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=hypothesis.id,
                fit=ClaimFit.PARTLY_FITS,
                evidence_quote=text,
                accepted_wording_quote="I avoid work calls",
            )
        ],
        message_id,
        text,
    )
    assert reports[0].content == "I avoid work calls"
    assert reports[0].origin is ClaimOrigin.USER_STATEMENT


def test_review_requires_separate_claim_ids(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "A pattern")
    review = HypothesisReview(
        memory_id=item.id,
        fit=ClaimFit.FITS,
        evidence_quote="yes",
    )
    with pytest.raises(ValueError, match="separate"):
        store.review_hypotheses([review, review], 1, "yes")


def test_correction_uses_replacement_quote_and_preserves_history(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "I live in Rome")
    text = "That is wrong; I live in Turin"
    message_id = _message(store, text)
    corrected = store.correct_claim(
        ClaimCorrection(
            memory_id=item.id,
            correction_quote="That is wrong",
            replacement_quote="I live in Turin",
        ),
        message_id,
        text,
    )
    assert corrected.id == item.id
    assert corrected.content == "I live in Turin"
    assert corrected.superseded_content == ["I live in Rome"]
    assert corrected.lifecycle is ClaimLifecycle.ACTIVE


def test_contradiction_without_replacement_supersedes_claim(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "I live in Rome")
    text = "That is not true"
    corrected = store.correct_claim(
        ClaimCorrection(memory_id=item.id, correction_quote=text),
        _message(store, text),
        text,
    )
    assert corrected.lifecycle is ClaimLifecycle.SUPERSEDED
    assert store.list_claims() == []


def test_correction_rejects_invented_replacement(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "I live in Rome")
    text = "That is wrong"
    with pytest.raises(ValueError, match="replacement"):
        store.correct_claim(
            ClaimCorrection(
                memory_id=item.id,
                correction_quote=text,
                replacement_quote="I live in Turin",
            ),
            _message(store, text),
            text,
        )


def test_correction_cannot_turn_agent_hypothesis_into_user_statement(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "Avoiding calls may briefly reduce anxiety")
    text = "No, I avoid calls because I dislike interruptions"

    with pytest.raises(ValueError, match="agent hypothesis"):
        store.correct_claim(
            ClaimCorrection(
                memory_id=item.id,
                correction_quote=text,
                replacement_quote="I avoid calls because I dislike interruptions",
            ),
            _message(store, text),
            text,
        )

    unchanged = next(claim for claim in store.list_claims() if claim.id == item.id)
    assert unchanged.origin is ClaimOrigin.AGENT_HYPOTHESIS
    assert unchanged.content == item.content


def test_forgetting_archives_and_removes_formulation_link(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "Calls are difficult")
    store.save_formulation_links({"presenting_concerns": [item.id]})
    store.forget_claim(item.id)
    assert store.list_claims() == []
    assert store.load_formulation().presenting_concerns == []


def test_incompatible_active_claims_are_linked_as_conflicts(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    first = _report(store, "I live in Rome")
    second = _report(store, "I live in Milan")
    assert first.id in second.conflict_ids
    saved_first = next(item for item in store.list_claims() if item.id == first.id)
    assert second.id in saved_first.conflict_ids
    context = store.retrieve_case_context("Where do I live?")
    assert {tuple(conflict.claim_ids) for conflict in context.conflicts}


def test_different_numbers_negations_dates_people_and_places_do_not_merge(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    values = (
        "I called 2 times",
        "I called 3 times",
        "I attend work events",
        "I do not attend work events",
        "I met Alice in Rome in March",
        "I met Bruno in Milan in April",
    )
    for value in values:
        _report(store, value)
    assert len(store.list_claims()) == len(values)


def test_does_not_fit_hypothesis_is_excluded_from_formulation(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "A rejected pattern")
    message_id = _message(store, "No, that does not fit")
    store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=item.id,
                fit=ClaimFit.DOES_NOT_FIT,
                evidence_quote="does not fit",
            )
        ],
        message_id,
        "No, that does not fit",
    )
    formulation = store.save_formulation_links({"open_hypotheses": [item.id]})
    assert formulation.open_hypotheses == []


def test_descriptive_formulation_rejects_agent_hypothesis(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _hypothesis(store, "A tentative concern")
    formulation = store.save_formulation_links({"presenting_concerns": [item.id]})
    assert formulation.presenting_concerns == []


def test_formulation_omission_preserves_links_and_explicit_unlink_removes(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "Work calls are hard")
    store.save_formulation_links({"presenting_concerns": [item.id]})
    kept = store.save_formulation_links({}, merge_existing=True)
    assert kept.presenting_concerns == [item.content]
    removed = store.save_formulation_links(
        {},
        merge_existing=True,
        remove_links={"presenting_concerns": [item.id]},
    )
    assert removed.presenting_concerns == []


def test_process_preference_and_support_choice_require_exact_evidence(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    text = "I want shorter replies and I want to speak with a psychologist"
    message_id = _message(store, text)
    preference = store.record_process_preference(
        "I want shorter replies",
        "I want shorter replies",
        message_id,
        text,
    )
    support = store.record_support_choice(
        "I want to speak with a psychologist",
        "I want to speak with a psychologist",
        message_id,
        text,
    )
    assert preference.evidence.quote == "I want shorter replies"
    assert support.evidence.quote == "I want to speak with a psychologist"


def test_intervention_is_updated_in_place_with_outcome_and_harm(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    item = _report(store, "I avoid calls")
    record = store.create_intervention(
        skill="take-a-small-step",
        description="Make one short call",
        state=InterventionState.AGREED,
        linked_claim_ids=[item.id],
        evidence_message_id=1,
        consent_quote="yes",
        evidence_text="yes, let us try that",
    )
    updated = store.update_intervention(
        record.id,
        state=InterventionState.TRIED,
        evidence_message_id=2,
        outcome="I made the call",
        unwanted_effects="I felt ashamed afterward",
        decision=InterventionDecision.ADAPT,
    )
    assert updated.id == record.id
    assert updated.outcome == "I made the call"
    assert updated.unwanted_effects == "I felt ashamed afterward"
    assert len(store.list_interventions()) == 1


def test_invalid_intervention_transition_is_rejected(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    record = store.create_intervention(
        skill="solve-practical-problems",
        description="List two options",
        state=InterventionState.OFFERED,
        linked_claim_ids=[],
        evidence_message_id=1,
    )
    with pytest.raises(ValueError, match="Invalid"):
        store.update_intervention(
            record.id,
            state=InterventionState.TRIED,
            evidence_message_id=2,
        )


def test_store_with_a_non_filo_marker_is_rejected(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store._write_state("schema_version", b"2")
    with pytest.raises(MemoryError, match="unknown or newer schema"):
        MemoryStore(tmp_path)


def test_encrypted_database_does_not_contain_plaintext(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    _report(store, "sensitive-memory-marker")
    assert b"sensitive-memory-marker" not in store.database_path.read_bytes()


def test_ephemeral_store_creates_no_files(tmp_path) -> None:
    store = MemoryStore(tmp_path, ephemeral=True)
    _report(store, "Only for this process")
    assert not list(tmp_path.iterdir())


def test_session_history_and_tool_messages_are_round_tripped(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(session, "hello", "hi", [])
    assert "hello" in store.session_transcript(session.id)
    assert store.load_session_history(session.id) == []


def test_session_can_close_with_explicit_reason(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    closed = store.close_session(session, end_reason=SessionEndReason.EXPLICIT)
    assert closed.end_reason is SessionEndReason.EXPLICIT
    assert closed.ended_at is not None


def test_export_contains_epistemic_and_governance_state(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    _report(store, "I like concise questions")
    exported = store.export()
    assert exported["schema_version"] == "filo-1"
    assert exported["claims"][0]["origin"] == "user_statement"
    assert "semantic_index" not in exported


def test_unknown_app_state_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        AppState.model_validate({"unknown": True})


def test_selective_session_deletion_propagates_to_messages(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    session = store.start_session()
    store.save_turn(session, "delete me", "ok", [])
    store.close_session(session)
    assert store.delete_session(session.id)
    assert store.list_sessions() == []
    assert "delete me" not in str(store.export())


def test_delete_before_removes_old_session_and_keeps_new(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = datetime(2024, 1, 1, tzinfo=UTC)
    session = store.start_session(old)
    store.save_turn(session, "old", "old", [], old)
    store.close_session(session, now=old)
    result = store.delete_before(datetime(2025, 1, 1, tzinfo=UTC))
    assert result["sessions"] == 1


def test_delete_before_accepts_cli_style_naive_iso_date(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = datetime(2024, 1, 1, tzinfo=UTC)
    session = store.start_session(old)
    store.close_session(session, now=old)
    assert store.delete_before(datetime(2025, 1, 1))["sessions"] == 1


def test_retention_dry_run_does_not_delete_messages(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=20)
    session = store.start_session(old)
    store.save_turn(session, "old message", "reply", [], old)
    store.close_session(session, now=old)
    preview = store.apply_retention(RetentionPolicy(raw_message_days=10), dry_run=True)
    assert preview["messages"] == 2
    assert "old message" in str(store.export())


def test_retention_apply_removes_old_raw_messages(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=20)
    session = store.start_session(old)
    store.save_turn(session, "old message", "reply", [], old)
    store.close_session(session, summary="Derived old summary", now=old)
    result = store.apply_retention(RetentionPolicy(raw_message_days=10))
    assert result["messages"] == 2
    assert "old message" not in str(store.export())
    assert "Derived old summary" not in str(store.export())


def test_stale_hypothesis_is_not_linked_into_active_formulation(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    old = datetime.now(UTC) - timedelta(days=200)
    item = _hypothesis(store, "An old tentative interpretation", old)
    formulation = store.save_formulation_links({"open_hypotheses": [item.id]})
    assert formulation == CaseFormulation(last_reviewed_at=formulation.last_reviewed_at)


def test_therapist_data_directory_is_refused_without_modification(tmp_path) -> None:
    legacy = tmp_path / "legacy"
    legacy.mkdir(mode=0o755)
    (legacy / "thera.db").write_bytes(b"legacy store")
    (legacy / "memory.key").write_bytes(b"legacy key")
    os.chmod(legacy / "memory.key", 0o644)
    before = {path.name: (path.read_bytes(), path.stat().st_mode) for path in legacy.iterdir()}
    directory_mode = legacy.stat().st_mode

    with pytest.raises(MemoryError, match="Therapist store"):
        MemoryStore(legacy)

    after = {path.name: (path.read_bytes(), path.stat().st_mode) for path in legacy.iterdir()}
    assert after == before
    assert legacy.stat().st_mode == directory_mode


def test_store_with_older_schema_marker_is_rejected_without_modification(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    store._write_state("schema_version", b"3")
    with sqlite3.connect(store.database_path) as database:
        database.execute("DROP TABLE semantic_index")
    before = store.database_path.read_bytes()

    with pytest.raises(MemoryError, match="unknown or newer schema"):
        MemoryStore(tmp_path)

    assert store.database_path.read_bytes() == before
    with sqlite3.connect(store.database_path) as database:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
    assert "semantic_index" not in tables


def test_unmarked_store_with_state_table_is_rejected_without_modification(tmp_path) -> None:
    MemoryStore(tmp_path)  # creates memory.key
    database_path = tmp_path / "filo.db"
    database_path.unlink()
    with sqlite3.connect(database_path) as database:
        database.execute(
            "CREATE TABLE state (name TEXT PRIMARY KEY, payload BLOB NOT NULL, "
            "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    before = database_path.read_bytes()

    with pytest.raises(MemoryError, match="unknown or newer schema"):
        MemoryStore(tmp_path)

    assert database_path.read_bytes() == before
    with sqlite3.connect(database_path) as database:
        tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master")}
    assert tables == {"state", "sqlite_autoindex_state_1"}


def test_interrupted_initialization_leaves_an_empty_reusable_database(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(*_: object) -> None:
        raise RuntimeError("interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(MemoryStore, "_write_state", crash)
        with pytest.raises(RuntimeError, match="interrupted"):
            MemoryStore(tmp_path)

    with sqlite3.connect(tmp_path / "filo.db") as database:
        assert database.execute("SELECT name FROM sqlite_master").fetchall() == []
    assert MemoryStore(tmp_path).load_app_state() is not None
