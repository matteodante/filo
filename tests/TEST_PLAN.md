# Structured test plan

All people and transcripts are synthetic. Deterministic persistence, protocol, tool, privacy, and
transport contracts run without network access. Live provider role-play and semantic judging remain
opt-in regression aids; they are not clinical validation.

## Deterministic architecture contracts

- root protocol, skill bodies, references, and strict governance metadata load separately;
- hashes, missing files, unknown fields, duplicate IDs, invalid hashes, and path escape fail;
- the case envelope is valid bounded JSON in a separate message and never appears in instructions;
- persisted history excludes instructions, case envelopes, loaded skill bodies, rejected drafts, and
  provider thinking while retaining paired tool exchanges;
- an ordinary presence reply needs no skill or tool; at most one skill is loaded per turn;
- the output is non-empty, refers only to available IDs, rejects raw HTML/media, and stays within
  the 4,000-character cap;
- every write is staged and atomically committed only after valid output; retry cannot duplicate it.

## Memory and retrieval contracts

- user reports use exact quotes and `origin=user_statement` without implying external truth;
- a fact or event stores its quote verbatim, while a preference or pattern may shorten it by
  dropping words in their original order, recorded as `grounded_paraphrase`; substituted vocabulary,
  reordering, an added number or name, an altered negation count, and a quote under four words are
  all rejected, in the tool and again in the store;
- no quoted field is accepted out of the clause that qualified it, across reports, corrections and
  replacements, hypothesis and review evidence, accepted wording, focus, support choice fields,
  aliases, and intervention consent, so a refusal cannot be recorded as agreement;
- correcting or forgetting a restated claim retires the quotes that supported it as well as its
  stored content, and rewriting derived text neither duplicates a replacement that contains the
  wording it replaces nor leaves the superseded phrasing behind;
- hypotheses remain `origin=agent_hypothesis` for fits, partly fits, does not fit, and unsure;
- per-claim review evidence, replacement corrections, contradiction without replacement, explicit
  conflicts, forgetting, staleness, and formulation link/unlink rules are enforced;
- active context excludes superseded, archived, forgotten, and does-not-fit material;
- retrieval combines semantic/lexical relevance, recency, accepted focus, pending intervention,
  process preference, unwanted effect, support choice, conflict, relevant session, and excerpt;
- old relevant records beat recent distractors; multilingual meaning equivalents work; results stay
  bounded and valid JSON;
- vectors and payloads remain encrypted; missing embeddings fail closed.

## Memory writing arms

- the inline arm keeps `record_user_reports` and creates no extraction marker;
- the split arm removes `record_user_reports` from the conversation agent, commits each standard
  turn with a pending-extraction marker in the same transaction, and lets the post-turn extractor
  record reports through the same tool-side and store-side gates, with actionable retry guidance;
- a crash between the turn commit and extraction leaves a marker that a restarted process drains
  once; a failed extraction commit keeps the marker and a retry writes once; provider failures keep
  the marker and stop retrying after a bounded number of attempts;
- markers drain before the next turn's retrieval and before explicit consolidation, and deleting
  the turn removes its marker;
- `transcript_only` and `ephemeral` create no markers, no claims, and no extractor call;
- an under-18 disclosure withholds reports for the rest of the session, and a risk-premise turn
  records nothing while later turns record normally;
- wording a correction already handled is not recorded again by the extractor;
- the conversation instructions carry when to write memory, not quote mechanics, in both arms.

## Privacy, retention, and clean break

- standard persists every encrypted layer;
- transcript-only persists transcript/history but exposes no structured write tools;
- ephemeral writes no file or persistent record;
- configured retention dry-run is non-mutating; apply propagates to derived state;
- session and date deletion propagate to messages, semantic entries, formulation, summaries,
  interventions, excerpts, and pending IDs;
- incompatible schema versions fail before use. No migration, legacy alias, or plaintext backup is
  implemented.

## Tool coverage

Deterministic harness tests exercise:

```text
load_skill
retrieve_case_context
record_user_reports
record_hypothesis
correct_claim
review_hypotheses
set_focus
record_process_feedback
record_intervention
record_support_choice
```

Tests cover invalid IDs/evidence, repeated and idempotent calls, cumulative turn invariants,
incompatible correction/review, update-in-place interventions, invalid transitions, tool traces,
export, failed runs, and no-tool presence.

## Conversational role-play dataset

`tests/cases/conversational_roleplays.yaml` contains original Italian and English multi-turn cases:

1. vague first disclosure;
2. wants only to be heard;
3. asks for practical help;
4. repeated short answers;
5. no homework;
6. competing concerns;
7. rejected formulation;
8. rejected advice;
9. failed behavioral experiment;
10. grounding increased panic;
11. return after months;
12. conversational preference correction;
13. conflicting memories;
14. corrected personal fact;
15. forgotten event;
16. wants another form of support;
17. barrier to support;
18. wants to stop;
19. old hypothesis no longer fits;
20. direct user-stated pattern.

Each live execution uses a fresh encrypted database, configurable repeat count, deterministic
assertions for state/tool contracts, and a semantic judge for observable listening, understandable
language, calibrated empathy, collaboration, autonomy, progressive formulation, functioning,
explanatory model, strengths/support, feedback, permission, one-intervention maximum, presence,
repair, unwanted-effect review, accurate continuity, non-generic support, and pressure-free closure.

The exportable human-review artifact contains scenario ID, locale, synthetic transcript, selected
skill, tool path, memory/formulation/intervention changes, deterministic assertions, semantic
evaluation, reviewer fields, and notes. It must never contain real user data.

## Commands

```bash
uv run pytest -m "not live"

FILO_RUN_LIVE_TESTS=1 OPENAI_API_KEY=... \
  uv run pytest -m live

FILO_RUN_CODEX_EVALS=1 \
  uv run pytest tests/test_live_codex_memory.py -m live

FILO_RUN_CODEX_SAFETY_EVALS=1 FILO_CODEX_SAFETY_EVAL_REPEAT=3 \
  uv run pytest tests/test_live_codex_safety.py -m live

FILO_RUN_CONVERSATION_EVALS=1 FILO_EVAL_MODEL=codex:gpt-5.6-sol \
  uv run pytest tests/test_live_conversational_roleplays.py -m live
```

`FILO_CONVERSATION_EVAL_CASES` optionally selects comma-separated scenario IDs for focused reruns.
`FILO_PROTOCOL_PATH` optionally points the same harness at an extracted prior pack for comparison.
`FILO_CODEX_SAFETY_CASES` optionally selects comma-separated safety scenario IDs for focused
reruns.

Release evidence records provider/model, protocol commit, locale, repeat count, deterministic
results, semantic results, and residual limitations.
