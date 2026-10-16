from pathlib import Path
from typing import Any

from pydantic_evals import Dataset
from pydantic_evals.evaluators import EqualsExpected

from filo.protocol import ProtocolPack

PACK_ROOT = Path("protocols/transdiagnostic")
CASES_PATH = Path(__file__).parent / "cases" / "conversation_skills.yaml"


def test_written_conversation_skill_contracts() -> None:
    loaded = Dataset[dict[str, Any], dict[str, bool], dict[str, Any]].from_file(CASES_PATH)
    dataset = Dataset(
        name=loaded.name,
        cases=loaded.cases,
        evaluators=[EqualsExpected()],
    )

    def audit_skill(inputs: dict[str, Any]) -> dict[str, bool]:
        text = " ".join((PACK_ROOT / inputs["path"]).read_text(encoding="utf-8").casefold().split())
        return {
            "all_required_terms_present": all(
                " ".join(term.casefold().split()) in text for term in inputs["required_terms"]
            )
        }

    report = dataset.evaluate_sync(audit_skill, progress=False)

    assert not report.failures, report.render(include_errors=True)
    assert all(result.value for case in report.cases for result in case.assertions.values()), (
        report.render(include_input=True, include_output=True, include_reasons=True)
    )


def test_every_catalog_entry_says_when_the_skill_adds_value() -> None:
    pack = ProtocolPack.load(PACK_ROOT)
    assert len(pack.skill_ids) <= 11
    for skill_id in pack.skill_ids:
        assert pack.get_skill_metadata(skill_id).description.startswith("Use when ")
        assert "## What a strong move does" in pack.get_skill(skill_id)
        assert "## Following up" in pack.get_skill(skill_id)
