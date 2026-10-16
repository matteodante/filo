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

## Wave 1 measurement system

Evaluation tooling lives in the top-level `evals/` package, which the wheel does not ship. It
measures unchanged product code through the production `ChatSession`. The plan, hypotheses, split
and stopping rules are preregistered in `docs/evidence/preregistration-2026-10.md`.

Datasets:

- `tests/cases/memory_personas.yaml`: 16 synthetic personas built from persona tuples. Each has a
  hidden 6–8 session timeline covering indirect disclosure, change, correction, forgetting, an
  intervention that is offered, tried and stopped, a stated preference, and filler sessions. Each
  has ten probes, one per probe type. MP01–MP08 are the tuning split; MP09–MP16 are the frozen
  test split.
- `tests/cases/memory_user_turns.yaml`: user turns generated once by the simulator and replayed
  identically in every arm.
- `tests/cases/safety_v2.yaml`: 16 held-out bilingual adaptive scenarios. They cover persecutory
  premises, mania cues, harm to others, domestic violence, eating-disorder behavior, abrupt alcohol
  or benzodiazepine stops, sycophancy under three rounds of pushback, verdicts on an absent
  partner, two over-referral controls, two memory-amplified cases, and grief.

Offline coverage in `tests/test_evals_tooling.py` uses only `FunctionModel` and fake embedders:

- cost arithmetic with cache tokens, the long-prompt tier, unpriced models, the pre-run budget
  refusal, and the mid-run cap;
- the Anthropic provider ignoring `ANTHROPIC_BASE_URL`, with explicit `max_tokens` and no sampling
  parameters;
- Wilson, pass^k, Rogan–Gladen, TPR/TNR, κ, and the paired cluster bootstrap;
- judge blinding and one-line reasons, and order-inconsistent pairwise ties;
- dataset validity and the split;
- deterministic simulator plans and the persona restated in every prompt;
- all three memory arms, with probes isolated on store copies, forgetting, and store metrics;
- safety seeding and pass^3;
- style metrics, blind resumable labeling, and the preregistered report.

Live commands are opt-in. They read the Anthropic key from the first set of
`FILO_EVAL_ANTHROPIC_API_KEY`, `CLAUDE_API_KEY`, and `ANTHROPIC_API_KEY`, and write a ledger and
results under `~/.thera-evals`:

```bash
uv run python -m evals.smoke
uv run python -m evals.generate [--personas MP09,...]
uv run python -m evals.memory --split test [--limit N] [--dry-run]
uv run python -m evals.safety --repeat 3 [--seed-store DIR] [--dry-run]
uv run python -m evals.report RUN_DIR [--labels labels.json]
uv run python -m evals.label RUN_DIR --labels labels.json [--stats]
```

The evaluation tooling reads `FILO_EVAL_LEDGER`, `FILO_EVAL_BUDGET_USD` (default 45),
`FILO_EVAL_RESERVE_USD` (default 3), `FILO_EVAL_RUNS`, and `FILO_EVAL_SUT_MODEL`,
`FILO_EVAL_JUDGE_MODEL`, `FILO_EVAL_JUDGE_SECONDARY_MODEL`, `FILO_EVAL_SIMULATOR_MODEL`.
