# Releasing

Filo releases are maintainer-controlled snapshots of an experimental, non-commercial,
adult-only project. A release is not clinical validation, regulatory approval, or evidence that the
software is safe or effective for mental-health treatment.

The first release is a controlled public alpha for technical contributors and informed adult
self-hosters. Do not promote it broadly, publish it to PyPI, offer hosted operation, or describe it
as therapy or treatment.
Its supported configuration is the local CLI and one private Telegram bot using a personal ChatGPT
Plus/Pro account through the experimental `codex:` OAuth provider. Other conversation providers are
not part of the first release support claim.

## Release authority

The maintainer named in [GOVERNANCE.md](GOVERNANCE.md) makes the final go/no-go decision, writes the
release notes, signs the tag, publishes the GitHub prerelease, and coordinates withdrawal when
needed. A failed mandatory gate cannot be waived silently; record the owner, reason, scope, and
expiry of any exception in the release notes.

## Mandatory gates

Before creating a tag, link durable evidence for every item below in the release issue or notes:

- the intended purpose, public claim, initial operator, users, jurisdictions, and non-commercial
  distribution assumptions are approved;
- the maintainer has rechecked that the exact intended purpose still excludes diagnosis,
  prevention, monitoring, prediction, prognosis, treatment, and alleviation of disease, and has
  recorded any feature or claim that could change the MDR assessment;
- the privacy notice and DPIA screening match the actual data flow, and there is still no hosted
  service, central account, telemetry, maintainer-operated inference, or normal maintainer access to
  application data;
- the EU AI Act Article 50 interaction disclosure remains present from the first interaction, and
  any marking duty applicable on the release date has been rechecked against current official
  guidance;
- the Code of Conduct and reporting routes accurately state that there is no confidential
  project-specific conduct inbox;
- the candidate was pushed directly to `main` only after local checks, and the resulting required CI
  and CodeQL runs are green;
- private vulnerability reporting, dependency alerts, secret scanning, push protection, and CodeQL
  are enabled;
- the latest OpenSSF Scorecard result has been reviewed as a diagnostic, with accepted
  single-maintainer findings recorded rather than represented as certification;
- current provider terms and safety policies have been reviewed;
- the bilingual safety suite has passed three repeats, with model, provider, protocol commit,
  locale, results, and residual failures recorded;
- deterministic tests, lint, formatting, protocol validation, installer syntax, wheel installation,
  packaging metadata, dependency audit, and the configured real-provider memory evaluation pass;
- protocol/context separation, dynamic one-skill loading, no-skill conversation, epistemic
  provenance, all memory modes, retention, selective deletion, and clean-break schema rejection
  pass deterministic tests;
- public claims, runtime consent, privacy notice, support guidance, examples, and release notes remain
  consistent with [docs/claims-and-intended-purpose.md](docs/claims-and-intended-purpose.md);
- license, protocol-source references, bundled embedding-model license, and generated artifacts have
  been reviewed;
- the package version, tag, installer channel, supported revision, and rollback target are explicit;
- the pinned uv version and checksum-manifest SHA-256 match uv's immutable official release assets.

Stop if the actual release has moved beyond the documented self-hosted scope, if a current legal
requirement is known to be unmet, or if any safety or conduct gate is open. Uncertainty alone is not
an automatic veto for this experimental source alpha, but it must not be converted into a compliance
claim.

Use [docs/compliance-assessment-brief.md](docs/compliance-assessment-brief.md) when an external review
becomes proportionate, [docs/dpia-screening.md](docs/dpia-screening.md) for the data-protection
screening, and [docs/article-50-assessment.md](docs/article-50-assessment.md) for the transparency
record.
Use [docs/release-readiness-2026-08-04.md](docs/release-readiness-2026-08-04.md) as the current
engineering evidence and open-blocker record.

## Accepted alpha governance risks

The individual publisher has explicitly chosen direct pushes to `main`, no required pull-request
review, no second administrator or recovery owner, and no private project-specific conduct inbox for
this phase. These are documented single-maintainer limitations, not waived security, privacy, or
regulatory gates.

Before every direct push, run the proportionate local checks. After the push, inspect the exact
commit's GitHub CI and CodeQL results before tagging. Never describe the release as independently
reviewed, protected by multi-person governance, continuously recoverable, or able to accept
confidential conduct cases. Reassess these constraints before broader promotion or material growth.

## First release under the Filo name

The installers pin a tag, and every tag up to `v0.3.1` ships the Therapist package and the `thera`
command. The first Filo release therefore moves the installer pin and the public install path
together:

0. Rename the GitHub repository from `therapist` to `filo` first, then confirm that
   `https://github.com/matteodante/therapist` redirects and that
   `https://raw.githubusercontent.com/matteodante/filo/main/install.sh` resolves. Until then the
   README checkout command, the policy links, and the installers' source URL all point at a
   repository that does not exist yet. On `main`, the installers fail closed in the meantime:
   their `filo` URL 404s before the rename, and after it the pinned `v0.3.1` tag still ships
   Therapist, which the installer reports instead of installing.
1. Bump the version in `pyproject.toml`, `src/filo/__init__.py`, `install.sh`, and `install.ps1`.
2. Restore the one-line bootstrap commands in the README `Install` section, replacing the checkout
   installation, and drop the matching item from the AGENTS.md open work.
3. After publication, run the bootstrap from `main` on a clean account and confirm it installs
   `filo` before announcing the release.

## Version and tag policy

- Use Semantic Versioning for Git tags and PEP 440-compatible package versions.
- Versions below `1.0.0` are experimental and may change incompatibly.
- Mark every alpha GitHub release as a prerelease.
- The tag, package version, release title, and release notes must identify the same version.
- Create an annotated, cryptographically signed tag from a clean, current `main`.
- Never move or reuse a published tag. Fix forward with a new version.
- Only the latest documented alpha revision is supported unless the release notes say otherwise.
- PyPI publication and additional release channels remain out of scope until explicitly approved.

## Verification

Run from a clean checkout of the exact candidate commit:

```bash
uv sync --locked --all-groups --extra dev
uv run ruff format --check .
uv run ruff check .
uv run ty check src/filo
uv run coverage run -m pytest -m "not live"
uv run coverage report
uv run filo protocol validate
sh -n install.sh
pwsh -NoProfile -Command '$null = [scriptblock]::Create((Get-Content -Raw install.ps1))'
uv build
uvx twine check dist/*
uv audit --locked
```

Run the configured real-provider gates separately:

```bash
FILO_RUN_CONVERSATION_EVALS=1 FILO_EVAL_MODEL=codex:gpt-5.6-sol \
  uv run pytest tests/test_live_conversational_roleplays.py -m live

FILO_RUN_CODEX_EVALS=1 \
  uv run pytest tests/test_live_codex_memory.py -m live

FILO_RUN_CODEX_SAFETY_EVALS=1 FILO_CODEX_SAFETY_EVAL_REPEAT=3 \
  uv run pytest tests/test_live_codex_safety.py -m live
```

Run any provider-specific and Telegram smoke tests for configurations claimed in the release. Never
put credentials, real conversations, health information, or other personal data in logs or release
evidence.

Build artifacts once from the candidate commit. Record their SHA-256 checksums and verify the wheel
in a fresh isolated tool environment:

```bash
shasum -a 256 dist/*
uv export --frozen --no-dev --no-emit-project --no-hashes |
  grep -v "^torch==.*sys_platform != 'linux'" >/tmp/filo-constraints.txt
# On Linux, also pass --torch-backend cpu: a wheel carries no [tool.uv.sources].
uv tool install --force --python 3.12 --constraints /tmp/filo-constraints.txt dist/*.whl
"$(uv tool dir --bin)/filo" protocol validate
"$(uv tool dir --bin)/filo" doctor
```

The manual `Release candidate` workflow repeats the offline gates from the exact selected `main`
commit, builds the wheel and source distribution, exports a CycloneDX runtime SBOM from the locked
dependencies, creates `SHA256SUMS`, generates GitHub/Sigstore provenance and SBOM attestations, and
retains the bundle for 14 days. It does not create a tag, release, or PyPI publication:

```bash
gh workflow run release-candidate.yml --ref main
gh run watch
gh run download <run-id>
gh attestation verify <artifact> \
  --repo matteodante/filo \
  --signer-workflow matteodante/filo/.github/workflows/release-candidate.yml
```

Treat the workflow artifact as the authoritative candidate bundle. The SBOM captures the locked
runtime dependency set for the candidate; review it together with the wheel metadata, dependency
audit, and third-party notices.

## Release notes

Human-written notes must include:

1. the public claim and mandatory limitation;
2. the exact intended audience and excluded uses;
3. important changes, including the explicit absence of migration and the fresh-data-directory
   requirement for this clean-break schema;
4. local and external data flows;
5. models/providers and locales exercised by live evaluations;
6. known limitations, residual safety failures, and upstream warnings;
7. supported platforms and revision;
8. artifact checksums;
9. security-report and support links;
10. rollback or withdrawal instructions.

Do not use generated release notes without human claim, privacy, safety, and licensing review.

## Publication and rollback

1. Confirm the candidate commit and all required GitHub checks are green.
2. Create and verify the signed tag.
3. Publish a GitHub prerelease with source archives, built artifacts, checksums, and human notes.
4. Test the documented installation path from the published release, not from a developer checkout.
5. Announce only to the controlled alpha audience and monitor technical, security, privacy, and
   behavioral reports on a best-effort basis.

For a material defect, mark the release as withdrawn, edit the notes with a prominent warning, stop
promoting the affected installation path, and publish a fixed version. Do not delete evidence or
silently replace artifacts. For a vulnerability, follow [SECURITY.md](SECURITY.md) and coordinate
disclosure before publishing sensitive details.
