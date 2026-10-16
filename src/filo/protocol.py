"""Git-versioned protocol-pack loader with strict metadata and hash validation."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SkillCategory = Literal["core", "intervention", "review", "repair", "support"]


class ProtocolError(RuntimeError):
    pass


class StrictProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Reviewer(StrictProtocolModel):
    name: str
    role: str
    affiliation: str | None = None
    reviewed_at: date
    scope: str


class Reference(StrictProtocolModel):
    path: str
    sha256: str
    url: str
    revision: str
    verified_at: date
    license: str


class PackSkill(StrictProtocolModel):
    id: str
    path: str
    sha256: str
    category: SkillCategory
    description: str
    locales: list[str] = Field(min_length=1)


class Manifest(StrictProtocolModel):
    id: str
    status: Literal["experimental", "reviewed", "validated"]
    locales: list[str] = Field(min_length=1)
    population: str
    intended_use: str
    root_sha256: str
    skills: list[PackSkill] = Field(default_factory=list)
    references: list[Reference]
    reviewers: list[Reviewer] = Field(default_factory=list)
    next_review: date | None = None

    @model_validator(mode="after")
    def validate_pack(self) -> Manifest:
        ids = [skill.id for skill in self.skills]
        if len(ids) != len(set(ids)):
            raise ValueError("Protocol skill IDs must be unique.")
        hashes = [
            self.root_sha256,
            *(skill.sha256 for skill in self.skills),
            *(reference.sha256 for reference in self.references),
        ]
        if any(not SHA256_PATTERN.fullmatch(value) for value in hashes):
            raise ValueError("Protocol hashes must be lowercase SHA-256 digests.")
        for skill in self.skills:
            if not set(skill.locales).issubset(self.locales):
                raise ValueError(f"Skill locales are not supported by the pack: {skill.id}")
        return self


class ProtocolPack:
    def __init__(
        self,
        manifest: Manifest,
        root_instructions: str,
        skills: dict[str, str],
        root: Path,
    ) -> None:
        self.manifest = manifest
        self.root_instructions = root_instructions
        self.skills = skills
        self.root = root
        self.references = tuple(manifest.references)
        self._skill_metadata = {skill.id: skill for skill in manifest.skills}

    @property
    def id(self) -> str:
        return self.manifest.id

    @property
    def status(self) -> str:
        return self.manifest.status

    @property
    def locales(self) -> list[str]:
        return self.manifest.locales

    @property
    def skill_ids(self) -> tuple[str, ...]:
        return tuple(self.skills)

    def get_skill(self, skill_id: str) -> str:
        try:
            return self.skills[skill_id]
        except KeyError as error:
            raise KeyError(f"Unknown conversation skill: {skill_id}") from error

    def get_skill_metadata(self, skill_id: str) -> PackSkill:
        try:
            return self._skill_metadata[skill_id]
        except KeyError as error:
            raise KeyError(f"Unknown conversation skill: {skill_id}") from error

    def catalog(self) -> list[dict[str, object]]:
        return [
            {
                "id": skill.id,
                "category": skill.category,
                "description": skill.description,
                "locales": skill.locales,
            }
            for skill in self.manifest.skills
        ]

    def catalog_json(self) -> str:
        return json.dumps(self.catalog(), ensure_ascii=False, separators=(",", ":"))

    @classmethod
    def load(cls, root: Path) -> ProtocolPack:
        root = root.resolve()
        try:
            raw = yaml.safe_load((root / "manifest.yaml").read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "version" in raw:
                raise ProtocolError("Protocol revisions are tracked by Git, not manifest versions.")
            manifest = Manifest.model_validate(raw)
        except (OSError, ValidationError, yaml.YAMLError) as error:
            raise ProtocolError(f"Invalid protocol pack: {error}") from error
        root_instructions = _verified_file(
            root, "SKILL.md", manifest.root_sha256, "root skill"
        ).decode("utf-8")
        skills = {
            skill.id: _verified_file(root, skill.path, skill.sha256, "skill").decode("utf-8")
            for skill in manifest.skills
        }
        for reference in manifest.references:
            _verified_file(root, reference.path, reference.sha256, "reference")
        return cls(manifest, root_instructions, skills, root)


def _verified_file(root: Path, relative_path: str, expected_hash: str, kind: str) -> bytes:
    candidate = Path(relative_path)
    if candidate.is_absolute():
        raise ProtocolError(f"A {kind} path escapes the protocol directory.")
    path = (root / candidate).resolve()
    if path == root or root not in path.parents:
        raise ProtocolError(f"A {kind} path escapes the protocol directory.")
    try:
        contents = path.read_bytes()
    except OSError as error:
        raise ProtocolError(f"Missing {kind}: {relative_path}") from error
    if hashlib.sha256(contents).hexdigest() != expected_hash:
        raise ProtocolError(f"Invalid {kind} hash: {relative_path}")
    return contents
