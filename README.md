# Therapist

**Experimental, self-hosted AI conversation for adult self-reflection.**

Therapist runs in a terminal or one private, allowlisted Telegram chat. It supports Italian and
English, keeps local state encrypted, and separates direct user reports from tentative agent
hypotheses.

> [!WARNING]
> Therapist is an AI, not a psychologist or psychotherapist. It is not therapy, diagnosis, medical
> advice, emergency care, or human monitoring. Its output can be wrong. The protocol is
> experimental and has not been clinically validated.

## Install

macOS or Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/matteodante/therapist/main/install.sh | sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://raw.githubusercontent.com/matteodante/therapist/main/install.ps1 | iex"
```

The tagged-alpha installer needs no administrator privileges, existing Python, or Git checkout. It
installs an isolated Python 3.12 tool, runs guided setup, and finishes with `thera doctor`.

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
semantically calls `load_therapeutic_skill`; no skill is also valid. At most one skill and one
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
thera chat --memory-mode standard
thera chat --memory-mode transcript-only
thera chat --memory-mode ephemeral
thera privacy show
thera privacy set-default ephemeral
```

- `standard`: encrypted transcript/history plus all structured and semantic state.
- `transcript-only`: encrypted transcript/history, with no new claims, formulation mutations,
  intervention records, or support choices.
- `ephemeral`: process memory only; no conversation content is written to disk (Telegram still saves
  its update position).

Changing mode does not delete earlier data. Retention is disabled by default and can be configured
and applied explicitly:

```bash
thera retention show
thera retention set --raw-message-days 90 --session-summary-days none \
  --stale-hypothesis-days 180
thera retention dry-run
thera retention apply
thera delete-session <id>
thera delete-before 2026-01-01
```

Local deletion cannot remove provider, Telegram, plaintext export, backup, or terminal-capture
copies. See [PRIVACY.md](PRIVACY.md).

## Commands

```text
thera setup
thera chat [--plain] [--memory-mode standard|transcript-only|ephemeral]
thera telegram [--memory-mode standard|transcript-only|ephemeral]
thera telegram-service install|status|restart|uninstall
thera auth login|status|logout
thera memory show|case|sessions|interventions
thera memory review <id> fits|partly_fits|does_not_fit|unsure <evidence>
thera memory correct <id> <exact-replacement>
thera memory forget <id>
thera memory model status|verify|install|remove
thera privacy show|set-default
thera retention show|set|dry-run|apply
thera export [--output path]
thera delete-session <id>
thera delete-before <ISO-date>
thera delete-data
thera protocol validate
thera doctor
```

The supported first-alpha provider is a personal ChatGPT Plus/Pro account through the experimental
`codex:` OAuth path. A running chat or Telegram listener renews the ChatGPT token on its own before
it expires or after a rejected request, and picks up `thera auth logout` or a new login without a
restart. Setup also lists the models of an OpenAI-compatible `local:` server when one is
reachable, so conversations can stay on your own hardware; set `THERA_LOCAL_BASE_URL` when that
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
uv run ty check src/therapist
uv run coverage run -m pytest -m "not live"
uv run coverage report
uv run thera protocol validate
uv build
```

Live provider evals are opt-in and use synthetic transcripts only. The evaluation artifacts are
engineering regression aids, not clinical validation. Full project scope and constraints are in
[AGENTS.md](AGENTS.md); release gates are in [RELEASING.md](RELEASING.md).
