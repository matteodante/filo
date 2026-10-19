# Optional external compliance assessment brief

**Licensing update — 2026-10-19:** Original Filo material remains AGPL-3.0-or-later,
which permits commercial use. [LICENSING.md](../LICENSING.md) now documents a separately negotiated
commercial route and third-party exceptions, including the bundled WHO-5 text. References below to
non-commercial operation describe the original alpha assessment, not an AGPL restriction. They do
not establish clearance for commercial licensing activity or delivery. Reassess the affected
assumptions before undertaking that activity; this update is not a new regulatory conclusion.

**Prepared:** 2026-07-24
**Project:** Filo  
**Publisher:** Matteo Dante, acting as an individual  
**Use when:** the project materially expands beyond the current private self-hosted alpha, or the
maintainer chooses to obtain an external opinion

This brief preserves implementation facts and questions for a future external review. That review is
not a prerequisite for the current free, single-user, self-hosted alpha. This document is not a
self-issued legal opinion, conformity assessment, or compliance claim.

## Proposed release

Filo is free, non-commercial, AGPL-3.0-or-later software for informed adult self-hosters. Its
claim is:

> An open-source AI agent for reflection, not code.

The mandatory limitation says it is experimental and not clinically validated; it is not therapy,
diagnosis, medical advice, emergency care, or human monitoring; output can be wrong; and the selected
provider and Telegram receive required content.

There is no PyPI release, SaaS, account system, paid support, advertising, telemetry, donation,
sponsorship, maintainer-operated inference, institutional deployment, or minor use in scope. Source
and installers are globally accessible through GitHub, so territorial reach requires explicit
analysis even though the intended release is narrow. Free distribution is not assumed to remove EU
AI Act or medical-device obligations; that conclusion is part of the requested review.

## Functional facts

- One adult uses one private local instance through a terminal or allowlisted Telegram bot.
- A generative model holds a natural conversation in Italian or English.
- The system creates an evidence-linked, correctable longitudinal formulation from user text.
- In standard mode it stores complete messages, session summaries, evidence-linked user reports and
  agent hypotheses, explicit conflicts, formulation, conversational preferences, intervention
  lifecycle/outcomes/unwanted effects, and support choices in encrypted SQLite.
- Transcript-only stores encrypted transcript/history without new structured state; ephemeral mode
  writes no conversation content to disk (Telegram still saves its update position). Local
  retention is optional and has no background worker.
- An optional WHO-5 wellbeing self-check records five self-reported numbers when the user sends
  them and shows the score and its trend, without cutoffs, labels, or screening; the scores are
  never sent to the model provider.
- Root protocol instructions, successful history, and a separately labelled bounded JSON case-data
  envelope remain distinct. One verified skill may be loaded dynamically; no skill is valid.
- It performs local semantic retrieval with a pinned Hugging Face embedding model.
- The user can inspect, review, correct, forget, export, selectively delete, and fully delete local
  state. A Therapist data directory is never read or migrated; Filo stores migrate forward in one
  verified transaction, and unknown or newer stores are rejected without modification.
- It gives contextual possible-danger language and resource guidance but does not perform a clinical
  risk assessment, diagnosis, score, alert, human escalation, or emergency monitoring.
- The first alpha support claim uses a personal ChatGPT Plus/Pro account through experimental
  ChatGPT Codex OAuth. Other local and PydanticAI conversation providers remain technical escape
  hatches and are not advertised or release-cleared.
- The supported transports are the local CLI and one allowlisted private Telegram bot. Telegram
  transports message content; the maintainer receives no normal application data.

The canonical product and architecture facts are in [AGENTS.md](../AGENTS.md), the claim boundary in
[claims-and-intended-purpose.md](claims-and-intended-purpose.md), and the flow in
[PRIVACY.md](../PRIVACY.md).

## Written decisions requested

### Medical-device and consumer-product status

1. Under Regulation (EU) 2017/745 and MDCG 2019-11 rev.1, does this exact intended purpose and
   functionality qualify as medical-device software?
2. Which features or public wording would change that result, including mental-health monitoring,
   symptom scales, named conditions, intervention recommendations, or safety claims?
3. What obligations apply to free open-source distribution by an individual if the software remains
   outside the MDR?
4. Which EU and Italian consumer, product-safety, product-liability, accessibility, or professional
   practice rules still apply despite the non-commercial and experimental labels?

### EU AI Act

1. Is Matteo Dante a provider, deployer, distributor, importer, product manufacturer, or another
   actor for this composed open-source system?
2. Does the open-source exception affect any obligation for this release and, if so, exactly which
   conditions and exclusions apply?
3. Does Article 50(2) require machine-readable marking of terminal and Telegram replies, and which
   approved method is adequate?
4. Are the current first-interaction disclosures sufficient for Article 50(1)?
5. Do any prohibited-practice or high-risk classifications apply to the actual functionality or
   reasonably foreseeable use?

### GDPR and electronic communications

1. Map controller, joint-controller, processor, recipient, and independent-controller roles for the
   publisher, self-hoster, model providers, Telegram, GitHub, and Hugging Face.
2. State territorial scope and the limits of the personal-or-household exemption.
3. If the publisher is a controller, identify Article 6 and Article 9 conditions, transparency,
   retention, data-subject, security, breach, processor-contract, and recordkeeping duties.
4. Decide whether the screening in [dpia-screening.md](dpia-screening.md) requires a full DPIA and
   whether prior supervisory consultation is needed.
5. State the required international-transfer and subprocessor review for each supported provider.

### Provider and distribution terms

1. Determine whether personal ChatGPT Codex OAuth may be used by this independent third-party client
   as a conversation provider and which compatibility claim, if any, may be published.
2. Identify notices, attribution, acceptable-use, age, geographic, or data-processing terms that
   must appear in setup or documentation.
3. Confirm whether GitHub source archives and copy-paste installers create additional publisher or
   product obligations.

## Requested deliverables

- a dated medical-device qualification and classification memorandum tied to the exact claim and
  commit;
- a dated AI Act actor/classification and Article 50 decision, including any implementation
  requirement and transition date;
- a GDPR role, legal-basis, Article 9, transfer, provider-contract, and DPIA decision;
- a provider-terms opinion covering the selected personal ChatGPT Codex OAuth configuration;
- a short residual-risk and mandatory-change list suitable for the release record.

Every conclusion should identify assumptions, jurisdiction, source, effective date, reviewer, and
change triggers. A conclusion limited to one provider or deployment mode must not be generalized to
the others.

## Primary legal sources

- [Regulation (EU) 2017/745](https://eur-lex.europa.eu/eli/reg/2017/745/oj)
- [MDCG 2019-11 rev.1: qualification and classification of software](https://health.ec.europa.eu/latest-updates/update-mdcg-2019-11-rev1-qualification-and-classification-software-regulation-eu-2017745-and-2025-06-17_en)
- [Regulation (EU) 2024/1689](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [European Commission final Article 50 guidelines](https://digital-strategy.ec.europa.eu/en/library/guidelines-transparency-obligations-providers-and-deployers-ai-systems)
- [GDPR](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [EDPB Guidelines 07/2020 on controller and processor concepts](https://www.edpb.europa.eu/documents/guideline/guidelines-072020-on-the-concepts-of-controller-and-processor-in-the-gdpr_en)
