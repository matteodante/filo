# Telegram channel research — 2026-07-22

## Decision

Add Telegram as an optional private transport around the existing `ChatSession`. Use direct Bot API
calls for `getMe`, `deleteWebhook`, `setMyCommands`, `getUpdates`, and `sendMessage`; Telegram has no
official Python SDK and a bot framework is unnecessary for this five-method sequential adapter.
Long polling requires no public ingress or TLS configuration and is OpenClaw's default Telegram mode.

## Current contract

- Reuse the configured model, protocol pack, conversational locale, `MemoryStore`, and
  `ChatSession`; keep the static transport interface in English.
- Read the bot token and positive numeric user ID from the encrypted local configuration written by
  `filo setup`; never print or export the token.
- Validate with `getMe`, remove a webhook without dropping pending messages, install the command menu,
  and poll only `message` updates with a positive timeout.
- Authorize before commands or model execution: private chat, non-bot sender, exact numeric allowlist.
- Ignore groups and unauthorized updates without replying. Accept text only and use plain-text output.
- Require Telegram-specific consent describing both Telegram and any remote model provider.
- Process updates sequentially and persist `update_id + 1` in encrypted application state.
- Keep Telegram commands to `/start`, `/help`, and `/end`. Memory controls, auth, export, deletion,
  and process control remain local-only.
- Treat HTTP 400, 401, and 409 as fatal configuration/ownership failures; retry transient polling
  transport failures without logging message content or credentials.

Telegram allows text messages up to 4096 characters. This adapter caps inbound text and splits
outbound text at 4000 characters, preferring paragraph, newline, and word boundaries.

## Known reliability boundary

Sequential polling plus a durable watermark prevents ordinary restart replay. It cannot guarantee
exactly-once processing if the process dies after `ChatSession` commits memory but before the offset
is saved. The eventual solution is an encrypted inbox and an external idempotency key committed
atomically with the conversational turn. Ambiguous `sendMessage` failures must not be blindly retried
because Telegram may already have delivered the reply.

## Deferred

Groups, media, voice, callbacks, formatting, streaming previews, webhooks, pairing, multiple users,
multiple bot tokens, generic channel abstractions, remote memory administration, and SaaS scaling.

## Primary sources

- Telegram Bot API: https://core.telegram.org/bots/api
- `getUpdates`: https://core.telegram.org/bots/api#getupdates
- `sendMessage`: https://core.telegram.org/bots/api#sendmessage
- `setMyCommands`: https://core.telegram.org/bots/api#setmycommands
- Telegram Bots FAQ: https://core.telegram.org/bots/faq
- Telegram bot features: https://core.telegram.org/bots/features
- OpenClaw Telegram channel:
  https://github.com/openclaw/openclaw/blob/main/docs/channels/telegram.md
