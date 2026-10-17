# Data protection impact assessment screening

**Status:** maintainer screening completed for the planned public alpha
**Date:** 2026-07-24
**Owner:** Matteo Dante, individual maintainer and publisher

This is an engineering record, not legal advice or a compliance claim. It maps the current design
against official screening criteria and records the proportionate release decision.

## Release facts

- Filo is free, non-commercial, open-source software for one adult self-hoster.
- It supports private self-reflection and organization of user-provided thoughts; it is not intended
  for diagnosis, treatment, monitoring, or emergency use.
- The maintainer provides source and installers but does not host inference, operate user instances,
  receive telemetry, or receive application data during normal use.
- The first alpha support claim uses experimental personal ChatGPT Codex OAuth. Other local and
  PydanticAI conversation providers remain technical escape hatches and are not release-cleared.
- The supported transports are the local CLI and one allowlisted private Telegram bot. Local
  content is retained until the user corrects, forgets, exports, or deletes it.
- Minors, organizational deployment, patients, employees, students, paid operation, donations,
  sponsorship, and SaaS are outside this alpha.

## Processing map

| Stage | Data | Purpose | Location or recipient | Retention and control |
| --- | --- | --- | --- | --- |
| Conversation | Free text that can reveal health, relationships, beliefs, and other highly personal information | Generate a reply and maintain continuity | Local process, then the selected model provider if remote | Standard/transcript-only retain encrypted transcripts; ephemeral does not persist; provider terms apply externally |
| Structured memory | Exact evidence, user reports, agent hypotheses, conflicts, formulation links, process feedback, interventions/outcomes/harms, support choices, and summaries | Longitudinal recall and user correction | Encrypted local SQLite in standard mode only | Configurable local retention or explicit correction, forgetting, selective deletion, or full deletion |
| Wellbeing self-check | Optional WHO-5 answers (five numbers) recorded on the user's request | Let the user follow their own wellbeing over time; no screening, cutoff, or label | Encrypted local SQLite; Telegram if recorded there; never sent to the model provider | Raw-message retention, `filo memory forget`, export, and full deletion; none in transcript-only mode |
| Semantic retrieval | Encrypted derived vectors and bounded candidate messages | Rank relevant local context | Local device | Rebuildable cache; affected rows removed on correction or forgetting |
| Credentials | Provider keys, Codex tokens, Telegram token and allowlisted ID | Authenticate user-selected services | Encrypted local store; service recipient on use | Local until logout or data deletion; provider terms apply |
| Telegram transport | Messages, replies, tool events, and requested memory views | Deliver the private bot conversation | Telegram plus selected model provider | Telegram and provider terms apply; local deletion cannot delete their copies |
| Public support | Synthetic reproduction and repository metadata only | Maintain the project | GitHub | GitHub terms and repository history apply |

The wellbeing self-check row was added on 2026-10-16 with Wave 5 of the improvement program. The
maintainer reviewed it on 2026-10-17 and approved it as written: the five answers are a personal
record the user sends, kept encrypted on the local machine and never sent to the model provider,
so the processing stays within the screening above.

The public channels prohibit real conversations, health information, secrets, identifiers, database
files, and exports. The application has no telemetry, advertising, analytics, automatic crash
upload, maintainer-controlled inference, or central user account.

## Screening criteria

The European Commission states that a DPIA is required when processing is likely to create a high
risk and identifies systematic evaluation, sensitive data at scale, and systematic public
monitoring as examples. The Italian Garante's mandatory list and the EDPB criteria must be evaluated
against the actual controller, deployment, users, scale, and providers.

| Criterion | Preliminary result | Reason |
| --- | --- | --- |
| Evaluation or scoring | Present | The software derives patterns, hypotheses, formulation links, and intervention history from user text, even though it prohibits diagnosis and consequential scores. |
| Automated decisions with legal or similarly significant effect | Not intended | The software does not grant, deny, rank, or decide access to services or rights. |
| Systematic monitoring | Present in the user's private archive; no maintainer monitoring | Longitudinal continuity observes conversation patterns over time. |
| Sensitive or highly personal data | Present and foreseeable | Free text can reveal health and other special-category or intimate information. |
| Large-scale processing | Not established | One local instance serves one adult; aggregate scale and external-provider processing need separate analysis. |
| Matching or combining datasets | Limited | The application combines only the user's own archive and structured memory; selected providers may have separate practices. |
| Vulnerable data subjects | Reasonably foreseeable | Adult-only intent does not remove the possibility of distress, dependency, disability, or other vulnerability. |
| Innovative technology | Present | A generative AI agent builds longitudinal, semantic, evidence-linked memory. |
| Preventing access to a service or contract | Not intended | No eligibility or service-access decision exists. |

Multiple criteria are present in the software's local operation. They do not by themselves establish
publisher-side processing: the maintainer distributes source and installers but does not determine
an individual's conversation content, receive it, host inference, run the Telegram bot, or observe
usage. The normal operator is one adult using their own device and accounts for private activity.

On those facts, this screening does not identify publisher-side processing that makes a full DPIA a
proportionate prerequisite to publishing the source alpha. It also does not make a general finding
for every self-hoster or external provider. Each operator chooses and contracts with those services,
and their legal position depends on actual use.

## Existing controls

- explicit AI identity, adult-only intent, and no clinical, emergency, or human-monitoring claim;
- encrypted sensitive SQLite payloads and credentials with a separate local key;
- no product telemetry or maintainer-operated backend;
- bounded model context and local semantic retrieval;
- exact evidence requirements, tentative hypothesis state, correction, forgetting, export, and
  deletion controls;
- allowlisted private Telegram transport and encrypted update state;
- Telegram disclosure that cloud chats are not end-to-end encrypted and require separate deletion;
- public-report rules that prohibit personal data;
- opt-in remote providers with disclosed external data flow.
- strict separation of protocol instructions from a separately labelled JSON case-data envelope;
- standard, transcript-only, and ephemeral modes, plus disabled-by-default retention periods;
- no plaintext migration backup; a Therapist data directory is never read, Filo stores migrate
  forward in one verified transaction, and unknown or newer stores fail closed.

These controls reduce exposure but do not establish GDPR compliance, anonymity, confidentiality, or
provider deletion.

## Release decision and change triggers

- Publishing this source alpha is not treated as operating a hosted processing service.
- The repository notice documents local and external recipients without claiming that Filo,
  Telegram, or ChatGPT is confidential, anonymous, zero-retention, or GDPR compliant.
- A dedicated privacy email, external GDPR opinion, processor contract, and full DPIA are not gates
  for this release because the publisher does not receive normal application data.
- Users remain responsible for the accounts and providers they choose and for uses outside the
  intended private single-user scope.
- Re-screen before SaaS, telemetry, central accounts, organizational use, minors, clinical claims,
  paid operation, donations, sponsorship, or any maintainer access to user data. If that later
  screening identifies likely high residual risk, complete a DPIA and consult the supervisory
  authority where Article 36 requires it.

## Primary sources

- [GDPR, including Articles 4, 6, 9, 25, 28, 35 and 36](https://eur-lex.europa.eu/eli/reg/2016/679/oj)
- [European Commission: when a DPIA is required](https://commission.europa.eu/law/law-topic/data-protection/information-business-and-organisations/obligations/when-data-protection-impact-assessment-dpia-required_en)
- [Italian Garante: processing subject to mandatory DPIA](https://www.garanteprivacy.it/home/docweb/-/docweb-display/docweb/9058979)
- [EDPB Guidelines 07/2020 on controller and processor concepts](https://www.edpb.europa.eu/documents/guideline/guidelines-072020-on-the-concepts-of-controller-and-processor-in-the-gdpr_en)
