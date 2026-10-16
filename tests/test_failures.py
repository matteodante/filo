from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.function import FunctionModel
from test_telegram_delivery import USER_TEXT, FakeBotApi, _archived_user_messages, _channel, _update

import filo.auth as auth
from filo.chat import ChatSession
from filo.failures import ContextFull, FailureKind, SetupProblem, classify
from filo.memory import MemoryMode, MemoryStore, TelegramDelivery
from filo.protocol import ProtocolPack
from filo.telegram import (
    CONSENT_VERSION,
    TELEGRAM_SECRET,
    TelegramBot,
    TelegramChannel,
    _ConfigurationChanged,
)

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def _http(status: int, body: object = None) -> ModelHTTPError:
    return ModelHTTPError(status_code=status, model_name="gpt-5.6-sol", body=body)


def test_usage_limit_reports_when_it_resets() -> None:
    relative = classify(
        _http(429, {"error": {"type": "usage_limit_reached", "resets_in_seconds": 7_500}}), NOW
    )
    absolute = classify(
        _http(429, {"type": "usage_limit_reached", "resets_at": NOW.timestamp() + 600}), NOW
    )
    unknown = classify(_http(429, {"error": {"type": "usage_limit_reached"}}), NOW)

    assert relative.kind is FailureKind.USAGE_LIMIT
    assert "in about 2 h 5 min" in relative.message
    assert "in about 10 min" in absolute.message
    assert "try again later" in unknown.message


def test_rate_limit_without_a_usage_limit_is_transient() -> None:
    failure = classify(_http(429, {"error": {"code": "rate_limit_exceeded"}}), NOW)

    assert failure.kind is FailureKind.TRANSIENT
    assert "rate limiting" in failure.message


@pytest.mark.parametrize(
    "error",
    [
        auth.LoginRequired("Not logged in."),
        auth._HTTPError(400, '{"error": "invalid_grant"}'),
        _http(401),
        _http(403, {"error": {"code": "invalid_token"}}),
    ],
)
def test_login_problems_ask_for_filo_auth_login(error: Exception) -> None:
    failure = classify(error, NOW)

    assert failure.kind is FailureKind.LOGIN
    assert "filo auth login" in failure.message


def test_login_problem_is_found_through_a_wrapping_error() -> None:
    try:
        try:
            raise _http(401)
        except ModelHTTPError as cause:
            raise RuntimeError("wrapped") from cause
    except RuntimeError as error:
        assert classify(error, NOW).kind is FailureKind.LOGIN


def test_a_403_without_an_auth_code_is_not_a_login_problem() -> None:
    failure = classify(_http(403, "<html>challenge</html>"), NOW)

    assert failure.kind is FailureKind.TRANSIENT
    assert "refused" in failure.message


@pytest.mark.parametrize(
    "detail",
    [
        {"type": "usage_limit_reached", "resets_in_seconds": float("inf")},
        {"type": "usage_limit_reached", "resets_in_seconds": 10**30},
        {"type": "usage_limit_reached", "resets_at": float("inf")},
        {"type": "usage_limit_reached", "resets_at": 10**30},
        {"type": "usage_limit_reached", "resets_at": float("nan")},
    ],
)
def test_classify_never_raises_on_extreme_reset_times(detail: dict[str, object]) -> None:
    failure = classify(_http(429, {"error": detail}), NOW)

    assert failure.kind in {FailureKind.USAGE_LIMIT, FailureKind.TRANSIENT}
    assert failure.notice()


def test_an_unreachable_token_endpoint_is_transient() -> None:
    assert classify(auth.AuthError("network down"), NOW).kind is FailureKind.TRANSIENT


@pytest.mark.parametrize(
    "error",
    [
        ContextFull("too long"),
        _http(400, {"error": {"code": "context_length_exceeded", "message": "user text"}}),
    ],
)
def test_a_full_context_points_to_end(error: Exception) -> None:
    failure = classify(error, NOW)

    assert failure.kind is FailureKind.CONTEXT_FULL
    assert "/end" in failure.message


def test_setup_problem_keeps_its_instruction() -> None:
    failure = classify(SetupProblem("Run `filo memory model install`."), NOW)

    assert failure.kind is FailureKind.SETUP
    assert failure.message == "Run `filo memory model install`."


def test_provider_text_never_reaches_the_notice() -> None:
    failure = classify(_http(500, {"error": {"message": "echo of a private message"}}), NOW)

    assert failure.kind is FailureKind.TRANSIENT
    assert "private" not in failure.notice()
    assert "/retry" in failure.notice()
    assert "/crisis" in failure.notice()


def test_telegram_retries_the_failed_message_and_records_the_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeBotApi(_update())
    monkeypatch.setattr("filo.telegram.urlopen", api)
    model_calls: list[str] = []
    channel, store = _channel(tmp_path, model_calls)
    queue = store.queue_telegram_output
    failed = False

    def failing_once(deliveries: list[TelegramDelivery], **kwargs: Any) -> bool:
        nonlocal failed
        if deliveries and not failed:
            failed = True
            raise OSError("disk full")
        return queue(deliveries, **kwargs)

    monkeypatch.setattr(store, "queue_telegram_output", failing_once)

    channel.process_update(_update())

    assert channel.session.failed_text == USER_TEXT
    assert _archived_user_messages(store) == []
    assert store.last_failure()["kind"] == "transient"  # type: ignore[index]
    assert "/retry" in api.delivered[-1][1]

    retry = _update()
    retry["update_id"] = 42
    retry["message"]["text"] = "/retry"
    channel.process_update(retry)

    assert channel.session.failed_text is None
    assert _archived_user_messages(store) == [USER_TEXT]

    nothing = _update()
    nothing["update_id"] = 43
    nothing["message"]["text"] = "/retry"
    channel.process_update(nothing)

    assert api.delivered[-1][1] == "Nothing to retry."


def _failing_model(message: str) -> FunctionModel:
    async def stream(_messages: list[Any], _info: Any):
        raise RuntimeError(message)
        yield ""  # pragma: no cover - makes this an async generator

    return FunctionModel(stream_function=stream)


def _failed_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, MemoryStore]:
    monkeypatch.setattr("filo.telegram.urlopen", FakeBotApi(_update()))
    channel, store = _channel(tmp_path, [])
    channel.session.model = _failing_model("provider down")
    channel.process_update(_update())
    assert channel.session.failed_text == USER_TEXT
    return channel, store


def test_a_local_deletion_drops_the_failed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel, store = _failed_turn(tmp_path, monkeypatch)

    store.delete_all()

    assert channel.session.failed_text is None


def test_end_drops_the_failed_message(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    channel, _store = _failed_turn(tmp_path, monkeypatch)

    channel.session.end()

    assert channel.session.failed_text is None


def test_a_configuration_reload_drops_the_failed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    channel, store = _failed_turn(tmp_path, monkeypatch)
    store.save_secret(TELEGRAM_SECRET, b"token-a")
    state = store.load_app_state()
    state.telegram_allowed_user_id = 42
    store.save_app_state(state)
    reloads: list[bool] = []

    def listen() -> None:
        if not reloads:
            reloads.append(True)
            raise _ConfigurationChanged
        raise KeyboardInterrupt

    monkeypatch.setattr(channel, "_listen", listen)
    monkeypatch.setattr("filo.telegram.TelegramBot", lambda token: object())

    with pytest.raises(KeyboardInterrupt):
        channel.run()

    assert channel.session.failed_text is None


def test_ephemeral_failures_leave_no_trace_on_disk_or_in_the_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("filo.telegram.urlopen", FakeBotApi(_update()))
    disk = MemoryStore(tmp_path)
    state = disk.load_app_state()
    state.telegram_consent_version = CONSENT_VERSION
    disk.save_app_state(state)
    memory = MemoryStore(ephemeral=True)
    session = ChatSession(
        _failing_model("echo of a private message"),
        ProtocolPack.load(Path("protocols/transdiagnostic")),
        memory,
        "en-US",
        memory_mode=MemoryMode.EPHEMERAL,
        deletion_epoch=disk.telegram_output_epoch,
    )
    channel = TelegramChannel(
        TelegramBot("synthetic-token"), session, memory, 42, configuration_store=disk
    )

    channel.process_update(_update())

    assert disk.last_failure() is None
    log = capsys.readouterr().out
    assert "Model error (transient)" in log
    assert "private message" not in log
    assert session.failed_text == USER_TEXT
