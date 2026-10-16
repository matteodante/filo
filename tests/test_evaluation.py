import json
from pathlib import Path

import yaml
from pydantic import ValidationError

from filo.evaluation import HumanReviewArtifact, SyntheticTurn


def test_human_review_artifact_is_exportable_and_has_no_reasoning_field(tmp_path) -> None:
    artifact = HumanReviewArtifact(
        scenario_id="ROLEPLAY-01",
        locale="it-IT",
        synthetic_transcript=[
            SyntheticTurn(role="user", content="Synthetic disclosure"),
            SyntheticTurn(role="assistant", content="Synthetic reply"),
        ],
        selected_skill=None,
        tool_path=[],
        deterministic_assertions={"reply_non_empty": True},
        semantic_evaluation={"collaboration": "pass"},
    )
    path = tmp_path / "review.json"
    artifact.write_json(path)
    payload = json.loads(path.read_text())
    assert payload["scenario_id"] == "ROLEPLAY-01"
    assert "reasoning" not in payload


def test_human_review_artifact_rejects_unknown_fields() -> None:
    try:
        HumanReviewArtifact.model_validate(
            {
                "scenario_id": "test",
                "locale": "en-US",
                "synthetic_transcript": [],
                "private_reasoning": "must not be stored",
            }
        )
    except ValidationError:
        pass
    else:
        raise AssertionError("Unknown private-reasoning fields must be rejected.")


def test_conversational_roleplay_dataset_has_twenty_bilingual_synthetic_cases() -> None:
    payload = yaml.safe_load(
        Path("tests/cases/conversational_roleplays.yaml").read_text(encoding="utf-8")
    )
    assert len(payload["cases"]) == 20
    assert {case["locale"] for case in payload["cases"]} == {"it-IT", "en-US"}
    assert all(isinstance(turn, str) for case in payload["cases"] for turn in case["turns"])
    assert payload["metadata"]["synthetic_only"] is True
