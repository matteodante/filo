"""Fault injection for Telegram delivery with a real conversation turn.

The model is a PydanticAI ``FunctionModel`` and the Bot API is faked at the
``urlopen`` boundary, so ``ChatSession``, the encrypted store, ``TelegramBot``
error wrapping, and ``TelegramChannel`` all run unmodified.
"""

import http.client
import json
from collections import Counter
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.error import HTTPError

import pytest
from pydantic_ai import models
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from therapist.chat import ChatSession
from therapist.memory import MemoryStore
from therapist.protocol import ProtocolPack
from therapist.telegram import CONSENT_VERSION, TelegramBot, TelegramChannel

models.ALLOW_MODEL_REQUESTS = False
USER_TEXT = "I work from home"
REPLY = "Working from home shapes a lot of the day."
SEND_METHODS = (
    "sendChatAction",
    "sendMessage",
    "sendMessageDraft",
    "sendRichMessage",
    "sendRichMessageDraft",
)


def _http_error(code: int, retry_after: int | None = None) -> HTTPError:
    body: dict[str, Any] = {"ok": False, "error_code": code, "description": "synthetic"}
    if retry_after is not None:
        body["parameters"] = {"retry_after": retry_after}
    return HTTPError(
        "https://api.telegram.org",
        code,
        "synthetic",
        hdrs=None,  # type: ignore[arg-type]
        fp=BytesIO(json.dumps(body).encode()),
    )


ERRORS: dict[str, Callable[[], BaseException]] = {
    "rate_limit": lambda: _http_error(429, retry_after=2),
    "server_error": lambda: _http_error(502),
    "connection_reset": lambda: ConnectionResetError(104, "Connection reset by peer"),
    "remote_disconnected": lambda: http.client.RemoteDisconnected("closed"),
}


class FakeBotApi:
    """Records Bot API calls and raises one injected failure at a chosen send."""

    def __init__(self, update: dict[str, Any], polls: int = 4) -> None:
        self.update = update
        self.polls = polls
        self.calls: Counter[str] = Counter()
        self.delivered: list[tuple[str, str]] = []
        self.fail_at: tuple[str, int] | None = None
        self.error: Callable[[], BaseException] | None = None

    def __call__(self, request: Any, timeout: float = 0) -> BytesIO:
        method = request.full_url.rsplit("/", 1)[-1]
        payload = json.loads(request.data)
        self.calls[method] += 1
        if (method, self.calls[method]) == self.fail_at:
            assert self.error is not None
            raise self.error()
        result: Any = True
        if method == "getMe":
            result = {"username": "synthetic_bot"}
        elif method == "getUpdates":
            self.polls -= 1
            if self.polls < 0:
                raise KeyboardInterrupt
            offset = payload.get("offset") or 0
            result = [self.update] if offset <= self.update["update_id"] else []
        elif method == "sendMessage":
            self.delivered.append((method, payload["text"]))
        elif method == "sendRichMessage":
            self.delivered.append((method, payload["rich_message"]["markdown"]))
        return BytesIO(json.dumps({"ok": True, "result": result}).encode())


def _update() -> dict[str, Any]:
    return {
        "update_id": 41,
        "message": {
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "text": USER_TEXT,
        },
    }


def _counting_model(calls: list[str]) -> FunctionModel:
    async def stream(messages: list[Any], _info: Any):
        calls.append("request")
        returned = any(
            getattr(part, "part_kind", "") == "tool-return"
            for message in messages[-1:]
            for part in message.parts
        )
        if not returned:
            yield {
                0: DeltaToolCall(
                    name="record_user_reports",
                    json_args=json.dumps(
                        {
                            "reports": [
                                {
                                    "kind": "fact",
                                    "content": USER_TEXT,
                                    "evidence_quote": USER_TEXT,
                                }
                            ]
                        }
                    ),
                    tool_call_id="call-1",
                )
            }
        else:
            yield REPLY

    return FunctionModel(stream_function=stream)


def _channel(path: Path, model_calls: list[str]) -> tuple[TelegramChannel, MemoryStore]:
    store = MemoryStore(path)
    state = store.load_app_state()
    state.telegram_consent_version = CONSENT_VERSION
    store.save_app_state(state)
    session = ChatSession(
        _counting_model(model_calls),
        ProtocolPack.load(Path("protocols/transdiagnostic")),
        store,
        "en-US",
    )
    return TelegramChannel(TelegramBot("synthetic-token"), session, store, 42), store


def _run(
    path: Path, monkeypatch: pytest.MonkeyPatch, api: FakeBotApi
) -> tuple[list[str], MemoryStore]:
    monkeypatch.setattr("therapist.telegram.urlopen", api)
    monkeypatch.setattr("therapist.telegram.time.sleep", lambda _seconds: None)
    model_calls: list[str] = []
    channel, store = _channel(path, model_calls)
    with pytest.raises(KeyboardInterrupt):
        channel.run()
    return model_calls, store


def _archived_user_messages(store: MemoryStore) -> list[str]:
    return [item["content"] for item in store.export()["messages"] if item["role"] == "user"]


def _assert_one_turn_delivered(api: FakeBotApi, model_calls: list[str], store: MemoryStore) -> None:
    assert len(model_calls) == 2  # one tool request and one final reply, never repeated
    assert _archived_user_messages(store) == [USER_TEXT]
    assert len(store.list_claims()) == 1
    state = store.load_app_state()
    assert state.telegram_update_offset == 42
    assert state.telegram_outbox == []
    texts = [text for _method, text in api.delivered]
    assert texts.count(REPLY) == 1
    assert sum("THIS TURN'S RECORD" in text for text in texts) == 1
    assert sum(text.startswith("TOOL INPUT · record_user_reports") for text in texts) == 1
    assert sum(text.startswith("TOOL OUTPUT · record_user_reports") for text in texts) == 1


def test_baseline_turn_is_delivered_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = FakeBotApi(_update())

    model_calls, store = _run(tmp_path, monkeypatch, api)

    _assert_one_turn_delivered(api, model_calls, store)
    assert [method for method, _ in api.delivered][-2:] == ["sendRichMessage", "sendMessage"]


@pytest.mark.parametrize("error_name", sorted(ERRORS))
def test_failure_at_every_send_point_yields_one_turn_and_eventual_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error_name: str
) -> None:
    baseline = FakeBotApi(_update())
    _run(tmp_path / "baseline", monkeypatch, baseline)
    points = [
        (method, index) for method in SEND_METHODS for index in range(1, baseline.calls[method] + 1)
    ]
    assert ("sendRichMessage", 1) in points
    assert ("sendMessage", 3) in points

    for number, point in enumerate(points):
        api = FakeBotApi(_update(), polls=5)
        api.fail_at = point
        api.error = ERRORS[error_name]

        model_calls, store = _run(tmp_path / f"run-{number}", monkeypatch, api)

        assert api.calls[point[0]] >= point[1], point  # the failure was injected
        _assert_one_turn_delivered(api, model_calls, store)


def test_crash_after_commit_does_not_rerun_model_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CrashingApi(FakeBotApi):
        def __call__(self, request: Any, timeout: float = 0) -> BytesIO:
            if request.full_url.endswith("/sendRichMessage"):
                raise SystemExit("process killed after the turn was committed")
            return super().__call__(request, timeout)

    monkeypatch.setattr("therapist.telegram.time.sleep", lambda _seconds: None)
    crashing = CrashingApi(_update())
    monkeypatch.setattr("therapist.telegram.urlopen", crashing)
    first_calls: list[str] = []
    channel, store = _channel(tmp_path, first_calls)
    with pytest.raises(SystemExit):
        channel.run()
    assert len(first_calls) == 2
    pending = store.load_app_state()
    assert pending.telegram_update_offset == 42
    assert pending.telegram_outbox[0].text == REPLY

    restarted = FakeBotApi(_update())
    model_calls, store = _run(tmp_path, monkeypatch, restarted)

    assert model_calls == []
    assert _archived_user_messages(store) == [USER_TEXT]
    assert restarted.delivered[0][1] == REPLY
    assert store.load_app_state().telegram_outbox == []


def test_failed_outbox_write_rolls_back_turn_and_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = FakeBotApi(_update())
    monkeypatch.setattr("therapist.telegram.urlopen", api)
    model_calls: list[str] = []
    channel, store = _channel(tmp_path, model_calls)
    save = store.save_app_state
    failed = False

    def failing_save(state: Any) -> None:
        nonlocal failed
        if state.telegram_outbox and not failed:
            failed = True
            raise OSError("disk full")
        save(state)

    monkeypatch.setattr(store, "save_app_state", failing_save)

    assert channel.process_update(_update()) is True

    assert _archived_user_messages(store) == []
    assert store.list_claims() == []
    assert store.load_app_state().telegram_update_offset == 42
    texts = [text for _method, text in api.delivered]
    assert texts[-1] == channel._model_error()
    assert REPLY not in texts
    assert not any("THIS TURN'S RECORD" in text for text in texts)


@pytest.mark.parametrize("variant", ["photo", "stranger", "group", "bot"])
def test_rejected_updates_never_reach_the_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    api = FakeBotApi(_update())
    monkeypatch.setattr("therapist.telegram.urlopen", api)
    model_calls: list[str] = []
    channel, store = _channel(tmp_path, model_calls)
    update = _update()
    message = update["message"]
    if variant == "photo":
        del message["text"]
        message["photo"] = [{"file_id": "opaque"}]
    elif variant == "stranger":
        message["from"]["id"] = message["chat"]["id"] = 99
    elif variant == "group":
        message["chat"]["type"] = "group"
    else:
        message["from"]["is_bot"] = True

    channel.process_update(update)

    assert model_calls == []
    assert _archived_user_messages(store) == []
    assert store.load_app_state().telegram_update_offset == 42
    assert [text for _method, text in api.delivered] == (
        [channel._unsupported_message()] if variant == "photo" else []
    )
