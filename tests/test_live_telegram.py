import os
import secrets
import time

import pytest

from filo.cli import TELEGRAM_SECRET
from filo.memory import MemoryStore
from filo.telegram import TelegramBot

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("FILO_RUN_TELEGRAM_TEST") != "1",
        reason="Set FILO_RUN_TELEGRAM_TEST=1 after configuring Telegram.",
    ),
]


def test_configured_telegram_rich_stream() -> None:
    store = MemoryStore()
    token = store.load_secret(TELEGRAM_SECRET)
    chat_id = store.load_app_state().telegram_allowed_user_id
    if token is None or chat_id is None:
        pytest.skip("Run `filo setup` and configure Telegram first.")

    bot = TelegramBot(token.decode())
    draft_id = secrets.randbits(31) or 1
    bot.send_rich_message_draft(chat_id, draft_id, "**Telegram live test…**")
    time.sleep(0.3)
    bot.send_rich_message_draft(
        chat_id,
        draft_id,
        "**Telegram live test**\n\nStreaming draft updated successfully.",
    )
    bot.send_rich_message(
        chat_id,
        "✅ **Telegram live test passed**\n\n"
        "Rich draft replacement and persistent final delivery succeeded.",
    )
