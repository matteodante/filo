# English-first language audit

Date: 2026-10-19. Baseline: `ed739f9` plus the English-first ordering changes in this revision.

## Scope and method

Inventoried 239 tracked files and parsed all 65 Python files, including all 14 runtime modules.
Searched runtime code, scripts, protocol packs, configuration, CI, installers, documentation,
and test/evaluation sources for language selection, locale defaults, Italian wording, and
non-English identifiers. Reviewed the matching runtime strings and locale branches. Searches
are an audit aid, not a formal proof that every possible untranslated phrase is absent.

## Findings

| Area | Result |
| --- | --- |
| Code and schemas | Identifiers, comments, docstrings, tool descriptions, and schema explanations are English. Italian quotations in memory-validation explanations illustrate actual linguistic cases. |
| CLI and Telegram defaults | The shared startup path resolves explicit `--locale`, then the saved locale, then `en-US`. The setup prompt defaults to `en-US` when no choice is saved. |
| Static interfaces | Terminal widgets, CLI help, setup labels, errors, Telegram command labels and bot descriptions are English. |
| Localized responses | Italian context notices and crisis text in `chat.py`, and WHO-5 strings in `wellbeing.py`, are selected through locale-specific branches. English is the fallback. |
| Model instructions | The root protocol, all eleven skills, tool contracts, and evaluation instructions are authored in English. Conversation follows the user's language; the Italian-register section is itself written in English. |
| Memory processing | Italian negation and date expressions are language-support data, not the primary language of the code. Removing them would weaken Italian behavior. |
| Tests and benchmarks | Italian and other-language examples exercise localization, multilingual retrieval, safety, and quoted-evidence handling. Test identifiers and explanations remain English. |
| Public material | README, demo, transcript, contribution guidance, release documentation, and package description use English. Italian is documented as an additional option. |
| Historical evidence | Bilingual synthetic transcripts, measurements, and original-source titles are retained as evidence, not translated or rewritten. |

## Corrections

English now appears before Italian in CLI locale choices, the setup language menu, and the
protocol manifest's supported-locale lists. Ordering did not previously change the runtime default,
which was already English. No protocol instructions or questionnaire wording were changed.

The setup regression test covers an unset locale, an explicitly saved English choice, and an
explicitly saved Italian choice, checking both the displayed default and persisted selection.
Contributor guidance now states the English-first rule explicitly.

## Boundary

Italian support is logically scoped to localization, language-aware processing, and dedicated
examples/tests; it is not a separately installed package. The application can answer someone
writing in Italian, and a saved Italian preference remains respected. English-first does not mean
forcing English replies on users who deliberately converse in Italian.
