import hashlib
import json
from pathlib import Path

import pytest
import yaml

from filo.cli import main
from filo.protocol import ProtocolError, ProtocolPack

ROOT = Path("protocols/transdiagnostic")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _valid_pack(tmp_path: Path) -> Path:
    (tmp_path / "skills" / "listen").mkdir(parents=True, exist_ok=True)
    (tmp_path / "references").mkdir(exist_ok=True)
    (tmp_path / "SKILL.md").write_text("Root only", encoding="utf-8")
    skill = tmp_path / "skills" / "listen" / "SKILL.md"
    skill.write_text("Skill body", encoding="utf-8")
    reference = tmp_path / "references" / "source.md"
    reference.write_text("Source notes", encoding="utf-8")
    manifest = {
        "id": "test.pack",
        "status": "experimental",
        "locales": ["en-US"],
        "population": "Adults",
        "intended_use": "Testing",
        "root_sha256": _sha(tmp_path / "SKILL.md"),
        "skills": [
            {
                "id": "listen",
                "path": "skills/listen/SKILL.md",
                "sha256": _sha(skill),
                "category": "core",
                "description": "Listen carefully",
                "locales": ["en-US"],
            }
        ],
        "references": [
            {
                "path": "references/source.md",
                "sha256": _sha(reference),
                "url": "https://example.test",
                "revision": "current",
                "license": "external",
                "verified_at": "2026-07-24",
            }
        ],
        "reviewers": [],
        "next_review": None,
    }
    (tmp_path / "manifest.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return tmp_path


def test_root_and_skills_are_loaded_separately() -> None:
    pack = ProtocolPack.load(ROOT)
    assert pack.status == "experimental"
    assert len(pack.skill_ids) == 11
    assert "Skill catalog" not in pack.root_instructions
    assert all(body not in pack.root_instructions for body in pack.skills.values())


def test_skill_lookup_and_metadata_are_available() -> None:
    pack = ProtocolPack.load(ROOT)
    body = pack.get_skill("repair-misattunement")
    metadata = pack.get_skill_metadata("repair-misattunement")
    assert "mismatch" in body.casefold()
    assert metadata.category == "repair"
    assert set(metadata.locales) == {"it-IT", "en-US"}


def test_missing_skill_lookup_fails() -> None:
    with pytest.raises(KeyError):
        ProtocolPack.load(ROOT).get_skill("missing")


def test_manifest_governance_metadata_is_exposed() -> None:
    pack = ProtocolPack.load(ROOT)
    assert pack.manifest.population
    assert pack.manifest.reviewers == []
    assert pack.manifest.next_review is None
    assert pack.references


def test_catalog_is_valid_json_without_skill_bodies() -> None:
    pack = ProtocolPack.load(ROOT)
    catalog = json.loads(pack.catalog_json())
    assert [item["id"] for item in catalog] == list(pack.skill_ids)
    assert all(set(item) == {"id", "category", "description", "locales"} for item in catalog)
    assert "Recognize the specific mismatch" not in pack.catalog_json()


def test_unknown_manifest_field_is_rejected(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["unknown"] = True
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ProtocolError, match="Extra inputs"):
        ProtocolPack.load(root)


def test_duplicate_skill_id_is_rejected(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["skills"].append(dict(manifest["skills"][0]))
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ProtocolError, match="unique"):
        ProtocolPack.load(root)


def test_path_escape_is_rejected(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["skills"][0]["path"] = "../outside.md"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ProtocolError, match="escapes"):
        ProtocolPack.load(root)


def test_invalid_or_missing_hash_is_rejected(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["root_sha256"] = "deadbeef"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ProtocolError, match="lowercase SHA-256"):
        ProtocolPack.load(root)


def test_changed_skill_and_reference_are_rejected(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    (root / "skills" / "listen" / "SKILL.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ProtocolError, match="hash"):
        ProtocolPack.load(root)
    root = _valid_pack(tmp_path)
    (root / "references" / "source.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ProtocolError, match="hash"):
        ProtocolPack.load(root)


def test_manifest_version_is_rejected_in_favor_of_git_history(tmp_path: Path) -> None:
    root = _valid_pack(tmp_path)
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["version"] = "1.0.0"
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(ProtocolError, match="tracked by Git"):
        ProtocolPack.load(root)


def test_protocol_validation_output_includes_governance(capsys) -> None:
    assert main(["--protocol", str(ROOT), "protocol", "validate"]) == 0
    output = capsys.readouterr().out
    assert "Status: experimental" in output
    assert "Population:" in output
    assert "Skills: 11" in output
    assert "Categories:" in output
    assert "Reviewers: 0" in output
    assert "Next review: not scheduled" in output
    assert "Hash validation: OK" in output
