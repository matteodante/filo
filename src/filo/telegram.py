"""Private, single-user Telegram interface using the standard Bot API."""

from __future__ import annotations

import dataclasses
import hashlib
import http.client
import json
import re
import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from filo.chat import ChatSession, ChatTurn, TurnStreamEvent, TurnStreamKind, crisis_resources
from filo.memory import MemoryMode, MemoryStore, TelegramDelivery

API_ROOT = "https://api.telegram.org"
TELEGRAM_SECRET = "telegram_bot_token"
CONSENT_VERSION = "alpha-5"
MESSAGE_LIMIT = 4_000
MEMORY_PAGE_SIZE = 10
SESSION_PAGE_SIZE = 5
INTERVENTION_PAGE_SIZE = 10
DRAFT_INTERVAL_SECONDS = 0.25
TYPING_INTERVAL_SECONDS = 4.0
TYPING_JOIN_SECONDS = 1.0
# Rejections that a retry cannot fix and that concern only the one message. An
# invalid or revoked token (401, 404) is fatal instead, and anything ambiguous,
# such as 408, 429 or 5xx, is retried.
PERMANENT_DELIVERY_ERRORS = frozenset({400, 403})


class TelegramError(RuntimeError):
    """A privacy-safe Telegram transport error."""

    def __init__(
        self,
        message: str,
        *,
        error_code: int | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retry_after = retry_after
        # 408 and 429 ask the client to repeat the request; other 4xx codes do not.
        self.fatal = (
            error_code is not None and 400 <= error_code < 500 and error_code not in {408, 429}
        )


class _ConfigurationChanged(Exception):
    """Setup changed the pairing or bot token while this listener was running."""


class TelegramBot:
    def __init__(self, token: str) -> None:
        token = token.strip()
        if not token:
            raise ValueError("Telegram bot token cannot be empty.")
        self._base_url = f"{API_ROOT}/bot{token}"

    def get_me(self) -> dict[str, Any]:
        result = self._call("getMe", {})
        if not isinstance(result, dict):
            raise TelegramError("Telegram returned an invalid bot identity.")
        return result

    def delete_webhook(self) -> None:
        self._call("deleteWebhook", {"drop_pending_updates": False})

    def configure_interface(self, chat_id: int) -> None:
        commands = [
            ("start", "Notice and consent"),
            ("help", "Complete guide"),
            ("crisis", "Emergency resources, no AI reply"),
            ("status", "Agent and archive status"),
            ("case", "Shared formulation"),
            ("memory", "Structured memory"),
            ("sessions", "Sessions and summaries"),
            ("interventions", "Recorded interventions"),
            ("privacy", "Data, providers and limits"),
            ("end", "Close the session"),
        ]
        self._call("deleteMyCommands", {})
        self._call(
            "setMyCommands",
            {
                "commands": [
                    {"command": command, "description": description}
                    for command, description in commands
                ],
                "scope": {"type": "chat", "chat_id": chat_id},
            },
        )
        self._call(
            "setMyDescription",
            {
                "description": "Experimental AI for adult self-reflection with encrypted local "
                "memory. Not therapy, diagnosis, medical advice, monitoring, or emergency care. "
                "Privacy: https://github.com/matteodante/filo/blob/main/PRIVACY.md"
            },
        )

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        if not isinstance(result, list):
            raise TelegramError("Telegram returned an invalid update list.")
        return [item for item in result if isinstance(item, dict)]

    def send_message(self, chat_id: int, text: str) -> None:
        for chunk in split_message(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk})

    def send_message_draft(self, chat_id: int, draft_id: int, text: str) -> None:
        self._call(
            "sendMessageDraft",
            {"chat_id": chat_id, "draft_id": draft_id, "text": text},
        )

    def send_rich_message_draft(self, chat_id: int, draft_id: int, text: str) -> None:
        self._call(
            "sendRichMessageDraft",
            {
                "chat_id": chat_id,
                "draft_id": draft_id,
                "rich_message": {"markdown": text},
            },
        )

    def send_rich_message(self, chat_id: int, text: str) -> None:
        self._call(
            "sendRichMessage",
            {"chat_id": chat_id, "rich_message": {"markdown": text}},
        )

    def send_typing(self, chat_id: int) -> None:
        self._call("sendChatAction", {"chat_id": chat_id, "action": "typing"})

    def _call(self, method: str, payload: dict[str, Any], timeout: int = 20) -> Any:
        request = Request(
            f"{self._base_url}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "User-Agent": "filo-cli/0.1",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                body = json.load(response)
        except HTTPError as error:
            description, error_code, retry_after = _http_error_details(error)
            raise TelegramError(
                f"Telegram API rejected the request: {description}",
                error_code=error_code,
                retry_after=retry_after,
            ) from error
        except (OSError, http.client.HTTPException, ValueError) as error:
            # Covers URLError, timeouts, resets, TLS failures, truncated bodies,
            # and malformed JSON. Only the class name is kept: messages can
            # carry the request URL, which contains the bot token.
            raise TelegramError(
                f"Telegram is unavailable or returned an invalid response ({type(error).__name__})."
            ) from error
        if not isinstance(body, dict) or body.get("ok") is not True:
            description = (
                body.get("description", "unknown error")
                if isinstance(body, dict)
                else "unknown error"
            )
            error_code = body.get("error_code") if isinstance(body, dict) else None
            parameters = body.get("parameters") if isinstance(body, dict) else None
            retry_after = (
                parameters.get("retry_after")
                if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int)
                else None
            )
            raise TelegramError(
                f"Telegram API error: {description}",
                error_code=error_code if isinstance(error_code, int) else None,
                retry_after=retry_after,
            )
        return body.get("result")


@dataclass
class TelegramChannel:
    bot: TelegramBot
    session: ChatSession
    store: MemoryStore
    allowed_user_id: int
    configuration_store: MemoryStore | None = None
    # Ephemeral mode writes no conversation content to disk, so its queued
    # replies live only here and are lost if the process stops.
    _ephemeral_outbox: list[TelegramDelivery] = dataclasses.field(
        default_factory=list, init=False, repr=False
    )

    def __post_init__(self) -> None:
        # The configuration this listener started with. A later change by setup
        # deauthorizes the old account or bot even while this process runs.
        self._configured_as = self._configuration()

    def _configuration(self) -> tuple[int | None, str | None]:
        """The persisted pairing and a fingerprint of the stored bot token, never the token."""
        token = self.config_store.load_secret(TELEGRAM_SECRET)
        fingerprint = hashlib.sha256(token).hexdigest() if token else None
        return self.config_store.load_app_state().telegram_allowed_user_id, fingerprint

    @property
    def config_store(self) -> MemoryStore:
        return self.configuration_store or self.store

    @property
    def _ephemeral(self) -> bool:
        return self.session.memory_mode is MemoryMode.EPHEMERAL

    def _allows(self, user_id: object, paired: int | None) -> bool:
        """Whether this listener may talk to ``user_id`` under the persisted ``paired`` account.

        Once setup pairs another account, or delete-data removes the pairing,
        even a running listener stops exchanging messages with the old one,
        while an explicit --allowed-user-id keeps working as long as the
        pairing it started with is unchanged.
        """
        return user_id == self.allowed_user_id and (
            paired == self._configured_as[0] or user_id == paired
        )

    def _paired(self, user_id: object) -> bool:
        return self._allows(user_id, self.config_store.load_app_state().telegram_allowed_user_id)

    def _check_configuration(self) -> None:
        # Consuming updates after setup re-paired or replaced the bot would
        # confirm the new configuration's messages to Telegram and move the
        # offset past them, so this listener reloads first.
        if self._configuration() != self._configured_as:
            raise _ConfigurationChanged

    def run(self) -> None:
        while True:
            try:
                self._listen()
            except _ConfigurationChanged:
                state = self.config_store.load_app_state()
                token = self.config_store.load_secret(TELEGRAM_SECRET)
                if not token or not state.telegram_allowed_user_id:
                    print("Telegram is no longer configured; the listener is stopping.")
                    return
                print("Telegram configuration changed; reloading it.")
                # Reloading in the same process does not depend on a service
                # manager restarting it, which the Windows scheduled task and a
                # systemd unit after delete-data would not do.
                self.bot = TelegramBot(token.decode())
                self.allowed_user_id = state.telegram_allowed_user_id
                self._configured_as = self._configuration()

    def _listen(self) -> None:
        identity = self.bot.get_me()
        self.bot.delete_webhook()
        self.bot.configure_interface(self.allowed_user_id)
        username = identity.get("username", "unknown")
        print(f"Telegram bot @{username} is listening. Press Ctrl-C to stop.")
        offset = self.config_store.load_app_state().telegram_update_offset
        while True:
            self._check_configuration()
            try:
                self.deliver_pending()
                if self._extract_reports():
                    self.deliver_pending()
                updates = self.bot.get_updates(offset)
            except TelegramError as error:
                if error.fatal:
                    raise
                print(f"Telegram polling or delivery error: {error}")
                time.sleep(error.retry_after or 3)
                continue
            except sqlite3.OperationalError as error:
                if not _locked(error):
                    raise
                print("The local store is busy; retrying.")
                time.sleep(1)
                continue
            for update in updates:
                update_id = update.get("update_id")
                if not isinstance(update_id, int):
                    continue
                if offset is not None and update_id < offset:
                    continue
                self._check_configuration()
                try:
                    self.accept_update(update)
                except sqlite3.OperationalError as error:
                    # A CLI command held the store past the busy timeout. The
                    # update was not consumed, so the next poll returns it.
                    if not _locked(error):
                        raise
                    print("The local store is busy; retrying.")
                    time.sleep(1)
                    break
                offset = update_id + 1
                try:
                    self.deliver_pending()
                except TelegramError as error:
                    print(f"Telegram delivery error: {error}")
                    if error.fatal:
                        raise
                    time.sleep(error.retry_after or 3)
                    break
                except sqlite3.OperationalError as error:
                    if not _locked(error):
                        raise
                    print("The local store is busy; retrying.")
                    time.sleep(1)
                    break

    def process_update(self, update: dict[str, Any]) -> bool:
        handled = self.accept_update(update)
        self.deliver_pending()
        if self._extract_reports():
            self.deliver_pending()
        return handled

    def _extract_reports(self) -> bool:
        """Record split-arm reports once the turn's offset and replies are safely stored.

        Runs only after ``accept_update`` committed the offset, so nothing here can make an update
        run again, and no error here may stop the listener: a failed extraction keeps its marker
        for the next attempt. Returns whether a change notice was queued.
        """
        try:
            before = self._durable_snapshot()
            epoch = self.config_store.telegram_output_epoch()
            if not self.session.extract_pending():
                return False
            changes = self._durable_changes(before)
            return bool(changes) and self.config_store.queue_telegram_output(
                [TelegramDelivery(chat_id=self.allowed_user_id, text=changes)],
                rendered_at_epoch=epoch,
            )
        except Exception as error:  # Extraction is best effort; its marker is retried later.
            print(f"Memory extraction error: {type(error).__name__}")
            return False

    def accept_update(self, update: dict[str, Any]) -> bool:
        """Handle one update and persist its replies with the offset that consumes it.

        A model turn stores its replies inside the turn transaction, so once the
        turn is committed the update is never handed to the model again.
        """
        update_id = update.get("update_id")
        outbox: list[TelegramDelivery] = []

        def enqueue(*, consent: bool = False, rendered_at_epoch: int | None = None) -> bool:
            queued = self.config_store.queue_telegram_output(
                [] if self._ephemeral else outbox,
                update_offset=update_id + 1 if isinstance(update_id, int) else None,
                consent_version=CONSENT_VERSION if consent else None,
                rendered_at_epoch=rendered_at_epoch,
            )
            if queued and self._ephemeral:
                self._ephemeral_outbox.extend(outbox)
            return queued

        handled, queued = self._handle_update(update, outbox, enqueue)
        if not queued:
            enqueue()
        return handled

    def deliver_pending(self) -> None:
        """Send queued replies in order, removing each one only after delivery."""
        cursor = 0
        while True:
            # The durable queue goes first: replies committed by an earlier run in
            # standard or transcript-only mode still need delivery after a restart
            # in ephemeral mode.
            durable = self.config_store.list_telegram_output(limit=1, after_id=cursor)
            if durable:
                entry_id, delivery = durable[0]
                cursor = entry_id
            elif self._ephemeral and self._ephemeral_outbox:
                entry_id, delivery = None, self._ephemeral_outbox[0]
            else:
                return
            paired = self.config_store.load_app_state().telegram_allowed_user_id
            if delivery.chat_id == paired != self.allowed_user_id:
                # Queued for the persisted pairing while this listener runs
                # with --allowed-user-id: leave it for that account's listener
                # and keep delivering the rest.
                continue
            if not self._allows(delivery.chat_id, paired):
                # Queued for an account that is no longer paired; never send
                # private content to it.
                print("Telegram dropped a queued message for a chat that is no longer allowed.")
            else:
                try:
                    self._deliver(delivery)
                except TelegramError as error:
                    if error.error_code not in PERMANENT_DELIVERY_ERRORS:
                        raise
                    # A permanent rejection of one message (for example a
                    # blocked bot) must not wedge every later reply behind it.
                    print(f"Telegram rejected a queued message permanently: {error}")
            if entry_id is None:
                self._ephemeral_outbox.pop(0)
            else:
                self.config_store.remove_telegram_output(entry_id)

    def _deliver(self, delivery: TelegramDelivery) -> None:
        if not delivery.rich:
            self.bot.send_message(delivery.chat_id, delivery.text)
            return
        try:
            self.bot.send_rich_message(delivery.chat_id, delivery.text)
        except TelegramError as error:
            print(f"Telegram rich message delivery error: {error}")
            if error.error_code != 400:
                raise
            self.bot.send_message(delivery.chat_id, delivery.text)

    def _handle_update(
        self,
        update: dict[str, Any],
        outbox: list[TelegramDelivery],
        enqueue: Callable[..., bool],
    ) -> tuple[bool, bool]:
        """Return whether the update was handled and whether its replies are already queued."""
        message = update.get("message")
        if not isinstance(message, dict):
            return False, False
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, dict) or not isinstance(sender, dict):
            return False, False
        chat_id = chat.get("id")
        if (
            chat.get("type") != "private"
            or sender.get("is_bot") is True
            or not isinstance(chat_id, int)
            or not self._paired(sender.get("id"))
        ):
            return False, False

        def send(text: str, *, rich: bool = False) -> None:
            if rich:
                outbox.append(TelegramDelivery(chat_id=chat_id, text=text, rich=True))
                return
            outbox.extend(
                TelegramDelivery(chat_id=chat_id, text=chunk) for chunk in split_message(text)
            )

        text = message.get("text")
        if not isinstance(text, str) or not text.strip():
            send(self._unsupported_message())
            return True, False
        text = text.strip()
        if len(text) > MESSAGE_LIMIT:
            send(self._message_too_long())
            return True, False
        command = _command_name(text)
        state = self.config_store.load_app_state()
        if command == "/start":
            send(self._consent_notice(state.telegram_consent_version == CONSENT_VERSION))
            return True, False
        if command == "/crisis":
            # Static text, no model call and no stored data, so it is answered
            # before consent and still works when the provider is down.
            send(crisis_resources(self.session.locale))
            return True, False

        consent = "I UNDERSTAND"
        if state.telegram_consent_version != CONSENT_VERSION:
            if text == f"/consent {consent}":
                # Consent, its confirmation, and the offset commit together, so a
                # crash cannot record one without the others.
                send(self._consent_recorded())
                enqueue(consent=True)
                return True, True
            send(self._consent_notice(False))
            return True, False

        if command == "/end":
            send(self._command_response(command, None) or self._unknown_command())
            return True, False
        if command:
            # These views render stored records. A local deletion between the
            # read and the queue write would otherwise let a discarded record
            # reach Telegram, so the view is rendered again when one intervened.
            while True:
                epoch = self.config_store.telegram_output_epoch()
                outbox.clear()
                response = self._command_response(command, _command_argument(text))
                send(response or self._unknown_command())
                if enqueue(rendered_at_epoch=epoch):
                    return True, True
        before = self._durable_snapshot()
        draft_id = secrets.randbits(31) or 1
        last_draft_at = 0.0
        draft_retry_at = 0.0
        undelivered_tool_events: list[str] = []

        def render(event: TurnStreamEvent) -> None:
            nonlocal last_draft_at, draft_retry_at
            if event.kind is not TurnStreamKind.REPLY:
                if undelivered_tool_events:
                    undelivered_tool_events.append(event.text)
                    return
                try:
                    self.bot.send_message(chat_id, event.text)
                except TelegramError as error:
                    undelivered_tool_events.append(event.text)
                    print(f"Telegram tool trace delivery error: {error}")
                return
            now = time.monotonic()
            if now < draft_retry_at or now - last_draft_at < DRAFT_INTERVAL_SECONDS:
                return
            draft = event.text[:MESSAGE_LIMIT]
            if not draft:
                return
            last_draft_at = now
            rich = _safe_rich_markdown(draft)
            try:
                if rich:
                    self.bot.send_rich_message_draft(chat_id, draft_id, draft)
                else:
                    self.bot.send_message_draft(chat_id, draft_id, draft)
            except TelegramError as error:
                print(f"Telegram draft delivery error: {error}")
                if error.retry_after:
                    draft_retry_at = now + error.retry_after
                if rich and error.error_code == 400:
                    try:
                        self.bot.send_message_draft(chat_id, draft_id, draft)
                    except TelegramError as fallback_error:
                        print(f"Telegram plain draft delivery error: {fallback_error}")

        def queue_turn(turn: ChatTurn) -> None:
            for tool_event in undelivered_tool_events:
                send(tool_event)
            if turn.notice:
                send(turn.notice)
            send(turn.text, rich=_safe_rich_markdown(turn.text))
            changes = self._durable_changes(before)
            if changes:
                send(changes)
            if self._ephemeral or enqueue(rendered_at_epoch=epoch):
                return
            # A local deletion landed while this reply was being written, and the
            # reply or its change summary may quote what it removed. The turn
            # stays committed; only the notice below is sent.
            outbox.clear()
            send(self._memory_changed())
            enqueue()

        epoch = self.config_store.telegram_output_epoch()

        typing_stopped = threading.Event()
        typing = threading.Thread(
            target=self._keep_typing, args=(chat_id, typing_stopped), daemon=True
        )
        typing.start()
        try:
            self.session.respond(text, on_event=render, on_commit=queue_turn)
        except Exception as error:  # Provider SDKs expose different error types.
            # The class name alone cannot separate a refused request from an
            # unreachable provider. Add the HTTP status when the SDK exposes
            # one, and keep the body out: it can echo conversation content.
            status = getattr(error, "status_code", None)
            status_note = f" (HTTP {status})" if status else ""
            # The type alone cannot separate a refused request from a dropped
            # connection. Bound the message: a provider can echo prompt content
            # back in an error, and conversations must not reach the log.
            summary = " ".join(str(error).split())[:200]
            cause = error.__cause__
            cause_note = f" <- {type(cause).__name__}" if cause is not None else ""
            print(
                f"Model error while handling Telegram message: "
                f"{type(error).__name__}{status_note}{cause_note}: {summary}"
            )
            # Any failure inside the turn transaction rolled the queued replies
            # back with it, so the caller queues this notice instead.
            outbox.clear()
            send(self._model_error())
            return True, False
        finally:
            typing_stopped.set()
            # A sendChatAction already in flight can take up to the request
            # timeout; the finished reply must not wait for it.
            typing.join(timeout=TYPING_JOIN_SECONDS)
        # An ephemeral turn commits to memory only; its replies are queued by
        # the caller once the turn has returned, never inside that commit.
        return True, not self._ephemeral

    def _memory_changed(self) -> str:
        return (
            "Your memory changed while I was answering, so I did not send that reply. "
            "Please send your message again."
        )

    def _keep_typing(self, chat_id: int, stopped: threading.Event) -> None:
        # Telegram shows a chat action for about five seconds; a model turn can
        # take much longer, so repeat it until the turn ends.
        while True:
            wait = TYPING_INTERVAL_SECONDS
            try:
                self.bot.send_typing(chat_id)
            except TelegramError as error:
                print(f"Telegram typing indicator error: {error}")
                # Flood control asks for silence until its deadline; typing is
                # cosmetic, so it waits rather than delaying the reply further.
                wait = max(wait, error.retry_after or 0)
            if stopped.wait(wait):
                return

    def _command_response(self, command: str, argument: str | None) -> str | None:
        if command == "/help":
            return (
                "CONVERSATION\n"
                "/start — identity, notice, and consent\n"
                "/status — agent, session, and archive status\n"
                "/crisis — emergency resources, without a model reply\n"
                "/end — close and summarize the session\n\n"
                "TRANSPARENCY\n"
                "/case — formulation and linked evidence\n"
                "/memory [page] — active facts and hypotheses\n"
                "/sessions [page|ID] — session summaries\n"
                "/interventions [page] — interventions and outcomes\n"
                "/privacy — data flow and limits\n\n"
                "Correction, forgetting, export, deletion, and authentication "
                "remain on the local CLI for safety."
            )
        if command == "/status":
            return self._status()
        if command == "/case":
            return self._case()
        if command == "/memory":
            return self._memory(argument)
        if command == "/sessions":
            return self._sessions(argument)
        if command == "/interventions":
            return self._interventions(argument)
        if command == "/privacy":
            return self._privacy()
        if command == "/end":
            closed = self.session.end()
            return (
                "No active session." if closed is None else "Session closed; transcript preserved."
            )
        return None

    def _status(self) -> str:
        state = self.config_store.load_app_state()
        session = self.store.active_session()
        memories = self.store.list_claims()
        sessions = self.store.list_sessions()
        completed_sessions = sum(item.ended_at is not None for item in sessions)
        interventions = self.store.list_interventions()
        formulation = self.store.load_formulation()
        session_status = (
            f"active since {_date(session.started_at)} · ID {session.id}"
            if session
            else "no active session"
        )
        context_status = (
            f"{session.last_context_tokens}/{self.session.context_window_tokens} estimated tokens"
            if session and hasattr(self.session, "context_window_tokens")
            else "not available"
        )
        return (
            "STATUS\n"
            "Identity: experimental AI, without human monitoring\n"
            "Channel: private Telegram chat with the authorized user\n"
            f"Model: {state.default_model or 'not configured'}\n"
            f"Session: {session_status}\n"
            f"Context: {context_status}\n"
            f"Active memory: {len(memories)} items\n"
            f"Sessions: {len(sessions)} ({completed_sessions} closed)\n"
            f"Recorded interventions: {len(interventions)}\n"
            f"Current focus: {formulation.accepted_focus or 'not set'}\n"
            f"Memory mode: {self.session.memory_mode.value}\n"
            "Semantic memory: "
            f"{'local and encrypted' if state.embedding_model else 'not configured'}"
        )

    def _case(self) -> str:
        formulation = self.store.load_formulation()
        labels = {
            "presenting_concerns": "Concerns",
            "situations_and_triggers": "Situations and triggers",
            "emotions_and_body": "Emotions and body",
            "meanings_and_thoughts": "Meanings and thoughts",
            "actions_and_coping": "Actions and coping",
            "short_term_consequences": "Short-term consequences",
            "longer_term_consequences": "Longer-term consequences",
            "relationship_patterns": "Relationships",
            "maintaining_factors": "Maintaining factors",
            "strengths_and_protective_factors": "Strengths",
            "exceptions": "Exceptions",
            "course_and_duration": "Course",
            "functioning_impact": "Impact",
            "user_explanation": "User explanation",
            "social_and_cultural_context": "Social and cultural context",
            "prior_helpful_or_harmful_support": "Helpful or harmful support",
            "preferred_help": "Preferences",
            "process_preferences": "Conversation preferences",
            "open_hypotheses": "Open hypotheses",
            "shared_hypotheses": "Shared hypotheses",
            "open_questions": "Open questions",
        }
        lines = ["SHARED FORMULATION"]
        if formulation.accepted_focus:
            lines.append(f"Focus: {formulation.accepted_focus}")
        if formulation.proposed_focus:
            lines.append(f"Proposed focus: {formulation.proposed_focus}")
        for field, label in labels.items():
            values = getattr(formulation, field)
            if not values:
                continue
            lines.append(f"\n{label}")
            evidence = formulation.evidence.get(field, [])
            lines.extend(
                f"• {value}" + (f" [evidence: {evidence[index]}]" if index < len(evidence) else "")
                for index, value in enumerate(values)
            )
        if len(lines) == 1:
            lines.append("It has not been built yet.")
        lines.append("\nInterpretations remain hypotheses until you confirm them.")
        return "\n".join(lines)

    def _memory(self, argument: str | None) -> str:
        items = self.store.list_claims()
        page, selected, pages = _page(items, argument, MEMORY_PAGE_SIZE)
        title = f"ACTIVE MEMORY · page {page}/{pages}"
        if not selected:
            return title + "\nNo active items."
        lines = [title]
        for item in selected:
            evidence = ", ".join(
                f"{reference.message_id}: {reference.quote or '[no exact quote]'}"
                for reference in item.evidence
            )
            lines.append(
                f"\n{item.id} · {_memory_label(item.kind.value)} · "
                f"{item.origin.value} · {item.fit.value} · {item.lifecycle.value}\n"
                f"{item.content}\nEvidence: {evidence} · updated {_date(item.last_seen_at)}"
            )
        lines.append("\nArchived and superseded items are excluded. Use /memory N to change page.")
        return "\n".join(lines)

    def _sessions(self, argument: str | None) -> str:
        sessions = self.store.list_sessions()
        if argument:
            found = next((item for item in sessions if item.id == argument), None)
            if found is not None or not argument.isdecimal():
                return self._session_detail(found)
        page, selected, pages = _page(sessions, argument, SESSION_PAGE_SIZE)
        title = f"SESSIONS · page {page}/{pages}"
        if not selected:
            return title + "\nNo sessions."
        lines = [title]
        for item in selected:
            state = "active" if item.ended_at is None else "closed"
            summary = item.summary or "summary not available yet"
            lines.append(f"\n{item.id} · {_date(item.started_at)} · {state}\n{summary}")
        lines.append("\nUse /sessions N for a page or /sessions ID for details.")
        return "\n".join(lines)

    def _session_detail(self, session: Any) -> str:
        if session is None:
            return "Session not found."
        labels = (
            "Summary",
            "Themes",
            "User-defined concerns",
            "Meaningful changes",
            "Interventions discussed",
            "What was tried",
            "Outcomes",
            "Unwanted effects",
            "Process feedback",
            "Support choices",
            "Open questions",
        )
        lines = [
            f"SESSION {session.id}",
            f"{_date(session.started_at)} → "
            + (_date(session.ended_at) if session.ended_at else "active"),
        ]
        values = (
            session.summary,
            session.themes,
            session.user_defined_concerns,
            session.meaningful_changes,
            session.interventions_discussed,
            session.tried,
            session.outcomes,
            session.unwanted_effects,
            session.process_feedback,
            session.support_choices,
            session.open_questions,
        )
        for label, value in zip(labels, values, strict=True):
            if value:
                content = (
                    "\n".join(f"• {item}" for item in value) if isinstance(value, list) else value
                )
                lines.append(f"\n{label}\n{content}")
        if session.consolidation_error:
            lines.append(f"\nConsolidation failed: {session.consolidation_error}")
        if session.end_reason:
            lines.append(f"\nClosed because: {session.end_reason.value}")
        return "\n".join(lines)

    def _interventions(self, argument: str | None) -> str:
        items = self.store.list_interventions()
        page, selected, pages = _page(items, argument, INTERVENTION_PAGE_SIZE)
        title = f"INTERVENTIONS · page {page}/{pages}"
        if not selected:
            return title + "\nNo recorded interventions."
        lines = [title]
        for item in selected:
            details = [
                f"\n{item.id} · {_status_label(item.state.value)}",
                f"Method: {item.skill}",
                item.description,
            ]
            if item.prediction:
                details.append(f"Prediction: {item.prediction}")
            if item.outcome:
                details.append(f"Outcome: {item.outcome}")
            if item.user_appraisal:
                details.append(f"Appraisal: {item.user_appraisal}")
            if item.unwanted_effects:
                details.append(f"Unwanted effects: {item.unwanted_effects}")
            details.append(f"Decision: {item.decision.value}")
            if item.linked_claim_ids:
                details.append("Linked claims: " + ", ".join(item.linked_claim_ids))
            if item.follow_up_information:
                details.append(f"Follow-up: {item.follow_up_information}")
            lines.extend(details)
        lines.append("\nUse /interventions N to change page.")
        return "\n".join(lines)

    def _privacy(self) -> str:
        state = self.config_store.load_app_state()
        policy = state.retention_policy
        return (
            "PRIVACY AND TRANSPARENCY\n"
            "• Telegram receives messages, replies, and data you ask to view here.\n"
            "• Telegram cloud chats are not end-to-end encrypted.\n"
            "• The model provider receives the message and only selected context.\n"
            f"• Current memory mode: {self.session.memory_mode.value}.\n"
            "• In standard mode, archive, structured memory, and semantic index are encrypted "
            "on the host; transcript-only writes no structured state; ephemeral writes no "
            "conversation content to disk, though the Telegram update position is still saved.\n"
            f"• Local retention: raw messages={policy.raw_message_days or 'indefinite'}, "
            f"session summaries={policy.session_summary_days or 'indefinite'}, "
            f"stale hypotheses={policy.stale_hypothesis_days or 'indefinite'} days.\n"
            "• Semantic retrieval uses a local model; it does not establish facts or evidence.\n"
            "• No person reads or monitors the chat.\n"
            "• The bot cannot contact help, locate you, or act outside the chat.\n"
            "• Agent tool inputs and outputs are shown and retained in encrypted history.\n"
            "• Any durable changes are disclosed after each reply.\n"
            "• Internal prompts, tokens, and private reasoning are not shown.\n\n"
            "Use the local CLI for export, correction, forgetting, and local deletion. "
            "Changing mode does not erase existing data. Local deletion cannot remove provider, "
            "Telegram, plaintext export, backup, or terminal-capture copies."
        )

    def _durable_snapshot(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        return (
            {item.id: item for item in self.store.list_claims()},
            {item.id: item for item in self.store.list_interventions()},
            self.store.load_formulation().model_dump(),
        )

    def _durable_changes(
        self, before: tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
    ) -> str | None:
        old_memory, old_interventions, old_formulation = before
        changes: list[str] = []
        for item in self.store.list_claims():
            old = old_memory.get(item.id)
            if old is None:
                label = _memory_label(item.kind.value)
                changes.append(f"Saved {label}: {item.content} [{item.id}]")
            elif old.model_dump() != item.model_dump():
                changes.append(f"Memory updated: {item.content} [{item.id}]")
        for item in self.store.list_interventions():
            old = old_interventions.get(item.id)
            if old is None or old.model_dump() != item.model_dump():
                status = _status_label(item.state.value)
                changes.append(f"Intervention {status}: {item.description} [{item.id}]")
        formulation = self.store.load_formulation().model_dump()
        for field in ("accepted_focus", "proposed_focus"):
            if old_formulation.get(field) != formulation.get(field) and formulation.get(field):
                label = "Accepted focus" if field == "accepted_focus" else "Proposed focus"
                changes.append(f"{label}: {formulation[field]}")
        if not changes:
            return None
        return "THIS TURN'S RECORD\n" + "\n".join(f"• {change}" for change in changes)

    def _consent_notice(self, consented: bool) -> str:
        notice = (
            "Filo is experimental AI for adults using it privately for self-reflection. "
            "It is not therapy, diagnosis, medical advice, emergency care, or human monitoring, "
            "and its output can be wrong. "
            "Telegram receives messages, replies, and data you choose to view here; "
            "its cloud chats are not end-to-end encrypted. "
            "Any remote provider receives messages, successful session history, a separate "
            "bounded case-data envelope, and a dynamically loaded skill when used. Embeddings run "
            f"locally. Current memory mode: {self.session.memory_mode.value}. Local retention does "
            "not govern provider, Telegram, export, backup, or terminal copies. Plaintext exports "
            "must be protected. By continuing, you confirm that you are at least 18 and accept "
            "these data flows. "
        )
        return notice + (
            "Consent is already recorded: you can message me."
            if consented
            else "To continue, type exactly: /consent I UNDERSTAND"
        )

    def _consent_recorded(self) -> str:
        return "Consent recorded. You can message me or use /help."

    def _message_too_long(self) -> str:
        return "That message is too long. Please split it into shorter parts."

    def _unsupported_message(self) -> str:
        return "I can only read and archive text. This content was not processed."

    def _unknown_command(self) -> str:
        return "Unknown command. Use /help."

    def _model_error(self) -> str:
        return (
            "I couldn't respond. Please try again shortly. "
            "If you may be in danger, /crisis shows emergency resources."
        )


def split_message(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    remaining = text.strip()
    if not remaining:
        return [" "]
    chunks: list[str] = []
    while len(remaining) > limit:
        boundary = max(
            remaining.rfind("\n\n", 0, limit + 1),
            remaining.rfind("\n", 0, limit + 1),
            remaining.rfind(" ", 0, limit + 1),
        )
        if boundary < limit // 2:
            boundary = limit
        chunks.append(remaining[:boundary].rstrip())
        remaining = remaining[boundary:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


def _safe_rich_markdown(text: str) -> bool:
    return "![" not in text and re.search(r"<[A-Za-z][^>]*>", text) is None


def _command_name(text: str) -> str | None:
    first = text.split(maxsplit=1)[0]
    if not first.startswith("/"):
        return None
    return first.split("@", 1)[0].lower()


def _command_argument(text: str) -> str | None:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) == 2 and parts[1].strip() else None


def _page(items: list[Any], argument: str | None, size: int) -> tuple[int, list[Any], int]:
    pages = max(1, (len(items) + size - 1) // size)
    page = int(argument) if argument and argument.isdecimal() else 1
    page = min(max(page, 1), pages)
    start = (page - 1) * size
    return page, items[start : start + size], pages


def _date(value: str) -> str:
    return datetime.fromisoformat(value).astimezone().strftime("%d/%m/%Y %H:%M")


def _memory_label(value: str) -> str:
    labels = {
        "fact": "fact",
        "preference": "preference",
        "event": "event",
        "pattern": "pattern",
        "hypothesis": "hypothesis",
    }
    return labels.get(value, value)


def _status_label(value: str) -> str:
    labels = {
        "offered": "offered",
        "agreed": "agreed",
        "tried": "tried",
        "not_tried": "not tried",
        "stopped": "stopped",
    }
    return labels.get(value, value)


def _http_error_details(error: HTTPError) -> tuple[str, int, int | None]:
    try:
        body = json.loads(error.read())
    except (OSError, http.client.HTTPException, ValueError):
        return f"HTTP {error.code}", error.code, None
    if not isinstance(body, dict):
        return f"HTTP {error.code}", error.code, None
    description = body.get("description")
    error_code = body.get("error_code")
    parameters = body.get("parameters")
    retry_after = (
        parameters.get("retry_after")
        if isinstance(parameters, dict) and isinstance(parameters.get("retry_after"), int)
        else None
    )
    return (
        description if isinstance(description, str) else f"HTTP {error.code}",
        error_code if isinstance(error_code, int) else error.code,
        retry_after,
    )


def _locked(error: sqlite3.OperationalError) -> bool:
    return "locked" in str(error) or "busy" in str(error)
