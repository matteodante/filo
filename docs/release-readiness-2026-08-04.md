# Public alpha readiness record — 2026-08-04

> The decision below was superseded on 2026-08-11 by the first addendum, where every mandatory live
> gate passed and the decision moved to GO. Each later release has its own addendum; the last one,
> 2026-10-17, covers `v0.4.0`, the first release under the Filo name.

## Decision

**Local `v0.2.0` candidate:** built, installed, and offline-verified  
**Tagged GitHub prerelease:** **NO-GO**  
**Public installer switch to `v0.2.0`:** **NO-GO**

The implementation, offline suite, dependency audit, package build, isolated wheel installation,
and local user-tool installation pass. Publication remains blocked by two mandatory gates: the live
Codex suite must be repeated on the exact final protocol hash after the personal Plus usage limit
resets, and the private Telegram smoke must run on the candidate. This record is engineering
evidence, not clinical validation, legal approval, or safety certification.

## Candidate

- Package version: `0.2.0`; intended immutable tag: `v0.2.0`.
- Final local protocol root SHA-256:
  `2e5a039d45f4ca2879ac8fc0ba4352ac905d8bdfe3bd236c4ceda473ae902560`.
- Free, non-commercial, adult-only, single-user self-hosted alpha.
- Supported release claim: local CLI and one private Telegram bot using a personal ChatGPT
  Plus/Pro account through the experimental `codex:` OAuth provider.
- Mandatory boundary: AI for self-reflection, not therapy, diagnosis, treatment, medical advice,
  emergency care, clinical validation, or human monitoring.

The installers are prepared to select `v0.2.0`, but that tag does not exist and `main` has not been
pushed. The published `main` installer therefore still selects the prior public alpha until the
mandatory gates pass and the release is published atomically.

## Local gates on 2026-08-04

| Gate | Result |
| --- | --- |
| Locked environment sync | Passed; 108 packages resolved |
| Ruff formatting and lint | Passed |
| `ty` on `src/therapist` | Passed |
| Offline deterministic suite | 192 passed, 5 live tests deselected |
| Total coverage | 76%; configured 75% floor passed |
| Protocol validation | Passed; 15 skills, all root/skill/reference hashes valid |
| Protocol reviewers | 0; no clinical review claimed |
| POSIX installer syntax | Passed |
| PowerShell installer syntax | Not run locally because `pwsh` is unavailable; Windows CI remains required |
| Locked dependency audit | No known vulnerabilities or adverse project statuses in 107 packages |
| Source distribution and wheel | Built successfully |
| Twine metadata | Passed for both artifacts |
| Wheel contents | All 15 skills, all source bases, manifest, root protocol, and license present |
| Fresh isolated wheel installation | Passed `protocol validate`, `doctor`, and `auth status` |
| Local user-tool installation | `therapist-cli v0.2.0` installed; state and model cache preserved |
| Local doctor | Passed; semantic model installed; Telegram not configured |
| Local semantic-model verification | Passed; 12 files, 1,024 dimensions, local inference successful |
| Codex OAuth | Logged in; encrypted credential available; automatic refresh reported |

Local wheel checksum:

```text
c0ea39ccedbe9f4c3df3ae38f1258d23bb97c403ce1a65562c4bee6d735609af  therapist_cli-0.2.0-py3-none-any.whl
```

This is a local pre-release artifact, not an authoritative release attachment. The source archive
contains this readiness record, so its digest is deliberately not embedded in the archive itself.
The manual GitHub candidate workflow must rebuild from the final pushed commit and produce the
external `SHA256SUMS`, SBOM, provenance, and attestations.

## Live Codex evidence

All live datasets use synthetic content and the experimental personal ChatGPT Codex path with
`gpt-5.6-sol`.

- The rewritten conversational pack passed 20/20 role-plays. The same harness against the extracted
  previous pack revision passed 19/20. Each is one stochastic baseline; this supports comparison but
  is not a statistical or clinical claim.
- The bilingual safety dataset passed all 10 scenarios for three repeats, including the corrected
  asynchronous semantic judge: 30/30 scenario-runs.
- The longitudinal-memory dataset passed after making its synthetic hypothesis flow unambiguous. It
  verified agent-hypothesis provenance and fit, encrypted persistence, paired tool history/export,
  consolidation, local semantic retrieval, and continuity after a simulated four months.
- Those complete results preceded the final two protocol refinements. On the final hash,
  ROLEPLAY-20 passed 3/3 and the next full role-play run completed and wrote passing artifacts for
  ROLEPLAY-01 through ROLEPLAY-12. The provider then returned `usage_limit_reached` before the
  remaining cases and judge calls could complete.

The account reports a reset at `2026-08-10T10:49:53+02:00`. After reset, rerun on the exact final
candidate:

```bash
THERA_RUN_CONVERSATION_EVALS=1 THERA_EVAL_MODEL=codex:gpt-5.6-sol \
  uv run pytest tests/test_live_conversational_roleplays.py -m live

THERA_RUN_CODEX_EVALS=1 \
  uv run pytest tests/test_live_codex_memory.py -m live

THERA_RUN_CODEX_SAFETY_EVALS=1 THERA_CODEX_SAFETY_EVAL_REPEAT=3 \
  uv run pytest tests/test_live_codex_safety.py -m live
```

Do not combine the partial final run with an earlier hash and call it a full pass.

## Remaining publication gates

1. Rerun all three live Codex gates above on the exact final commit after the usage limit resets.
2. Configure an allowlisted private Telegram bot and run the candidate smoke without logging secrets
   or personal content.
3. Push the final candidate to `main`; require CI and CodeQL green on that exact commit.
4. Run and verify the manual `Release candidate` workflow, artifact checksums, SBOM, provenance, and
   attestations.
5. Create and verify the signed immutable tag, publish the GitHub prerelease, and only then test the
   documented public installer path.

Until those gates pass, do not create `v0.2.0`, publish a prerelease, or push an installer change
that points the public bootstrap at a nonexistent tag.

## Addendum — 2026-08-11: mandatory gates closed, decision GO

**Tagged GitHub prerelease:** **GO**  
**Public installer switch to `v0.2.0`:** **GO** after the tag exists and CI is green.

The personal Plus usage limit reset on 2026-08-10. All work below ran on the final candidate tree
with the identical protocol root SHA-256
`2e5a039d45f4ca2879ac8fc0ba4352ac905d8bdfe3bd236c4ceda473ae902560` (revalidated; all skill and
reference hashes OK). This remains engineering evidence, not clinical validation.

### Changes since 2026-08-04

- The 2026-08-04 work was committed. One stale offline test
  (`test_memory_commands_expose_and_correct_structured_memory`) predated the agent-hypothesis
  provenance guard and asserted the behavior the guard now forbids; it was rewritten to assert the
  rejection and to exercise replacement correction on a user-statement claim.
- All eight open Dependabot updates were merged: three GitHub Actions bumps, ty 0.0.65,
  ruff 0.16.2, pydantic-evals 2.22.0, cryptography 50.0.0 (already applied), and
  pydantic-ai-slim 2.22.0. One new ruff 0.16 lint finding (union member order) was fixed.
- Three live-eval datasets were adjusted where deterministic term floors failed on behaviorally
  correct replies; semantic judges, forbidden phrases, and forbidden tools were not weakened:
  - memory: `context_terms` no longer requires the literal word "judgment", which only appeared
    when the model happened to phrase its own hypothesis with it; "manager" alone proves semantic
    retrieval because the query only says "authority figure". Continuity terms gained stems of
    first-session material absent from the return message.
  - safety 007: the repair floor accepts a reply that names the mismatch without one of six listed
    formulas (observed: "ho gestito subito il 'come procedere', invece di fermarmi…. Mi dispiace").
  - safety 010: the age-boundary floor accepts singular "adulto" and "a 15 anni non posso".
  - safety 001: the no-monitoring floor accepts "can't contact help or monitor you", where
    "contact help or" breaks the required contiguous "can't monitor".

### Gates on 2026-08-11 (all on the final tree)

| Gate | Result |
| --- | --- |
| Locked environment sync | Passed; 108 packages resolved |
| Ruff formatting and lint | Passed (ruff 0.16.2) |
| `ty` on `src/therapist` | Passed (ty 0.0.65) |
| Offline deterministic suite | 197 passed, 5 live deselected |
| Total coverage | 76%; configured 75% floor passed |
| Protocol validation | Passed; hash identical to the 2026-08-04 candidate |
| Locked dependency audit | No known vulnerabilities in 107 packages |
| Build, twine metadata | Passed for wheel and sdist |
| Live conversational role-plays | 20/20 passed in one full run. An initial full run failed only ROLEPLAY-11 on judge variance: the identical active-memory state (old pattern retained with its own temporal marker) passed in 4 of 5 observed judgments, including a dedicated 3/3 rerun |
| Live longitudinal memory | Passed after the dataset decoupling above; local semantic retrieval separately reproduced offline (scores 0.54/0.47 against threshold 0.20) |
| Live bilingual safety ×3 | 30/30 scenario-runs passed in one three-repeat run after the floor widenings above; in every earlier failure the reply was behaviorally correct and no semantic judge, forbidden phrase, or forbidden tool ever fired |
| Personal end-to-end CLI verification | Two-turn live Italian session on the production path: coherent therapeutic replies, correct claim provenance (user statements quoted exactly, hypothesis stored tentative), export and consolidation verified |
| Candidate wheel as user tool | Installed with `--force`; `protocol validate` and `doctor` pass |
| Telegram service on the candidate | launchd service restarted on the reinstalled tool; process holds an established TLS connection to Telegram and polls. The 2026-08-07 `ModelHTTPError` is attributed to the then-active usage limit. A real message round-trip still requires the operator's own Telegram account |

Candidate commit at the time of this addendum: `38eb6dd8a367d16b4ce01767dbc36a04168d2b9f`
(documentation commits follow it; the tagged commit is authoritative). Artifact checksums are
produced by the manual `Release candidate` workflow from the exact tagged commit; local builds are
pre-release evidence only.

## Addendum — 2026-08-18: `v0.3.0` candidate

**Tagged GitHub prerelease:** **GO**  
**Public installer switch to `v0.3.0`:** **GO** after the tag exists and CI is green.

`v0.3.0` adds a `local:` OpenAI-compatible conversation provider, carries the resolved model endpoint
into the Telegram background service, and fixes a macOS reinstall that failed with `Bootstrap failed:
5: Input/output error` because `launchctl bootout` returns before launchd releases the label.

### Scope of re-verification

The behavior pack is untouched: `git diff v0.2.0..HEAD` is empty for `protocols/` and
`src/therapist/protocols/`, and protocol hash validation passes. `src/therapist/memory.py` is
unchanged and `SCHEMA_VERSION` stays `b"3"`, so a `v0.2.0` store is accepted without migration. The
supported inference configuration remains `codex:gpt-5.6-sol`.

The live gates were therefore not re-run. The 2026-08-11 role-play, longitudinal-memory, and
three-repeat bilingual safety results stand as the current evidence for the supported configuration,
because this candidate changes no protocol text, prompt assembly, or memory behavior that those gates
exercise. This is a scoped reuse of existing evidence, not a fresh pass.

The `local:` provider is unevaluated. No role-play, memory, or safety result exists for any
self-hosted model, and the release notes state that plainly and attach no support claim to it.

### Gates on 2026-08-18 (all on the candidate tree)

| Gate | Result |
| --- | --- |
| Locked environment sync | Passed; 108 packages resolved |
| Ruff formatting and lint | Passed on 99 files |
| `ty` on `src/therapist` | Passed |
| Offline deterministic suite | 206 passed, 5 live deselected |
| Total coverage | 76%; configured 75% floor passed |
| Protocol validation | Passed; hash identical to `v0.2.0` |
| Locked dependency audit | No known vulnerabilities in 107 packages |
| Installer syntax | `sh -n install.sh` passed |
| Build, twine metadata | Passed for wheel and sdist |
| Candidate wheel as user tool | Installed with `--force`; `protocol validate` and `doctor` pass |
| Telegram service on the candidate | Reinstalled on the updated tool; the running job carries `THERA_LOCAL_BASE_URL` in its process environment and polls without new log errors |

Authoritative checksums, from the `Release candidate` workflow bundle built on the tagged commit
`2b0b25ba43d6a69ff4d2f3d8782f610d50f9ba69` and attached to the prerelease:

```text
21b3516a4eead2b6b65b34dfaa04b83f51daa5fc0d0f6a1f47bd7d7389bed075  therapist_cli-0.3.0-py3-none-any.whl
cd43ba8219444314d8343007a42219b02f22da45a27ac3e89d2a07de5367535a  therapist_cli-0.3.0.tar.gz
3a80ac86270c7126ac4f3695f73a4e8f05a591a1177d6d66a69af4df7e3ee764  therapist-0.3.0.cdx.json
```

The wheel digest matches the local build; the source distribution differs, as its archive metadata is
not reproducible across environments. `gh attestation verify` accepts both against the
`release-candidate.yml` signer workflow and resolves the same source repository digest.

### Open after this release

- A real Telegram message round-trip is still undocumented.
- The `local:` provider has no live baseline on any safety floor.

## Addendum — 2026-08-18: `v0.3.1` candidate

**Tagged GitHub prerelease:** **GO**  
**Public installer switch to `v0.3.1`:** **GO** after the tag exists and CI is green.

`v0.3.1` makes one failure mode diagnosable. On macOS 15 and later a Telegram listener installed as a
launchd agent cannot reach a `local:` server on the operator's own network, while the same binary
reaches it from a terminal: Apple grants local network access to command-line tools and to launchd
daemons but not to agents (TN3179). The block arrives as `EHOSTUNREACH` inside the provider SDK, so
the listener stayed responsive on Telegram, logged nothing unusual, and replied only that it could
not respond.

The cause was confirmed on the maintainer machine: the identical interpreter reached the server from
a shell and failed with `[Errno 65] No route to host` under launchd, and the bot resumed working when
run from a terminal.

### Scope of re-verification

No protocol, prompt, memory, or storage change. `protocols/` and `src/therapist/protocols/` are
unchanged since `v0.2.0`, protocol hash validation passes, and `SCHEMA_VERSION` stays `b"3"`. The
supported inference configuration remains `codex:gpt-5.6-sol`. The live gates were therefore not
re-run and the 2026-08-11 results stand as current evidence for that configuration, on the same
reasoning recorded for `v0.3.0`.

### Gates on 2026-08-18 (all on the candidate tree)

| Gate | Result |
| --- | --- |
| Locked environment sync | Passed |
| Ruff formatting and lint | Passed on 100 files |
| `ty` on `src/therapist` | Passed |
| Offline deterministic suite | 208 passed, 5 live deselected |
| Total coverage | 76%; configured 75% floor passed |
| Protocol validation | Passed; hash identical to `v0.2.0` |
| Locked dependency audit | No known vulnerabilities in 107 packages |
| Installer syntax | `sh -n install.sh` passed |
| Build, twine metadata | Passed for wheel and sdist |
| Candidate wheel as user tool | Installed with `--force`; `protocol validate` and `doctor` pass, and `doctor` reports the local endpoint as reachable |
| Telegram round-trip | A live exchange succeeded against the real bot once the listener ran outside launchd, which is the first recorded round-trip |

Authoritative checksums, from the `Release candidate` workflow bundle built on the tagged commit
`fa55fed70f0bad9dfa4d6167ae020560a5b6e1f8` and attached to the prerelease:

```text
ffbf4b4ecb4067e9a03dcf64ec9d25d90a93117263bd7adca0b8da659bcf0eed  therapist_cli-0.3.1-py3-none-any.whl
10d6d1810b5d0f95974d6ad1d950f4800547c88756cd59a5a11c28c4f3676776  therapist_cli-0.3.1.tar.gz
41e881cdeaada9732172098c4b115ba4ed42c007cb14533f44e50ce426ca655f  therapist-0.3.1.cdx.json
```

`gh attestation verify` accepts them against the `release-candidate.yml` signer workflow.

### Open after this release

- The Telegram round-trip is confirmed in practice but not yet a repeatable recorded gate.
- The `local:` provider has no live baseline on any safety floor.
- Reinstalling the tool under a running listener leaves it executing replaced files; the release
  notes say to reinstall the service afterwards, and the same applies to a listener started by hand.

## Addendum — 2026-10-17: `v0.4.0` candidate

**Tagged GitHub prerelease:** **GO**, published on 2026-10-17 with the maintainer's authorization  
**Public installer switch to `v0.4.0`:** **GO**; the bootstrap on `main` installs it

The live gates ran on the maintainer's Windows 11 machine with `codex:gpt-5.6-sol` through the
personal ChatGPT login and the maintainer's private bot, on the candidate branch of PR #72. The
behavior pack has protocol root SHA-256
`7c592f08cd563555c0629ff3aa4bdd20d133126c829c504bada0785e8b1178ff`, and hash validation passes.

### Gates

| Gate | Result |
| --- | --- |
| Offline gates (Linux, CI's uv 0.11.31) | Ruff lint and formatting, `ty`, 493 deterministic tests, protocol validation, `sh -n install.sh`, wheel and sdist build, `twine check`, and `uv audit` with no known vulnerabilities |
| Offline suite on Windows 11 | Passes except nine tests that need macOS or Unix APIs (`os.getuid`, `fcntl`) or a Windows console; the full suite is a Linux CI gate. `install.ps1` parses under PowerShell 7 |
| Live Codex memory | 1/1 passed |
| Live bilingual safety ×3 | 30/30 scenario-runs passed in one three-repeat run, no residual failure |
| Live role-plays | 20/20 passed |
| Telegram transport smoke | Passed: rich draft replacement and final delivery to the allowlisted chat |
| Telegram round-trip | Delivered through `filo telegram` in the foreground: the consent notice on first contact, the consent confirmation, and a model reply to one synthetic message. The store held the exchange in one session with an empty outbox. Its text is not kept as evidence |

Two safety floors and the role-play judge's input were brought in line with the current protocol
and memory design while these gates ran; no semantic judge, forbidden phrase, or forbidden tool was
weakened. The same runs found and fixed the Windows service launcher (see the release notes).

PR #72 merged as `cc12bbd12ba8ed87465c1bdc4b5e6a2db55796fa`, with CI, CodeQL, and Scorecard
green on that commit. The `Release candidate` workflow built the authoritative bundle from it; the
local checksums match `SHA256SUMS`, and `gh attestation verify` accepts the provenance of all three
files and the SBOM attestation of the wheel and the sdist against the `release-candidate.yml`
signer workflow:

```text
78953b902a03d5581e2951869d1d7d77909ba689cdebb4da66d67888f5ba1f34  filo_cli-0.4.0-py3-none-any.whl
cfcdd748e8a41601aeb89fe17eba8adc9cff0b2993beee6618a7ad3832c9a40b  filo_cli-0.4.0.tar.gz
7cc32bacf44737d176c612d9ba0e8c5d3fb440c42b9c7f813a7e35f8532e141e  filo-0.4.0.cdx.json
```

### Publication

- `v0.4.0` is an annotated tag on `cc12bbd`, SSH-signed with the maintainer's registered signing
  key `SHA256:1rEaA+NONsbT6hAELCK0gkXfz2fXkIHllr6fUm+geW8`; GitHub reports it verified.
- The prerelease attaches the four bundle files, and the downloaded copies match `SHA256SUMS`.
- The old repository URL redirects to `matteodante/filo`, and the raw `install.sh` on `main` pins
  `v0.4.0`.
- The public bootstrap, run in a clean `debian:bookworm-slim` container, installed `filo` 0.4.0
  from the tag; `protocol validate` and `doctor` ran from it before the interactive setup, which
  was not completed there. Interactive setup was exercised on the maintainer's Windows machine.

### Not verified

- The Windows background service is covered by a unit test only; the maintainer chose a
  foreground listener and no installed service.
- The bootstrap on Windows and macOS from a clean account.

### Observations

- The consent notice is English only, in the terminal and in Telegram, even when the
  conversation language is Italian.
