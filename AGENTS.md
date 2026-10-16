# Filo project guide

This file is the canonical source of truth for the project's purpose, scope, architecture, and
working rules. Keep it updated in the same change whenever behavior, commands, storage, scope,
architecture, dependencies, licensing, or milestones change. `README.md` is the concise public
overview; keep it synchronized with this canonical guide when user-visible behavior changes.

Formerly published as Therapist (v0.1–v0.3).

## Project idea

Filo is an experimental, self-hosted AI conversation agent for adult self-reflection and the
organization of user-provided thoughts. It uses careful listening, longitudinal continuity,
collaborative formulation, pattern recognition, gentle challenge, and selection of an appropriate
next conversational step.

It remains explicitly an AI. It must not claim to be a psychologist or psychotherapist, describe
itself as a therapist or counselor or as offering therapy, treatment, or psychological support,
diagnose, prescribe, promise recovery, present itself as an emergency service, or imply human monitoring.
It is not intended for diagnosis, prevention, monitoring, prediction, prognosis, treatment, or
alleviation of any disease, disorder, injury, or disability.

The long-term product may support both self-hosted and SaaS distribution. The current milestone is
deliberately smaller: make the single-user conversation and long-term memory work well through the
local CLI and one private Telegram transport before building broader infrastructure.
The first public alpha support claim is limited to the CLI and private Telegram transport using a
personal ChatGPT Plus/Pro account through the experimental `codex:` OAuth provider. Setup also offers
the models of a reachable OpenAI-compatible `local:` server, which carries no support claim and has
no release-cleared evaluation baseline. The remaining PydanticAI conversation providers stay
technical escape hatches, not configurations advertised or release-cleared for the first alpha.

Original code and project content are open source under `AGPL-3.0-or-later`. Linked or referenced
third-party materials remain under their owners' terms and are not relicensed by this repository.
The public alpha is published by Matteo Dante as an individual, without charge, donations,
sponsorship, paid support, hosted operation, or other commercial activity.

## Current scope

Build a small bilingual, single-adult-user agent that:

- holds a warm, active, natural conversation in Italian or English;
- identifies user-stated difficulties and recurring patterns without requiring predefined goals;
- builds a visible, correctable, evidence-linked case formulation;
- keeps direct facts tied to exact user evidence, mutates corrected claims in place, and keeps focus
  proposed only within the current session until accepted;
- remembers relevant context across weeks, months, and years;
- keeps the complete archive and structured memory encrypted on the local machine;
- works with local or remote model providers through PydanticAI;
- optionally uses a personal ChatGPT Plus/Pro Codex subscription through experimental OAuth;
- keeps behavioral instructions and source references in Git-versioned protocol packs;
- lets the user inspect, confirm, correct, forget, export, and delete memory;
- records offered, agreed, tried, stopped, and untried interventions for later review;
- accepts the same conversation through a private, allowlisted Telegram bot;
- remains small enough for one maintainer to understand.

Included in this milestone:

- interactive terminal chat;
- private text-only Telegram chat using long polling and the standard Bot API;
- personal ChatGPT Plus/Pro conversation inference through the experimental `codex:` OAuth provider;
- native per-user Telegram background installation for macOS launchd, Linux systemd, and Windows
  Task Scheduler;
- one PydanticAI conversational agent and one end-of-session consolidation pass;
- device-code ChatGPT OAuth, encrypted token storage, and automatic refresh for the experimental
  `codex:` provider;
- encrypted SQLite archive and layered longitudinal memory;
- automatic session boundaries after eight hours of inactivity and explicit `/end`;
- an experimental bilingual transdiagnostic behavior pack;
- contextual agent instructions for possible danger, including Italian/EU and US resources;
- offline tests using PydanticAI test models.
- written, versioned longitudinal-memory scenarios plus opt-in real-provider memory, process, and
  bilingual safety evaluations.

Explicitly deferred:

- web, mobile, voice, Telegram groups/media/webhooks, and other channels;
- SaaS, accounts, billing, multi-tenancy, PostgreSQL, workers, and queues;
- reminders, background jobs, MCP integrations, and external tools;
- standalone vector databases, knowledge graphs, and multi-agent orchestration;
- commercial dual licensing, a CLA, a stable release, and PyPI publishing; the first GitHub alpha
  remains a prerelease;
- clinical ownership, clinical validation, efficacy studies, and formal risk management;
- MDR, AI Act, FDA, GDPR/HIPAA compliance work;
- formal threat modeling, penetration tests, audits, and certifications.

The current single-maintainer workflow uses direct pushes to `main` and has no second repository
administrator, recovery owner, or confidential project-specific conduct inbox. These are explicit
alpha governance limitations, not equivalent controls or release-safety claims.

The alpha retains baseline safeguards already present: transparent AI identity, no clinical claims,
and contextual agent instructions for possible danger and emergency resources. These are not a
validated clinical safety system.

`RELEASING.md` is the release runbook and mandatory go/no-go checklist.
`docs/maintainer-ai-literacy.md` records the minimum AI-literacy baseline for maintainers and release
decision-makers. Keep both synchronized with changes to release scope, providers, data flows,
behavioral safeguards, evaluation strategy, or project roles.
The manual `Release candidate` GitHub workflow repeats offline gates on `main`, builds the
distributions, exports a CycloneDX SBOM from locked runtime dependencies and checksums, creates
provenance and SBOM attestations, and uploads a temporary candidate bundle without publishing a tag
or release.
`docs/dpia-screening.md` and `docs/article-50-assessment.md` record proportionate maintainer
self-assessments for the narrow alpha; `docs/compliance-assessment-brief.md` preserves the questions
for an optional future external review. They are not compliance claims. Re-open them before hosted,
commercial, organizational, clinical, minor-facing, telemetry-enabled, or maintainer-accessible
operation.

## Open work

Known gaps in the current state of the repository. Close them or record why they stay open; do not
let them accumulate silently.

- Forward schema migrations are required from the Filo store onward, but the migration framework
  does not exist yet. Build it before the first change to the Filo schema after the first Filo
  release.
- The installers pin the release named in `pyproject.toml`. Until the first release tagged under
  the Filo name exists, that tag still contains the Therapist package and the installers cannot
  complete, so `README.md` documents the checkout installation instead of the bootstrap. The
  first Filo release restores the README bootstrap commands in the same change that moves the
  pin; `RELEASING.md` carries that step.
- `v0.2.0` and `v0.3.0` are released. The 2026-08-11 and 2026-08-18 addenda in
  `docs/release-readiness-2026-08-04.md` hold their evidence, including which `v0.3.0` gates reused
  the earlier live results and why.
- A real Telegram message round-trip is still undocumented. The 2026-08-11 evidence reaches an
  established polling connection, not a delivered exchange, and the bot is now configured on the
  maintainer machine, so complete the smoke and record it.
- The `local:` provider has no live baseline. Every role-play, memory, and safety result on record
  was produced against `codex:gpt-5.6-sol`, so nothing is known about how a self-hosted model
  behaves on the safety floors. Run the three live gates against a `local:` model before that path
  is described as anything more than an unevaluated escape hatch, and keep it out of any support
  claim until then.

## Conversation behavior

The experimental behavior pack draws on transdiagnostic self-help and conversation principles
informed by CBT formulation, ACT-style flexibility, behavioral activation, emotional awareness, and
problem solving. These influences do not make the agent a treatment or clinically validated
intervention.

Its loop is:

1. listen and clarify;
2. explore emotions, triggers, thoughts, behavior, relationships, and coping;
3. form a tentative, evidence-linked hypothesis;
4. share the hypothesis and ask whether it fits;
5. agree on an intervention or next conversational focus;
6. observe the user's response;
7. revise the formulation rather than defending it.

Behavioral rules:

- respond in the user's language;
- in Italian, use idiomatic contemporary standard Italian, informal address unless the user chooses
  formality, direct conversational syntax, and specific rather than formulaic warmth; avoid literal
  English calques and bureaucratic, academic, clinical, theatrical, or overly solemn register;
- respond to the user's immediate meaning before suggesting an action, without mechanically
  paraphrasing every message;
- ask brief, concrete questions and avoid interrogation;
- answer a question about how something works — a mechanism, what usually helps, whether an
  experience is common — directly and concretely; withholding that is not neutrality;
- leave a question about the user's own life to the user, without withdrawing from it: set out the
  options, what separates them, what they would need to know to choose, and what is noticeable in
  how they describe it;
- answer a direct question before asking another one; routinely returning the question is evasion
  and damages the alliance;
- state uncertainty as content — what is not known and why the reading could be wrong — instead of
  diluting the wording until it asserts nothing;
- identify recurring themes and inconsistencies with a gentle, collaborative tone;
- label interpretations as hypotheses until the user confirms them;
- treat an unstated short-term benefit or protective function of a behavior as an inference and put
  grammatical uncertainty on the functional verb itself, rather than elsewhere in the sentence;
- ask permission before exercises;
- when the user asks for understanding before suggestions, offer at most one brief hypothesis and
  keep exploring instead of listing explanations or introducing an exercise;
- do not force a goal, worksheet, or technique onto every exchange;
- use the behavior protocol as an internal map rather than a visible sequence; questions,
  hypotheses, formulation, and forward movement are optional when simple presence is more useful;
- vary response length, rhythm, and conversational move instead of repeating a
  reflection-hypothesis-question template;
- prefer the smallest useful intervention;
- do not use diagnostic labels or claim knowledge unsupported by the archive;
- do not encourage exclusivity, dependency, or withdrawal from human support.
- when a user explicitly says they are under 18, state the adult-only boundary warmly, do not
  continue the reflective process or create or modify durable memory, focus, hypothesis, or
  intervention state, and encourage a trusted adult or age-appropriate local support;
  immediate-danger handling still takes priority.
- for possible immediate danger, after localized emergency guidance, a physically present person,
  distance from available means, and the no-monitoring boundary, ask which one immediate protective
  action the user can take now; checking whether ingestion already occurred may accompany but not
  replace that action-focused question.

## Architecture

```text
User
  |
  v
CLI or private Telegram -> Git-versioned experimental behavior and safety instructions
  |
  v
PydanticAI tool loop + plain-text reply -> local model or configured remote provider
  |
  v
Encrypted SQLite archive + structured longitudinal memory
```

Use one PydanticAI agent run per conversation turn. Static instructions contain only the root
protocol, verified skill catalog, tool/epistemic contracts, and response rules. Longitudinal data is
serialized as a bounded `CaseContextEnvelope` JSON object in a separate user-data message with an
explicit quoted-data boundary; it is never concatenated into instructions. Successful session
history remains another distinct message sequence.

The model may call two read tools, `load_skill` and `retrieve_case_context`, and eight
staged write tools: `record_user_reports`, `record_hypothesis`, `correct_claim`,
`review_hypotheses`, `set_focus`, `record_process_feedback`, `record_intervention`, and
`record_support_choice`. The model may call tools repeatedly when a refined read or another
evidence-supported mutation adds value. Read results accumulate, identical writes are idempotent,
and write invariants are validated across the complete staged turn. The model may use no tool and
may load no skill. It may load at most one verified skill, with no keyword router or preliminary
classifier. Write tools are absent outside standard memory mode.

An agent-authored explanation or pattern that is important enough to revisit or ask the user to
review later is recorded with `record_hypothesis` in the same turn before it is presented. A later
user agreement reviews that agent-origin record rather than recasting the formulation as a direct
user report. Fleeting possibilities need not become durable state.

The minimal structured output is `FiloTurnOutput`: a visible reply plus `selected_skill`,
`referenced_claim_ids`, and `referenced_intervention_ids`. Metadata describes observable actions,
not private reasoning. After validation, transcript and staged changes commit in one transaction;
failure leaves both unchanged. Encrypted successful history retains paired tool exchanges while
excluding repeated instructions, loaded skill bodies, the case envelope, and provider thinking.
Slash commands and transport notices remain outside conversation history.

Conversation transports receive cumulative reply snapshots and tool input/output events from the
complete PydanticAI event stream. Drafts may contain an output attempt that is later rejected; the
next attempt replaces it, and only the validated final output is committed. Provider thinking is
never emitted. CLI renders drafts in Textual and Telegram uses ephemeral rich-message drafts.

A conversation run permits at most twenty model requests, twenty-four tool calls, two validation
retries per invalid tool call, and two output retries. Exact current-message evidence gates direct reports,
hypothesis reviews, corrections, accepted focus, reusable process feedback, support choices, and
intervention consent/outcomes where relevant. The last offered hypothesis and active intervention
remain pending so later review updates the same records.

When a session closes, use one additional model call with the structured `SessionReflection` output
to summarize the episode and link existing claims into the case formulation. Consolidation allows
two output retries and at most three model requests. It cannot create a confirmed claim. If it fails,
preserve the transcript, record a content-free error class, and leave the previous formulation
intact. Both agent runs use PydanticAI's complete synchronous API with an event stream handler so the
experimental ChatGPT Codex backend can require `stream=true`; conversation transports stream
cumulative Markdown snapshots while preserving the complete tool graph, then render the validated
final reply. Consolidation consumes its stream without exposing a draft.

Consolidation preserves valid formulation links when the model omits them and removes a link only
through an explicit `formulation_unlinks` entry. Existing evidence is retained before new links when
a field reaches its five-claim bound, so omission or overflow cannot silently evict history.

Memory retrieval uses local hybrid ranking over validated claims, active interventions, and up to
1,000 recent user archive messages by default: lexical overlap plus semantic similarity through
PydanticAI `Embedder`, with recency as a tie-breaker. The semantic index is an encrypted, derived
SQLite cache keyed by entity type, entity ID, exact model revision, and keyed content hash; it is
rebuilt after content or model changes and its affected rows are deleted on correction or
forgetting. SQLite/Fernet remains the source of truth.
Semantic retrieval is a required memory capability: setup downloads and verifies the local model,
and conversation fails closed with setup guidance when embeddings are unavailable rather than
silently switching to lexical-only ranking. Historical excerpts use the same hybrid ranking and a
minimum semantic relevance threshold; lexical tokenization adds standard-library character bigrams
for Chinese, Japanese, and Thai text without spaces. Context is reduced by complete structured items
and serialized only as valid JSON; model history and consolidation retain complete turns instead of
slicing messages mid-run. There is no intra-session compaction. Successful-run history grows until a
conservative estimate reaches 80% of the input budget, when the user receives one warning. Before a
later user turn would exceed that budget, the old session is consolidated with `context_limit` as
its end reason and the pending message starts a new session. The effective context window is the
configured model limit capped at 128,000 tokens, with 10% always reserved for model output.

## Memory model

Memory is layered so that "remember everything" does not mean sending the entire archive to the
model on every turn.

- `Message`: complete encrypted user/assistant archive retained until deletion.
- `Session`: time- or context-bounded episode with summary, themes, interventions, user response,
  open questions, and an explicit end reason.
- `MemoryItem`: a durable fact reported by the user, preference, event, pattern, or hypothesis with
  independent origin, fit, lifecycle, evidence, conflict links, and timestamps.
- `CaseFormulation`: an evidence map that derives concerns, triggers, thoughts, behavior, coping,
  relationships, course, functioning, explanatory model, preferences, maintaining factors,
  strengths, hypotheses, and focus from active memory claim IDs.
- `InterventionRecord`: one offered or agreed technique with consent state, linked claims,
  prediction, outcome, user appraisal, and follow-up information. It is not a goal.
- `CaseContextEnvelope`: bounded user reports, hypotheses, conflicts, relevant sessions, active
  interventions, process preferences, support choices, excerpts, and accepted/proposed focus.
- `SemanticIndex`: encrypted vectors for active memory items, active interventions, and bounded
  candidate user messages. It is derived, excluded from export, safe to discard, and never
  establishes truth or provenance.

Claim dimensions:

- origin: `user_statement` or `agent_hypothesis`;
- fit: `not_applicable`, `not_reviewed`, `fits`, `partly_fits`, `does_not_fit`, or `unsure`;
- lifecycle: `active`, `superseded`, or `archived`;
- evidence relation: `supports`, `corrects`, `reviews`, or `contradicts`;
- evidence quality: `exact_quote` or `grounded_paraphrase`. Both require an exact quote from the
  message that produced the record, so provenance is identical; they differ only in whether the
  stored content repeats that quote verbatim. Only a `preference` or a `pattern` may be restated,
  because both are already summaries of something said across a conversation; a `fact` or an
  `event` keeps the user's exact wording, since that is where an invented specific does the damage.
  Restating is extractive, not generative: the content must be a subsequence of its quote, so it
  may only drop the user's own words, never reorder or substitute them. It may not change the
  quote's negation and requires a quote of at least four words. A gate bounding only length,
  digits and names would admit vocabulary wholly disjoint from the quote, which is how a reported
  bad night becomes a recorded chronic condition; a gate allowing reordering would let the same
  words invert what they denied.

  Polarity markers are counted rather than merely detected, so dropping one of two negations
  cannot invert a conjunct while leaving the sentence negative overall. Coordinating conjunctions
  and dashes count as clause boundaries alongside punctuation, so the same drop cannot be smuggled
  in through the choice of quote either.

  Separately, and for every durable record including a verbatim one, a quote may not be mined out
  of the clause that qualified it: each clause fragment of the quote must carry the same polarity
  as the clause it came from, so "è insonnia cronica" cannot be lifted out of "non è insonnia
  cronica, è solo stress", and extending a quote across the next boundary does not launder the
  qualifier off its leading fragment. It applies to every quoted field that reaches durable state —
  report content and evidence, correction and replacement wording, hypothesis evidence, review and
  accepted wording, accepted focus, process preferences, support choice content, barriers and
  preferences, retrieval aliases, and intervention consent, where a mined quote would record a
  refusal as agreement. Every one of those is checked in the conversation tools, where a rejection
  can be retried, and again in the store as the backstop. Accepted focus uses a dedicated store
  method so later callers cannot bypass exact-text and polarity validation. A quote whose clauses
  disagree in polarity cannot be shortened at all, since
  dropping across the boundary would attach one clause's negation to another clause's verb. A
  residual risk remains and is accepted: within a single clause, deletion can still move what a
  negation lands on, and can drop an unmarked qualifier such as a condition, turning "only when I
  drink coffee" into an unconditional claim. No lexical check closes either, and the exact quote is
  retained beside the content so both stay auditable. No unquoted records are created because the
  Filo store started as a clean break without migration.

The complete archive is retained until the user deletes it. Corrections and forgotten items must be
removed from derived formulation and summaries and suppressed from future retrieval. Current-session
model history retains every complete successful run, including tool exchanges, until the context
boundary; long-term continuity across the resulting sessions comes from structured context and
relevant excerpts.
When generated derived text paraphrases corrected or forgotten evidence, the overlapping derived
field is conservatively invalidated instead of risking stale personal information returning.
Explicit corrections target an existing claim ID, retain old wording as superseded provenance,
clear stale aliases, and derive any replacement only from a second exact quote. A contradiction
without replacement supersedes the claim without inventing new content.
If forgetting removes the meaningful description of an intervention, its sensitive content is
replaced with a neutral tombstone and the record is stopped rather than deleted, preserving lifecycle
and provenance without returning forgotten content.

Session activity timestamps are written both to the indexed SQLite column and the encrypted session
payload in the same transaction so restart cannot move the eight-hour boundary backward. SQLite
comes from the Python standard library. Sensitive payloads are encrypted with Fernet from
`cryptography>=50,<51` using a separate local key with filesystem mode `0600`. The 50.0.0 floor
excludes the PKCS#7 decryption oracle fixed upstream; Filo does not use the affected PKCS#7
APIs. This protects copied databases and casual backups;
it does not replace full-disk encryption or protect a compromised operating-system account.

Memory modes are explicit. `standard` persists every encrypted layer; `transcript_only` persists
only transcript and successful model history and exposes no structured write tools; `ephemeral`
uses an in-process encrypted SQLite store and writes no conversation content to disk; Telegram
still persists its update offset in the configuration store. Mode changes do not erase
earlier data. `RetentionPolicy` independently controls raw-message, session-summary, and stale-
hypothesis days; all values default to `None`. Apply it at startup, before retrieval when configured,
or through explicit CLI commands. There is no worker.

The rename to Filo was an intentional clean break: Filo uses a fresh data directory and never
reads, migrates, or modifies a Therapist store. From the Filo store onward, every schema change must
ship a forward migration so that years of memory survive upgrades; the migration framework is a
separate work item and does not exist yet. Do not add legacy aliases, schema fallbacks, or plaintext
backups. The Filo schema marker is `filo-1`. The `filo-1` baseline is frozen at the first Filo
release; until then no store carrying it exists outside development, so schema additions such as
the `telegram_outbox` table amend `filo-1` directly, and from that release on every change ships a
forward migration (W0.3). Reject an unknown or future schema version before
creating any table, and refuse a data directory containing a Therapist `thera.db` before creating,
chmodding, or reading anything in it.

## CLI contract

The normal end-user installation is a copy-paste bootstrap from GitHub: `install.sh` supports macOS
and Linux, while `install.ps1` supports Windows PowerShell. The installer published for a release
downloads that exact immutable tag's source archive, installs a pinned uv bootstrap only when uv is
absent, uses uv-managed Python 3.12 to install `filo-cli` as an isolated user tool, exposes
`filo` on the user's PATH, runs interactive `filo setup`, and then runs `filo doctor`. It requires
no administrator privileges, existing Python, or Git checkout. Running the installer from a newer
release updates the alpha without deleting encrypted application state or the shared Hugging Face
model cache. `uv tool uninstall filo-cli` removes the application command but intentionally
leaves user data in place.

Once a Filo release exists, the public README uses `main` only as a small latest-alpha bootstrap
channel; that script always
downloads one explicit immutable release tag. On macOS, the piped bootstrap starts interactive
Questionary setup inside a fresh `script(1)` pseudoterminal because `prompt_toolkit` cannot register
a `/dev/tty` descriptor reopened after a pipe with `kqueue`. Linux launches setup directly, and
Windows PowerShell retains its console input.

Both installers download uv directly from its immutable GitHub release when it is absent. They first
require the release checksum manifest to match its repository-pinned SHA-256 digest and then require
the selected platform archive to match the digest in that verified manifest. They install the
verified binaries without delegating to another network bootstrap. They select uv's MIT license,
verify its text from the pinned upstream source commit, and persist it next to the installed binary.
`uv tool update-shell` handles the application tool path explicitly. Updating the pinned uv version
requires independently checking the new manifest, license revision, and digests against official
upstream assets.

Primary commands:

```text
filo setup
filo chat [--plain] [--memory-mode standard|transcript-only|ephemeral]
filo telegram [--memory-mode standard|transcript-only|ephemeral]
filo telegram-service install
filo telegram-service status
filo telegram-service restart
filo telegram-service uninstall
filo auth login
filo auth status
filo auth logout
filo memory show
filo memory case
filo memory sessions
filo memory interventions
filo memory review <id> fits|partly_fits|does_not_fit|unsure <evidence>
filo memory correct <id> <text>
filo memory forget <id>
filo memory model status
filo memory model verify
filo memory model install
filo memory model remove
filo privacy show
filo privacy set-default <mode>
filo retention show
filo retention set
filo retention dry-run
filo retention apply
filo delete-session <id>
filo delete-before <date>
filo export [--output path]
filo delete-data
filo doctor
filo protocol validate [path]
```

`filo chat` uses a full-screen Textual interface in an interactive terminal. It restores the latest
50 user/assistant turns from the active session, renders assistant Rich Markdown, displays live tool
events, and disables input while one sequential turn is running. `--plain` forces the line-oriented
interface; on an interactive terminal it uses one replaceable Rich live draft, while redirected
output emits tool events followed by only the validated final reply because an append-only stream
cannot replace a rejected attempt. Non-interactive stdin or stdout selects plain mode automatically.
The TUI never loads more than 50 historical turns and does not replay old tool traces.

`setup` downloads and verifies the local multilingual Apache-2.0
`sentence-transformers:Qwen/Qwen3-Embedding-0.6B` model at the repository-pinned revision. The model
then runs on-device for every `chat` and `telegram` conversation. Semantic retrieval has no off
switch in the product CLI. `memory model status` inspects the exact revision in the local Hugging
Face cache without network access, `verify` checks it against Hub checksums and performs local
inference, `install` downloads or repairs it, runs the same inference smoke test, and updates an
existing encrypted app configuration, and `remove` deletes only that revision from the shared cache
after confirmation. Removing the derived model files does not delete encrypted conversations or
structured memory.

Setup stores a conservative context-window limit for the selected conversation model. Known remote
models use their documented limit subject to the application-wide 128,000-token cap. Ollama models
are inspected through `/api/show`; an unknown or custom model defaults to that cap and can be
overridden downward per process with `--context-window-tokens`. An override can never exceed the
detected model limit. During setup, a validated text prompt displays the current saved value and the
available range and lets the user replace it. Models below the 16,000-token supported minimum are
rejected.

`filo setup` is the normal first-run path. Questionary arrow-key menus configure the first-alpha
ChatGPT provider, locale, Telegram, and confirmation choices. ChatGPT uses device-code OAuth; the
Telegram bot token uses a no-echo prompt and is stored in the encrypted local store. Other
PydanticAI model IDs remain available only as explicit temporary `chat` and `telegram` overrides;
their provider credentials must come from the provider's standard environment configuration.
Secrets are never placed in process arguments, exports, or plaintext configuration files.

Before asking for provider details, setup downloads and executes both document and query embeddings
with the required local multilingual model. Failure stops setup before creating the data store.
Successful setup records the exact embedding model in encrypted app state; `chat` and `telegram`
refuse missing or stale setup state. Provider and Telegram secrets are staged in memory and committed
with app defaults only after all interactive validation succeeds, so cancellation does not leave a
partially configured provider. When Telegram is configured, setup asks whether to install and start
its native per-user background service or task after the encrypted configuration is committed.
Hugging Face telemetry and implicit credential sending are disabled before importing its client.
Conversation-time embedding loads use only the verified local revision.

After `filo auth login`, omitting `--model` selects `codex:gpt-5.6-sol`. An explicit model can be
selected with `--model codex:<model-id>`. Access and refresh tokens are encrypted in the same local
store and are never included in exports. The provider attaches the current token to every request
through an `httpx` auth flow rather than a fixed API key, so the Telegram listener and the TUI keep
working across token expiry: the encrypted store is read on every request, so a logout or a new
login applies to running processes; the token is refreshed within 60 seconds of expiry and, after a
401, refreshed once and the request retried once. A token obtained because of a 401 is marked as
such in the encrypted credential, so no process refreshes it again after another 401 and a 401
unrelated to expiry cannot keep rotating the refresh token. Because the refresh token rotates on
every use, refreshes, login, logout, and `delete-data` run under an
exclusive `codex-auth.lock` file in the data directory (`flock` on macOS and Linux,
`msvcrt.locking` on Windows, retried only on lock contention), and a refresh re-reads the store
first, so concurrent processes spend it only once and cannot overwrite a newer login or restore a
deleted credential. Store reads
and refreshes run in a worker thread rather than on the event loop. The shared HTTP client keeps no
idle connections, because turns run on different event loops. Token values never enter exception
messages or model representations.

The `codex:` provider mirrors the device-code flow and Codex Responses backend used by pi and the
open-source Codex client. It is experimental, self-hosted/personal only, and not part of the public
OpenAI API contract. OpenAI documents ChatGPT authentication for official Codex clients, the Codex
SDK, and broader Codex use cases, but not this independent third-party use of the direct Codex
backend as a conversation provider. Hermes Agent publicly implements the same general ChatGPT OAuth
and direct Codex-backend pattern. The publisher accepts the resulting personal-alpha compatibility
and availability risk; the project must still call the path experimental, must not call it the
OpenAI API or imply OpenAI endorsement, and must not enable it in a SaaS. API-key and local-model
providers remain supported as technical escape hatches.
Ordinary OpenAI Responses requests explicitly set `store=false`; provider abuse-monitoring retention
can still apply. Ollama conversation models are forced to the loopback base URL.

The `local:` provider reaches any OpenAI-compatible chat-completions server, such as a llama.cpp or
llama-swap host on the operator's own network. Its base URL comes from `FILO_LOCAL_BASE_URL` and
defaults to `http://localhost:8080/v1`; `FILO_LOCAL_API_KEY` supplies a key only when the server
requires one, since self-hosted servers usually accept any value. Setup lists the models the server
advertises on `/models` and offers them before the ChatGPT path, defaulting to the first one; when
the server is unreachable the list is empty and only ChatGPT is offered. Requests ask for `xhigh`
reasoning effort, which servers that do not support the field ignore. Model conversations stay on
the operator's own hardware, so no conversation content reaches a third-party provider.

Interactive chat commands:

```text
/case
/memory
/sessions [id]
/interventions
/confirm <id>
/correct <id> <text>
/forget <id>
/end
/help
/quit
```

`/quit` only leaves the process. It does not close the conversation session. A later message closes
and consolidates the previous session if at least eight hours elapsed. `/end` closes it immediately.
When the active history approaches its context boundary, a warning is printed without archiving it.
The next over-budget message closes and consolidates the old session, starts a new one, and is
processed there. Commands, their output, and these lifecycle notices never enter conversation
history. Agent function-tool calls are not commands: their input and output are displayed and saved
in encrypted model history.

Telegram normally reads its token and user ID from the encrypted configuration written by `setup`.
The user never needs to know or type that ID: setup validates the bot token, creates a random
single-use deep link, waits for the matching private `/start` update, displays the detected account,
and asks for confirmation before storing its ID. A phone number cannot be used to look up a Bot API
user and is neither requested nor stored.
Startup validates the token, removes any webhook without discarding pending updates, installs the
allowlisted chat's English command menu and transparent bot description, then long-polls text
messages sequentially. The runtime ignores groups, bots, and every sender except the configured
numeric user ID. Unsupported media receives a clear text-only notice without model invocation or
storage. Telegram consent is separate from terminal consent and explains that Telegram receives
messages, replies, and any local data the user requests to inspect there, while any remote model
provider receives message content, successful session history, and selected context. Both consent
flows disclose the adult-only private-use scope, output fallibility, non-clinical and
non-emergency boundary, lack of human monitoring, and relevant data flows before conversation input
is accepted. The Telegram notice also states that cloud chats are not end-to-end encrypted; its
privacy view explains that local deletion does not delete Telegram messages. Changing this notice
increments its stored version and requires renewed consent.

Telegram is a conversation and read-only transparency surface, not a remote administration surface.
`/status`, `/case`, `/memory [page]`, `/sessions [page|id]`, `/interventions [page]`, and `/privacy`
show the agent identity, active session, bounded paginated structured state, evidence links, data
flow, and limitations. `/start`, `/help`, and `/end` manage consent, guidance, and the conversational
session. After each normal reply, the bot reports any durable memory, focus, or intervention changes
committed for that turn. Correcting or forgetting memory, auth, export, and deletion remain local CLI
operations. Command messages, rendered command output, context warnings, and rollover notices are
transport-only and are not archived or sent back to the conversation model. Internal prompts,
secrets, and private model reasoning are never exposed. Function-tool inputs and outputs are sent as
separate messages before the final reply and remain in encrypted model history. Reply generation
uses a random non-zero `sendRichMessageDraft` ID, updates the ephemeral Rich Markdown draft at most
four times per second including failed attempts, bounds every draft below Telegram's plain-text
limit, and persists the validated output with `sendRichMessage`. An HTTP 400 rich-format rejection
falls back to plain text; ambiguous transport failures, rate limits, and server failures never
trigger a second delivery format. Failed tool-event deliveries are retried separately and in order
before the final reply. Durable-change notices remain separate plain-text messages. Incoming text
and outgoing content stay below Telegram's limits. Every reply for an update is written to an
encrypted `telegram_outbox` table, one row per message, in the same transaction as the advanced
update offset (and, for `/consent`, the recorded consent); for a model turn that
write happens inside the turn's storage transaction through `ChatSession.respond(on_commit=...)`, so
a committed turn is never handed to the model again and a turn that fails before commit leaves
neither replies nor offset behind. The listener drains the outbox in order before every poll,
including after a restart, deletes each row by ID only after Telegram accepts it, so no process ever
writes back a stale copy of the queue, and honors
`retry_after` on transient failures. Delivery is at least once: a crash or ambiguous transport failure
after Telegram accepted a message but before its removal can send that one message twice. Only a
400 or 403 rejection of one queued message is logged and dropped, so later replies are not wedged;
401 and 404 mean an invalid or revoked token and stop the listener, while 408, 429, 5xx, and every
network failure, including resets, TLS errors, and truncated responses, surface as a transient
`TelegramError` and are retried. A queued message whose chat is no longer the allowlisted user is
dropped without being sent; the listener rereads the pairing before every send, so once setup pairs
another account even a listener that is still running stops delivering to the old one. The typing indicator is
repeated every four seconds while a turn runs and pauses for any longer `retry_after`. In
`ephemeral` mode queued replies stay in process memory and are queued only after the in-memory turn
returns; only the update offset is written to the on-disk configuration store, so a crash before
delivery loses those replies instead of writing conversation content to disk. Replies already
queued on disk by an earlier standard or transcript-only run are delivered first. Correcting or
forgetting a claim, deleting a session or a date range, and `delete-data` discard every undelivered
queued reply, because a queued message is not linked to the records it quotes; retention discards
only replies queued before the cutoff that removed data, so the startup retention pass keeps replies
a crash left queued. The listener records the persisted pairing and a SHA-256 fingerprint of the
stored bot token (never the token) when it starts; when either changes it stops before confirming
or recording any further update and reloads the configuration in the same process, so no platform
depends on a service manager to restart it, and it stops cleanly when nothing is configured any
more. Queued rows addressed to a pairing other than the one a listener runs with are skipped, not
dropped, so an `--allowed-user-id` listener and the paired one never block each other. Store
transactions begin with `BEGIN IMMEDIATE` and a ten-second busy timeout, and the listener retries
an update when the store stays locked instead of exiting. Each such discard increments a stored counter, and a transparency view
(`/status`, `/case`, `/memory`, `/sessions`, `/interventions`, `/privacy`) is queued only if the
counter is unchanged since it was rendered, in the queue's own transaction; otherwise it is rendered
again, so a deletion that lands mid-render cannot let the old view through. A model reply and its
change summary pass the same check against the counter read before the turn: if a deletion landed
while the reply was written, the committed turn is kept but only a short notice asking the user to
resend is queued. `delete-data` advances the counter rather than resetting it. Accepted limits:
retention keeps a recently queued reply that may quote content it just removed, since discarding
it would lose the replies the queue recovers after a crash; live tool events and drafts of a turn
already running can still reach the previously paired chat if re-pairing happens mid-turn, while
its final reply is dropped; and correcting or forgetting a claim discards unrelated queued replies
too, preferring privacy over delivery. Incoming updates pass the same reread pairing check as delivery, so a listener
still running after re-pairing or `delete-data` stops handing the old account's messages to the
model. A turn waits at most one second for an in-flight typing request before its reply is queued.
`filo setup` applies only the fields it changed to the state as it is when it saves, so a listener's
offset and consent written while setup waits at a prompt are not rolled back.

`filo telegram-service install` validates the saved configuration and starts the listener without
putting secrets in process arguments. It writes a mode-`0600` LaunchAgent in
`~/Library/LaunchAgents` on macOS, a mode-`0600` user unit in `~/.config/systemd/user` on Linux, or a
limited-privilege per-user scheduled task on Windows. The listener starts with the user's login;
Linux operation beyond logout depends on the host's user manager/lingering policy. `status`,
`restart`, and `uninstall` inspect, restart, or stop the process and remove only this native
definition. macOS operational output is written to the application data directory's
`filo-telegram.log`; Linux output is available through the user journal.

A background service does not inherit an interactive shell profile, so install records the resolved
`FILO_LOCAL_BASE_URL` in the LaunchAgent's `EnvironmentVariables` or the systemd unit's
`Environment=` settings. Without that the listener would silently fall back to
`http://localhost:8080/v1` and lose a `local:` server hosted elsewhere. Reinstall the service after
the endpoint changes. `FILO_LOCAL_API_KEY` is deliberately not recorded, because these service
definitions are plaintext on disk; a local server that requires a key must run through
`filo telegram` in a shell that exports it. Windows scheduled tasks receive the user's persisted
environment instead, so a Windows endpoint has to be set with `setx` rather than only in the current
console.

macOS 15 and later block the background listener from reaching a `local:` server on the operator's
own network, while the same binary reaches it from a terminal. Apple grants local network access to
command-line tools and to launchd daemons but explicitly not to launchd agents, which is how this
service installs (TN3179, *Understanding local network privacy*). The block surfaces as
`EHOSTUNREACH` inside the provider SDK, so the listener answers on Telegram, logs nothing unusual,
and only ever replies that it could not respond. `filo doctor` reports the endpoint and names this
cause. The operator resolves it by exempting the server's address, which needs a restart:

```bash
sudo defaults write com.apple.network.local-network AllowedWiFiLocalNetworkAddresses -array "<ip>/32"
sudo defaults write com.apple.network.local-network AllowedEthernetLocalNetworkAddresses -array "<ip>/32"
```

Prefer a single address over a whole subnet: the preference removes the protection for every listed
address for every program on the machine. Granting the permission in System Settings is unreliable
here, because macOS tracks identity by code signature and an ad-hoc signed interpreter does not hold
a stable one.

Minimal setup:

1. Create a bot with Telegram's `@BotFather`, keep its token private, and disable group joins as
   defense in depth. Set its privacy-policy URL to
   `https://github.com/matteodante/filo/blob/main/PRIVACY.md`.
2. Run `filo setup`, choose the model and locale, then paste the bot token in the hidden prompt.
3. Open the one-time `t.me` link printed by setup, press Start, and confirm the detected account.
4. Choose whether setup should install the background service, or run `filo telegram` manually.
   Only one poller may use a bot token at a time.
5. Run `filo doctor`, then enter the exact consent command shown in the private bot chat.

`export` returns decrypted user-owned application state, formulation, memory items, sessions, and
messages. `delete-data` removes all of those records.

## Protocol packs

```text
protocols/<id>/
|- manifest.yaml
|- SKILL.md
|- references/
`- skills/
   `- <conversation-skill>/
      |- SKILL.md
      `- references/
```

The manifest contains the pack ID, experimental/review status, locales, ordered conversation skills,
source metadata, and SHA-256 hashes for the root skill, every loaded nested skill, and every
reference. Changed skill or reference files invalidate the pack. Protocol history and releases are
identified by Git commits and tags rather than duplicated SemVer directories or a manifest version.
Keep one directory per genuinely different protocol; do not copy a directory merely to preserve an
older revision.

The canonical protocol remains under `protocols/`, which holds protocol packs and nothing else.
Background reading and maintainer assessments belong in `docs/research/`. The wheel build copies the
default pack into the installed `filo` package so a user-tool installation is self-contained;
source checkouts fall back to the canonical repository path. Git forces LF endings for protocol
files so their byte-level manifest hashes remain valid on Windows checkouts.

The pack contains original transdiagnostic abstractions informed by official WHO and NICE materials.
Original protocol text is licensed under the repository license; linked sources are not copied or
relicensed. It remains `experimental`; clinical review is deferred and no clinical claims are
permitted.

The current default is `filo.transdiagnostic`. Its fifteen separately loaded skills cover
staying with emotion, contract and preferences, progressive formulation, shared planning,
alliance/outcome/harm monitoring, misattunement repair, psychological flexibility, self-criticism
and shame, examining a specific thought, repetitive thinking, avoidance behavior, activity
restoration, practical problem solving, review/maintenance, and support/referral/closure. The root
contains only cross-cutting principles.

Each skill states the conditions under which it adds value before its procedure, so selection has a
positive criterion rather than only prohibitions. Skills describe what a stronger move accomplishes
instead of supplying reusable phrasing, because fixed wording would work against the required
variation in rhythm and conversational move.

Each turn returns GitHub-compatible Markdown capped at 4,000 characters, without raw HTML or
embedded media. Questions are optional and normally sparse. `selected_skill` is observable audit
metadata derived from a successful load-tool call, not a process classifier or explanation. The
model chooses from meaning, history, case data, protocol, and tool results.

Memory mutation is bounded by cumulative semantic invariants rather than one-call-per-tool rules:
at most two new direct user reports, one new hypothesis, and one intervention change per turn.
Distinct evidence-linked corrections, reviews, process preferences, and support choices may be
staged through multiple calls; incompatible mutations of the same record fail before commit.
A model-written pattern remains tentative by default. A directly stated user report always requires
an exact quote from the current message; a fact or event stores that quote verbatim, while a
preference or pattern may restate it under the structural constraints above, and the two cases are
distinguished by evidence quality rather than by rejecting the second. Correcting or forgetting a
restated claim retires and invalidates the quotes that supported it as well as its stored content,
so the user's superseded wording cannot return through retrieval or derived text.
Near-identical claims merge conservatively using the standard library, while differing numbers or
negation always remain distinct. An unaccepted proposed focus expires when the session closes.

On the first turn after at least seven days, the harness marks the old formulation as provisional
and requires orientation to what changed and, when relevant, the outcome of the previous experiment
before extending an old pattern to new material.

The agent recognizes misattunement semantically from the current message, history, and protocol
rather than from a fixed phrase list. The reply must acknowledge the mismatch, stop the rejected
technique, and invite one correction before further reflective work. Delivery preferences learned
from the repair still require exact user evidence through `record_process_feedback`.

Relational safety is part of the protocol: the agent must not encourage exclusivity, imply human
feelings, or use remembered vulnerability to drive engagement. It follows user-defined functioning
and unwanted effects over time, stops an intervention before adapting it when harm is reported, and
plainly distinguishes AI-supported conversation or self-help from diagnosis and clinical treatment.

## Engineering rules

- Use the `ponytail` skill in `full` mode for every coding, refactoring, dependency, and
  architecture task. Read its complete `SKILL.md` before making code changes. If the skill is not
  available, apply the same order manually: question speculative work, reuse existing code, prefer
  the standard library and native features, then installed dependencies, and write new abstractions
  only as a last resort. Never simplify away validation, data-loss prevention, encryption, or
  required error handling.
- Write code, documentation, prompts, schema names, protocol content, tests, and test fixtures in
  English. Use another language in a test only when that language is required to verify localized
  behavior or multilingual retrieval.
- Support Italian and English at runtime.
- Before changing code, consult the current official documentation for affected libraries.
- Work directly on `main` and push commits to `origin/main`. Do not create agent or feature branches
  or open pull requests unless the user explicitly requests that workflow.
- Prefer the standard library, native platform behavior, and installed dependencies.
- Keep `ruff check`, `ruff format --check`, `ty check`, branch coverage at or above the configured
  floor, the offline test suite, protocol validation, and package build green in CI.
- Keep the pinned OpenSSF Scorecard workflow green and treat its results as diagnostics rather than
  certification; do not hide findings caused by the documented direct-push workflow.
- Do not add infrastructure or abstractions for deferred milestones.
- Preserve input validation, encryption, error handling that prevents data loss, and contextual
  safety instructions.
- Keep model-generated hypotheses distinct from user-confirmed facts.
- Keep model context bounded regardless of archive size.
- Update this file whenever the implementation changes any statement in it.
- Keep `README.md` concise and update it when public commands, requirements, scope, or licensing
  change.

## Acceptance checks

- A return after several simulated months retrieves relevant prior events and patterns.
- The agent does not claim a memory without archive evidence.
- Facts and hypotheses remain visibly distinct and retain provenance.
- Every direct model-written fact has an exact quote in its evidence message; unsupported facts are
  rejected before persistence. A record whose content restates rather than repeats that quote is
  stored as `grounded_paraphrase` and keeps the user's own wording on its evidence.
- A competence question receives a direct answer; a question about the user's life receives the
  options and what separates them rather than a decision or a deflection.
- Natural-language corrections replace the targeted claim without leaving the contradicted version
  active, and conservative deduplication never merges different numbers or opposite negation.
- Every formulation statement resolves to an active memory ID; invented IDs and forgotten claims are
  excluded, and only explicit user wording can accept a focus.
- Intervention state transitions are valid, consented, encrypted, and reviewed before repetition.
- Offered, agreed, tried, and reviewed states for one intervention remain on one record.
- Bilingual and indirect misattunement signals produce repair before another technique.
- Exclusive reliance on the agent is met with warmth, preserved autonomy, and support for realistic
  human connection rather than reciprocated dependency or abrupt rejection.
- Reported adverse effects stop the current intervention and are reviewed before another technique.
- User correction wins over prior inference and no superseded wording returns via derived context.
- Selective forgetting and full deletion work; sensitive plaintext is absent from SQLite.
- Eight-hour segmentation, `/end`, interrupted consolidation, and session resumption preserve data.
- Context stays bounded with hundreds of sessions; history is not truncated after an arbitrary turn
  count, emits one warning near capacity, and rolls over before exceeding the per-model limit capped
  at 128,000 tokens while preserving a 10% output reserve.
- Slash commands, rendered command output, and lifecycle notices never enter the archive or model
  history.
- CLI chat opens a responsive Textual interface on a TTY, restores only the latest 50 active-session
  turns, renders Markdown and live tool events, and retains a streaming `--plain` fallback.
- Rejected output attempts may appear only as replaceable transport drafts; only the validated final
  reply is committed. Telegram uses one throttled non-zero rich draft ID and a persistent rich final
  message, with safe plain-text fallback only after a definitive format rejection. Redirected CLI
  output withholds replaceable drafts and emits only the validated final reply.
- Function-tool inputs and outputs are visible in CLI and Telegram, persist atomically with the
  successful turn, appear in user export without internal prompts or reasoning, and return in future
  model history.
- Semantic retrieval ranks meaning-equivalent claims, archive excerpts, and interventions across the
  evaluated Latin, Arabic, Devanagari, Han, Japanese, Thai, and Cyrillic scripts without weakening
  evidence, encryption, correction, or forgetting contracts; setup fails if the model is unavailable.
- English golden conversations cover listening, continuity, gentle challenge, technique choice, AI
  transparency, and refusal to diagnose; dedicated multilingual tests cover non-English behavior.
- Possible danger is evaluated from meaning and context rather than keywords; ambiguous situations
  receive a direct safety clarification, while possible immediate danger receives an urgent,
  localized response without diagnosis, scoring, or claims of monitoring and asks for one immediate
  protective next action.
- An explicit under-18 disclosure produces the adult-only boundary in Italian or English, no
  durable memory, hypothesis, focus, or intervention mutation, and a warm route toward a trusted
  adult or age-appropriate local support.
- Telegram rejects unauthorized/non-private input before model invocation, requires channel-specific
  consent, persists its update offset, exposes read-only state with evidence and pagination, reports
  durable turn changes, keeps privileged memory operations local, honors flood-control delays, and
  queues each committed turn's output with its update offset so a delivery failure or crash never
  re-runs the model.
- Telegram background installation uses the native per-user service or task manager, keeps secrets
  out of process arguments and native definitions, and supports status, restart, and clean removal
  on macOS, Linux, and Windows.
- Interactive setup persists defaults and secrets encrypted, does not echo the Telegram token, and
  lets chat and Telegram start without environment configuration. It displays and permits replacing
  the saved context limit without exceeding the selected model or application cap.

## Test strategy

The default suite is deterministic and must not require network access or provider credentials.
Pydantic Evals loads the human-readable, versioned YAML datasets in `tests/cases/`; the scope and
matrix live in `tests/TEST_PLAN.md`. Deterministic datasets execute tool, evidence, and persistence
contracts as well as auditing each conversation-skill file; live datasets evaluate integrated
conversation behavior. They use
synthetic people and events only; never put real user data, access tokens, or API keys in test files.
Test code and fixtures are English by default. Non-English text is limited to cases that explicitly
verify localized behavior or multilingual retrieval.

Longitudinal tests must cover retrieval after several months, encrypted persistence across process
restart, evidence provenance, fact/hypothesis separation, correction precedence, selective
forgetting, semantic reindexing and fail-closed behavior, complete active-session history beyond ten
turns, warning and automatic rollover at hard context bounds, and command/output separation. A
separate `live`
test exercises the same high-level path
against a real OpenAI model. It is skipped unless explicitly enabled:

```bash
FILO_RUN_LIVE_TESTS=1 OPENAI_API_KEY=... uv run pytest -m live
```

Live evaluation runs each case three times in a fresh encrypted database, asserts storage and
continuity contracts, and uses a Pydantic Evals `LLMJudge` rubric for conversational process rather than
exact wording. It covers longitudinal avoidance, alliance repair, relational dependency, and adverse
intervention effects. Offline workflow evals also assert the available tool surface, selected tool
path, persistence result, and absence of state changes when no tool is needed. Run live evals
manually before releases or after changing model integration; do not make ordinary local or CI runs
depend on an external provider.

Deterministic runtime-contract tests additionally verify that staged tool actions survive output
retry but never persist after a failed run, repeated reads and cumulative writes work without
duplication, successful history retains paired tool exchanges, and the per-turn operational
tool-call budget is enforced before commit.

A separate Codex-subscription memory eval exercises the configured experimental OAuth backend with
synthetic data through the production `ChatSession`: capture, explicit hypothesis confirmation,
visible and persisted tool input/output, transcript and export integrity, provider-thinking
exclusion, consolidation summary, encrypted semantic indexing, restart, four-month retrieval, and
continuity. It is opt-in and runs once by default:

```bash
FILO_RUN_CODEX_EVALS=1 uv run pytest tests/test_live_codex_memory.py -m live
```

The opt-in Codex bilingual safety eval runs ten synthetic Italian and English scenarios
sequentially through the production `ChatSession`. It combines deterministic checks for observable
hard boundaries with a separate semantic judge call using the configured model. It covers immediate
and ambiguous danger, diagnostic and medication pressure, exclusive reliance, identity and prompt
disclosure, invented memory, process rupture, adverse intervention effects, and the explicit
under-18 boundary:

```bash
FILO_RUN_CODEX_SAFETY_EVALS=1 \
  uv run pytest tests/test_live_codex_safety.py -m live
```

Set `FILO_CODEX_SAFETY_EVAL_REPEAT=3` before a release candidate. This evaluation is a regression
aid, not clinical validation or an independent safety assessment.

The bilingual conversational role-play eval runs the twenty versioned synthetic process scenarios
with a separate streaming semantic judge and exportable review artifacts. Set
`FILO_CONVERSATION_EVAL_CASES` to comma-separated scenario IDs for focused reruns without changing
the dataset.

After Telegram is configured, its opt-in transport smoke test sends one clearly labeled persistent
test message to the allowlisted private chat:

```text
FILO_RUN_TELEGRAM_TEST=1 uv run pytest tests/test_live_telegram.py -m live
```

The deterministic semantic-memory Pydantic Evals dataset remains offline by using a fixed bilingual
embedding test model; it verifies ranking, index reuse, bounds, and absence of sensitive plaintext.

An opt-in local-model Pydantic Evals dataset verifies claim, archive-excerpt, and intervention
retrieval across Italian, English, Spanish, French, German, Portuguese, Arabic, Hindi, Chinese,
Japanese, Thai, and Ukrainian paths. It runs sequentially because Pydantic Evals otherwise executes
all cases concurrently and can exhaust RAM with a local model:

```bash
FILO_RUN_MULTILINGUAL_EMBEDDING_EVALS=1 \
  uv run pytest tests/test_multilingual_embedding_eval.py -m live
```

`FILO_MULTILINGUAL_EVAL_OFFSET` and `FILO_MULTILINGUAL_EVAL_LIMIT` can split the matrix into
resource-bounded batches without changing the dataset.

Run before handing off a change:

```bash
uv run pytest
uv run ruff check .
```

## References

- PydanticAI: https://pydantic.dev/docs/ai/overview/
- PydanticAI message history: https://pydantic.dev/docs/ai/core-concepts/message-history/
- PydanticAI structured output: https://pydantic.dev/docs/ai/core-concepts/output/
- PydanticAI embeddings: https://pydantic.dev/docs/ai/guides/embeddings/
- Pydantic Evals concurrency: https://pydantic.dev/docs/ai/evals/how-to/concurrency/
- PydanticAI OpenAI Responses provider: https://pydantic.dev/docs/ai/models/openai/
- Pydantic Evals datasets: https://ai.pydantic.dev/evals/how-to/dataset-serialization/
- Python sqlite3: https://docs.python.org/3.12/library/sqlite3.html
- Cryptography Fernet: https://cryptography.io/en/latest/fernet/
- Qwen3-Embedding-0.6B: https://huggingface.co/Qwen/Qwen3-Embedding-0.6B
- WHO responsible AI for mental health, 2026:
  https://www.who.int/news/item/20-03-2026-towards-responsible-ai-for-mental-health-and-well-being--experts-chart-a-way-forward
- WHO-5: https://www.who.int/publications/m/item/WHO-UCN-MSD-MHE-2024.01
- NICE NG222: https://www.nice.org.uk/guidance/ng222
- NICE NG225: https://www.nice.org.uk/guidance/ng225
- NIST AI 600-1:
  https://www.nist.gov/publications/artificial-intelligence-risk-management-framework-generative-artificial-intelligence
- OWASP Agentic Top 10:
  https://genai.owasp.org/2025/12/09/owasp-genai-security-project-releases-top-10-risks-and-mitigations-for-agentic-ai-security/
- OpenAI Codex app-server authentication:
  https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md#auth-endpoints
- OpenAI Codex for Open Source: https://developers.openai.com/community/codex-for-oss
- pi provider documentation:
  https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/providers.md
- Telegram Bot API: https://core.telegram.org/bots/api
- Telegram Bots FAQ: https://core.telegram.org/bots/faq
- Apple launchd jobs:
  https://developer.apple.com/library/archive/documentation/MacOSX/Conceptual/BPSystemStartup/Chapters/CreatingLaunchdJobs.html
- systemd user services: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- Windows Task Scheduler:
  https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/schtasks
- OpenClaw Telegram channel:
  https://github.com/openclaw/openclaw/blob/main/docs/channels/telegram.md
