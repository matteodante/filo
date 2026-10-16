"""Experimental ChatGPT OAuth for the Codex subscription backend."""

from __future__ import annotations

import base64
import json
import math
import time
import webbrowser
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel
from pydantic_ai.models.openai import OpenAIResponsesModel, OpenAIResponsesModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from filo.memory import MemoryStore

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE_URL = "https://auth.openai.com"
DEVICE_USER_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE_URL}/codex/device"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
JWT_CLAIM = "https://api.openai.com/auth"
SECRET_NAME = "openai-codex"


class CodexCredential(BaseModel):
    access_token: str
    refresh_token: str
    expires_at: int
    account_id: str


class AuthError(RuntimeError):
    pass


class _HTTPError(AuthError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"OpenAI authentication failed ({status}): {body}")
        self.status = status
        self.body = body


def login_codex(
    store: MemoryStore,
    *,
    notify: Callable[[str], None] = print,
    open_browser: Callable[[str], object] = webbrowser.open,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CodexCredential:
    device = _request_json(DEVICE_USER_CODE_URL, {"client_id": CLIENT_ID})
    device_id = _required_str(device, "device_auth_id")
    user_code = _required_str(device, "user_code")
    interval_value = device.get("interval", 5)
    if isinstance(interval_value, str):
        try:
            interval_value = int(interval_value.strip(), 10)
        except ValueError as error:
            raise AuthError(
                "OpenAI authentication response contains an invalid interval."
            ) from error
    if (
        not isinstance(interval_value, int | float)
        or isinstance(interval_value, bool)
        or not math.isfinite(interval_value)
        or interval_value <= 0
    ):
        raise AuthError("OpenAI authentication response contains an invalid interval.")
    interval = max(1.0, float(interval_value))
    notify(f"Open {DEVICE_VERIFICATION_URI} and enter code: {user_code}")
    open_browser(DEVICE_VERIFICATION_URI)

    deadline = monotonic() + 15 * 60
    while monotonic() < deadline:
        sleep(interval)
        try:
            result = _request_json(
                DEVICE_TOKEN_URL,
                {"device_auth_id": device_id, "user_code": user_code},
            )
        except _HTTPError as error:
            code = _error_code(error.body)
            if error.status in {403, 404} or code == "deviceauth_authorization_pending":
                continue
            if code == "slow_down":
                interval += 5
                continue
            raise
        credential = _exchange_code(
            _required_str(result, "authorization_code"),
            _required_str(result, "code_verifier"),
        )
        save_credential(store, credential)
        return credential
    raise AuthError("ChatGPT login timed out after 15 minutes.")


def load_credential(store: MemoryStore) -> CodexCredential | None:
    payload = store.load_secret(SECRET_NAME)
    return None if payload is None else CodexCredential.model_validate_json(payload)


def save_credential(store: MemoryStore, credential: CodexCredential) -> None:
    store.save_secret(SECRET_NAME, credential.model_dump_json().encode())


def logout_codex(store: MemoryStore) -> None:
    store.delete_secret(SECRET_NAME)


def codex_model(store: MemoryStore, model_name: str) -> OpenAIResponsesModel:
    credential = load_credential(store)
    if credential is None:
        raise AuthError("Not logged in. Run `filo auth login` first.")
    if credential.expires_at <= int(time.time()) + 60:
        credential = _refresh(credential.refresh_token)
        save_credential(store, credential)
    settings = OpenAIResponsesModelSettings(
        openai_store=False,
        openai_reasoning_effort="medium",
        openai_text_verbosity="low",
        extra_headers={
            "OpenAI-Beta": "responses=experimental",
            "chatgpt-account-id": credential.account_id,
            "originator": "filo-cli",
        },
    )
    return OpenAIResponsesModel(
        model_name,
        provider=OpenAIProvider(base_url=CODEX_BASE_URL, api_key=credential.access_token),
        settings=settings,
    )


def _exchange_code(code: str, verifier: str) -> CodexCredential:
    token = _request_json(
        TOKEN_URL,
        {
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "code_verifier": verifier,
            "redirect_uri": DEVICE_REDIRECT_URI,
        },
        form=True,
    )
    return _credential_from_token(token)


def _refresh(refresh_token: str) -> CodexCredential:
    token = _request_json(
        TOKEN_URL,
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        },
        form=True,
    )
    return _credential_from_token(token)


def _credential_from_token(token: dict[str, object]) -> CodexCredential:
    access = _required_str(token, "access_token")
    refresh = _required_str(token, "refresh_token")
    expires_in = token.get("expires_in")
    if not isinstance(expires_in, int | float):
        raise AuthError("OpenAI token response is missing expires_in.")
    account_id = _account_id(access)
    return CodexCredential(
        access_token=access,
        refresh_token=refresh,
        expires_at=int(time.time() + expires_in),
        account_id=account_id,
    )


def _account_id(token: str) -> str:
    try:
        encoded = token.split(".")[1]
        encoded += "=" * (-len(encoded) % 4)
        payload = json.loads(base64.urlsafe_b64decode(encoded))
        account_id = payload[JWT_CLAIM]["chatgpt_account_id"]
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise AuthError("The ChatGPT token does not contain an account ID.") from error
    if not isinstance(account_id, str) or not account_id:
        raise AuthError("The ChatGPT token contains an invalid account ID.")
    return account_id


def _request_json(url: str, payload: dict[str, object], *, form: bool = False) -> dict[str, object]:
    if form:
        data = urlencode(payload).encode()
        content_type = "application/x-www-form-urlencoded"
    else:
        data = json.dumps(payload).encode()
        content_type = "application/json"
    request = Request(
        url,
        data=data,
        headers={"Content-Type": content_type, "User-Agent": "filo-cli/0.1"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            result = json.load(response)
    except HTTPError as error:
        body = error.read().decode(errors="replace")
        raise _HTTPError(error.code, body) from error
    except (URLError, TimeoutError, json.JSONDecodeError) as error:
        raise AuthError(f"OpenAI authentication request failed: {error}") from error
    if not isinstance(result, dict):
        raise AuthError("OpenAI authentication returned an invalid response.")
    return result


def _required_str(value: dict[str, object], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result:
        raise AuthError(f"OpenAI authentication response is missing {key}.")
    return result


def _error_code(body: str) -> str | None:
    try:
        error = json.loads(body).get("error")
    except (json.JSONDecodeError, AttributeError):
        return None
    if isinstance(error, str):
        return error
    return error.get("code") if isinstance(error, dict) else None
