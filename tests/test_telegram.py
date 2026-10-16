import sqlite3
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest

from filo.chat import ChatTurn, TurnStreamEvent, TurnStreamKind, crisis_resources
from filo.memory import MemoryKind, MemoryMode, MemoryStore, UserReport
from filo.telegram import (
    TelegramBot,
    TelegramChannel,
    TelegramError,
    split_message,
)


class FakeBot:
    def __init__(self, updates: list[list[dict[str, Any]]] | None = None) -> None:
        self.messages: list[tuple[int, str]] = []
        self.updates = iter(updates or [])
        self.deleted_webhook = False
        self.commands_set = False
        self.typing: list[int] = []
        self.interface: int | None = None
        self.message_drafts: list[tuple[int, int, str]] = []
        self.rich_drafts: list[tuple[int, int, str]] = []
        self.rich_messages: list[tuple[int, str]] = []

    def get_me(self) -> dict[str, str]:
        return {"username": "test_filo_bot"}

    def delete_webhook(self) -> None:
        self.deleted_webhook = True

    def configure_interface(self, chat_id: int) -> None:
        self.commands_set = True
        self.interface = chat_id

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        try:
            return next(self.updates)
        except StopIteration as error:
            raise KeyboardInterrupt from error

    def send_message(self, chat_id: int, text: str) -> None:
        self.messages.append((chat_id, text))

    def send_typing(self, chat_id: int) -> None:
        self.typing.append(chat_id)

    def send_message_draft(self, chat_id: int, draft_id: int, text: str) -> None:
        self.message_drafts.append((chat_id, draft_id, text))

    def send_rich_message_draft(self, chat_id: int, draft_id: int, text: str) -> None:
        self.rich_drafts.append((chat_id, draft_id, text))

    def send_rich_message(self, chat_id: int, text: str) -> None:
        self.rich_messages.append((chat_id, text))


class FakeSession:
    def __init__(self) -> None:
        self.received: list[str] = []
        self.end_calls = 0
        self.memory_mode = MemoryMode.STANDARD
        self.locale = "en-US"

    def respond(
        self, text: str, *, on_event: object | None = None, on_commit: object | None = None
    ) -> ChatTurn:
        self.received.append(text)
        reply = f"reply: {text}"
        if on_event:
            on_event(TurnStreamEvent(TurnStreamKind.REPLY, reply))  # type: ignore[operator]
        return _committed(ChatTurn(reply), on_commit)

    def extract_pending(self) -> int:
        return 0

    def end(self) -> None:
        self.end_calls += 1
        return None


def _committed(turn: ChatTurn, on_commit: object | None) -> ChatTurn:
    if on_commit:
        on_commit(turn)  # type: ignore[operator]
    return turn


def private_update(update_id: int, user_id: int, text: str) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {
            "from": {"id": user_id},
            "chat": {"id": user_id, "type": "private"},
            "text": text,
        },
    }


def test_channel_ignores_groups_and_users_outside_allowlist(tmp_path: Path) -> None:
    bot = FakeBot()
    session = FakeSession()
    channel = TelegramChannel(bot, session, MemoryStore(tmp_path), 42)  # type: ignore[arg-type]
    group = private_update(1, 42, "secret")
    group["message"]["chat"]["type"] = "group"

    assert channel.process_update(group) is False
    assert channel.process_update(private_update(2, 99, "secret")) is False
    assert bot.messages == []
    assert session.received == []


def test_channel_explains_unsupported_media_without_model(tmp_path: Path) -> None:
    bot = FakeBot()
    session = FakeSession()
    channel = TelegramChannel(bot, session, MemoryStore(tmp_path), 42)  # type: ignore[arg-type]
    update = private_update(1, 42, "placeholder")
    del update["message"]["text"]
    update["message"]["photo"] = [{"file_id": "opaque"}]

    assert channel.process_update(update) is True
    assert bot.messages == [
        (42, "I can only read and archive text. This content was not processed.")
    ]
    assert session.received == []


def test_channel_requires_separate_consent_then_uses_shared_session(
    tmp_path: Path,
) -> None:
    bot = FakeBot()
    session = FakeSession()
    store = MemoryStore(tmp_path)
    store_state = store.load_app_state()
    store_state.consent_version = "alpha-2"
    store.save_app_state(store_state)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Hello"))
    assert "/consent I UNDERSTAND" in bot.messages[-1][1]
    assert "Filo is experimental AI" in bot.messages[-1][1]
    assert "at least 18" in bot.messages[-1][1]
    assert "output can be wrong" in bot.messages[-1][1]
    assert "separate bounded case-data envelope" in bot.messages[-1][1]
    assert "not end-to-end encrypted" in bot.messages[-1][1]
    assert session.received == []

    channel.process_update(private_update(2, 42, "/consent I UNDERSTAND"))
    assert store.load_app_state().telegram_consent_version == "alpha-5"

    channel.process_update(private_update(3, 42, "/start"))
    assert "already recorded" in bot.messages[-1][1]

    channel.process_update(private_update(4, 42, "I feel stuck"))
    assert session.received == ["I feel stuck"]
    assert bot.rich_messages[-1] == (42, "reply: I feel stuck")
    assert bot.rich_drafts[-1][0] == 42
    assert bot.rich_drafts[-1][1] > 0
    assert bot.rich_drafts[-1][2] == "reply: I feel stuck"
    assert bot.typing == [42]


def test_crisis_command_answers_before_consent_without_model_or_archive(
    tmp_path: Path,
) -> None:
    bot = FakeBot()
    session = FakeSession()
    session.locale = "it-IT"
    store = MemoryStore(tmp_path)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]

    assert channel.process_update(private_update(1, 42, "/crisis")) is True

    assert bot.messages == [(42, crisis_resources("it-IT"))]
    assert bot.typing == []
    assert session.received == []
    assert store.list_sessions() == []
    assert store.load_app_state().telegram_consent_version is None


def test_model_failure_points_to_the_crisis_command(tmp_path: Path) -> None:
    class FailingSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            self.received.append(text)
            raise RuntimeError("provider unavailable")

    bot = FakeBot()
    session = FailingSession()
    store = MemoryStore(tmp_path)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]
    channel.process_update(private_update(1, 42, "/consent I UNDERSTAND"))

    channel.process_update(private_update(2, 42, "I am not safe"))
    assert "/crisis" in bot.messages[-1][1]

    channel.process_update(private_update(3, 42, "/crisis"))
    assert bot.messages[-1] == (42, crisis_resources("en-US"))
    assert session.received == ["I am not safe"]


def test_run_persists_update_watermark_after_dispatch(tmp_path: Path) -> None:
    update = private_update(7, 42, "/start")
    bot = FakeBot([[update, update]])
    channel = TelegramChannel(
        bot,
        FakeSession(),
        MemoryStore(tmp_path),
        42,  # type: ignore[arg-type]
    )

    with pytest.raises(KeyboardInterrupt):
        channel.run()

    assert bot.deleted_webhook is True
    assert bot.commands_set is True
    assert bot.interface == 42
    assert len(bot.messages) == 1
    assert channel.store.load_app_state().telegram_update_offset == 8


def test_run_queues_delivery_and_retries_without_reprocessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FailingDeliveryBot(FakeBot):
        failures = 1

        def send_message(self, chat_id: int, text: str) -> None:
            if self.failures:
                self.failures -= 1
                raise TelegramError("rate limited", error_code=429, retry_after=7)
            super().send_message(chat_id, text)

    bot = FailingDeliveryBot([[private_update(7, 42, "/start")], []])
    store = MemoryStore(tmp_path)
    sleeps: list[int] = []
    monkeypatch.setattr("filo.telegram.time.sleep", sleeps.append)
    channel = TelegramChannel(bot, FakeSession(), store, 42)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        channel.run()

    state = store.load_app_state()
    assert state.telegram_update_offset == 8
    assert store.list_telegram_output() == []
    assert sleeps == [7]
    assert len(bot.messages) == 1


def test_split_message_preserves_content_with_telegram_safe_chunks() -> None:
    text = ("word " * 1_100).strip()

    chunks = split_message(text)

    assert len(chunks) == 2
    assert all(len(chunk) <= 4_000 for chunk in chunks)
    assert " ".join(chunks) == text


def test_transparency_commands_show_status_memory_case_and_privacy(
    tmp_path: Path,
) -> None:
    bot = FakeBot()
    session = FakeSession()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    state.embedding_model = "sentence-transformers:test"
    store.save_app_state(state)
    active = store.start_session()
    message_id = store.save_turn(active, "I avoid calls", "What happens?", [])
    item = store.add_user_reports(
        [
            UserReport(
                kind=MemoryKind.FACT,
                content="I avoid calls",
                evidence_quote="I avoid calls",
            )
        ],
        message_id,
        "I avoid calls",
    )[0]
    formulation = store.load_formulation()
    formulation.presenting_concerns = [item.content]
    formulation.evidence = {"presenting_concerns": [item.id]}
    store.save_formulation(formulation)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]
    transcript_before = store.session_transcript(active.id)

    for update_id, command in enumerate(("/status", "/memory", "/case", "/privacy"), start=1):
        assert channel.process_update(private_update(update_id, 42, command)) is True

    output = "\n".join(message for _, message in bot.messages)
    assert "Active memory: 1 items" in output
    assert f"{item.id} · fact · user_statement" in output
    assert f"[evidence: {item.id}]" in output
    assert f"Evidence: {message_id}: I avoid calls" in output
    assert "Telegram receives messages" in output
    assert "not end-to-end encrypted" in output
    assert "Local deletion cannot remove provider" in output
    assert "Agent tool inputs and outputs are shown" in output
    assert "Internal prompts, tokens, and private reasoning are not shown" in output
    assert session.received == []
    assert store.session_transcript(active.id) == transcript_before
    assert "/status" not in store.session_transcript(active.id)


def test_memory_command_is_paginated(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    session = store.start_session()
    for index in range(12):
        message_id = store.save_turn(session, f"fact {index}", "ok", [])
        store.add_user_reports(
            [
                UserReport(
                    kind=MemoryKind.FACT,
                    content=f"fact {index}",
                    evidence_quote=f"fact {index}",
                )
            ],
            message_id,
            f"fact {index}",
        )
    channel = TelegramChannel(bot, FakeSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "/memory 2"))

    assert "page 2/2" in bot.messages[-1][1]
    assert bot.messages[-1][1].count(" · fact · user_statement") == 2


def test_normal_turn_discloses_committed_memory_change(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    active = store.start_session()

    class RecordingSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            message_id = store.save_turn(active, text, "Understood.", [])
            store.add_user_reports(
                [
                    UserReport(
                        kind=MemoryKind.FACT,
                        content=text,
                        evidence_quote=text,
                    )
                ],
                message_id,
                text,
            )
            if on_event:
                on_event(  # type: ignore[operator]
                    TurnStreamEvent(TurnStreamKind.REPLY, "Understood.")
                )
            return _committed(ChatTurn("Understood."), on_commit)

    channel = TelegramChannel(bot, RecordingSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "I work from home"))

    assert bot.rich_messages[-1] == (42, "Understood.")
    assert "THIS TURN'S RECORD" in bot.messages[-1][1]
    assert "Saved fact: I work from home" in bot.messages[-1][1]


def test_forgotten_claim_is_disclosed_by_id_without_its_content(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    active = store.start_session()
    text = "my brother crashed the boat"
    message_id = store.save_turn(active, text, "Understood.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text)],
        message_id,
        text,
    )[0]

    class ForgettingSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            request_id = store.save_turn(active, text, "Done.", [])
            store.forget_content(["my brother crashed the boat"], "forget", request_id, text)
            return _committed(ChatTurn("Done."), on_commit)

    channel = TelegramChannel(bot, ForgettingSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Please forget the boat thing"))

    assert bot.rich_messages[-1] == (42, "Done.")
    assert f"Memory forgotten [{claim.id}]" in bot.messages[-1][1]
    assert "brother" not in bot.messages[-1][1]


class SplitSession(FakeSession):
    """Records a report after the turn, as the split memory-writing arm does."""

    def __init__(self, store: MemoryStore, bot: FakeBot) -> None:
        super().__init__()
        self.store = store
        self.bot = bot
        self.message_id = 0
        self.seen_at_extraction: list[tuple[int | None, list[tuple[int, str]]]] = []

    def respond(
        self, text: str, *, on_event: object | None = None, on_commit: object | None = None
    ) -> ChatTurn:
        self.received.append(text)
        session = self.store.active_session() or self.store.start_session()
        with self.store.transaction():
            self.message_id = self.store.save_turn(session, text, "Understood.", [])
            return _committed(ChatTurn("Understood."), on_commit)

    def extract_pending(self) -> int:
        if not self.message_id:
            return 0
        self.seen_at_extraction.append(
            (self.store.load_app_state().telegram_update_offset, list(self.bot.rich_messages))
        )
        report = UserReport(
            kind=MemoryKind.FACT, content="I work from home", evidence_quote="I work from home"
        )
        self.store.add_user_reports([report], self.message_id, "I work from home")
        self.message_id = 0
        return 1


def _consented(store: MemoryStore) -> MemoryStore:
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    return store


def test_reports_extracted_after_delivery_are_disclosed_after_the_reply(tmp_path: Path) -> None:
    bot = FakeBot()
    store = _consented(MemoryStore(tmp_path))
    session = SplitSession(store, bot)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "I work from home"))

    # The offset and the reply were stored and delivered before extraction ran.
    assert session.seen_at_extraction == [(2, [(42, "Understood.")])]
    assert "Saved fact: I work from home" in bot.messages[-1][1]


def test_a_failed_extraction_notice_never_reruns_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RateLimitedNoticeBot(FakeBot):
        failures = 1

        def send_message(self, chat_id: int, text: str) -> None:
            if self.failures:
                self.failures -= 1
                raise TelegramError("rate limited", error_code=429, retry_after=7)
            super().send_message(chat_id, text)

    bot = RateLimitedNoticeBot([[private_update(7, 42, "I work from home")], []])
    store = _consented(MemoryStore(tmp_path))
    session = SplitSession(store, bot)
    monkeypatch.setattr("filo.telegram.time.sleep", lambda _seconds: None)
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        channel.run()

    assert session.received == ["I work from home"]
    assert len(store.export()["messages"]) == 2
    assert [text for _, text in bot.messages if "Saved fact" in text] != []
    assert store.load_app_state().telegram_update_offset == 8


def test_a_database_error_during_extraction_does_not_stop_the_listener(tmp_path: Path) -> None:
    class BrokenStoreSession(FakeSession):
        def extract_pending(self) -> int:
            raise sqlite3.OperationalError("disk I/O error")

    bot = FakeBot([[private_update(7, 42, "hello")], [private_update(8, 42, "again")]])
    store = _consented(MemoryStore(tmp_path))
    session = BrokenStoreSession()
    channel = TelegramChannel(bot, session, store, 42)  # type: ignore[arg-type]

    with pytest.raises(KeyboardInterrupt):
        channel.run()

    assert session.received == ["hello", "again"]


def test_normal_turn_sends_tool_input_and_output_before_reply(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)

    class ToolSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            self.received.append(text)
            assert on_event is not None
            on_event(  # type: ignore[operator]
                TurnStreamEvent(
                    TurnStreamKind.TOOL_INPUT,
                    'TOOL INPUT · record_user_reports\n{"content": "detail"}',
                )
            )
            on_event(  # type: ignore[operator]
                TurnStreamEvent(
                    TurnStreamKind.TOOL_OUTPUT,
                    'TOOL OUTPUT · record_user_reports · success\n{"staged": 1}',
                )
            )
            on_event(  # type: ignore[operator]
                TurnStreamEvent(TurnStreamKind.REPLY, "Done.")
            )
            return _committed(
                ChatTurn(
                    "Done.",
                    tool_trace=(
                        'TOOL INPUT · record_user_reports\n{"content": "detail"}\n\n'
                        'TOOL OUTPUT · record_user_reports · success\n{"staged": 1}'
                    ),
                ),
                on_commit,
            )

    channel = TelegramChannel(bot, ToolSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Remember this"))

    assert bot.messages == [
        (42, 'TOOL INPUT · record_user_reports\n{"content": "detail"}'),
        (42, 'TOOL OUTPUT · record_user_reports · success\n{"staged": 1}'),
    ]
    assert bot.rich_messages == [(42, "Done.")]


def test_bot_configures_commands_only_for_allowlisted_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = TelegramBot("token")
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        bot,
        "_call",
        lambda method, payload: calls.append((method, payload)),
    )

    bot.configure_interface(42)

    assert calls[0] == ("deleteMyCommands", {})
    commands = calls[1][1]
    assert commands["scope"] == {"type": "chat", "chat_id": 42}
    assert {item["command"] for item in commands["commands"]} == {
        "start",
        "help",
        "crisis",
        "status",
        "case",
        "memory",
        "sessions",
        "interventions",
        "privacy",
        "recap",
        "wellbeing",
        "retry",
        "end",
    }
    assert calls[2][0] == "setMyDescription"
    assert "PRIVACY.md" in calls[2][1]["description"]


def test_bot_uses_native_rich_draft_and_final_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot = TelegramBot("token")
    calls: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(
        bot,
        "_call",
        lambda method, payload: calls.append((method, payload)),
    )

    bot.send_rich_message_draft(42, 7, "**Partial**")
    bot.send_rich_message(42, "**Complete**")

    assert calls == [
        (
            "sendRichMessageDraft",
            {
                "chat_id": 42,
                "draft_id": 7,
                "rich_message": {"markdown": "**Partial**"},
            },
        ),
        (
            "sendRichMessage",
            {"chat_id": 42, "rich_message": {"markdown": "**Complete**"}},
        ),
    ]


def test_bot_preserves_retry_after_from_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = BytesIO(
        b'{"ok":false,"error_code":429,"description":"Too Many Requests",'
        b'"parameters":{"retry_after":6}}'
    )
    error = HTTPError(
        "https://api.telegram.org",
        429,
        "Too Many Requests",
        hdrs=None,
        fp=body,
    )
    monkeypatch.setattr(
        "filo.telegram.urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    with pytest.raises(TelegramError) as raised:
        TelegramBot("token").get_me()

    assert raised.value.error_code == 429
    assert raised.value.retry_after == 6
    assert not raised.value.fatal


def test_channel_throttles_streamed_drafts_with_one_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)

    class StreamingSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            assert on_event is not None
            for partial in ("**One", "**One two", "**One two three**"):
                on_event(  # type: ignore[operator]
                    TurnStreamEvent(TurnStreamKind.REPLY, partial)
                )
            return _committed(ChatTurn("**One two three**"), on_commit)

    times = iter((1.0, 1.1, 1.3))
    monkeypatch.setattr("filo.telegram.time.monotonic", lambda: next(times))
    monkeypatch.setattr("filo.telegram.secrets.randbits", lambda _: 99)
    channel = TelegramChannel(bot, StreamingSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Stream this"))

    assert bot.rich_drafts == [
        (42, 99, "**One"),
        (42, 99, "**One two three**"),
    ]
    assert bot.rich_messages == [(42, "**One two three**")]


def test_channel_bounds_rejected_draft_to_plain_message_limit(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)

    class LongDraftSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            assert on_event is not None
            on_event(TurnStreamEvent(TurnStreamKind.REPLY, "x" * 5_000))  # type: ignore[operator]
            return _committed(ChatTurn("Done."), on_commit)

    channel = TelegramChannel(bot, LongDraftSession(), store, 42)  # type: ignore[arg-type]
    channel.process_update(private_update(1, 42, "Stream this"))

    assert len(bot.rich_drafts[0][2]) == 4_000
    assert bot.rich_messages == [(42, "Done.")]


def test_channel_rate_limits_draft_attempts_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class RateLimitedDraftBot(FakeBot):
        def __init__(self) -> None:
            super().__init__()
            self.draft_attempts: list[tuple[int, int, str]] = []

        def send_rich_message_draft(self, chat_id: int, draft_id: int, text: str) -> None:
            self.draft_attempts.append((chat_id, draft_id, text))
            raise TelegramError("rate limited", error_code=429, retry_after=5)

    class StreamingSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            assert on_event is not None
            for partial in ("One", "One two", "One two three", "One two three four"):
                on_event(TurnStreamEvent(TurnStreamKind.REPLY, partial))  # type: ignore[operator]
            return _committed(ChatTurn("Done."), on_commit)

    bot = RateLimitedDraftBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    times = iter((1.0, 1.1, 1.3, 6.1))
    monkeypatch.setattr("filo.telegram.time.monotonic", lambda: next(times))
    channel = TelegramChannel(bot, StreamingSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Stream this"))

    assert [attempt[2] for attempt in bot.draft_attempts] == [
        "One",
        "One two three four",
    ]


def test_channel_uses_plain_delivery_for_unsafe_markdown(tmp_path: Path) -> None:
    bot = FakeBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)

    class UnsafeSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            reply = "![remote image](https://example.com/image.png)"
            assert on_event is not None
            on_event(TurnStreamEvent(TurnStreamKind.REPLY, reply))  # type: ignore[operator]
            return _committed(ChatTurn(reply), on_commit)

    channel = TelegramChannel(bot, UnsafeSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Show something"))

    assert bot.rich_drafts == []
    assert bot.message_drafts[0][2].startswith("![remote image]")
    assert bot.rich_messages == []
    assert bot.messages[-1][1].startswith("![remote image]")


def test_channel_falls_back_to_plain_only_for_rejected_rich_format(
    tmp_path: Path,
) -> None:
    class RejectedRichBot(FakeBot):
        def send_rich_message(self, chat_id: int, text: str) -> None:
            raise TelegramError("bad rich format", error_code=400)

    bot = RejectedRichBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    channel = TelegramChannel(bot, FakeSession(), store, 42)  # type: ignore[arg-type]

    channel.process_update(private_update(1, 42, "Hello"))

    assert bot.messages[-1] == (42, "reply: Hello")


def test_channel_does_not_plain_fallback_after_transient_rich_error(
    tmp_path: Path,
) -> None:
    class UnavailableRichBot(FakeBot):
        def send_rich_message(self, chat_id: int, text: str) -> None:
            raise TelegramError("rate limited", error_code=429, retry_after=5)

    bot = UnavailableRichBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)
    channel = TelegramChannel(bot, FakeSession(), store, 42)  # type: ignore[arg-type]

    with pytest.raises(TelegramError, match="rate limited"):
        channel.process_update(private_update(1, 42, "Hello"))

    assert bot.messages == []


def test_channel_retries_missing_tool_events_separately_without_duplicates(
    tmp_path: Path,
) -> None:
    class FirstToolFailureBot(FakeBot):
        failed = False

        def send_message(self, chat_id: int, text: str) -> None:
            if not self.failed and text.startswith("TOOL INPUT"):
                self.failed = True
                raise TelegramError("temporary failure")
            super().send_message(chat_id, text)

    bot = FirstToolFailureBot()
    store = MemoryStore(tmp_path)
    state = store.load_app_state()
    state.telegram_consent_version = "alpha-5"
    store.save_app_state(state)

    class ToolSession(FakeSession):
        def respond(
            self, text: str, *, on_event: object | None = None, on_commit: object | None = None
        ) -> ChatTurn:
            assert on_event is not None
            on_event(TurnStreamEvent(TurnStreamKind.TOOL_INPUT, "TOOL INPUT · test"))  # type: ignore[operator]
            on_event(TurnStreamEvent(TurnStreamKind.TOOL_OUTPUT, "TOOL OUTPUT · test"))  # type: ignore[operator]
            on_event(TurnStreamEvent(TurnStreamKind.REPLY, "Done."))  # type: ignore[operator]
            return _committed(ChatTurn("Done."), on_commit)

    channel = TelegramChannel(bot, ToolSession(), store, 42)  # type: ignore[arg-type]
    channel.process_update(private_update(1, 42, "Use a tool"))

    assert bot.messages == [
        (42, "TOOL INPUT · test"),
        (42, "TOOL OUTPUT · test"),
    ]
    assert bot.rich_messages == [(42, "Done.")]
