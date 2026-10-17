# Offline quality instruments, October 2026 (W0.6)

Item W0.6 of `docs/improvement-program-2026-10.md`. No model provider was called and no API key
was used. All data is synthetic: template-generated or hand-written invented people and events.
Measured on 2026-10-16 and 2026-10-17 in a 4-vCPU, 15 GB Linux cloud container, CPU only, with
the pinned `Qwen/Qwen3-Embedding-0.6B` revision `97b0c614` that `filo setup` installs. Commands
are in `tests/TEST_PLAN.md`.

The gate precision corpus was not built; see the deviation in the program.

## Summary

- **Mutation testing.** The quote gates and the supersession code went from 56.3% to 78.5% of
  mutants killed (504 → 703 of 895). Mutants that change behavior rather than only a message went
  from 180 surviving to 9, and those 9 are equivalent or only change a message (listed below).
  The largest gap found: when a forgotten or corrected claim was the basis for an intervention,
  nothing tested how that record was cleaned up (tombstone, stop, unlink, clearing the pending
  intervention). Every mutant there survived.
- **Retrieval.** Raw ranking with the real model is good: for 91% of queries the top round is a
  relevant one, and every relevant claim ranks in the top 3. The 0.40 floor suits claims and
  sessions. It is too strict for rounds, which after W2.5 are the primary memory: it keeps only 60%
  of relevant query–round pairs. In 6 of the 10 cross-lingual cases it drops a memory round that
  ranks first by a wide margin.
- **Scale.** On a three-year archive of 150 sessions and 4,551 rounds, a warm envelope takes about
  0.55 s per message, almost all of it the query embedding. A cold first message after opening the
  store takes about 1 s, the store opens in 2 ms, and the database is 32 MB. Indexing runs at 3.6
  rounds/s on this CPU, so an archive written before round indexing takes about 21 minutes of turns
  to catch up. The envelope stays bounded by the 16,000-character cap, but only because the cap
  trims it. The unbounded envelope grows with the archive (2.5k → 19k → 29k characters), mostly
  from claims that match on a single word, and the trimming drops excerpts first.
- **A product bug, found and fixed.** With the embedding model actually installed, the Telegram
  listener's lazily resolved embedder hit an `assert isinstance(model, EmbeddingModel)` on every
  use, because `Embedder.model` is still the `sentence-transformers:` spec string until first
  use. Every Telegram turn therefore failed as a transient error. CI never saw it because it has
  no model in its cache. Fixed with `infer_embedding_model`, plus a regression test.

## 1. Mutation testing

mutmut 3.8.0, run ad hoc with `uv run --with mutmut`. `uvx` cannot run it, because mutmut must
import the project's own environment. Configuration is `[tool.mutmut]` in `pyproject.toml`; the
lockfile is unchanged. The run is limited to the eight suites that exercise the gates, plus
`tests/test_quote_gates.py`. The rest of the suite reads repository files that mutmut does not
copy into its sandbox.

To make the tool-side checks mutable, each write tool's quote validation in `filo.chat` was moved
out of its `@agent.tool` closure into an undecorated module-level function (`_check_hypothesis`,
`_check_correction`, `_check_review`, `_check_accepted_focus`, `_check_process_feedback`,
`_check_consent`, `_check_support_choice`). Behavior is unchanged and the whole suite passes
unmodified. Turn-cumulative staging stays in the tools.

Scope (895 mutants):
- every tool-side check: `_check_*`, `_quote_in_text`, `_validate_user_reports`;
- the store gates: `quote_keeps_clause_polarity`, `is_grounded_paraphrase`,
  `_has_uniform_polarity`, the negation counters, `_supported_quote`, `_require_*`,
  `_near_duplicate` and `_claims_conflict`;
- supersession: `correct_claim`, `claims_as_of`, `_replace_derived`, `_carries`,
  `_rounds_carrying` and `_invalidate_derived_text`.

| | Killed | Survived | Score | Survivors changing behavior |
|---|---|---|---|---|
| Before | 504 | 391 | 56.3% | 180 |
| After | 703 | 192 | 78.5% | 9 (all equivalent or message-only) |

Of the 192 survivors after the new tests, 183 only change the text of a `ModelRetry` or
`ValueError` message: a literal wrapped in `XX`, or `None`. Message wording guides the model's
retry but is not a gate, so it was not pinned. The other 9:
- 5 replace the `subject` named in a retry message with `None`, so they only change the message.
- 1 changes `ratio() >= 0.9` to `> 0.9` in `_near_duplicate`, which only matters at an exact
  ratio of 0.9.
- 3 weaken `correct_claim`'s deletion of the evidence rounds' index rows. They are equivalent
  because the same rounds are suppressed right after, and suppression deletes their index rows
  too.

Gaps the new tests close, in `tests/test_quote_gates.py`:
- **Tool-side gates were mostly reached only through whole turns,** where the store backstop or a
  sibling check hid a weakened condition. Alias validation was the worst case: every mutant of
  both `_check_aliases` and `_require_quoted_aliases` survived. Turning `or` into `and` in the
  review, focus, consent and merge-target conditions also survived.
- **Polarity counting:** subtracting weak negations from plain ones; counting a single weak
  marker; a quote whose first fragment is empty; the uniform-polarity rule that forbids shortening
  a quote whose clauses disagree in polarity.
- **Near-duplicate and conflict rules:** the negation and name checks that keep "never" apart from
  "usually", and conflicts on numbers or negation alone, which AGENTS.md requires.
- **Supersession:**
  - the relations recorded on correction evidence;
  - longest-wording-first rewriting (the existing test passed only because its quote also sorted
    first alphabetically);
  - a contradiction without a replacement removing the old wording instead of rewriting it to
    itself;
  - the proposed focus rewritten on correction;
  - pending hypotheses cleared;
  - only supporting rounds suppressed, never the correcting one;
  - the three-word threshold for matching rounds by content;
  - the observation and supersession instants in `claims_as_of`;
  - case-insensitive protection of an existing replacement.
- **Forgetting reaching interventions:** the record is tombstoned and stopped, its link to the
  claim is removed, the pending intervention is cleared, and an unrelated record is left alone.

One limit: mutmut mutates function bodies, not module-level constants, so the negation and
clause-boundary regular expressions themselves (`_NEGATION`, `_WEAK_NEGATION`,
`_CLAUSE_BOUNDARY`) are outside this measure. They are covered only by the example-based tests.
The gate precision corpus was meant to measure them.

## 2. Retrieval benchmark

`scripts/benchmark_retrieval.py` builds the labeled set in `tests/cases/retrieval_benchmark.yaml`
in an ephemeral store:
- 16 topic sessions with 31 rounds, summaries, and 5 pattern or preference claims;
- 6 near-miss rounds and 30 filler rounds, in small-talk sessions between the topics;
- 32 queries, two per topic, Italian and English, often in the other language from the memory and
  with little shared vocabulary.

Each query goes through the production `retrieve_case_context`. "Envelope" is what the model would
receive, after the relevance floor and the per-type cut. "Raw" ranks every item by cosine alone.
hit@k means at least one relevant item in the top k. recall@k is the share of a topic's relevant
items in the top k; rounds have two relevant items per topic, so their recall@1 is at most 0.5.

| Type (queries) | Envelope hit@1 | hit@3 | hit@5 | recall@5 | Raw hit@1 | hit@3 | recall@5 |
|---|---|---|---|---|---|---|---|
| Claims (10) | 0.90 | 0.90 | 0.90 | 0.90 | 0.90 | 1.00 | 1.00 |
| Rounds (32) | 0.78 | 0.88 | 0.91 | 0.62 | 0.91 | 0.97 | 0.78 |
| Sessions (32) | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 | 0.97 | 1.00 |

Irrelevant items per query in the envelope: 0.30 claims, 1.06 rounds, 0.56 sessions. Envelope
recall@5 for rounds is 0.70 on topics that also carry a claim, which lifts its evidence rounds,
and 0.59 on topics without one.

Relevance floor, share of relevant pairs kept and irrelevant pairs let through:

| Floor | Claims kept / through | Rounds kept / through | Sessions kept / through |
|---|---|---|---|
| 0.30 | 100% / 45.0% | 90.3% / 21.8% | 100% / 33.2% |
| 0.35 | 90% / 27.5% | 79.0% / 7.3% | 96.9% / 12.8% |
| **0.40** | **90% / 5.0%** | **59.7% / 1.7%** | **96.9% / 1.8%** |
| 0.45 | 80% / 0.0% | 51.6% / 0.6% | 84.4% / 0.4% |
| 0.50 | 60% / 0.0% | 38.7% / 0.2% | 81.2% / 0.1% |

There are 10, 62 and 32 relevant pairs, against 40, 2,082 and 992 irrelevant ones.

`tests/cases/multilingual_semantic_memory.yaml`, replayed as rounds without a claim (one store per
case): the memory round has the highest cosine in all 10 cases, 0.35–0.54 against at most 0.25
for any distractor. It reaches the envelope first in only 4, because 6 score below 0.40 and share
fewer than two tokens with the query. Five of the six are cross-script or cross-language pairs
(FR→DE, DE→PT, HI→EN, TH→EN, UK→EN).

**Reading.** 0.40 is still right for claims and sessions: it lets through under 5% of irrelevant
pairs while keeping 90–97% of relevant ones. For rounds it costs 40% of relevant pairs to save a
few percent of noise, and ranking is not the problem. The envelope recalls fewer rounds than raw
cosine ranks (hit@1 0.78 vs 0.91) because of the floor. A separate round floor near 0.35 would
keep 79% of relevant round pairs at 7% noise; the excerpt cut of five then bounds how much noise
reaches the model. This benchmark is small (32 queries, one embedding model) and is not a
preregistered study. The change is recommended for a measured follow-up, preferably checked
against the W1 memory probes, and was not made here.

## 3. Scale and latency

`scripts/benchmark_scale.py` writes an encrypted on-disk store over three simulated years:
- 150 sessions of about 30 rounds each (4,551 rounds);
- a pattern claim every third session (48 claims; some templated reports merge);
- template text in both languages with invented names, numbers and days.

It indexes the archive through ordinary retrievals, which embed at most 64 new rounds each, as
production does. Then it reopens the store and measures envelope builds: `retrieve_case_context`
plus `_bounded_context_json`, cold for the first message after opening and warm over the next 20.
Model load and first embedding take 7.5 s; a query embedding alone takes 456 ms (median).

SCALE_TABLE

**Reading.**
- **Latency** is dominated by the query embedding. Retrieval itself adds about 100 ms over the
  whole three-year archive, and it barely grows with archive size. The cold first message pays
  about 0.4 s more, to load and decrypt the vector matrix. Opening the store is negligible.
- **Indexing** costs about 0.28 s per round on this CPU. In normal use that is one or two rounds
  per turn. An archive that predates round indexing catches up 64 rounds per turn, about 18 s of
  extra embedding on each of the first ~70 turns. AGENTS.md describes that path; this measures it.
- **Disk:** about 7 KB per round including its encrypted 1,024-dimension vector.
- **Context stays bounded,** but by the 16,000-character cap rather than by the per-type cuts. The
  unbounded envelope reaches 29k characters at 150 sessions. Claims fill it, because a claim
  counts as relevant on a single shared word (rounds and sessions need two), and each claim carries
  its evidence. `_bounded_context_json` then removes items in a fixed order, and
  `relevant_excerpts` comes first, so rounds, the primary memory since W2.5, are the first thing
  dropped. Sessions are absent in this benchmark only because its generated summaries ("Talked
  about work and sleep.") are too generic to pass either floor.

Recommended follow-ups, not made here because they change retrieval behavior and need their own
measurement:
- trim claims before excerpts in `_bounded_context_json`;
- require the same two-token lexical overlap for claims as for rounds and sessions;
- give rounds their own semantic floor, as in section 2.
