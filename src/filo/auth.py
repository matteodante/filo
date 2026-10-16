"""Experimental ChatGPT OAuth for the Codex subscription backend."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import math
import threading
import time
import webbrowser
from collections.abc import Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import httpx2
from openai import OpenAIError
from pydantic import BaseModel, Field
from pydantic_ai.models.openai import OpenAIResponsesModelSettings
from pydantic_ai.models.openai_codex import OpenAICodexModel
from pydantic_ai.providers.openai_codex import (
    OpenAICodexCredentials,
    OpenAICodexCredentialSource,
    OpenAICodexProvider,
)

from filo.memory import MemoryStore
from filo.process_lock import exclusive_lock

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_BASE_URL = "https://auth.openai.com"
DEVICE_USER_CODE_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{AUTH_BASE_URL}/api/accounts/deviceauth/token"
TOKEN_URL = f"{AUTH_BASE_URL}/oauth/token"
DEVICE_REDIRECT_URI = f"{AUTH_BASE_URL}/deviceauth/callback"
DEVICE_VERIFICATION_URI = f"{AUTH_BASE_URL}/codex/device"
JWT_CLAIM = "https://api.openai.com/auth"
SECRET_NAME = "openai-codex"
LOCK_NAME = "codex-auth.lock"
REFRESH_MARGIN_SECONDS = 60


class CodexCredential(BaseModel):
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_at: int
    account_id: str
    # Stored with the credential so every process honors it: a token obtained
    # because of a 401 is never refreshed again after another 401.
    issued_after_rejection: bool = False


class AuthError(RuntimeError, OpenAIError):
    """A ChatGPT login or token failure. As an `OpenAIError`, the SDK raises it unchanged from a
    request instead of retrying it and reporting a connection failure."""


class LoginRequired(AuthError):
    """No usable ChatGPT login: the user has to run `filo auth login`."""


class _HTTPError(AuthError):
    def __init__(self, status: int, body: str) -> None:
        # The body stays out of the message: a token endpoint response must
        # never reach a log through an exception string.
        code = _error_code(body)
        super().__init__(
            f"OpenAI authentication failed (HTTP {status}"
            + (f", {code})" if code else ")")
            + ". Run `filo auth login` if this persists."
        )
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
        # A running listener may be refreshing the previous login; writing
        # under its lock keeps that refresh from overwriting this one.
        with credential_lock(store):
            save_credential(store, credential)
        return credential
    raise AuthError("ChatGPT login timed out after 15 minutes.")


def load_credential(store: MemoryStore) -> CodexCredential | None:
    payload = store.load_secret(SECRET_NAME)
    return None if payload is None else CodexCredential.model_validate_json(payload)


def save_credential(store: MemoryStore, credential: CodexCredential) -> None:
    store.save_secret(SECRET_NAME, credential.model_dump_json().encode())


def logout_codex(store: MemoryStore) -> None:
    with credential_lock(store):
        store.delete_secret(SECRET_NAME)


def credential_lock(store: MemoryStore) -> contextlib.AbstractContextManager[None]:
    """Serialize a credential change with any refresh in another process."""
    return exclusive_lock(store.directory / LOCK_NAME)


class CodexTokenSource(OpenAICodexCredentialSource):
    """Hand out a current access token, refreshing it at most once across processes.

    The refresh token rotates on every use, so the Telegram listener and an
    interactive chat must not both spend it. Refreshes run under an exclusive
    lock file next to the encrypted store, and the store is re-read after the
    lock is acquired so a process that waited reuses the other one's result.
    """

    def __init__(
        self,
        store: MemoryStore,
        *,
        clock: Callable[[], float] = time.time,
        refresh: Callable[[str], CodexCredential] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._refresh = refresh or _refresh
        self._lock_path = store.directory / LOCK_NAME
        self._thread_lock = threading.Lock()

    def current(self) -> CodexCredential:
        # Read the store on every request so a logout or a new login made by
        # another process takes effect on the next turn.
        stored = load_credential(self._store)
        if stored is None:
            raise LoginRequired("Not logged in. Run `filo auth login` first.")
        if not self._expiring(stored):
            return stored
        return self._refreshed(stored)

    def after_rejection(self, rejected: CodexCredential) -> CodexCredential | None:
        """Return a different credential after a 401, or None when retrying cannot help.

        A token that was itself obtained because of a 401 and is rejected again
        points at a problem a refresh does not fix, so it is not refreshed again:
        each refresh rotates the refresh token.
        """
        if rejected.issued_after_rejection:
            return None
        return self._refreshed(rejected, after_rejection=True)

    def _expiring(self, credential: CodexCredential) -> bool:
        return credential.expires_at <= self._clock() + REFRESH_MARGIN_SECONDS

    def _refreshed(
        self, stale: CodexCredential, *, after_rejection: bool = False
    ) -> CodexCredential:
        with self._thread_lock, exclusive_lock(self._lock_path):
            stored = load_credential(self._store)
            if stored is None:
                raise LoginRequired("Not logged in. Run `filo auth login` first.")
            if stored != stale and not self._expiring(stored):
                return stored
            credential = self._refresh(stored.refresh_token)
            if after_rejection:
                credential = credential.model_copy(update={"issued_after_rejection": True})
            save_credential(self._store, credential)
            return credential

    async def load(self) -> OpenAICodexCredentials:
        return _provider_credentials(await asyncio.to_thread(self.current))

    async def save(self, credentials: OpenAICodexCredentials) -> None:
        # The provider refreshes only through `FiloCodexProvider`, which never calls this.
        raise NotImplementedError("Refreshed tokens are saved by CodexTokenSource itself.")


class FiloCodexProvider(OpenAICodexProvider):
    """PydanticAI's Codex provider with Filo's token source behind every request.

    The built-in provider keeps credentials in memory and refreshes without an inter-process
    lock. Filo instead reads the encrypted store per request, so a logout or a new login reaches
    running processes, refreshes under `codex-auth.lock`, and never refreshes a token that was
    itself issued after a 401 when it is rejected again.
    """

    def __init__(self, tokens: CodexTokenSource, *, http_client: httpx2.AsyncClient) -> None:
        super().__init__(credential_source=tokens, http_client=http_client)
        self._tokens = tokens

    async def _prepare_request_credentials(
        self,
    ) -> tuple[OpenAICodexCredentials, Callable[[], Awaitable[OpenAICodexCredentials]]]:
        # Store reads, lock waits, and refreshes block, so they run in a worker
        # thread instead of stalling the event loop that streams the reply.
        credential = await asyncio.to_thread(self._tokens.current)

        async def replay() -> OpenAICodexCredentials:
            replacement = await asyncio.to_thread(self._tokens.after_rejection, credential)
            # Without a replacement the request is replayed unchanged, so the 401 reaches the
            # caller as an authentication error.
            return _provider_credentials(replacement or credential)

        return _provider_credentials(credential), replay


def _provider_credentials(credential: CodexCredential) -> OpenAICodexCredentials:
    return OpenAICodexCredentials(
        access_token=credential.access_token,
        refresh_token=credential.refresh_token,
        account_id=credential.account_id,
    )


def codex_model(
    store: MemoryStore, model_name: str, *, check_login: bool = True
) -> OpenAICodexModel:
    tokens = CodexTokenSource(store)
    if check_login:
        # Fail at startup rather than on the first message when login is needed.
        tokens.current()
    return OpenAICodexModel(
        model_name,
        provider=FiloCodexProvider(
            tokens,
            # Turns run on different event loops (the TUI uses a new worker thread per turn),
            # and a pooled connection bound to a closed loop fails, so no connection is kept
            # alive between requests.
            http_client=httpx2.AsyncClient(
                timeout=httpx2.Timeout(600, connect=5),
                limits=httpx2.Limits(max_keepalive_connections=0),
            ),
        ),
        settings=OpenAIResponsesModelSettings(
            openai_reasoning_effort="medium",
            openai_text_verbosity="low",
        ),
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
