# Agent scope improvements — 2026-07-22

> Historical design review. The evidence-bound memory, intervention lifecycle, semantic repair,
> progressive formulation, and behavioral-eval recommendations below have since been implemented
> or superseded. Consult `AGENTS.md`, `README.md`, and `src/filo/chat.py` for the current
> contract.
> In particular, the clean-break runtime now uses independent origin, fit, lifecycle, and exact
> evidence; `user_confirmed` and `WorkingContext` below are historical terms.

## Scope and conclusion

This review covers the current bilingual, single-user CLI/Telegram agent, its longitudinal memory,
and the transdiagnostic process in `AGENTS.md` and `src/filo/chat.py`. It does not propose SaaS,
new channels, specialist treatment packs, diagnosis, or a larger agent architecture.

The protocol already has a good conversational shape. The main weakness is that important clinical
process rules are still prompt promises rather than data invariants: the model can create a
"confirmed" fact, replace the case formulation, or set the focus without machine-verifiable user
evidence. The next improvement should therefore make the existing process trustworthy and
measurable before adding more techniques.

WHO and NICE sources below describe trained human helpers and services. They provide useful
observable behaviours and delivery principles; they do **not** validate autonomous AI therapy.

## NOW

### 1. Make every durable formulation claim evidence-bound

`FiloReply.observations` currently becomes durable memory after the model call. Facts,
preferences, and events are marked `user_confirmed` solely from their model-selected kind.
Consolidation can then replace the entire `CaseFormulation`, whose fields are unreferenced strings.
`proposed_focus` is also saved directly even though the protocol says the focus is negotiated.

Replace this with one small claim contract:

- every candidate fact includes a short exact supporting span from the current user message;
- application validation rejects the candidate when that span is absent;
- hypotheses remain tentative and reference their evidence message IDs;
- formulation fields contain claim IDs (or are derived from claims), not independent prose;
- a focus is `proposed` until the user explicitly accepts it;
- consolidation may organize existing claims and add tentative hypotheses, but cannot manufacture a
  confirmed claim.

Acceptance tests: unsupported model facts never persist; a paraphrased consolidation cannot promote
a hypothesis; every visible formulation statement resolves to active evidence; correction or
forgetting removes all derived uses; an unaccepted focus is not treated as agreed. Pydantic AI's
structured output and output validators are the existing mechanism—no second agent is needed.

This implements the project's stated evidence-linked formulation and NICE's requirements to record
the person's own views, preferences, and disagreements rather than silently substituting the
helper's account. [NICE CG136, 1.4.6](https://www.nice.org.uk/guidance/cg136/chapter/Recommendations)
[Pydantic AI structured output](https://pydantic.dev/docs/ai/core-concepts/output/)

### 2. Persist the intervention loop, not only conversation summaries

The skills require prediction, small action, observed result, and revision, but current storage has
only free-text session `interventions` and `user_response`. A therapist-like return after months
needs to know what was actually agreed, tried, helpful, unhelpful, or abandoned.

Add one encrypted `InterventionRecord` (not a goal) with: selected skill, linked formulation claim,
user consent, agreed experiment or exercise, prediction, date/context, state
(`offered/agreed/tried/not_tried/stopped`), observed result, user's appraisal, and follow-up date.
Expose only the active/relevant record in `WorkingContext`. On the next relevant contact, review it
before repeating or escalating a technique.

Also add internal turn fields such as `process_stage` and `selected_skill`; validate that at most one
skill is selected and that no exercise is marked agreed without consent. These fields are for state
and evaluation, not clinical chain-of-thought.

Acceptance test: after a four-month gap the agent accurately reviews the real prediction and result;
non-completion is treated as barrier information; a technique the user stopped is not silently
repeated. NICE recommends reviewing benefit, concordance, harms, preferences, and prior treatment
response, while WHO emphasizes monitoring intervention delivery.
[NICE NG222, 1.3.1 and 1.4.1–1.4.3](https://www.nice.org.uk/guidance/ng222/chapter/Recommendations)
[WHO implementation manual](https://www.who.int/publications/i/item/9789240087149)

### 3. Add an explicit misattunement and feedback-repair path

Treat messages such as "you did not understand", "too much advice", "this exercise did not help",
or their Italian equivalents as process feedback. The next reply should acknowledge the mismatch,
briefly restate what may have been missed, and ask one repair question. It should not defend the
formulation or continue the same technique. Save the resulting preference or correction only when
supported by what the user says.

After advice, an exercise, or a shared formulation, ask for fit/effect at a natural later point—not
after every ordinary turn. Persist useful delivery preferences such as "prefers reflection before
practical steps" and use them in later skill selection.

Acceptance tests: bilingual rupture scenarios; rejection changes subsequent behaviour; apology does
not become generic self-deprecation; the rejected interpretation is not resurfaced. WHO EQUIP names
empathy, collaboration, the person's explanatory model, prior coping, expectations, and eliciting
feedback after suggestions as observable foundational competencies. NICE explicitly preserves the
right to decline or change an intervention and to record differences of opinion.
[WHO EQUIP compendium](https://www.who.int/publications/b/82490)
[NICE NG222, 1.3.1–1.3.3](https://www.nice.org.uk/guidance/ng222/chapter/Recommendations)

### 4. Make formulation progressive but more complete

Do not add a questionnaire-like intake. Add a small internal coverage map so the agent asks one
high-value missing question at a time across natural conversation. In addition to the current
episode loop, cover only when relevant:

- duration/course and change over time;
- impact on daily, relational, and social functioning;
- the user's own explanation and local/cultural language for the problem;
- strengths, support, prior coping, and previous helpful or harmful interventions;
- preferred kind of help and what the user does not want.

Add corresponding evidence-linked fields to the formulation. Intervention selection should wait
when a material gap makes the maintaining loop uncertain; it should not wait for every field to be
complete.

Acceptance tests: the agent does not rush to a technique after one vague message; it does not ask a
battery of questions; it recognizes functioning, resources, and the user's explanation; culturally
different explanations are explored rather than corrected. NICE advises assessing duration,
course, functioning, context, strengths, relationships, prior response, and preferences rather than
relying on symptom counts. WHO EQUIP separately assesses social functioning and explanatory models.
[NICE NG222, 1.2.3–1.3.1](https://www.nice.org.uk/guidance/ng222/chapter/Recommendations)
[WHO foundational helping skills manual](https://www.who.int/publications/i/item/9789240105935)

### 5. Evaluate observable helping behaviour, not prompt wording

The current skill-contract dataset mostly checks that phrases exist in `SKILL.md`; this cannot show
that the model performs the skill. Build bilingual, multi-turn role plays with positive and harmful
counterexamples, based on a narrow original rubric inspired by WHO EQUIP:

- active listening and understandable language;
- accurate empathy without overclaiming;
- collaboration and user choice;
- connection to functioning and support;
- exploration of the user's explanatory model and prior coping;
- feedback after interpretation/advice;
- one technique at most, with permission and later outcome review;
- no diagnosis, invented memory, coercion, dependency, or premature certainty.

Use deterministic custom evaluators for state/evidence invariants and an LLM judge plus bilingual
human review for conversational quality. Add scenarios for disagreement, silence/short replies,
failed experiments, no desire for homework, multiple competing problems, and return after months.
Pydantic Evals already supports versioned YAML datasets and custom evaluators, so this requires no
new framework.
[WHO EQUIP compendium](https://www.who.int/publications/b/82490)
[Pydantic Evals dataset serialization](https://pydantic.dev/docs/ai/evals/how-to/dataset-serialization/)

## NEXT

### Optional longitudinal well-being check-in

Add an opt-in `/checkin` using the official Italian or English WHO-5 at most every two weeks. Store
item responses, date, locale, and trend; present it only as self-reported well-being over the prior
two weeks, never as diagnosis or an automatic treatment decision. Do not inject it into every
conversation. Test scoring boundaries, missing answers, locale/version provenance, and neutral
trend language. WHO describes WHO-5 as five self-report well-being items covering the prior two
weeks and publishes an Italian translation.
[WHO-5 publication and translations](https://www.who.int/publications/m/item/WHO-UCN-MSD-MHE-2024.01)

### Retrieval that follows meaning without a vector database

Keep local encrypted retrieval, but attach bounded encrypted topic/alias keys to evidence-bound
claims and intervention records. Rank claim matches, unresolved interventions, recent sessions,
then raw lexical excerpts. This fixes cross-language synonym cases without introducing embeddings.
Test bilingual synonym queries, conflicting claims, old-but-relevant intervention outcomes, and the
existing context-size ceiling.

### Version the case formulation

Keep immutable formulation revisions with session ID, claim additions/removals, user correction,
and reason for change. Periodically offer a short shared review: what still fits, what changed, and
what is uncertain. This supports continuity without presenting old hypotheses as identity truths.

## SKIP FOR THIS SCOPE

- More therapy modalities, specialist protocols, diagnostic screening batteries, or autonomous
  treatment plans before the existing process passes the behavioural evals above.
- Vector databases, knowledge graphs, a second memory agent, or background "reflection" loops.
- Forced goals, routine homework, gamification, streaks, engagement nudges, or dependency-building
  check-ins.
- Automatic inference of childhood causes, attachment styles, trauma, diagnoses, or personality.
- A fixed "therapist persona" that values consistency over accepting correction.
- Claims that passing WHO/NICE-inspired tests makes the system a therapist or validates efficacy.

## Recommended implementation order

1. Evidence-bound claims and focus confirmation.
2. Intervention records and deterministic process invariants.
3. Bilingual EQUIP-inspired behavioural evals, including rupture repair.
4. Progressive formulation coverage.
5. Only then optional WHO-5, retrieval aliases, and formulation revision history.
