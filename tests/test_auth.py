import base64
import json
from pathlib import Path

import pytest

import filo.auth as auth
from filo.auth import CodexCredential
from filo.memory import MemoryStore


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
