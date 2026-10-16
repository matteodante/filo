import asyncio
import base64
import json
import subprocess
import sys
import threading
import time
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import httpx2
import pytest
from pydantic_ai import Agent, models
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.openai_codex import OpenAICodexModel

import filo.auth as auth
from filo.auth import CodexCredential
from filo.cli import main
from filo.memory import MemoryStore
from filo.process_lock import exclusive_lock

CODEX_URL = "https://chatgpt.com/backend-api/codex/responses"


def _codex_client(
    tokens: auth.CodexTokenSource, transport: httpx2.MockTransport
) -> httpx2.AsyncClient:
    # The provider attaches PydanticAI's credential-injecting auth to the client it is given.
    client = httpx2.AsyncClient(transport=transport)
    auth.FiloCodexProvider(tokens, http_client=client)
    return client


def _token(account_id: str) -> str:
    payload = json.dumps(
        {"https://api.openai.com/auth": {"chatgpt_account_id": account_id}}
    ).encode()
    encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    return f"header.{encoded}.signature"


def test_device_login_saves_encrypted_tokens(tmp_path: Path, monkeypatch: object) -> None:
    access = _token("account-123")

    def fake_request(url: str, payload: dict[str, object], *, form: bool = False):
        if url == auth.DEVICE_USER_CODE_URL:
            return {"device_auth_id": "device", "user_code": "ABCD", "interval": 1}
        if url == auth.DEVICE_TOKEN_URL:
            return {"authorization_code": "code", "code_verifier": "verifier"}
        assert form
        return {"access_token": access, "refresh_token": "refresh-secret", "expires_in": 3600}

    monkeypatch.setattr(auth, "_request_json", fake_request)  # type: ignore[attr-defined]
    messages: list[str] = []
    store = MemoryStore(tmp_path)

    credential = auth.login_codex(
        store,
        notify=messages.append,
        open_browser=lambda _url: None,
        sleep=lambda _seconds: None,
        monotonic=lambda: 0,
    )

    assert credential.account_id == "account-123"
    assert auth.load_credential(store) == credential
    assert "ABCD" in messages[0]
    database = (tmp_path / "filo.db").read_bytes()
    assert b"refresh-secret" not in database
    assert access.encode() not in database


def test_device_login_accepts_string_poll_interval(tmp_path: Path, monkeypatch: object) -> None:
    access = _token("account-123")

    def fake_request(url: str, payload: dict[str, object], *, form: bool = False):
        if url == auth.DEVICE_USER_CODE_URL:
            return {"device_auth_id": "device", "user_code": "ABCD", "interval": " 5 "}
        if url == auth.DEVICE_TOKEN_URL:
            return {"authorization_code": "code", "code_verifier": "verifier"}
        assert form
        return {"access_token": access, "refresh_token": "refresh", "expires_in": 3600}

    monkeypatch.setattr(auth, "_request_json", fake_request)  # type: ignore[attr-defined]
    sleeps: list[float] = []

    auth.login_codex(
        MemoryStore(tmp_path),
        notify=lambda _message: None,
        open_browser=lambda _url: None,
        sleep=sleeps.append,
        monotonic=lambda: 0,
    )

    assert sleeps == [5.0]


def test_device_login_rejects_invalid_poll_interval(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.setattr(
        auth,
        "_request_json",
        lambda *_args, **_kwargs: {
            "device_auth_id": "device",
            "user_code": "ABCD",
            "interval": "immediate",
        },
    )  # type: ignore[attr-defined]

    with pytest.raises(auth.AuthError, match="invalid interval"):
        auth.login_codex(MemoryStore(tmp_path))


def test_device_login_respects_pending_and_slow_down(tmp_path: Path, monkeypatch: object) -> None:
    access = _token("account-123")
    attempts = 0

    def fake_request(url: str, payload: dict[str, object], *, form: bool = False):
        nonlocal attempts
        if url == auth.DEVICE_USER_CODE_URL:
            return {"device_auth_id": "device", "user_code": "ABCD", "interval": 1}
        if url == auth.DEVICE_TOKEN_URL:
            attempts += 1
            if attempts == 1:
                raise auth._HTTPError(400, '{"error":"deviceauth_authorization_pending"}')
            if attempts == 2:
                raise auth._HTTPError(429, '{"error":{"code":"slow_down"}}')
            return {"authorization_code": "code", "code_verifier": "verifier"}
        assert form
        return {"access_token": access, "refresh_token": "refresh", "expires_in": 3600}

    monkeypatch.setattr(auth, "_request_json", fake_request)  # type: ignore[attr-defined]
    sleeps: list[float] = []

    auth.login_codex(
        MemoryStore(tmp_path),
        notify=lambda _message: None,
        open_browser=lambda _url: None,
        sleep=sleeps.append,
        monotonic=lambda: 0,
    )

    assert sleeps == [1.0, 1.0, 6.0]


def test_expired_token_is_refreshed_when_building_model(
    tmp_path: Path, monkeypatch: object
) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(
        store,
        CodexCredential(
            access_token=_token("old-account"),
            refresh_token="old-refresh",
            expires_at=0,
            account_id="old-account",
        ),
    )
    refreshed_access = _token("new-account")

    def fake_request(url: str, payload: dict[str, object], *, form: bool = False):
        assert url == auth.TOKEN_URL
        assert form
        assert payload["refresh_token"] == "old-refresh"
        return {
            "access_token": refreshed_access,
            "refresh_token": "new-refresh",
            "expires_in": 3600,
        }

    monkeypatch.setattr(auth, "_request_json", fake_request)  # type: ignore[attr-defined]

    model = auth.codex_model(store, "gpt-5.6-sol")

    assert model.model_name == "gpt-5.6-sol"
    assert auth.load_credential(store).account_id == "new-account"  # type: ignore[union-attr]


def test_logout_deletes_credentials(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(
        store,
        CodexCredential(
            access_token=_token("account"),
            refresh_token="refresh",
            expires_at=1,
            account_id="account",
        ),
    )

    auth.logout_codex(store)

    assert auth.load_credential(store) is None


def _credential(account: str, expires_at: int, refresh: str = "refresh") -> CodexCredential:
    return CodexCredential(
        access_token=_token(account),
        refresh_token=refresh,
        expires_at=expires_at,
        account_id=account,
    )


def test_token_source_refreshes_once_the_clock_passes_expiry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("first", expires_at=1_000, refresh="first-refresh"))
    now = [500.0]
    spent: list[str] = []

    def refresh(token: str) -> CodexCredential:
        spent.append(token)
        return _credential("second", expires_at=int(now[0]) + 3_600, refresh="second-refresh")

    tokens = auth.CodexTokenSource(store, clock=lambda: now[0], refresh=refresh)

    assert tokens.current().account_id == "first"
    now[0] = 1_000 - auth.REFRESH_MARGIN_SECONDS
    assert tokens.current().account_id == "second"
    assert tokens.current().account_id == "second"
    assert spent == ["first-refresh"]
    assert auth.load_credential(store).refresh_token == "second-refresh"  # type: ignore[union-attr]


def test_unauthorized_response_triggers_exactly_one_refresh_and_retry(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("stale", expires_at=10**10))
    spent: list[str] = []

    def refresh(token: str) -> CodexCredential:
        spent.append(token)
        return _credential("fresh", expires_at=10**10, refresh=f"refresh-{len(spent)}")

    seen: list[tuple[str, str]] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append((request.headers["Authorization"], request.headers["chatgpt-account-id"]))
        if request.headers["Authorization"] == f"Bearer {_token('stale')}":
            return httpx2.Response(401)
        return httpx2.Response(200, json={"ok": True})

    async def call(rejecting: bool) -> int:
        transport = httpx2.MockTransport(
            (lambda _request: httpx2.Response(401)) if rejecting else handler
        )
        tokens = auth.CodexTokenSource(store, refresh=refresh)
        async with _codex_client(tokens, transport) as client:
            response = await client.post(CODEX_URL, json={"input": "x"})
        return response.status_code

    assert asyncio.run(call(rejecting=False)) == 200
    assert spent == ["refresh"]
    assert seen == [(f"Bearer {_token('stale')}", "stale"), (f"Bearer {_token('fresh')}", "fresh")]

    assert asyncio.run(call(rejecting=True)) == 401
    assert spent == ["refresh"]  # the stored token came from a 401, so no further rotation


def test_codex_model_uses_the_pydantic_ai_codex_provider(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("account", expires_at=10**10))

    model = auth.codex_model(store, "gpt-5.6-sol")

    assert isinstance(model, OpenAICodexModel)
    assert isinstance(model._provider, auth.FiloCodexProvider)
    assert _token("account") not in repr(model.client.api_key)


def test_codex_model_can_defer_the_login_check(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)

    with pytest.raises(auth.LoginRequired):
        auth.codex_model(store, "gpt-5.6-sol")

    assert isinstance(auth.codex_model(store, "gpt-5.6-sol", check_login=False), OpenAICodexModel)


def test_missing_login_reaches_the_caller_without_sdk_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No request leaves the process: the auth flow stops it before the transport.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    store = MemoryStore(tmp_path)
    agent = Agent(auth.codex_model(store, "gpt-5.6-sol", check_login=False))

    with pytest.raises(auth.LoginRequired):
        agent.run_sync("hello")


_RACE = """
import sys, time
from pathlib import Path
import filo.auth as auth
from filo.cli import main
from filo.memory import MemoryStore

directory, counter, go, access = sys.argv[1:5]

def refresh(token):
    with open(counter, "a") as handle:
        handle.write(token + "\\n")
    time.sleep(0.5)
    return auth.CodexCredential(
        access_token=access, refresh_token="rotated", expires_at=int(time.time()) + 3600,
        account_id="racer",
    )

tokens = auth.CodexTokenSource(MemoryStore(Path(directory)), refresh=refresh)
while not Path(go).exists():
    time.sleep(0.01)
print(tokens.current().refresh_token)
"""


def test_concurrent_processes_spend_the_refresh_token_once(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "data")
    auth.save_credential(store, _credential("racer", expires_at=0, refresh="single-use"))
    counter = tmp_path / "refreshes"
    go = tmp_path / "go"
    racers = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _RACE,
                str(store.directory),
                str(counter),
                str(go),
                _token("racer"),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    time.sleep(1.0)
    go.touch()
    outputs = [racer.communicate(timeout=60)[0].strip() for racer in racers]

    assert [racer.returncode for racer in racers] == [0, 0]
    assert outputs == ["rotated", "rotated"]
    assert counter.read_text().splitlines() == ["single-use"]


def test_tokens_stay_out_of_errors_and_representations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = MemoryStore(tmp_path)
    credential = _credential("account", expires_at=0, refresh="refresh-secret-value")
    auth.save_credential(store, credential)
    leaked_body = b'{"error": {"code": "invalid_grant", "message": "refresh-secret-value"}}'

    def rejecting(request: object, timeout: float = 0) -> object:
        raise HTTPError(auth.TOKEN_URL, 400, "Bad Request", hdrs=None, fp=BytesIO(leaked_body))  # type: ignore[arg-type]

    monkeypatch.setattr(auth, "urlopen", rejecting)

    with pytest.raises(auth.AuthError) as raised:
        auth.codex_model(store, "gpt-5.6-sol")

    rendered = f"{raised.value} {raised.value!r} {credential!r} {credential}"
    assert "invalid_grant" in str(raised.value)
    for secret in ("refresh-secret-value", credential.access_token):
        assert secret not in rendered
    assert "refresh-secret-value" not in capsys.readouterr().out


def test_repeated_unauthorized_responses_refresh_only_once(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("stale", expires_at=10**10))
    spent: list[str] = []

    def refresh(token: str) -> CodexCredential:
        spent.append(token)
        return _credential("fresh", expires_at=10**10, refresh=f"refresh-{len(spent)}")

    tokens = auth.CodexTokenSource(store, refresh=refresh)

    async def calls() -> list[int]:
        transport = httpx2.MockTransport(lambda _request: httpx2.Response(401))
        async with _codex_client(tokens, transport) as client:
            return [(await client.post(CODEX_URL)).status_code for _ in range(3)]

    assert asyncio.run(calls()) == [401, 401, 401]
    assert spent == ["refresh"]  # a refreshed token that is still rejected is not rotated again


def test_logout_takes_effect_in_a_running_token_source(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("account", expires_at=10**10))
    tokens = auth.CodexTokenSource(store)

    auth.logout_codex(store)

    with pytest.raises(auth.AuthError, match="Not logged in"):
        tokens.current()


def test_login_waits_for_a_refresh_holding_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = MemoryStore(tmp_path)
    access = _token("new-login")

    def fake_request(url: str, payload: dict[str, object], *, form: bool = False):
        if url == auth.DEVICE_USER_CODE_URL:
            return {"device_auth_id": "device", "user_code": "ABCD", "interval": 1}
        if url == auth.DEVICE_TOKEN_URL:
            return {"authorization_code": "code", "code_verifier": "verifier"}
        return {"access_token": access, "refresh_token": "refresh", "expires_in": 3600}

    monkeypatch.setattr(auth, "_request_json", fake_request)
    login = threading.Thread(
        target=auth.login_codex,
        args=(store,),
        kwargs={
            "notify": lambda _message: None,
            "open_browser": lambda _url: None,
            "sleep": lambda _seconds: None,
            "monotonic": lambda: 0,
        },
    )

    with exclusive_lock(store.directory / auth.LOCK_NAME):
        login.start()
        login.join(timeout=0.5)
        assert login.is_alive()
        assert auth.load_credential(store) is None
    login.join(timeout=10)

    assert not login.is_alive()
    assert auth.load_credential(store).account_id == "new-login"  # type: ignore[union-attr]


def test_a_second_process_does_not_refresh_a_token_issued_after_rejection(
    tmp_path: Path,
) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("stale", expires_at=10**10))
    spent: list[str] = []

    def refresh(token: str) -> CodexCredential:
        spent.append(token)
        return _credential("fresh", expires_at=10**10, refresh=f"refresh-{len(spent)}")

    async def rejected_request(tokens: auth.CodexTokenSource) -> int:
        transport = httpx2.MockTransport(lambda _request: httpx2.Response(401))
        async with _codex_client(tokens, transport) as client:
            return (await client.post(CODEX_URL)).status_code

    listener = auth.CodexTokenSource(store, refresh=refresh)
    chat = auth.CodexTokenSource(store, refresh=refresh)  # separate state, as in another process

    assert asyncio.run(rejected_request(listener)) == 401
    assert asyncio.run(rejected_request(chat)) == 401
    assert asyncio.run(rejected_request(listener)) == 401

    assert spent == ["refresh"]
    assert auth.load_credential(store).issued_after_rejection  # type: ignore[union-attr]


def test_delete_data_waits_for_an_in_flight_refresh(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("account", expires_at=0))
    refreshing = threading.Event()
    release = threading.Event()

    def slow_refresh(token: str) -> CodexCredential:
        refreshing.set()
        release.wait(10)
        return _credential("account", expires_at=10**10, refresh="rotated")

    tokens: list[CodexCredential] = []
    refresher = threading.Thread(
        target=lambda: tokens.append(auth.CodexTokenSource(store, refresh=slow_refresh).current())
    )
    refresher.start()
    assert refreshing.wait(10)
    deleter = threading.Thread(
        target=main, args=(["--data-dir", str(tmp_path), "delete-data", "--yes"],)
    )
    deleter.start()
    deleter.join(timeout=0.5)
    assert deleter.is_alive()  # blocked on the refresh lock

    release.set()
    refresher.join(10)
    deleter.join(10)

    assert not deleter.is_alive()
    assert auth.load_credential(store) is None


def test_codex_request_carries_the_token_and_the_codex_dialect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The request goes to a mock transport, never the network.
    monkeypatch.setattr(models, "ALLOW_MODEL_REQUESTS", True)
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("account", expires_at=10**10))
    seen: list[httpx2.Request] = []

    def reject(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(400, json={"error": {"message": "stop here"}})

    client = httpx2.AsyncClient(transport=httpx2.MockTransport(reject))
    provider = auth.FiloCodexProvider(auth.CodexTokenSource(store), http_client=client)
    agent = Agent(OpenAICodexModel("gpt-5.6-sol", provider=provider))

    with pytest.raises(ModelHTTPError):
        agent.run_sync("hello")

    (request,) = seen
    body = json.loads(request.content)
    assert str(request.url) == CODEX_URL
    assert request.headers["Authorization"] == f"Bearer {_token('account')}"
    assert request.headers["chatgpt-account-id"] == "account"
    assert body["store"] is False
    assert body["stream"] is True
