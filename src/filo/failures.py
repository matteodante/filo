"""Turn a failed conversation turn into a message the user can act on.

Provider errors are classified by type, HTTP status, and the provider's error code, never shown
verbatim: a provider can echo conversation content back in an error body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from filo.auth import AuthError, LoginRequired


class SetupProblem(RuntimeError):
    """Local configuration the turn needs is missing; the message says how to fix it."""


class ContextFull(ValueError):
    """A message does not fit the model's context window even in a fresh session."""


class FailureKind(StrEnum):
    USAGE_LIMIT = "usage_limit"
    LOGIN = "login"
    CONTEXT_FULL = "context_full"
    SETUP = "setup"
    TRANSIENT = "transient"


@dataclass(frozen=True)
class Failure:
    kind: FailureKind
    message: str
    error_class: str

    def notice(self, *, retry: bool = True) -> str:
        parts = [self.message]
        if retry:
            parts.append("Send /retry to try the same message again.")
        parts.append("If you may be in danger, /crisis shows emergency resources.")
        return " ".join(parts)


_USAGE_LIMIT_TYPES = {"usage_limit_reached", "usage_not_included"}
_CONTEXT_CODES = {"context_length_exceeded", "context_window_exceeded"}
_AUTH_CODES = {
    "invalid_api_key",
    "invalid_token",
    "token_expired",
    "token_revoked",
    "unauthorized",
    "authentication_error",
}
_TRANSIENT_MESSAGE = "I couldn't respond. Please try again shortly."


def classify(error: BaseException, now: datetime | None = None) -> Failure:
    """Never raises: it runs inside the handlers that keep a listener or the TUI alive."""
    error_class = type(error).__name__
    try:
        return _classify(error, now or datetime.now(UTC), error_class)
    except Exception:
        return Failure(FailureKind.TRANSIENT, _TRANSIENT_MESSAGE, error_class)


def _classify(error: BaseException, now: datetime, error_class: str) -> Failure:
    chain = _chain(error)
    if any(isinstance(item, SetupProblem) for item in chain):
        message = next(str(item) for item in chain if isinstance(item, SetupProblem))
        return Failure(FailureKind.SETUP, message, error_class)
    if any(isinstance(item, ContextFull) for item in chain):
        return Failure(FailureKind.CONTEXT_FULL, _context_message(), error_class)
    statuses = {status for item in chain if (status := _status(item)) is not None}
    details = [detail for item in chain if (detail := _error_detail(item)) is not None]
    codes = {str(detail.get(key)) for detail in details for key in ("type", "code")}
    if codes & _USAGE_LIMIT_TYPES:
        reset = next((at for detail in details if (at := _reset_at(detail, now))), None)
        return Failure(FailureKind.USAGE_LIMIT, _usage_message(reset, now), error_class)
    if codes & _CONTEXT_CODES:
        return Failure(FailureKind.CONTEXT_FULL, _context_message(), error_class)
    if (
        any(isinstance(item, LoginRequired) for item in chain)
        or any(type(item).__name__ == "CredentialsRefreshError" for item in chain)
        or any(isinstance(item, AuthError) and _status(item) in {400, 401} for item in chain)
        or 401 in statuses
        # A 403 is a login problem only when the provider says so; a proxy challenge or a
        # regional block also answers 403, and logging in again would not help.
        or (403 in statuses and codes & _AUTH_CODES)
    ):
        return Failure(
            FailureKind.LOGIN,
            "The ChatGPT login is missing or no longer accepted. "
            "Run `filo auth login` on the computer that runs Filo.",
            error_class,
        )
    if 429 in statuses:
        message = "The model provider is rate limiting requests. Please try again in a minute."
    elif 403 in statuses:
        message = (
            "The model provider refused the request. Please try again later; "
            "if it keeps happening, check the provider's status."
        )
    else:
        message = _TRANSIENT_MESSAGE
    return Failure(FailureKind.TRANSIENT, message, error_class)


def _chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in chain and len(chain) < 8:
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _status(error: BaseException) -> int | None:
    for name in ("status_code", "status"):
        value = getattr(error, name, None)
        if isinstance(value, int):
            return value
    return None


def _error_detail(error: BaseException) -> dict[str, Any] | None:
    body = getattr(error, "body", None)
    if isinstance(body, str | bytes):
        try:
            body = json.loads(body)
        except ValueError:
            return None
    if not isinstance(body, dict):
        return None
    nested = body.get("error")
    return nested if isinstance(nested, dict) else body


def _reset_at(detail: dict[str, Any], now: datetime) -> datetime | None:
    resets_at = detail.get("resets_at")
    if isinstance(resets_at, int | float) and not isinstance(resets_at, bool):
        try:
            return datetime.fromtimestamp(resets_at, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    resets_in = detail.get("resets_in_seconds")
    if isinstance(resets_in, int | float) and not isinstance(resets_in, bool) and resets_in >= 0:
        try:
            return now + timedelta(seconds=resets_in)
        except (OverflowError, ValueError):
            return None
    return None


def _usage_message(reset: datetime | None, now: datetime) -> str:
    if reset is None:
        return "The ChatGPT usage limit is reached. Please try again later."
    minutes = max(1, round((reset - now).total_seconds() / 60))
    wait = f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"
    local = reset.astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()
    return f"The ChatGPT usage limit is reached. It resets at {local} (in about {wait})."


def _context_message() -> str:
    return (
        "This conversation no longer fits the model's context window. "
        "Send /end to close the session, then send your message again; if it still does not "
        "fit, send a shorter message or choose a model with a larger context window."
    )
