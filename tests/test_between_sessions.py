"""Wave 5: help between sessions (experiment follow-up, recap, wellbeing check)."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai import models
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from test_telegram_delivery import FakeBotApi, _channel, _update

from filo.chat import CASE_CONTEXT_PREAMBLE, ChatSession
from filo.cli import _chat_command, main
from filo.memory import (
    ClaimCorrection,
    ClaimFit,
    ClaimLifecycle,
    HypothesisReview,
    InterventionState,
    MemoryKind,
    MemoryMode,
    MemoryStore,
    RetentionPolicy,
    UserReport,
)
from filo.protocol import ProtocolPack
from filo.recap import build_recap, render_recap
from filo.wellbeing import ITEMS_EN, ITEMS_IT, parse_answers, wellbeing_command, wellbeing_offer

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


def test_deleting_an_experiments_session_clears_when_it_was_planned(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    _experiment(store)

    store.delete_session(store.active_session().id)  # type: ignore[union-attr]

    (record,) = store.list_interventions()
    assert record.state is InterventionState.STOPPED
    assert record.planned_for is None
    assert record.prediction is None


def _closed_session(store: MemoryStore, when: datetime, text: str, themes: list[str]) -> int:
    session = store.start_session(when)
    message_id = store.save_turn(session, text, "I hear you.", [], when)
    store.close_session(
        session, summary=f"Talked about {themes[0]}", themes=themes, now=when + timedelta(hours=1)
    )
    return message_id


def _recap_store(tmp_path: Path) -> tuple[MemoryStore, dict[str, str]]:
    store = MemoryStore(tmp_path)
    _closed_session(store, T0 - timedelta(days=40), "Old worries", ["work calls"])
    old_session = store.list_sessions()[-1].id
    _closed_session(store, T0, "Calls scare me", ["work calls", "sleep"])
    experiment = _experiment(store, when=T0 + timedelta(days=2))
    store.update_intervention(
        experiment.id,
        state=InterventionState.TRIED,
        evidence_message_id=1,
        consent_quote="I did it",
        evidence_text="I did it and it went ok",
        outcome="it went ok",
        now=T0 + timedelta(days=12),
    )
    store.close_session(store.active_session(), now=T0 + timedelta(days=2, hours=1))  # type: ignore[arg-type]
    message_id = _closed_session(
        store, T0 + timedelta(days=10), "I sleep six hours on weekdays", ["work calls"]
    )
    (report,) = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.FACT,
                content="I sleep six hours on weekdays",
                evidence_quote="I sleep six hours on weekdays",
            )
        ],
        message_id,
        "I sleep six hours on weekdays",
        now=T0 + timedelta(days=10),
    )
    store.record_wellbeing_check([3, 3, 2, 2, 4], now=T0 + timedelta(days=15))
    return store, {"experiment": experiment.id, "report": report.id, "old_session": old_session}


def test_recap_draws_only_on_records_in_the_period(tmp_path: Path) -> None:
    store, ids = _recap_store(tmp_path)

    recap = build_recap(store, 4, now=T0 + timedelta(days=20))

    assert [item.theme for item in recap.recurring_themes] == ["work calls"]
    assert len(recap.recurring_themes[0].session_ids) == 2  # the 40-day-old session is outside
    assert [item.id for item in recap.experiments] == [ids["experiment"]]
    assert recap.experiments[0].outcome == "it went ok"
    assert [item.id for item in recap.new_reports] == [ids["report"]]
    assert recap.new_reports[0].evidence_message_ids
    assert [item.percentage for item in recap.wellbeing] == [56]
    rendered = render_recap(recap)
    assert ids["experiment"] in rendered and ids["report"] in rendered
    assert ids["old_session"] not in rendered
    assert ids["old_session"] not in recap.model_dump_json()


def test_an_empty_recap_says_so(tmp_path: Path) -> None:
    rendered = render_recap(build_recap(MemoryStore(tmp_path), 4))

    assert "Nothing recorded in this period yet." in rendered


def test_recap_command_is_read_only_and_outside_the_conversation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store, ids = _recap_store(tmp_path)
    session = ChatSession("test", PACK, store, "en-US")
    before = store.export()["messages"]

    assert _chat_command("/recap 52", session, store)

    assert ids["experiment"] in capsys.readouterr().out
    assert store.export()["messages"] == before


def test_the_model_can_ask_for_a_recap(tmp_path: Path) -> None:
    store, ids = _recap_store(tmp_path)
    returned: list[str] = []

    async def stream(messages: list[Any], _info: Any):
        returns = [
            part.content
            for message in messages
            for part in message.parts
            if getattr(part, "part_kind", "") == "tool-return"
        ]
        if returns:
            returned.append(json.dumps(returns[-1]))
            yield "Here is what changed."
            return
        yield {
            0: DeltaToolCall(
                name="retrieve_case_context",
                json_args=json.dumps({"query": "recap", "recap_weeks": 52}),
                tool_call_id="recap",
            )
        }

    turn = ChatSession(FunctionModel(stream_function=stream), PACK, store, "en-US").respond(
        "What changed over the last weeks?"
    )

    (payload,) = returned
    assert ids["experiment"] in payload and '"recap"' in payload
    assert '"wellbeing": []' in payload  # scores stay on the device
    assert turn.metadata is not None
    assert turn.metadata.referenced_intervention_ids == [ids["experiment"]]
    assert ids["report"] in turn.metadata.referenced_claim_ids


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("3 4 2 3 4", [3, 4, 2, 3, 4]),
        ("3,4,2,3,4", [3, 4, 2, 3, 4]),
        ("34234", [3, 4, 2, 3, 4]),
        ("3 4 2 3", None),
        ("3 4 2 3 6", None),
        ("3 4 2 3 4 1", None),
        ("a b c d e", None),
    ],
)
def test_wellbeing_answers_are_five_numbers_from_zero_to_five(
    argument: str, expected: list[int] | None
) -> None:
    assert parse_answers(argument) == expected


@pytest.mark.parametrize("locale", ["en-US", "it-IT"])
def test_wellbeing_check_is_recorded_encrypted_with_a_trend_and_no_labels(
    tmp_path: Path, locale: str
) -> None:
    store = MemoryStore(tmp_path)

    view = wellbeing_command(None, checks=store, settings=store, locale=locale)
    first = wellbeing_command("2 2 2 2 2", checks=store, settings=store, locale=locale, now=T0)
    second = wellbeing_command(
        "3 3 3 3 3", checks=store, settings=store, locale=locale, now=T0 + timedelta(days=14)
    )

    items = ITEMS_IT if locale == "it-IT" else ITEMS_EN
    assert all(item in view for item in items)
    assert "CC BY-NC-SA 3.0 IGO" in view
    assert "10/25" in first and "40" in first
    assert "60 (+20)" in second
    for text in (view, first, second):
        lowered = text.lower()
        for word in ("depress", "cutoff", "cut-off", "screening", "poor", "low well"):
            assert word not in lowered
    assert [check.raw_score for check in store.list_wellbeing_checks()] == [10, 15]
    assert b"answers" not in store.database_path.read_bytes()


def test_wellbeing_is_offered_unprompted_once_then_only_if_enabled(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    def offer(day: int) -> str | None:
        return wellbeing_offer(
            checks=store, settings=store, locale="en-US", now=T0 + timedelta(days=day)
        )

    assert "I will not ask again" in (offer(0) or "")
    assert offer(1) is None
    assert offer(30) is None  # not enabled, so never again

    wellbeing_command("on", checks=store, settings=store, locale="en-US")
    assert offer(31) is not None
    assert offer(40) is None  # within two weeks of the last offer
    store.record_wellbeing_check([3, 3, 3, 3, 3], now=T0 + timedelta(days=44))
    assert offer(50) is None  # within two weeks of the last check
    assert offer(60) is not None

    wellbeing_command("off", checks=store, settings=store, locale="en-US")
    assert offer(90) is None


def test_wellbeing_checks_follow_the_memory_lifecycle(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    store = MemoryStore(tmp_path)
    old = store.record_wellbeing_check([1, 1, 1, 1, 1], now=T0 - timedelta(days=60))
    recent = store.record_wellbeing_check([4, 4, 4, 4, 4], now=T0)

    assert [item["raw_score"] for item in store.export()["wellbeing_checks"]] == [5, 20]

    assert main(["--data-dir", str(tmp_path), "memory", "wellbeing"]) == 0
    assert "80 (+60)" in capsys.readouterr().out

    removed = store.apply_retention(RetentionPolicy(raw_message_days=30), now=T0)
    assert removed["wellbeing_checks"] == 1
    assert [item.id for item in store.list_wellbeing_checks()] == [recent.id]
    assert not store.forget_wellbeing_check(old.id)

    assert main(["--data-dir", str(tmp_path), "memory", "forget", recent.id]) == 0
    assert store.list_wellbeing_checks() == []

    store.record_wellbeing_check([2, 2, 2, 2, 2])
    store.delete_all()
    assert store.list_wellbeing_checks() == []


def test_telegram_wellbeing_and_end_offer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeBotApi(_update())
    monkeypatch.setattr("filo.telegram.urlopen", api)
    channel, store = _channel(tmp_path, [])

    def send(update_id: int, text: str) -> str:
        update = _update()
        update["update_id"] = update_id
        update["message"]["text"] = text
        channel.process_update(update)
        return api.delivered[-1][1]

    assert "I have felt calm and relaxed" in send(50, "/wellbeing")
    assert "Recorded: 15/25" in send(51, "/wellbeing 3 3 3 3 3")
    assert len(store.list_wellbeing_checks()) == 1
    send(52, "/end")
    assert "I will not ask again" in api.delivered[-1][1]
    send(53, "/end")
    assert "I will not ask again" not in api.delivered[-1][1]
    assert "WHO-5" in send(54, "/recap")


def test_transcript_only_mode_records_no_wellbeing_check(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    session = ChatSession("test", PACK, store, "en-US", memory_mode=MemoryMode.TRANSCRIPT_ONLY)

    assert _chat_command("/wellbeing 3 3 3 3 3", session, store)

    assert store.list_wellbeing_checks() == []


def _hypothesis(store: MemoryStore, report_id: str, when: datetime) -> str:
    message_id = store.save_turn(
        store.active_session() or store.start_session(when),
        "yes, that fits me",
        "Noted.",
        [],
        when,
    )
    (report,) = [item for item in store.list_claims() if item.id == report_id]
    hypothesis = store.add_hypothesis(
        "Short sleep may make the calls feel harder",
        linked_claim_ids=[report_id],
        evidence_message_ids=[reference.message_id for reference in report.evidence],
        now=when,
    )
    store.review_hypotheses(
        [
            HypothesisReview(
                memory_id=hypothesis.id, fit=ClaimFit.FITS, evidence_quote="that fits me"
            )
        ],
        message_id,
        "yes, that fits me",
        now=when,
    )
    return hypothesis.id


def test_recap_never_brings_back_corrected_forgotten_or_archived_wording(tmp_path: Path) -> None:
    store, ids = _recap_store(tmp_path)
    when = T0 + timedelta(days=12)
    hypothesis_id = _hypothesis(store, ids["report"], when)
    message_id = store.save_turn(
        store.active_session(),  # type: ignore[arg-type]
        "that is wrong, I sleep fine",
        "Thanks for correcting me.",
        [],
        when,
    )
    store.correct_claim(
        ClaimCorrection(memory_id=ids["report"], correction_quote="that is wrong"),
        message_id,
        "that is wrong, I sleep fine",
        now=when,
    )
    store.forget_claim(hypothesis_id, now=when)

    recap = build_recap(store, 4, now=T0 + timedelta(days=20))
    payload = recap.model_dump_json() + render_recap(recap)

    assert "I sleep six hours on weekdays" not in payload
    assert "Short sleep may make the calls feel harder" not in payload
    assert hypothesis_id not in [item.id for item in recap.reviewed_hypotheses]
    (corrected,) = [item for item in recap.corrected if item.id == ids["report"]]
    assert corrected.content is None and corrected.evidence_message_ids == []
    assert f"{ids['report']}: no longer held" in render_recap(recap)
    assert [item.id for item in recap.new_reports] == []


def test_recap_drops_a_hypothesis_archived_by_retention(tmp_path: Path) -> None:
    store, ids = _recap_store(tmp_path)
    hypothesis_id = _hypothesis(store, ids["report"], T0 + timedelta(days=12))
    (hypothesis,) = [item for item in store.list_claims() if item.id == hypothesis_id]
    hypothesis.lifecycle = ClaimLifecycle.ARCHIVED  # what retention does to a stale hypothesis
    store._save_claim(hypothesis)

    recap = build_recap(store, 4, now=T0 + timedelta(days=20))

    assert recap.reviewed_hypotheses == []
    assert "Short sleep" not in render_recap(recap)


def test_the_model_recap_carries_no_wellbeing_scores(tmp_path: Path) -> None:
    store, _ids = _recap_store(tmp_path)

    recap = build_recap(store, 52, include_wellbeing=False)

    assert recap.wellbeing == []


def test_a_redelivered_telegram_wellbeing_update_records_one_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("filo.telegram.urlopen", FakeBotApi(_update()))
    channel, store = _channel(tmp_path, [])
    update = _update()
    update["message"]["text"] = "/wellbeing 3 3 3 3 3"
    queue = store.queue_telegram_output
    crashed = False

    def crash_once(*args: Any, **kwargs: Any) -> bool:
        nonlocal crashed
        if not crashed:
            crashed = True
            raise OSError("disk full")
        return queue(*args, **kwargs)

    monkeypatch.setattr(store, "queue_telegram_output", crash_once)

    with pytest.raises(OSError):
        channel.accept_update(update)  # the reply and offset failed, so the check rolled back
    assert store.list_wellbeing_checks() == []

    channel.process_update(update)  # Telegram hands the same update over again

    assert len(store.list_wellbeing_checks()) == 1
    assert store.load_app_state().telegram_update_offset == update["update_id"] + 1


@pytest.mark.parametrize("mode", [MemoryMode.EPHEMERAL, MemoryMode.TRANSCRIPT_ONLY])
def test_modes_that_keep_no_checks_never_offer_one(tmp_path: Path, mode: MemoryMode) -> None:
    disk = MemoryStore(tmp_path)
    memory = MemoryStore(ephemeral=True) if mode is MemoryMode.EPHEMERAL else disk
    session = ChatSession("test", PACK, memory, "en-US", memory_mode=mode)
    outputs: list[str] = []

    assert _chat_command("/end", session, disk, output=outputs.append)

    assert not any("WHO-5" in text for text in outputs)
    state = disk.load_app_state()
    assert not state.wellbeing_invited and state.wellbeing_offered_at is None


def test_delete_before_removes_older_wellbeing_checks(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    store.record_wellbeing_check([1, 1, 1, 1, 1], now=T0 - timedelta(days=10))
    recent = store.record_wellbeing_check([4, 4, 4, 4, 4], now=T0 + timedelta(days=10))

    assert store.delete_before(T0)["wellbeing_checks"] == 1

    assert [item.id for item in store.list_wellbeing_checks()] == [recent.id]
