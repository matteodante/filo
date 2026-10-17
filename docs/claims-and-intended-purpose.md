# Claims and intended purpose

This document is the proposed release baseline for the non-commercial public alpha. It records
product intent and claim boundaries; it is not legal advice, regulatory approval, or a medical-device
classification.

## Public claim

> **An open-source AI agent for reflection, not code.**

Supporting line:

> Local-first conversations and user-controlled memory for self-reflection and mental wellbeing.

Mandatory nearby limitation:

> Experimental and not clinically validated. It is not therapy, diagnosis, medical advice, or
> emergency care; no human monitors conversations. AI output can be wrong. Your selected model
> provider and, if enabled, Telegram receive the content needed to provide their services.

## Intended purpose

Filo is intended to support adult self-reflection and organization of user-provided thoughts.
It is not intended for diagnosis, prevention, monitoring, prediction, prognosis, treatment, or
alleviation of any disease, disorder, injury, or disability.

## Verified implementation boundary — 2026-07-24

The root protocol and dynamically loaded skills are separate from a bounded JSON case-data message.
The agent may use no skill, does not use a keyword router, and exposes only observable skill/claim/
intervention metadata—not reasoning. Direct reports and hypotheses have independent origin, fit, and
lifecycle fields with exact evidence. Explicit process preferences, intervention outcomes and
unwanted effects, support choices, conflicts, three memory modes, local retention, and selective
deletion are implementation descriptions, not claims of therapeutic benefit.

Since the rename to Filo, a Therapist data directory is never read or migrated, Filo stores migrate
forward in one verified transaction, and unknown or newer stores are rejected without
modification. The protocol manifest remains `experimental`, has no invented
reviewers, and records source availability verification dated 2026-07-24.

## Initial release assumptions

- the project name is Filo and the publisher is Matteo Dante acting as an individual;
- source and installers are distributed without charge and outside commercial activity;
- there is no donation, sponsorship, paid support, or other monetization program;
- one adult operates one private self-hosted instance;
- there is no hosted service, telemetry, advertising, analytics, paid support, or
  maintainer-controlled inference;
- the user separately selects and contracts with any remote model provider and Telegram;
- the maintainer does not receive application data unless the user deliberately submits it;
- institutional, employer, school, clinician, patient, client, and minor use are outside scope;
- no efficacy, safety, clinical equivalence, compliance, or regulatory-approval claim is made.

## Supported factual claims

Public material may describe behavior verified by code and tests:

- open-source under AGPL-3.0-or-later;
- self-hosted and local-first, with explicit remote-provider and Telegram data flows;
- OpenAI Responses storage disabled by the client, without implying zero provider retention;
- encrypted sensitive SQLite payloads and a separate local key;
- user inspection, correction, forgetting, export, and deletion controls;
- evidence-linked distinction between user-supported claims and agent hypotheses;
- bounded longitudinal context and local semantic retrieval;
- Italian and English conversation through terminal and private Telegram transports;
- experimental protocol and opt-in real-provider evaluations.

## Prohibited claims

Do not describe the project as:

- therapy, psychotherapy, treatment, clinical care, or a substitute for a professional;
- a psychologist, psychotherapist, clinician, medical device, or emergency service;
- able to diagnose, assess risk clinically, prescribe, monitor, keep a user safe, or contact help;
- proven safe, effective, confidential, anonymous, GDPR compliant, medically approved, or validated;
- equivalent or superior to therapy, a clinician, or an evidence-based treatment;
- suitable for minors, patients, institutions, workplaces, schools, or high-acuity care;
- fully local or private when a remote model or Telegram is configured.

Internal implementation terms such as formulation, intervention records, and protocol skills describe
software structures. They are not benefit, efficacy, or clinical-use claims.

## Surfaces covered by this inventory

The claim and limitation must remain consistent in:

- repository description, topics, README, package metadata, release notes, and future website;
- installers, setup, consent, CLI help, Telegram bot description, commands, and privacy view;
- screenshots, demonstrations, examples, issue templates, and contributor documentation;
- protocol documentation and model-facing instructions where they affect visible behavior.

## Release decision and change triggers

For the first public alpha, the maintainer records this non-medical intended-purpose assessment
without making a regulatory-compliance claim or requiring an external opinion. Reassess it, and
obtain specialist review where proportionate, before adding symptom scales, named-condition
pathways, medication advice, clinical dashboards, institutional deployment, efficacy claims,
minors, hosted operation, paid support, donations, sponsorship tied to the product, or maintainer
access to user data.

Wave 5 of the October 2026 improvement program added an optional WHO-5 wellbeing self-check: five
self-reported numbers, shown as a score and a trend without cutoffs, labels, or screening, and never
sent to the model provider. On 2026-10-17 the maintainer decided that it is not a symptom scale
under the trigger above: WHO-5 is a positively worded wellbeing index, and here it is only a
personal record, with no clinical cutoff, screening, diagnostic label, or alert. The matching row
of [dpia-screening.md](dpia-screening.md) was approved at the same time. Showing a cutoff or a
label, prompting action from a score, or adding a symptom questionnaire reopens this assessment.
