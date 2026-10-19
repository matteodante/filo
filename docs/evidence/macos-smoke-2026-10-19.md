# macOS installation and runtime verification

Date: 2026-10-19. Source revision: `734b9a60f688401b820aaba23398015ce6c5be4f`.
Environment: macOS 27.0 (26A428), Apple Silicon arm64, Python 3.12.
The installed application is Filo 0.4.0. This verifies this Mac; it is not a claim that every
macOS version or Intel Mac has been tested.

## Results

| Check | Result |
| --- | --- |
| Offline suite | 495 passed, 5 live tests deselected; 86% combined statement/branch coverage |
| Ruff lint and formatting | Passed |
| Type checking | `ty check src/filo` passed |
| Protocol pack | Hash validation passed; 11 skills loaded |
| POSIX installer | `sh -n install.sh` passed |
| Package build | Wheel and source distribution built successfully |
| Clean wheel installation | Installed into temporary isolated uv tool directories using exported locked constraints, with the documented non-Linux torch exception |
| Installed entry point | Protocol validation and doctor passed outside the source checkout with an empty temporary data path |
| Embedding model | `filo memory model verify`: 12 files verified; local inference returned 1024 dimensions |
| macOS interactive prompt | `prompt_toolkit` accepted piped input inside a fresh `script(1)` pseudoterminal and returned `PROMPT_RESULT=ok` |
| Native terminal UI | Real `FiloApp` rendered the header, Ready status and message input in a macOS PTY at 92×30, then exited cleanly; isolated store and synthetic session stub |
| Real ChatGPT memory evaluation | `test_configured_codex_longitudinal_memory` passed in 97.12 seconds using `codex:gpt-5.6-sol` |
| Telegram credentials | Read-only `getMe` request succeeded and identified a bot; no token or identity retained here |
| macOS background service | Native launchd service installed and running; doctor reported no recorded last failure |
| Local configuration | English (`en-US`) selected; data directory permissions `0700` |

The real-provider evaluation used synthetic messages in a temporary store. It checked archived
transcripts, evidence-linked memory, session consolidation, encrypted storage, semantic retrieval,
and continuity after a simulated four-month gap. It used the configured OAuth credential source
without loading personal conversations into the evaluation.

## Reproduction

```sh
uv run ruff check .
uv run ruff format --check .
uv run ty check src/filo
uv run coverage run -m pytest -q -m 'not live'
uv run coverage report
uv run filo protocol validate
sh -n install.sh
uv build
filo memory model verify
FILO_RUN_CODEX_EVALS=1 uv run pytest \
  tests/test_live_codex_memory.py::test_configured_codex_longitudinal_memory -q
filo doctor
filo telegram-service status
```

The clean-wheel smoke used the constraint export documented in `RELEASING.md`, temporary
`UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`, and an empty temporary data directory. It did not replace
the running Telegram installation. The native UI check used a temporary `FiloApp` subclass
that exited after rendering, attached to a real PTY via Python's `pty` module.

## Limits and follow-up

- Full Telegram message delivery to the iPhone requires the user's confirmation of the requested
  test message and reply. The bot credential and running listener checks alone do not prove that
  entire path; no test messages were sent automatically.
- The existing bilingual safety and conversational role-play suites were not rerun: this was a
  macOS compatibility and installation check, not a fresh behavioral release evaluation.
- Sleep/wake, logout/login, reboot recovery and Intel Macs were not exercised.
- A non-failing asyncio event-loop deprecation warning appeared in Pydantic's graph/evaluation
  dependencies. Embedding initialization also emitted PyTorch's macOS redirect-support notice;
  local inference succeeded.
- No application-code fix was required by these checks.
