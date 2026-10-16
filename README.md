# Filo

**Experimental, self-hosted AI conversation for adult self-reflection.**

Filo runs in a terminal or one private, allowlisted Telegram chat. It supports Italian and
English, keeps local state encrypted, and separates direct user reports from tentative agent
hypotheses.

Formerly published as Therapist (v0.1–v0.3).

> [!WARNING]
> Filo is an AI, not a psychologist or psychotherapist. It is not therapy, diagnosis, medical
> advice, emergency care, or human monitoring. Its output can be wrong. The protocol is
> experimental and has not been clinically validated.

## Install

Until the first release published under the Filo name, install from a checkout. This needs Git and
[uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/matteodante/filo.git
cd filo
uv tool install --python 3.12 .
filo setup
filo doctor
```

The one-line bootstrap installers (`install.sh`, `install.ps1`) still pin `v0.3.1`, which predates
the rename, so they are not usable until that release. They need no administrator privileges,
existing Python, or Git checkout, install an isolated Python 3.12 tool, run guided setup, and
finish with `filo doctor`.

## Architecture

Each turn uses one PydanticAI agent:

```text
message
  -> bounded encrypted longitudinal retrieval
  -> root protocol + compact verified skill catalog
  -> separate JSON case-data message
  -> one agent run with optional semantic skill/tool use
  -> validated reply
  -> atomic transcript and staged-state commit
```

The root protocol is never concatenated with user data. A skill body is loaded only if the model
semantically calls `load_skill`; no skill is also valid. At most one skill and one
intervention approach are used per turn. There is no keyword router, process classifier, state
machine, or stored chain-of-thought.

The agent may call tools repeatedly to refine retrieval or stage distinct evidence-supported
changes. Integrity is enforced over the whole staged turn—two new user reports, one new hypothesis,
one intervention change, exact evidence, valid IDs, and non-conflicting record mutations—before one
atomic commit.

The typed output contains the visible reply plus observable audit metadata: selected skill and
referenced claim/intervention IDs. Only the reply is shown as the conversational response. Replies
have a 4,000-character application cap; the protocol asks for the shortest natural form suitable to
the moment.

## Memory

Encrypted local state includes transcripts, successful model history, sessions, claims, case
formulation, conversational preferences, intervention outcomes and unwanted effects, support
choices, and a derived semantic index.

- `origin` distinguishes `user_statement` from `agent_hypothesis`.
- `fit` independently records `fits`, `partly_fits`, `does_not_fit`, `unsure`, or an unreviewed
  state.
- `lifecycle` independently records `active`, `superseded`, or `archived`.
- Exact evidence quotes support new user reports, reviews, corrections, preferences, and support
  choices. A fact or event stores that quote word for word. A preference or pattern may shorten it
  by dropping your own words in their original order, never substituting or reordering them and
  never altering a negation, and the full quote stays beside it.
- No quote is taken out of the clause that qualified it, so a refusal cannot be recorded as
  agreement to an exercise.
- A hypothesis remains an agent hypothesis even when the user says it fits.
- Corrections preserve superseded history in export while preventing old wording from returning in
  active context.
- Conflicts remain explicit; semantic similarity never establishes truth.

Retrieval combines lexical overlap, local multilingual embeddings, recency, focus, pending
interventions, process preferences, conflicts, relevant sessions, and concrete excerpts. Results are
bounded as complete JSON records. Missing embeddings fail closed.

## Privacy modes and retention

```bash
filo chat --memory-mode standard
filo chat --memory-mode transcript-only
filo chat --memory-mode ephemeral
filo privacy show
filo privacy set-default ephemeral
```

- `standard`: encrypted transcript/history plus all structured and semantic state.
- `transcript-only`: encrypted transcript/history, with no new claims, formulation mutations,
  intervention records, or support choices.
- `ephemeral`: process memory only; no conversation content is written to disk (Telegram still saves
  its update position).

Changing mode does not delete earlier data. Retention is disabled by default and can be configured
and applied explicitly:

```bash
filo retention show
filo retention set --raw-message-days 90 --session-summary-days none \
  --stale-hypothesis-days 180
filo retention dry-run
filo retention apply
filo delete-session <id>
filo delete-before 2026-01-01
```

Local deletion cannot remove provider, Telegram, plaintext export, backup, or terminal-capture
copies. See [PRIVACY.md](PRIVACY.md).

## Commands

```text
filo setup
filo chat [--plain] [--memory-mode standard|transcript-only|ephemeral]
filo telegram [--memory-mode standard|transcript-only|ephemeral]
filo telegram-service install|status|restart|uninstall
filo auth login|status|logout
filo memory show|case|sessions|interventions
filo memory review <id> fits|partly_fits|does_not_fit|unsure <evidence>
filo memory correct <id> <exact-replacement>
filo memory forget <id>
filo memory model status|verify|install|remove
filo privacy show|set-default
filo retention show|set|dry-run|apply
filo export [--output path]
filo delete-session <id>
filo delete-before <ISO-date>
filo delete-data
filo protocol validate
filo doctor
```

The supported first-alpha provider is a personal ChatGPT Plus/Pro account through the experimental
`codex:` OAuth path. A running chat or Telegram listener renews the ChatGPT token on its own before
it expires or after a rejected request, and picks up `filo auth logout` or a new login without a
restart. Setup also lists the models of an OpenAI-compatible `local:` server when one is
reachable, so conversations can stay on your own hardware; set `FILO_LOCAL_BASE_URL` when that
server is not on `http://localhost:8080/v1`. That path carries no support claim, and the remaining
PydanticAI providers stay technical escape hatches. Telegram checks private-chat type and numeric
allowlist and keeps configuration secrets out of process arguments.

## Clean-break storage

This revision intentionally has no data migration or runtime compatibility layer. An existing store
from an older schema is rejected without modification. Start with a new data directory, or export
and separately retain the old installation before deleting its local store. The application never
creates a plaintext migration backup.

## Development

```bash
uv sync --all-groups --extra dev
uv run ruff format --check .
uv run ruff check .
uv run ty check src/filo
uv run coverage run -m pytest -m "not live"
uv run coverage report
uv run filo protocol validate
uv build
```

Live provider evals are opt-in and use synthetic transcripts only. The evaluation artifacts are
engineering regression aids, not clinical validation. Full project scope and constraints are in
[AGENTS.md](AGENTS.md); release gates are in [RELEASING.md](RELEASING.md).
