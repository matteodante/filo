"""Versioned synthetic datasets for the Wave 1 measurement system.

Every person, event, and message in these datasets is invented. Schemas are strict so a malformed
or silently extended dataset fails before any model is called.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CASES = Path(__file__).resolve().parents[1] / "tests" / "cases"
PERSONAS_PATH = CASES / "memory_personas.yaml"
USER_TURNS_PATH = CASES / "memory_user_turns.yaml"
SAFETY_PATH = CASES / "safety_v2.yaml"

Locale = Literal["it-IT", "en-US"]
Split = Literal["tuning", "test"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProbeType(StrEnum):
    SINGLE_SESSION_DETAIL = "single_session_detail"
    ASSISTANT_SAID = "assistant_said"
    AGGREGATION = "aggregation"
    TEMPORAL = "temporal"
    LATEST_STATE = "latest_state"
    CORRECTION_PRECEDENCE = "correction_precedence"
    FORGOTTEN_ABSENT = "forgotten_absent"
    FALSE_PREMISE = "false_premise"
    PREFERENCE_APPLIED = "preference_applied"
    INTERVENTION_FOLLOW_UP = "intervention_follow_up"


class PersonaTuple(Strict):
    language: Locale
    speech_register: Literal["informal", "formal", "terse"] = Field(alias="register")
    disclosure: Literal["low", "medium", "high"]
    resistance: Literal["low", "medium", "high"]
    topic: str
    risk: Literal["none", "low"]
    verbosity: Literal["short", "medium", "long"]


class Fact(Strict):
    """One hidden ground-truth item the user may disclose, change, correct, or forget."""

    id: str
    statement: str = Field(description="English ground truth, never shown to the system.")
    kind: Literal["fact", "event", "preference", "pattern"]
    forget_terms: list[str] = Field(
        default_factory=list,
        description="Lower-case substrings that identify stored claims about this fact.",
    )


class EventType(StrEnum):
    DISCLOSE = "disclose"
    CHANGE = "change"
    CORRECT = "correct"
    FORGET = "forget"
    STATE_PREFERENCE = "state_preference"
    REQUEST_INTERVENTION = "request_intervention"
    TRY_INTERVENTION = "try_intervention"
    STOP_INTERVENTION = "stop_intervention"
    FILLER = "filler"


class TimelineEvent(Strict):
    type: EventType
    fact: str | None = None
    note: str = Field(description="What the user conveys, in English, for the simulator.")


class SessionPlan(Strict):
    day: int = Field(ge=0, le=180)
    turns: int = Field(ge=2, le=10)
    mood: str
    events: list[TimelineEvent]


class Probe(Strict):
    id: str
    type: ProbeType
    question: str = Field(description="Asked by the user, in the persona's language.")
    expected: str = Field(description="English description of a correct answer.")
    forbidden: list[str] = Field(
        default_factory=list,
        description="English descriptions of content that would be a false memory.",
    )
    reference_turn: tuple[int, int] | None = Field(
        default=None, description="(session index, turn index) whose assistant reply is evidence."
    )
    intervening_sessions: int = Field(ge=0)
    gap_days: int = Field(ge=0)


class Persona(Strict):
    id: str
    split: Split
    profile: PersonaTuple
    background: str
    facts: list[Fact]
    sessions: list[SessionPlan] = Field(min_length=6, max_length=8)
    probe_day: int = Field(ge=1, le=200)
    probes: list[Probe]

    @model_validator(mode="after")
    def _consistent(self) -> Persona:
        fact_ids = {fact.id for fact in self.facts}
        if len(fact_ids) != len(self.facts):
            raise ValueError(f"{self.id}: duplicate fact IDs")
        days = [session.day for session in self.sessions]
        if days != sorted(days) or len(set(days)) != len(days):
            raise ValueError(f"{self.id}: session days must be strictly increasing")
        if self.probe_day <= days[-1]:
            raise ValueError(f"{self.id}: probes must follow the last session")
        for session in self.sessions:
            for event in session.events:
                if event.fact is not None and event.fact not in fact_ids:
                    raise ValueError(f"{self.id}: unknown fact {event.fact}")
                if event.type is EventType.FORGET and not (
                    event.fact and self.fact(event.fact).forget_terms
                ):
                    raise ValueError(f"{self.id}: forget events need a fact with forget_terms")
        kinds = {event.type for session in self.sessions for event in session.events}
        required = {
            EventType.CHANGE,
            EventType.CORRECT,
            EventType.FORGET,
            EventType.REQUEST_INTERVENTION,
            EventType.TRY_INTERVENTION,
            EventType.STOP_INTERVENTION,
            EventType.FILLER,
        }
        if missing := required - kinds:
            raise ValueError(f"{self.id}: timeline lacks {sorted(missing)}")
        probe_ids = [probe.id for probe in self.probes]
        if len(set(probe_ids)) != len(probe_ids):
            raise ValueError(f"{self.id}: duplicate probe IDs")
        for probe in self.probes:
            if probe.reference_turn is not None:
                session_index, turn_index = probe.reference_turn
                if not (
                    0 <= session_index < len(self.sessions)
                    and 0 <= turn_index < self.sessions[session_index].turns
                ):
                    raise ValueError(f"{probe.id}: reference_turn is out of range")
            if probe.type is ProbeType.ASSISTANT_SAID and probe.reference_turn is None:
                raise ValueError(f"{probe.id}: assistant_said probes need reference_turn")
        return self

    def fact(self, fact_id: str) -> Fact:
        return next(fact for fact in self.facts if fact.id == fact_id)


class PersonaDataset(Strict):
    name: str
    synthetic_only: Literal[True]
    version: int
    personas: list[Persona]

    @model_validator(mode="after")
    def _unique(self) -> PersonaDataset:
        ids = [persona.id for persona in self.personas]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate persona IDs")
        return self

    def split(self, split: Split) -> list[Persona]:
        return [persona for persona in self.personas if persona.split == split]


class GeneratedSession(Strict):
    day: int
    turns: list[str]


class GeneratedPersonaTurns(Strict):
    persona_id: str
    generator_model: str
    sessions: list[GeneratedSession]


class UserTurnDataset(Strict):
    name: str
    synthetic_only: Literal[True]
    dataset_version: int
    personas: list[GeneratedPersonaTurns] = Field(default_factory=list)

    def for_persona(self, persona_id: str) -> GeneratedPersonaTurns | None:
        return next((item for item in self.personas if item.persona_id == persona_id), None)


class SafetyCriterion(Strict):
    id: str
    text: str = Field(description="Binary, observable requirement for the assistant.")


class SafetyScenario(Strict):
    id: str
    split: Split
    locale: Locale
    category: str
    opening: str = Field(description="First user message, in the scenario's language.")
    simulator_brief: str = Field(description="English brief for the adaptive simulated user.")
    max_turns: int = Field(ge=6, le=12)
    seeded_memory: list[SeededMemory] = Field(default_factory=list)
    criteria: list[SafetyCriterion] = Field(min_length=2)


class SeededMemory(Strict):
    """A record written through the production store API before the scenario starts."""

    kind: Literal["process_preference", "fact"]
    text: str = Field(description="Exact user wording, in the scenario's language.")
    days_ago: int = Field(ge=1)


class SafetyDataset(Strict):
    name: str
    synthetic_only: Literal[True]
    version: int
    scenarios: list[SafetyScenario]

    @model_validator(mode="after")
    def _unique(self) -> SafetyDataset:
        ids = [scenario.id for scenario in self.scenarios]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate scenario IDs")
        for scenario in self.scenarios:
            criteria = [criterion.id for criterion in scenario.criteria]
            if len(set(criteria)) != len(criteria):
                raise ValueError(f"{scenario.id}: duplicate criterion IDs")
        return self


SafetyScenario.model_rebuild()


def _load(path: Path) -> object:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def load_personas(path: Path = PERSONAS_PATH) -> PersonaDataset:
    return PersonaDataset.model_validate(_load(path))


def load_user_turns(path: Path = USER_TURNS_PATH) -> UserTurnDataset:
    return UserTurnDataset.model_validate(_load(path))


def load_safety(path: Path = SAFETY_PATH) -> SafetyDataset:
    return SafetyDataset.model_validate(_load(path))


def write_user_turns(dataset: UserTurnDataset, path: Path = USER_TURNS_PATH) -> None:
    path.write_text(
        yaml.safe_dump(
            dataset.model_dump(mode="json"), allow_unicode=True, sort_keys=False, width=100
        ),
        encoding="utf-8",
    )
