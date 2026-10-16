# Unreleased clean-break refactor

This is an engineering change record, not a published release or clinical-validation claim.

- Separated root protocol, verified skill catalog, dynamically loaded skill body, successful
  history, and bounded JSON case data.
- Expanded the experimental pack to fifteen conversational-process skills; status remains
  `experimental`, with no reviewers invented.
- Rewrote every skill in positive form: entry conditions first, then procedure, then the remaining
  prohibitions. Added staying with emotion, self-criticism and shame, examining one thought,
  repetitive thinking, and restoring activity, and gave the root explicit rules for answering
  competence questions, life questions, and direct questions, plus a precedence order for choosing
  among skills.
- Allowed a preference or pattern to shorten its evidence quote by dropping the user's own words in
  their original order, recorded as `grounded_paraphrase`; facts and events still store the quote
  verbatim. No quoted field may be taken out of the clause that qualified it, including intervention
  consent. Correcting or forgetting a restated claim now retires the quotes that supported it.
- Added a maintainer tool for recomputing manifest digests, removed unused per-skill agent interface
  descriptors, and moved background research out of `protocols/` into `docs/research/`.
- Replaced overloaded memory status with independent origin, fit, lifecycle, evidence relation,
  conflict, and staleness semantics.
- Added exact-evidence tools for user reports, hypotheses, corrections, reviews, conversational
  preferences, interventions/outcomes/unwanted effects, focus, and support choices.
- Added structured retrieval for reports, hypotheses, conflicts, sessions, preferences,
  interventions, support choices, and excerpts.
- Replaced the one-call-per-tool restriction with cumulative turn invariants. Repeated reads and
  distinct evidence-supported writes remain bounded, idempotent, and atomically committed.
- Added standard, transcript-only, and ephemeral modes; optional local retention; session/date
  deletion; a 4,000-character reply cap; and observable turn metadata.
- This revision intentionally provides no migration or legacy compatibility. Old stores are
  rejected without modification and require a fresh data directory.
- Updated consent/privacy text, deterministic tests, bilingual role-play specifications, and the
  exportable human-review artifact.

Remaining limitations include no clinical review or validation, no background retention worker, no
provider/Telegram deletion control, and opt-in live provider evaluation. Current results and release
blockers are recorded in `docs/release-readiness-2026-08-04.md`.

## Rename to Filo

Therapist is renamed to **Filo** as a clean break. The old name contradicted the project's own
boundary: Filo is an AI for adult self-reflection, not therapy, and the name now matches that.
Formerly published as Therapist (v0.1–v0.3); the earlier release notes keep the old name because
they record what shipped.

- Distribution `therapist-cli` → `filo-cli`; Python package `therapist` → `filo`; command
  `thera` → `filo`; environment prefix `THERA_` → `FILO_`, including the live-test flags.
- Default data directory `~/.therapist` → `~/.filo`, database `thera.db` → `filo.db`. Filo never
  reads, migrates, or modifies a Therapist directory.
- Native Telegram listener: LaunchAgent `io.github.matteodante.filo.telegram`, systemd user unit
  `filo-telegram.service`, Windows scheduled task `Filo Telegram`, and macOS log
  `filo-telegram.log`.
- Protocol pack `therapist.transdiagnostic` → `filo.transdiagnostic`. The root protocol now names
  the assistant Filo and adds one rule: it never describes itself as a therapist, psychologist, or
  counselor, or as offering therapy, treatment, or psychological support. All other safety and
  boundary rules are unchanged.
- The model tool `load_therapeutic_skill` is now `load_skill`, and the catalog and errors say
  "conversation skill", because tool traces are visible in the CLI and Telegram.
- The terminal consent notice moves to `alpha-4` and the Telegram notice to `alpha-5`, so both ask
  for consent again.
- Repository URLs point to `github.com/matteodante/filo`.
- Schema policy: the Filo store starts fresh with schema marker `filo-1`, and from now on every
  schema change must ship a forward migration. Unknown or future schema versions, including a
  Therapist store renamed to `filo.db`, are rejected before any table is created. A data directory
  that still contains `thera.db` is refused before Filo creates, chmods, or reads anything in it.
- An existing database without the `filo-1` marker is now rejected even when it has a `state`
  table, and a fresh store creates its tables and marker in one transaction.
- Until the first Filo release, the README documents the checkout installation, because the
  bootstrap installers still pin the pre-rename `v0.3.1` tag.
- Removed the outdated TUI screenshot, which still showed the old name and tool set.

### Upgrading an existing Therapist installation

1. Uninstall the old Telegram service with the old binary: `thera telegram-service uninstall`.
2. Remove the old tool: `uv tool uninstall therapist-cli`.
3. Install Filo with the installer for the first release published under the Filo name.
4. Run `filo setup`. ChatGPT login and Telegram configuration start fresh, because nothing is read
   from the old store.
5. The old `~/.therapist` directory is left untouched. Delete it manually once it is no longer
   needed.
6. Optionally rename the bot in `@BotFather`; its token keeps working either way, and its
   privacy-policy URL should point to `https://github.com/matteodante/filo/blob/main/PRIVACY.md`.
