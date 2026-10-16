# Third-party licensing and distribution audit — 2026-07-23

## Purpose and limits

This note records what Therapist currently distributes, downloads, or declares as a dependency and
the corresponding first-party licensing evidence. It is a release audit, not legal advice. It does
not replace an exhaustive license scan of every platform-specific transitive wheel selected from
`uv.lock`, and it does not establish that independently written protocol text can never be treated
as an adaptation of a cited source.

Sources were limited to the repository's built artifacts and configuration, upstream license files,
official package/model metadata, and official WHO, NICE, and Python packaging guidance.

## Executive conclusion

- The Therapist **wheel does not contain uv, the Qwen model, Python dependency source or binaries,
  the README screenshot, or WHO/NICE publications**. It contains Therapist's Python modules, the
  default protocol pack, package metadata, Therapist's AGPL license, and the project's third-party
  notice.
- The Therapist **sdist contains repository source and project documentation**, including the
  synthetic TUI screenshot, protocol citations, installers, tests, and `uv.lock`. It does not
  contain uv, model weights, or installed Python dependencies.
- All direct runtime Python dependencies use permissive MIT, Apache-2.0, or
  Apache-2.0-or-BSD-3-Clause licensing. They are referenced through `Requires-Dist` and installed as
  separate distributions rather than copied into Therapist.
- uv 0.11.31 is fetched directly from its immutable upstream GitHub release only when it is absent.
  It is not a Therapist package asset. uv is dual-licensed MIT or Apache-2.0 at the user's option,
  but the official release archives reviewed contain only the executables and no license file.
  Therapist selects the MIT option, verifies the license text from uv's pinned source commit, and
  persists it next to every installer-provided uv binary.
- `Qwen/Qwen3-Embedding-0.6B` is fetched at runtime from Hugging Face at one pinned commit. The
  model repository metadata says Apache-2.0, but that pinned snapshot contains no `LICENSE` file.
  Therapist must not mirror or bundle the model until the release bundle also carries the required
  Apache-2.0 license and any applicable upstream notices.
- WHO and NICE materials are cited by title and hyperlink; their publication files, illustrations,
  recommendation text, and scripts are not distributed. Keep this boundary. NICE expressly says
  that AI use of NICE content is not covered by its UK Open Content Licence, so any substantive
  reuse beyond citation requires a separate permission/licensing decision.
- The screenshot is a project-created PNG of the actual TUI using synthetic conversation text. No
  third-party logo, stock image, model output, or personal data was identified. It is included only
  in the sdist and repository documentation and needs no separate third-party notice on the facts
  reviewed.

## Distribution boundary

The authoritative package declaration is [`pyproject.toml`](../../pyproject.toml). It gives the
wheel only `src/therapist`, force-includes `protocols/transdiagnostic` inside that package, and
declares external libraries through dependency metadata. PyPA specifies that each `Requires-Dist`
entry names another required distribution; it does not mean that the dependency is embedded in the
wheel ([Core Metadata: `Requires-Dist`](https://packaging.python.org/en/latest/specifications/core-metadata/#requires-dist-multiple-use)).

Inspection of the current locally built artifacts found:

| Item | Wheel | sdist | Obtained later |
| --- | --- | --- | --- |
| Therapist Python code | Included | Included | No |
| Default transdiagnostic protocol pack | Included | Included | No |
| Therapist `LICENSE` and third-party notice | Included in `.dist-info/licenses/` | Included at root | No |
| Direct/transitive Python dependencies | Not included; `Requires-Dist` metadata only | Not included; declarations and lock file only | Installed as separate distributions |
| uv executables | Not included | Not included | Installer fetches upstream release when absent |
| Qwen embedding model files | Not included | Not included | `thera setup` fetches pinned Hugging Face revision |
| Synthetic TUI screenshot | Not included | Included | No |
| WHO/NICE publication files | Not included | Not included | Not downloaded by Therapist |
| WHO/NICE titles and links | Protocol citations included | Protocol citations included | No |

The wheel layout also follows PyPA's rule that a declared license file belongs under
`.dist-info/licenses/` ([Wheel specification](https://packaging.python.org/en/latest/specifications/binary-distribution-format/#the-dist-info-licenses-directory)).
The sdist similarly contains Therapist's declared license file as required by the
[source-distribution specification](https://packaging.python.org/en/latest/specifications/source-distribution-format/).

This boundary must be rechecked on every release against freshly built artifacts; source-tree
intent alone is insufficient evidence of archive contents.

## uv bootstrap

[`install.sh`](../../install.sh) and [`install.ps1`](../../install.ps1) pin uv 0.11.31, download the
matching archive and checksum manifest from the official `astral-sh/uv` GitHub release, verify both
levels of SHA-256, and copy the verified executable into the user's local binary directory. The
Therapist repository and Python artifacts do not host that executable.

The uv 0.11.31 source declares a choice of:

- [MIT](https://github.com/astral-sh/uv/blob/0.11.31/LICENSE-MIT); or
- [Apache-2.0](https://github.com/astral-sh/uv/blob/0.11.31/LICENSE-APACHE).

The reviewed official
[uv 0.11.31 release](https://github.com/astral-sh/uv/releases/tag/0.11.31) archives contain only
`uv`/`uvx` (and `uvw` on Windows), not `LICENSE-MIT` or `LICENSE-APACHE`. Therapist selects the MIT
option. Both installers download its text from uv's pinned source commit, verify its
repository-pinned SHA-256, and persist the exact file next to the executable as `uv-LICENSE-MIT`.
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md) carries the same copyright and license
notice in the repository and package metadata. The uv binary itself remains absent from the
Therapist wheel and sdist.

## Qwen embedding model

[`src/therapist/cli.py`](../../src/therapist/cli.py) pins
`Qwen/Qwen3-Embedding-0.6B` to commit
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. `thera setup` downloads that snapshot through
`huggingface_hub`; subsequent conversation-time loads use the local verified revision.

The official model repository and API metadata label the model
[Apache-2.0](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3).
The files listed at the pinned revision include model/configuration/tokenizer files and a model card,
but no `LICENSE` or `NOTICE` file
([official revision API](https://huggingface.co/api/models/Qwen/Qwen3-Embedding-0.6B/revision/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3)).

**Current requirement:** no model license belongs in the Therapist wheel or sdist because neither
contains model files. Keep the exact repository/revision visible and preserve the upstream model
card in the downloaded snapshot.  
**Release caution:** the missing license file in the upstream snapshot makes the metadata label the
only first-party license signal reviewed here. Before mirroring, caching for redistribution,
shipping offline media, or embedding weights in an image/archive, obtain or add the complete
Apache-2.0 license text and determine whether any model-specific notices are required.

## Direct Python dependencies

The exact resolved versions below come from [`uv.lock`](../../uv.lock). The license links point to
the corresponding upstream release where available.

| Direct runtime dependency | Locked version | Upstream license | Distribution finding |
| --- | ---: | --- | --- |
| `cryptography` | 49.0.0 | [Apache-2.0 OR BSD-3-Clause](https://github.com/pyca/cryptography/blob/49.0.0/LICENSE) | Separate installed distribution; not bundled |
| `huggingface-hub` | 1.24.0 | [Apache-2.0](https://github.com/huggingface/huggingface_hub/blob/v1.24.0/LICENSE) | Separate installed distribution; not bundled |
| `pydantic-ai-slim` | 2.15.0 | [MIT](https://github.com/pydantic/pydantic-ai/blob/v2.15.0/LICENSE) | Separate installed distribution; not bundled |
| `PyYAML` | 6.0.3 | [MIT](https://github.com/yaml/pyyaml/blob/6.0.3/LICENSE) | Separate installed distribution; not bundled |
| `questionary` | 2.1.1 | [MIT](https://github.com/tmbo/questionary/blob/2.1.1/LICENSE), with [upstream NOTICE](https://github.com/tmbo/questionary/blob/2.1.1/NOTICE) | Separate installed distribution; not bundled |
| `rich` | 15.0.0 | [MIT](https://github.com/Textualize/rich/blob/v15.0.0/LICENSE) | Separate installed distribution; not bundled |
| `textual` | 8.2.8 | [MIT](https://github.com/Textualize/textual/blob/v8.2.8/LICENSE) | Separate installed distribution; not bundled |

The `pydantic-ai-slim` extras make provider SDKs and the local embedding stack effective runtime
dependencies, but their packages remain separate. Notable resolved components include OpenAI and
Hugging Face libraries under Apache-2.0, Anthropic and Pydantic libraries under MIT, and scientific
packages under permissive licenses. `torch` and `numpy` publish compound license expressions because
their wheels contain separately licensed components; downstream redistributors must use the notice
files from the exact wheels they ship rather than reduce those packages to a single headline
license. The Linux lock can also select NVIDIA CUDA/cuDNN distributions subject to NVIDIA terms,
and `certifi`/`tqdm` introduce MPL-2.0-covered files. These remain separate downloads today but make
a future self-contained or offline bundle a new, platform-specific legal review.

Build and development tools `hatchling`, `pytest`, `ruff`,
[`ty`](https://github.com/astral-sh/ty/blob/0.0.63/LICENSE), and `pydantic-evals` are MIT-licensed.
[Coverage.py](https://github.com/coveragepy/coveragepy/blob/7.15.2/LICENSE.txt) is Apache-2.0 and
carries an upstream notice. These tools are not installed as Therapist runtime dependencies. This
audit did not enumerate all 100-plus platform-conditional transitive entries in `uv.lock`.

**Current requirement:** no dependency license text needs to be duplicated inside Therapist's
wheel while dependencies are installed as separate upstream distributions with their own package
metadata and license files.  
**Prudent notice:** add a generated or reviewed third-party notice/SBOM before distributing a
self-contained application bundle, container, offline installer, or environment archive. Such a
notice must be derived from the exact selected wheels for every supported platform, especially
compiled packages with bundled native components. Do not infer the complete obligation set only
from `uv.lock`.

## WHO and NICE source materials

The default protocol pack distributes independently written project text plus titles, dates, scope
notes, and links recorded in
[`protocols/transdiagnostic/references/sources.md`](../../protocols/transdiagnostic/references/sources.md). The
reviewed wheel and sdist contain no WHO/NICE PDF, image, logo, script, or copied publication file.

WHO's official linking rules permit external sites to link to WHO pages without permission, but
require informational use, no emblem, and no implication of association or endorsement
([WHO Linking Guidelines](https://www.who.int/about/policies/publishing/copyright/linking)).
Therapist links to complete WHO publication landing pages and expressly disclaims validation and
endorsement. On the reviewed facts, a hyperlink and bibliographic citation do not require bundling
the publication's Creative Commons license.

If WHO publication content is later copied or adapted, WHO states that publications since November
2016 are generally CC BY-NC-SA 3.0 IGO, that attribution and ShareAlike conditions apply, that
commercial use requires permission, and that the individual publication notice controls
([WHO Copyright, Licensing and Permissions](https://www.who.int/about/policies/publishing/copyright)).
That content must not be silently relicensed as AGPL project text.

NICE says its UK Open Content Licence is UK-only, international reuse beyond personal research and
study requires a fee and licence, and non-endorsement and source-attribution conditions apply
([NICE reuse framework](https://www.nice.org.uk/reusing-our-content);
[NICE UK Open Content Licence](https://www.nice.org.uk/reusing-our-content/nice-uk-open-content-licence)).
More importantly for this project, NICE says use of AI on NICE content is **not** covered by that
licence and must be licensed through its API
([NICE AI/API application page](https://www.nice.org.uk/reusing-our-content/nice-syndication-api/nice-syndication-api-application-form)).

**Release rule:** retain titles and landing-page links for source transparency, but do not copy
NICE recommendations, substantial algorithms, or WHO scripts/illustrations into prompts, fixtures,
model context, or distributable protocol files without a specific licensing review. Because the
protocol describes original abstractions as “informed by” these sources, qualified review or direct
confirmation from NICE remains prudent before making a stronger claim of substantive NICE reuse.

## Synthetic TUI screenshot

`docs/assets/therapist-tui.png` is a 1800×1184 RGBA PNG
captioned in the [`README`](../../README.md) as an actual Textual-interface capture using synthetic
data. Inspection found no embedded metadata, third-party logo, stock image, or real user content.
It renders project UI, synthetic text, and ordinary terminal glyphs.

> **Later note:** this screenshot was removed in the rename to Filo and survives only in Git
> history; the link was dropped because its target no longer exists.

The screenshot is project content under Therapist's
[`AGPL-3.0-or-later`](../../LICENSE), is present in the sdist, and is absent from the wheel. No
separate third-party notice was identified. Preserve the synthetic-data caption and do not replace
it with a real conversation capture.

## Release actions

1. Keep uv, model weights, Python dependencies, and WHO/NICE publications outside Therapist's wheel
   and sdist.
2. Reinspect the exact wheel and sdist contents after every packaging change.
3. Reverify that the chosen uv license text accompanies installer-provided binaries and remains in
   `THIRD_PARTY_NOTICES.md`.
4. Treat any future container, offline installer, mirrored model, or self-contained binary as a new
   distribution audit requiring exact per-platform license and notice collection.
5. Keep the Qwen repository and commit pin visible; do not mirror it until the Apache license text
   and any upstream notices are resolved.
6. Keep WHO/NICE use at citation/link level. Seek specific permission or qualified advice before
   feeding their content to the model or claiming substantive reuse.
7. Preserve the screenshot's synthetic provenance and absence of personal data.
