# Privacy and data flow

This notice describes the self-hosted experimental alpha. It is not a claim of regulatory
compliance.

## Identity, purpose, and operator

Filo is an AI for adults privately organizing and reflecting on user-provided thoughts. It is
not a human, psychologist, psychotherapist, clinical service, emergency service, or human-monitored
channel. It does not diagnose or prescribe, and its output can be wrong.

Matteo Dante publishes the free, non-commercial source code as an individual. The person installing
it operates the single-user instance and chooses the provider and Telegram bot. Normal application
use does not send conversations, memory, or credentials to the repository maintainer.

## Local storage

By default `~/.filo/filo.db` holds encrypted payloads for configuration, transcripts,
successful model history, sessions, claims and evidence, case formulation, process preferences,
interventions and unwanted effects, support choices, and the derived semantic index.
`~/.filo/memory.key` holds the Fernet key. Directory and key permissions are restricted where
the operating system supports them.

The pinned multilingual embedding model is downloaded from Hugging Face and performs document and
query inference locally. Vectors are encrypted and never establish truth, evidence, origin, fit, or
lifecycle.

Fernet protects a copied database without its key. It does not protect against a compromised user
account or operating system, malware, screen capture, terminal history, or a backup containing both
database and key.

## Memory modes

The current mode is shown by `filo privacy show`, Telegram `/status`, and Telegram `/privacy`.

- `standard` (default): encrypted transcript/history, claims, formulation, preferences,
  interventions, support choices, and semantic index.
- `transcript-only`: encrypted transcript/history only; no new structured or semantic mutations.
- `ephemeral`: in-process context only; no transcript, history, claims, formulation, intervention,
  support choice, or semantic index item is persisted.

Changing mode does not retroactively delete data.

## External recipients

| Configuration | Data sent outside the device |
| --- | --- |
| Local Ollama or a same-device `local:` server + terminal | No conversation content is intentionally sent to a remote model or transport |
| `local:` server on another host of the operator's own network | The same model input, over that network to hardware the operator controls, reaching no third-party provider |
| Remote PydanticAI model | Current message, successful active-session history, separate bounded case-data JSON, and a verified skill body only when dynamically loaded |
| Experimental personal Codex OAuth | The same model input through the user's ChatGPT Codex account under that product's terms |
| Telegram | Incoming messages, replies, visible tool events/notices, and local records explicitly viewed in Telegram; model input also reaches the chosen provider |

Ordinary OpenAI Responses requests set `store=false`, but provider abuse-monitoring or other
retention may still apply. Telegram bot chats are cloud chats and are not end-to-end encrypted.
Every external provider controls its own retention, training, abuse-monitoring, subprocessors,
transfer, and deletion rules.

No provider receives the local encryption key, local semantic vectors, stored credentials, internal
protocol instructions as an exposed view, or provider thinking. A dynamically loaded skill is part
of model input for that turn. Provider thinking and repeated instructions are removed before model
history is persisted.

## Retention, export, and deletion

Retention defaults to indefinite (`None`) for raw messages, session summaries, and stale
hypotheses. `filo retention set`, `dry-run`, and `apply` configure or execute local retention;
policy is also applied at conversation startup and before retrieval when configured. There is no
background worker.

`filo delete-session <id>`, `filo delete-before <date>`, claim forgetting, corrections, retention,
and `filo delete-data` propagate to derived semantic entries, formulation links, summaries,
excerpts, intervention content, and pending IDs where applicable. `filo export` returns decrypted
JSON; a file export is plaintext and must be protected.

Telegram replies, tool traces, and change summaries that Telegram has not yet accepted are kept
encrypted in a local queue table until delivery, and appear in `filo export` while pending. Every
deletion above, claim forgetting, and corrections discard all of them rather than sending content
that may quote deleted records; retention discards those queued before the cutoff that removed
data. In `ephemeral` mode they stay in process memory only and are lost if the listener stops.

Local deletion cannot delete data already held by a model provider or Telegram, or copies in
exports, backups, terminal capture, screenshots, or system-level logs. Delete Telegram messages or
the chat separately. Provider deletion must be handled under that provider's controls.

## Clean-break schema

This revision does not migrate older stores and does not create plaintext backups. An incompatible
database is rejected before use. Use a new data directory or deliberately delete the old local
store after preserving any export you need.

## Reporting

Do not put conversations, databases, exports, health information, credentials, or identifiers in a
GitHub issue. Use synthetic reproductions. Report vulnerabilities through
[GitHub private vulnerability reporting](https://github.com/matteodante/filo/security/advisories/new).
