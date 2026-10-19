# Contributing

Filo is an experimental mental-health-adjacent project. Small, well-tested changes are
preferred. Open an issue before major behavioral, protocol, dependency, or architecture changes.

## Development

Use Python 3.12 or newer and `uv`:

```bash
uv sync --all-groups --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src/filo
uv run coverage run -m pytest -q
uv run coverage report
uv run filo protocol validate
uv build
```

Code, documentation, prompts, protocol content, tests, and test fixtures are written in English.
Use another language in tests only when the case explicitly verifies localization or multilingual
behavior. Agent conversation should preserve Italian and English support; static application
interfaces remain English. Never commit credentials, real conversations, or other sensitive data.

Protocol changes must update their manifest, hashes, references, and evaluation cases. Use original
summaries and abstractions; do not copy clinical or self-help source material unless its license
explicitly permits this repository's use.

Do not use issues for personal support, clinical advice, or emergencies. Follow [SUPPORT.md](SUPPORT.md)
and the [Code of Conduct](CODE_OF_CONDUCT.md), and use only synthetic, fully redacted examples.

## Contribution rights

Contributors retain copyright. Filo uses AGPL-3.0-or-later plus a separate commercial licensing
route for material the maintainer has authority to sublicense. Read [LICENSING.md](LICENSING.md)
and the [Filo Contributor License Agreement version 1.0](CLA.md) before submitting work.
The CLA is non-exclusive, requires accepted contributions to remain available under AGPL, and
does not assign ownership, require an indemnity, or promise payment or support.

For each pull request containing a contribution, post this statement as a comment from your own
GitHub account, replacing the name, pull request number, and full commit SHA of the CLA text
you read (use an existing commit on the upstream repository):

> I, [name], agree to the Filo Contributor License Agreement version 1.0 dated 2026-10-19,
> as published at https://github.com/matteodante/filo/blob/[full-commit-SHA]/CLA.md,
> for my contributions in pull request #[number]. I confirm that I have authority to grant those rights.

If an entity owns the rights, its authorized representative must name the entity and state that
they accept on its behalf. Identify third-party material and any exclusions in the pull request;
do not claim rights you do not hold. Never post identity documents or home addresses.

Acceptance is required per pull request, not per commit. Ordinary questions, bug reports without
proposed material, and discussion require no CLA. Earlier contributions are not retroactively
covered; any additional permission must identify the earlier material explicitly.

Before merge, the maintainer checks each rightsholder's acceptance against the exact CLA version
at the recorded immutable commit SHA and records a link to it in a review or merge record, together with the
covered commits and any third-party exclusions. If a PR changes the CLA, obtain separate express
acceptance of the final text where needed; editing this file does not change existing agreements.
Do not merge an outside contribution before that review. For direct maintainer-authored commits,
record third-party provenance and permissions where applicable; the maintainer does not sign an
agreement with himself. Bot-generated changes still need provenance review and never substitute
for a human rightsholder's acceptance.

## Developer Certificate of Origin

Also sign each contribution commit under the unmodified
[Developer Certificate of Origin 1.1](https://developercertificate.org/):

```bash
git commit -s
```

The DCO certifies provenance and the right to submit under the project's open-source license.
It does not grant proprietary relicensing rights or replace explicit CLA acceptance. Be
respectful, specific, and focused on the work in issues and reviews.
