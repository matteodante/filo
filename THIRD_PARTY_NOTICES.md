# Third-party notices

Filo's original code, protocol text, documentation, and synthetic product screenshot are
licensed under `AGPL-3.0-or-later`. The items below remain under their owners' terms and are not
relicensed by Filo.

## uv

When `uv` is absent, the bootstrap installers download the official uv 0.11.31 platform archive
directly from its immutable GitHub release. Filo does not include or host the uv executable.
The archive and release checksum manifest are verified before installation.

Filo selects uv's MIT license option, verifies its text from uv's pinned source commit, and
installs the complete verified license next to the downloaded executable as `uv-LICENSE-MIT`.

> MIT License
>
> Copyright (c) 2025 Astral Software Inc.
>
> Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
> associated documentation files (the "Software"), to deal in the Software without restriction,
> including without limitation the rights to use, copy, modify, merge, publish, distribute,
> sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
> furnished to do so, subject to the following conditions:
>
> The above copyright notice and this permission notice shall be included in all copies or
> substantial portions of the Software.
>
> THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
> NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
> NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
> DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT
> OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

Upstream: <https://github.com/astral-sh/uv/tree/0.11.31>

## Local embedding model

Setup downloads `Qwen/Qwen3-Embedding-0.6B` at commit
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. The model is not included in Filo's wheel or
source distribution. Its official repository metadata identifies it as Apache-2.0, but the pinned
snapshot does not contain a `LICENSE` or `NOTICE` file. Do not mirror or redistribute the model as
part of Filo without a separate licensing review.

Upstream: <https://huggingface.co/Qwen/Qwen3-Embedding-0.6B/tree/97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3>

## Python dependencies

Runtime dependencies are declared in `pyproject.toml`, resolved in `uv.lock`, and installed as
separate upstream distributions. They are not vendored into Filo's wheel. Each retains its own
license and notices. A self-contained binary, container, offline environment, or mirrored package
set requires a new notice audit against the exact platform artifacts.

## WHO and NICE references

The protocol pack contains original project text plus bibliographic titles and links to official
WHO and NICE pages. It does not distribute their publications, scripts, images, logos, or copied
recommendation text. Those materials remain subject to the owners' terms. Do not add substantive
WHO or NICE content to prompts or distributable protocol files without a specific licensing review.

The evidence behind this notice is recorded in
[`docs/research/third-party-licensing-2026-07-23.md`](docs/research/third-party-licensing-2026-07-23.md).
