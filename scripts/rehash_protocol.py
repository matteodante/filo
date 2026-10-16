"""Maintainer tool: recompute protocol-pack hashes after editing skills or references.

The manifest pins a SHA-256 digest for the root skill, every nested skill, and every reference so
that an unreviewed edit invalidates the pack at load time. Editing protocol text therefore requires
recomputing those digests. This rewrites only the digest values, preserving manifest formatting,
comments, and key order.

This is a maintainer tool, not part of the product CLI: `filo` loads and verifies the pack before
dispatching any command, so it cannot repair a manifest it refuses to load.

    uv run python scripts/rehash_protocol.py protocols/transdiagnostic
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

PATH_LINE = re.compile(r"^\s*(?:-\s+)?path:\s*(\S+)\s*$")
SHA_LINE = re.compile(r"^(\s*sha256:\s*)[0-9a-f]{64}\s*$")
ROOT_SHA_LINE = re.compile(r"^(root_sha256:\s*)[0-9a-f]{64}\s*$")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rehash(pack: Path) -> int:
    """Rewrite stale digests in place and return the number of changed entries."""
    manifest = pack / "manifest.yaml"
    lines = manifest.read_text(encoding="utf-8").splitlines(keepends=True)
    updated: list[str] = []
    pending: Path | None = None
    changed = 0

    for line in lines:
        if root_match := ROOT_SHA_LINE.match(line):
            target = pack / "SKILL.md"
            replacement = f"{root_match[1]}{digest(target)}\n"
        elif sha_match := SHA_LINE.match(line):
            if pending is None:
                # Every digest belongs to the path declared just above it. Rewriting one without
                # that pairing would silently pin the wrong file.
                raise SystemExit("Found a sha256 entry with no preceding path in manifest.yaml")
            replacement = f"{sha_match[1]}{digest(pending)}\n"
            pending = None
        else:
            if path_match := PATH_LINE.match(line):
                pending = pack / path_match[1]
                if not pending.is_file():
                    raise SystemExit(f"Missing protocol file: {path_match[1]}")
            updated.append(line)
            continue
        if replacement != line:
            changed += 1
        updated.append(replacement)

    if changed:
        manifest.write_text("".join(updated), encoding="utf-8")
    return changed


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit(f"usage: {Path(__file__).name} <protocol-pack-directory>")
    pack = Path(argv[0]).resolve()
    if not (pack / "manifest.yaml").is_file():
        raise SystemExit(f"Not a protocol pack: {pack}")
    changed = rehash(pack)
    print(f"{pack.name}: {changed} digest(s) updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
