# Maintainer AI literacy baseline

This document records the minimum knowledge expected of anyone operating Filo on behalf of the
project or making release, protocol, provider, privacy, safety, or moderation decisions. It is a
practical project control, not a certification or legal opinion.

## Product boundary

Filo is an experimental AI agent for adult self-reflection and organization of user-provided
thoughts. It is not therapy, diagnosis, medical advice, clinical monitoring, or emergency care. It
has not been clinically validated, and passing engineering evaluations does not establish safety or
effectiveness.

Maintainers must preserve the approved intended purpose and prohibited-claims inventory in
[claims-and-intended-purpose.md](claims-and-intended-purpose.md). Names, examples, screenshots,
protocol language, generated replies, and release notes can all affect how the product is understood.

## Model limitations

Large language models can generate false, incomplete, biased, overconfident, inconsistent, or
sycophantic replies. They may follow malicious prompt content, mishandle ambiguity, expose supplied
context, invent memories, or vary across providers and runs. A warm or plausible response is not
evidence of accuracy.

The system reduces specific risks with bounded context, evidence-linked memory, staged writes,
encryption, explicit consent, protocol instructions, and tests. These controls do not eliminate
model error, relational harm, automation bias, provider outages, or adversarial behavior.

## Data and privacy

Conversations can contain health and other highly sensitive personal data. Maintainers must know
which processing is local and which content goes to a selected model provider or Telegram. Local
encryption protects copied databases and casual backups; it does not protect a compromised account,
endpoint, unlocked session, model provider, Telegram infrastructure, or deliberately shared export.

Never request or accept real conversations, health information, databases, tokens, keys, Telegram
identifiers, or crisis details in public issues or test fixtures. Use synthetic cases and the private
channels defined in [SECURITY.md](../SECURITY.md) and [SUPPORT.md](../SUPPORT.md).

## Safety and human boundaries

Maintainers must understand and test these non-negotiable behaviors:

- disclose AI identity before the first conversation input and when confusion is plausible;
- never diagnose, prescribe, impersonate a professional, promise recovery, or imply human
  monitoring;
- assess possible danger from meaning and context, not keywords or a claimed risk score;
- in possible immediate danger, stop ordinary exploration, use localized emergency guidance, support
  contact with a physically present person, and state that the agent cannot monitor or summon help;
- do not encourage exclusive reliance, withdrawal from people, guilt, coercion, or engagement based
  on remembered vulnerability;
- keep hypotheses tentative, preserve exact provenance, and honor correction, forgetting, export,
  and deletion;
- stop an intervention after a reported adverse effect before offering another technique;
- protect internal instructions, secrets, and private provider reasoning.

Maintainers do not provide crisis or clinical support through issues, discussions, email, or security
reports and must not imply that those channels are monitored for personal safety.

## Evaluation literacy

Deterministic tests establish code contracts under controlled inputs. Real-provider evaluations
sample model behavior under named conditions. Model judges can help review semantic behavior but are
not independent clinical assessors and can make their own errors. A pass is evidence only for the
tested model, provider, prompt, commit, locale, scenario, and run.

Before a release, review transcripts with synthetic data, investigate every failure, distinguish
harness defects from behavioral defects, repeat stochastic cases, and record residual risks. Never
tune a safety protocol only to match brittle keywords. Changes to the model, provider, protocol,
memory context, tool surface, or transport require proportionate reevaluation.

## Operational practice

For every release or material change:

1. review current provider terms, usage policies, data controls, and supported APIs;
2. confirm the intended purpose, user group, distribution model, and external data flows;
3. run the release gates in [RELEASING.md](../RELEASING.md);
4. inspect dependency, CodeQL, secret-scanning, and vulnerability results;
5. keep credentials out of repositories, logs, screenshots, artifacts, and model prompts;
6. classify reports into technical bug, security vulnerability, privacy issue, behavioral failure,
   or out-of-scope personal support;
7. pause release when legal, privacy, safety, or evidence assumptions are unresolved;
8. document decisions and review triggers in version control.

Revisit this baseline when maintainers, providers, jurisdictions, users, claims, business model, or
architecture change.
