import asyncio
import base64
import json
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError

import httpx
import pytest

import therapist.auth as auth
from therapist.auth import CodexCredential
from therapist.memory import MemoryStore


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
    database = (tmp_path / "thera.db").read_bytes()
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

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.headers["Authorization"], request.headers["chatgpt-account-id"]))
        if request.headers["Authorization"] == f"Bearer {_token('stale')}":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    async def call(rejecting: bool) -> int:
        transport = httpx.MockTransport(
            (lambda _request: httpx.Response(401)) if rejecting else handler
        )
        tokens = auth.CodexTokenSource(store, refresh=refresh)
        async with httpx.AsyncClient(auth=auth.CodexAuth(tokens), transport=transport) as client:
            response = await client.post("https://codex.invalid/responses", json={"input": "x"})
        return response.status_code

    assert asyncio.run(call(rejecting=False)) == 200
    assert spent == ["refresh"]
    assert seen == [(f"Bearer {_token('stale')}", "stale"), (f"Bearer {_token('fresh')}", "fresh")]

    assert asyncio.run(call(rejecting=True)) == 401
    assert spent == ["refresh", "refresh-1"]  # one refresh per rejected request, never a loop


def test_codex_model_sends_current_token_through_auth_flow(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path)
    auth.save_credential(store, _credential("account", expires_at=10**10))

    model = auth.codex_model(store, "gpt-5.6-sol")

    client = model.client._client  # the httpx client handed to the provider
    assert isinstance(client.auth, auth.CodexAuth)
    assert _token("account") not in repr(model.client.api_key)


_RACE = """
import sys, time
from pathlib import Path
import therapist.auth as auth
from therapist.memory import MemoryStore

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
