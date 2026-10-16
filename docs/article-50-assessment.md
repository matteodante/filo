# EU AI Act Article 50 assessment record

**Status:** maintainer assessment recorded; monitor the applicable marking deadline
**Date:** 2026-07-24
**Scope:** free, non-commercial, adult-only public alpha published by Matteo Dante

This record applies the European Commission's final Article 50 guidance to the actual alpha. It is
not legal advice or a compliance determination.

## Facts that are already clear

Filo is an AI system designed for a direct two-way exchange with natural persons. It produces
textual conversation through a terminal or Telegram and is distributed under the Filo name.
It uses third-party or local models but adds its own instructions, memory, tools, validation, storage,
and user interface.

The current product already tells a user clearly, before conversation, that:

- Filo is an AI;
- it is not therapy, diagnosis, medical advice, emergency care, or human monitoring;
- output can be wrong;
- the selected remote provider and Telegram receive required content.

This disclosure appears in the README, guided setup, terminal consent, Telegram consent and status
surfaces. It should remain clear, distinguishable, accessible, and present from the start of the
first interaction.

The revised notices also disclose the current memory mode, local retention, local embeddings,
dynamic skill/model input, plaintext exports, and the limits of local deletion. These additions
increase factual transparency; they are not a compliance determination.

## Role and obligation assessment

The Commission defines a provider as a person or body that develops or has an AI system developed
and places it on the EU market or puts it into service under its own name or trademark. For release
planning, Filo conservatively assumes that its publisher may be the provider of the composed
system rather than only a distributor of upstream models.

Article 2(12) does not create a general Article 50 safe harbor for free and open-source AI: its
exclusion expressly does not cover systems that fall under Article 50. Free distribution and an
open-source license therefore do not, by themselves, close this assessment.

If Filo is a provider:

- Article 50(1) interaction disclosure applies unless the AI nature is obvious; relying on that
  exception is unnecessary because explicit disclosure already exists.
- Article 50(2) may require generative text output to carry effective, reliable, robust,
  interoperable, machine-readable marks. Conversational replies are not source code, and no such
  marking is implemented today.

The Commission's final guidance says Article 50 applies from 2 August 2026. Systems placed on the
market before then receive a limited grace period only for Article 50(2) marking, until
2 December 2026. Publication before the application date is recorded as a transition fact, not a
general compliance conclusion.

## Release decision

The current alpha already implements the clear Article 50(1) interaction disclosure and does not
rely on the “obvious” exception. For a source alpha released before the obligations apply, the
unresolved Article 50(2) marking method is tracked against the official transition dates rather than
treated as a reason to withhold the repository or prerelease.

Before a release on or after the applicable deadline, recheck whether Filo is the relevant
provider, whether a transition rule applies to that exact version, and which interoperable marking
method is then required. Implement it where required or stop that release. Record the source, date,
scope, rationale, and next review date.

Any technical implementation must follow the Commission's final guidance and current Code of
Practice, including output-format interoperability and accessibility. Do not invent a custom marker
and call it compliant.

## Primary sources

- [Regulation (EU) 2024/1689, especially Articles 3 and 50](https://eur-lex.europa.eu/eli/reg/2024/1689/oj)
- [European Commission final Article 50 guidelines, 20 July 2026](https://digital-strategy.ec.europa.eu/en/library/guidelines-transparency-obligations-providers-and-deployers-ai-systems)
- [European Commission Article 50 questions and answers](https://digital-strategy.ec.europa.eu/en/faqs/transparency-obligations-under-article-50-ai-act)
- [Code of Practice on Transparency of AI-generated Content](https://digital-strategy.ec.europa.eu/en/policies/code-practice-ai-generated-content)
