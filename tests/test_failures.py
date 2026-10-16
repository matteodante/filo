from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from test_telegram_delivery import USER_TEXT, FakeBotApi, _archived_user_messages, _channel, _update

import filo.auth as auth
from filo.failures import ContextFull, FailureKind, SetupProblem, classify
from filo.memory import TelegramDelivery

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
        _http(403),
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
