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

## Developer Certificate of Origin

Contributions use the [Developer Certificate of Origin 1.1](https://developercertificate.org/)
instead of a contributor license agreement. Sign each commit with:

```bash
git commit -s
```

The sign-off certifies that you have the right to submit the contribution under this project's
AGPL-3.0-or-later license. Be respectful, specific, and focused on the work in issues and reviews.
