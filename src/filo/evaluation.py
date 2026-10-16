"""Synthetic-only export schema for human review of conversational evaluations."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SyntheticTurn(ReviewModel):
    role: str
    content: str


class ReviewerFields(ReviewModel):
    name: str | None = None
    role: str | None = None
    reviewed_at: str | None = None


class HumanReviewArtifact(ReviewModel):
    scenario_id: str
    locale: str
    synthetic_transcript: list[SyntheticTurn]
    selected_skill: str | None = None
    tool_path: list[str] = Field(default_factory=list)
    memory_mutations: list[dict[str, object]] = Field(default_factory=list)
    formulation_changes: list[dict[str, object]] = Field(default_factory=list)
    intervention_changes: list[dict[str, object]] = Field(default_factory=list)
    deterministic_assertions: dict[str, bool] = Field(default_factory=dict)
    semantic_evaluation: dict[str, object] = Field(default_factory=dict)
    reviewer: ReviewerFields = Field(default_factory=ReviewerFields)
    notes: str = ""

    def write_json(self, path: Path) -> None:
        """Write a plaintext synthetic review artifact chosen by the evaluator."""
        path.write_text(self.model_dump_json(indent=2) + "\n", encoding="utf-8")
