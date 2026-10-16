"""Encrypted longitudinal case memory backed by standard-library SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import sqlite3
import threading
import unicodedata
from collections import OrderedDict
from collections.abc import Callable, Generator, Iterable, Iterator
from contextlib import contextmanager, nullcontext, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

import numpy as np
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_ai import Embedder, ModelMessage, ModelMessagesTypeAdapter

SESSION_GAP = timedelta(hours=8)
LOCAL_OLLAMA_EMBEDDINGS_URL = "http://localhost:11434/v1"
# Filo stores start their own schema lineage at filo-1. Every later schema change raises the
# generation and adds the step that brings a store from the previous one to it.
SCHEMA_GENERATION = 1
SCHEMA_VERSION = f"filo-{SCHEMA_GENERATION}".encode()
SCHEMA_MARKER = re.compile(rb"filo-([1-9][0-9]*)")
# Ordered forward steps: MIGRATIONS[n] brings a filo-n store to filo-(n + 1). Each step receives
# the store, so it can decrypt and re-encrypt payloads, and the connection holding the exclusive
# migration transaction; it must not commit.
MIGRATIONS: dict[int, Callable[[MemoryStore, sqlite3.Connection], None]] = {}
MIGRATION_BACKUP = "filo.db.pre-migration"
LEGACY_THERAPIST_DATABASE = "thera.db"
SCHEMA_STATEMENTS = """
CREATE TABLE IF NOT EXISTS state (
    name TEXT PRIMARY KEY, payload BLOB NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY, started_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL, ended_at TEXT, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL,
    role TEXT NOT NULL, created_at TEXT NOT NULL, payload BLOB NOT NULL,
    FOREIGN KEY(session_id) REFERENCES sessions(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS memory_items (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, origin TEXT NOT NULL,
    fit TEXT NOT NULL, lifecycle TEXT NOT NULL,
    first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
    payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS interventions (
    id TEXT PRIMARY KEY, state TEXT NOT NULL,
    updated_at TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS process_preferences (
    id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS support_choices (
    id TEXT PRIMARY KEY, updated_at TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS semantic_index (
    entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
    model TEXT NOT NULL, content_hash TEXT NOT NULL,
    dimensions INTEGER NOT NULL, payload BLOB NOT NULL,
    PRIMARY KEY(entity_type, entity_id)
);
CREATE TABLE IF NOT EXISTS pending_extractions (
    message_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
);
CREATE TABLE IF NOT EXISTS telegram_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, payload BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS wellbeing_checks (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, payload BLOB NOT NULL
);
"""
DEFAULT_STALE_HYPOTHESIS_DAYS = 180
BUSY_TIMEOUT_SECONDS = 10.0
# Calibrated for Qwen3-Embedding-0.6B on 384 synthetic Italian/English query-memory pairs
# (scripts/calibrate_semantic_floor.py): 0.20 let 90% of unrelated pairs through, 0.40 lets 4%
# through while keeping 67% of related pairs. Ranking then keeps only the top items above it.
MIN_SEMANTIC_RELEVANCE = 0.4
# A long round shares some four-letter word with almost any query, so one shared token is not
# evidence of relevance for rounds and sessions the way it is for a short claim.
MIN_LEXICAL_OVERLAP = 2
# Embedding is local but not free: a store that predates round indexing, or one restored from
# export, would otherwise embed its whole archive inside one conversation turn. Newest rounds are
# embedded first; older ones stay lexically retrievable until later retrievals index them.
MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL = 64
MIN_FORGET_QUOTE_WORDS = 3
# Share of a forgotten wording's tokens that a round must repeat to count as carrying it, which
# catches an assistant reply echoing the story in slightly different order or inflection.
ROUND_CARRY_COVERAGE = 0.75
CONSOLIDATION_CLAIM_LIMIT = 50
# Decrypted round text is cached only for recently used rounds; scans that touch the whole
# archive (forgetting, quote lookup) decrypt as they go and keep nothing.
ROUND_TEXT_CACHE_SIZE = 1_024
# Rounds whose text is read for lexical scoring in one retrieval: the best semantic matches plus
# the most recent ones. Everything else is ranked by its vector alone.
ROUND_LEXICAL_CANDIDATES = 200


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat()


def _normalized(value: str) -> str:
    return " ".join(value.split()).strip()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MemoryKind(StrEnum):
    """Claim kind. ``fact`` means a factual statement reported by the user."""

    FACT = "fact"
    PREFERENCE = "preference"
    EVENT = "event"
    PATTERN = "pattern"
    HYPOTHESIS = "hypothesis"


PARAPHRASABLE_KINDS = frozenset({MemoryKind.PREFERENCE, MemoryKind.PATTERN})
"""Kinds whose content may restate its quote instead of repeating it.

A preference or a pattern is already a summary of something said across a conversation, so
restating it in plainer words is the point. A fact or an event is where an invented specific does
the damage, so those keep the user's exact wording.
"""

MIN_PARAPHRASE_QUOTE_WORDS = 4


class TimePrecision(StrEnum):
    DAY = "day"
    MONTH = "month"
    SEASON = "season"
    YEAR = "year"


_TIME_VALUE_PATTERNS = {
    TimePrecision.DAY: re.compile(r"\d{4}-\d{2}-\d{2}"),
    TimePrecision.MONTH: re.compile(r"\d{4}-(0[1-9]|1[0-2])"),
    TimePrecision.SEASON: re.compile(r"\d{4}-(spring|summer|autumn|winter)"),
    TimePrecision.YEAR: re.compile(r"\d{4}"),
}


class TimeExpression(BaseModel):
    """When a reported situation began, exactly as the user dated it.

    ``quote`` is the user's own time expression from the current message. ``value`` may resolve it
    only to the precision the expression itself carries: "last spring" can become a season, never
    a day, and a vague "lately" is not a time expression at all.
    """

    model_config = ConfigDict(extra="forbid")

    quote: str = Field(min_length=1, max_length=100)
    precision: TimePrecision
    value: str | None = Field(
        default=None,
        description="Optional: YYYY-MM-DD, YYYY-MM, YYYY-<season> or YYYY, matching precision.",
    )

    @model_validator(mode="after")
    def value_matches_quote(self) -> TimeExpression:
        if self.value is None:
            return self
        if not _TIME_VALUE_PATTERNS[self.precision].fullmatch(self.value):
            raise ValueError("The time value must use the format of its precision.")
        if self.precision is TimePrecision.DAY:
            datetime.strptime(self.value, "%Y-%m-%d")
        years = set(re.findall(r"\b\d{4}\b", self.quote))
        if years and self.value[:4] not in years:
            raise ValueError("The time value must keep the year the user wrote.")
        return self


class ClaimOrigin(StrEnum):
    USER_STATEMENT = "user_statement"
    AGENT_HYPOTHESIS = "agent_hypothesis"


class ClaimFit(StrEnum):
    NOT_APPLICABLE = "not_applicable"
    NOT_REVIEWED = "not_reviewed"
    FITS = "fits"
    PARTLY_FITS = "partly_fits"
    DOES_NOT_FIT = "does_not_fit"
    UNSURE = "unsure"


class ClaimLifecycle(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CORRECTS = "corrects"
    REVIEWS = "reviews"
    CONTRADICTS = "contradicts"


class EvidenceQuality(StrEnum):
    """How faithfully a stored claim preserves the wording of its evidence quote.

    Both levels require an exact quote from the message that produced them, so provenance is
    identical. They differ only in whether the stored content repeats that quote verbatim.
    """

    EXACT_QUOTE = "exact_quote"
    GROUNDED_PARAPHRASE = "grounded_paraphrase"


class MemoryMode(StrEnum):
    STANDARD = "standard"
    TRANSCRIPT_ONLY = "transcript_only"
    EPHEMERAL = "ephemeral"


class InterventionState(StrEnum):
    OFFERED = "offered"
    AGREED = "agreed"
    TRIED = "tried"
    NOT_TRIED = "not_tried"
    STOPPED = "stopped"


class InterventionDecision(StrEnum):
    CONTINUE = "continue"
    SIMPLIFY = "simplify"
    ADAPT = "adapt"
    STOP = "stop"
    DIFFERENT_SUPPORT = "different_support"
    UNDECIDED = "undecided"


class SessionEndReason(StrEnum):
    EXPLICIT = "explicit"
    INACTIVITY = "inactivity"
    CONTEXT_LIMIT = "context_limit"


def valid_intervention_transition(current: InterventionState, target: InterventionState) -> bool:
    allowed = {
        InterventionState.OFFERED: {
            InterventionState.OFFERED,
            InterventionState.AGREED,
            InterventionState.NOT_TRIED,
            InterventionState.STOPPED,
        },
        InterventionState.AGREED: {
            InterventionState.AGREED,
            InterventionState.TRIED,
            InterventionState.NOT_TRIED,
            InterventionState.STOPPED,
        },
        InterventionState.TRIED: {
            InterventionState.TRIED,
            InterventionState.AGREED,
            InterventionState.STOPPED,
        },
        InterventionState.NOT_TRIED: {
            InterventionState.NOT_TRIED,
            InterventionState.AGREED,
            InterventionState.STOPPED,
        },
    }
    return target in allowed.get(current, set())


class RetentionPolicy(StrictModel):
    raw_message_days: int | None = Field(default=None, ge=1)
    session_summary_days: int | None = Field(default=None, ge=1)
    stale_hypothesis_days: int | None = Field(default=None, ge=1)


class WellbeingCheck(StrictModel):
    """One WHO-5 self-report: five answers from 0 (at no time) to 5 (all of the time)."""

    id: str
    created_at: str
    answers: list[Annotated[int, Field(ge=0, le=5)]] = Field(min_length=5, max_length=5)

    @property
    def raw_score(self) -> int:
        return sum(self.answers)

    @property
    def percentage(self) -> int:
        return self.raw_score * 4


class TelegramDelivery(StrictModel):
    chat_id: int
    text: str = Field(min_length=1)
    rich: bool = False


class AppState(StrictModel):
    consent_version: str | None = None
    telegram_consent_version: str | None = None
    telegram_update_offset: int | None = Field(default=None, ge=0)
    default_model: str | None = None
    default_context_window_tokens: int | None = Field(default=None, ge=16_000, le=128_000)
    default_locale: str | None = None
    default_memory_mode: MemoryMode = MemoryMode.STANDARD
    retention_policy: RetentionPolicy = Field(default_factory=RetentionPolicy)
    embedding_model: str | None = None
    telegram_allowed_user_id: int | None = Field(default=None, gt=0)
    pending_hypothesis_id: str | None = None
    pending_intervention_id: str | None = None
    # The optional WHO-5 check: offered unprompted once, then every two weeks only if enabled.
    wellbeing_enabled: bool = False
    wellbeing_invited: bool = False
    wellbeing_offered_at: str | None = None


class EvidenceRef(StrictModel):
    message_id: int
    quote: str | None = Field(default=None, max_length=500)
    relation: EvidenceRelation
    quality: EvidenceQuality
    recorded_at: str


class UserReport(StrictModel):
    kind: MemoryKind = Field(
        description="fact or event for something that is or happened; preference or pattern for "
        "what the user wants or what recurs."
    )
    content: str = Field(
        min_length=1,
        max_length=500,
        description="For a fact or an event, exactly the evidence_quote text. For a preference or "
        "a pattern, either that text or a shortening of it that only drops words, keeping the "
        "remaining words in order and keeping every negation.",
    )
    evidence_quote: str = Field(
        min_length=1,
        max_length=500,
        description="A span copied character for character from the current user message, at "
        "least the whole clause that carries the report: include any negation or qualifier that "
        "governs it (what the user was denying or ruling out). If the message corrects earlier "
        "wording, quote only the replacement clause, never the superseded words.",
    )
    aliases: list[str] = Field(
        default_factory=list,
        max_length=5,
        description="Optional retrieval keys, each an exact span of the current user message.",
    )
    merge_into_id: str | None = Field(
        default=None,
        description="ID of an available user report of the same kind that this repeats.",
    )
    valid_from: TimeExpression | None = Field(
        default=None,
        description="Only when the current message dates the situation; never inferred.",
    )
    about_others: bool = Field(
        default=False,
        description="True when the report says what another person did, intends, thinks, or is, "
        "or that the user is being watched, followed, or targeted. It is then kept as the user's "
        "account, never as established fact about that person.",
    )


class ClaimCorrection(StrictModel):
    memory_id: str
    correction_quote: str = Field(min_length=1, max_length=500)
    replacement_quote: str | None = Field(default=None, min_length=1, max_length=500)


class HypothesisReview(StrictModel):
    memory_id: str
    fit: ClaimFit
    evidence_quote: str = Field(min_length=1, max_length=500)
    accepted_wording_quote: str | None = Field(default=None, min_length=1, max_length=500)


class MemoryItem(StrictModel):
    id: str
    kind: MemoryKind
    content: str
    origin: ClaimOrigin
    fit: ClaimFit
    lifecycle: ClaimLifecycle
    evidence: list[EvidenceRef] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    superseded_content: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    linked_claim_ids: list[str] = Field(default_factory=list, max_length=10)
    first_seen_at: str
    last_seen_at: str
    last_reviewed_at: str | None = None
    observed_at: str | None = None
    valid_from: TimeExpression | None = None
    # When a correction replaced this claim's wording or superseded it outright; with observed_at
    # it says what the store held as true at a given time.
    superseded_at: str | None = None
    # The user's account of another person or of being targeted, never fact about that person.
    about_others: bool = False


class SessionRecord(StrictModel):
    id: str
    started_at: str
    last_activity_at: str
    ended_at: str | None = None
    summary: str = ""
    themes: list[str] = Field(default_factory=list)
    user_defined_concerns: list[str] = Field(default_factory=list)
    meaningful_changes: list[str] = Field(default_factory=list)
    interventions_discussed: list[str] = Field(default_factory=list)
    tried: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    unwanted_effects: list[str] = Field(default_factory=list)
    process_feedback: list[str] = Field(default_factory=list)
    support_choices: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    consolidation_error: str | None = None
    end_reason: SessionEndReason | None = None
    context_warning_sent: bool = False
    last_context_tokens: int = Field(default=0, ge=0)
    # Set when the user said in this session that they are under 18; the post-turn extractor then
    # records no user report for the rest of the session.
    user_reports_withheld: bool = False
    # Set when content in this still-open session was forgotten; it then closes without a
    # reflection, exactly as a closed session holding forgotten content loses its own.
    reflection_withheld: bool = False


SESSION_REFLECTION_LIST_FIELDS = (
    "themes",
    "user_defined_concerns",
    "meaningful_changes",
    "interventions_discussed",
    "tried",
    "outcomes",
    "unwanted_effects",
    "process_feedback",
    "support_choices",
    "open_questions",
    "risk_notes",
)
"""Every model-written session field besides the summary.

Correction, forgetting, retention and closing all walk this one tuple, so a new reflection field
cannot be left out of a cleanup path.
"""


class InterventionRecord(StrictModel):
    id: str
    skill: str
    description: str = Field(min_length=1, max_length=500)
    linked_claim_ids: list[str] = Field(default_factory=list, max_length=5)
    state: InterventionState
    offered_at: str
    consent_evidence: EvidenceRef | None = None
    evidence_message_ids: list[int] = Field(default_factory=list)
    prediction: str | None = Field(default=None, max_length=500)
    planned_for: str | None = Field(default=None, max_length=500)
    context: str | None = Field(default=None, max_length=500)
    outcome: str | None = Field(default=None, max_length=500)
    user_appraisal: str | None = Field(default=None, max_length=500)
    unwanted_effects: str | None = Field(default=None, max_length=500)
    decision: InterventionDecision = InterventionDecision.UNDECIDED
    follow_up_information: str | None = Field(default=None, max_length=500)
    updated_at: str


class ProcessPreference(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef
    created_at: str
    updated_at: str


class SupportChoice(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef
    barrier: str | None = Field(default=None, max_length=500)
    preference: str | None = Field(default=None, max_length=500)
    created_at: str
    updated_at: str


FORMULATION_LIST_FIELDS = (
    "presenting_concerns",
    "situations_and_triggers",
    "emotions_and_body",
    "meanings_and_thoughts",
    "actions_and_coping",
    "short_term_consequences",
    "longer_term_consequences",
    "relationship_patterns",
    "maintaining_factors",
    "strengths_and_protective_factors",
    "exceptions",
    "course_and_duration",
    "functioning_impact",
    "user_explanation",
    "social_and_cultural_context",
    "prior_helpful_or_harmful_support",
    "preferred_help",
    "process_preferences",
    "open_hypotheses",
    "shared_hypotheses",
    "open_questions",
)


class CaseFormulation(StrictModel):
    presenting_concerns: list[str] = Field(default_factory=list)
    situations_and_triggers: list[str] = Field(default_factory=list)
    emotions_and_body: list[str] = Field(default_factory=list)
    meanings_and_thoughts: list[str] = Field(default_factory=list)
    actions_and_coping: list[str] = Field(default_factory=list)
    short_term_consequences: list[str] = Field(default_factory=list)
    longer_term_consequences: list[str] = Field(default_factory=list)
    relationship_patterns: list[str] = Field(default_factory=list)
    maintaining_factors: list[str] = Field(default_factory=list)
    strengths_and_protective_factors: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)
    course_and_duration: list[str] = Field(default_factory=list)
    functioning_impact: list[str] = Field(default_factory=list)
    user_explanation: list[str] = Field(default_factory=list)
    social_and_cultural_context: list[str] = Field(default_factory=list)
    prior_helpful_or_harmful_support: list[str] = Field(default_factory=list)
    preferred_help: list[str] = Field(default_factory=list)
    process_preferences: list[str] = Field(default_factory=list)
    open_hypotheses: list[str] = Field(default_factory=list)
    shared_hypotheses: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    accepted_focus: str | None = None
    proposed_focus: str | None = None
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    last_reviewed_at: str | None = None


FORMULATION_FIELDS = FORMULATION_LIST_FIELDS


class ContextClaim(StrictModel):
    id: str
    kind: MemoryKind
    content: str
    origin: ClaimOrigin
    fit: ClaimFit
    lifecycle: ClaimLifecycle
    evidence: list[EvidenceRef]
    first_seen_at: str
    last_seen_at: str
    last_reviewed_at: str | None
    observed_at: str | None = None
    valid_from: TimeExpression | None = None
    about_others: bool = False
    # The formulation fields this claim supports: the formulation reaches the model only through
    # claims relevant to the turn.
    formulation_fields: list[str] = Field(default_factory=list)
    conflict_ids: list[str]
    semantic_relevance: float | None = None
    lexical_relevance: float = 0.0
    recency_rank: int = 0
    stale: bool = False


class ClaimConflict(StrictModel):
    claim_ids: list[str] = Field(min_length=2, max_length=2)
    claims: list[ContextClaim] = Field(min_length=2, max_length=2)


class ContextSession(StrictModel):
    id: str
    started_at: str
    ended_at: str | None
    summary: str
    themes: list[str]
    outcomes: list[str]
    unwanted_effects: list[str]
    process_feedback: list[str]
    support_choices: list[str]
    risk_notes: list[str] = Field(default_factory=list)
    semantic_relevance: float | None = None


class ContextIntervention(StrictModel):
    id: str
    skill: str
    description: str
    state: InterventionState
    prediction: str | None
    planned_for: str | None = None
    outcome: str | None
    user_appraisal: str | None
    unwanted_effects: str | None
    decision: InterventionDecision
    follow_up_information: str | None
    offered_at: str
    updated_at: str
    semantic_relevance: float | None = None


class ContextPreference(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef
    updated_at: str
    semantic_relevance: float | None = None


class ContextSupportChoice(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef
    barrier: str | None
    preference: str | None
    updated_at: str


class ContextExcerpt(StrictModel):
    """One conversation round: a user message and the reply it received."""

    message_id: int
    quote: str
    assistant_reply: str = ""
    created_at: str
    semantic_relevance: float | None = None
    lexical_relevance: float = 0.0


class CaseContextEnvelope(StrictModel):
    schema_version: int = 3
    today: str | None = None
    latest_session: ContextSession | None = None
    elapsed_since_latest_session: str | None = None
    accepted_focus: str | None = None
    proposed_focus: str | None = None
    user_reports: list[ContextClaim] = Field(default_factory=list)
    hypotheses: list[ContextClaim] = Field(default_factory=list)
    # Hypotheses the user said do not fit, so they are not proposed again.
    rejected_hypotheses: list[ContextClaim] = Field(default_factory=list)
    conflicts: list[ClaimConflict] = Field(default_factory=list)
    relevant_sessions: list[ContextSession] = Field(default_factory=list)
    active_interventions: list[ContextIntervention] = Field(default_factory=list)
    # Agreed experiments from before this session with no reported outcome, on its first turn.
    follow_up_due: list[str] = Field(default_factory=list)
    process_preferences: list[ContextPreference] = Field(default_factory=list)
    support_choices: list[ContextSupportChoice] = Field(default_factory=list)
    relevant_excerpts: list[ContextExcerpt] = Field(default_factory=list)


class CaseContextResult(CaseContextEnvelope):
    pass


class MemoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _RoundRef:
    """Plaintext metadata of a round, read without decrypting anything."""

    id: int
    session_id: str
    created_at: str
    reply_id: int | None


class _VectorTable:
    """Unit vectors of one entity type, kept as one float32 matrix and updated in place.

    Rows are added and removed as entities change; scoring is one matrix-vector product over
    the occupied rows. Memory is one row per indexed entity and nothing else.
    """

    def __init__(self) -> None:
        self.ids: list[str] = []
        self.rows: dict[str, int] = {}
        self.hashes: dict[str, str] = {}
        self.matrix = np.zeros((0, 0), dtype=np.float32)

    def reserve(self, rows: int, dimensions: int) -> None:
        """Grow once to hold ``rows`` vectors, instead of doubling while loading them."""
        if self.matrix.shape[1] != dimensions:
            # Empty, or about to be reset by put() for a new dimension.
            self.__init__()
            self.matrix = np.zeros((max(rows, 16), dimensions), dtype=np.float32)
        elif rows > self.matrix.shape[0]:
            grown = np.zeros((rows, dimensions), dtype=np.float32)
            grown[: len(self.ids)] = self.matrix[: len(self.ids)]
            self.matrix = grown

    def put(self, entity_id: str, content_hash: str, unit: np.ndarray) -> None:
        if self.matrix.shape[1] != unit.size:
            # A different model or dimension invalidates every row at once.
            self.__init__()
            self.matrix = np.zeros((16, unit.size), dtype=np.float32)
        row = self.rows.get(entity_id)
        if row is None:
            row = len(self.ids)
            if row == self.matrix.shape[0]:
                grown = np.zeros((max(16, row * 2), unit.size), dtype=np.float32)
                grown[:row] = self.matrix[:row]
                self.matrix = grown
            self.ids.append(entity_id)
            self.rows[entity_id] = row
        self.matrix[row] = unit
        self.hashes[entity_id] = content_hash

    def remove(self, entity_id: str) -> None:
        row = self.rows.pop(entity_id, None)
        self.hashes.pop(entity_id, None)
        if row is None:
            return
        last = len(self.ids) - 1
        if row != last:
            moved = self.ids[last]
            self.ids[row] = moved
            self.rows[moved] = row
            self.matrix[row] = self.matrix[last]
        self.ids.pop()

    def scores(self, query_vector: np.ndarray) -> dict[str, float]:
        if not self.ids or self.matrix.shape[1] != query_vector.size:
            return {}
        values = self.matrix[: len(self.ids)] @ query_vector
        return dict(zip(self.ids, values.tolist(), strict=True))


@dataclass(frozen=True)
class _Round:
    """A user message and the assistant reply saved with it, keyed by the user message ID."""

    id: int
    session_id: str
    created_at: str
    user: str
    assistant: str
    tokens: frozenset[str]

    @property
    def text(self) -> str:
        return f"{self.user}\n{self.assistant}"


class MemoryStore:
    def __init__(
        self,
        directory: Path | None = None,
        *,
        embedding_model: str | None = None,
        embedder: Embedder | None = None,
        ephemeral: bool = False,
    ) -> None:
        if (
            embedding_model
            and embedder is None
            and not embedding_model.startswith(("sentence-transformers:", "ollama:"))
        ):
            raise ValueError("Semantic memory requires a local embedding model.")
        if embedding_model and embedding_model.startswith("ollama:"):
            os.environ["OLLAMA_BASE_URL"] = LOCAL_OLLAMA_EMBEDDINGS_URL
        self._ephemeral = ephemeral
        self._ephemeral_database: sqlite3.Connection | None = None
        # PydanticAI runs synchronous tools in worker threads; the one in-memory connection is
        # shared across them, so every use of it is serialized.
        self._ephemeral_lock = threading.RLock()
        # Each thread has its own open transaction: a thread must never run its statements inside
        # another thread's transaction, where they would commit or roll back with it.
        self._local = threading.local()
        self._embedding_model = embedding_model or ("injected" if embedder else None)
        self._embedder = embedder or (Embedder(embedding_model) if embedding_model else None)
        # Derived, in-process only: unit vectors keyed by entity and content hash, and the
        # decrypted text of archived rounds, which never change once written. Both are rebuilt
        # from SQLite on demand and pruned when their source rows disappear.
        self._cache_lock = threading.Lock()
        self._vector_tables: dict[str, _VectorTable] = {}
        self._round_cache: OrderedDict[int, _Round] = OrderedDict()
        if ephemeral:
            self.directory = Path(":memory:")
            self.database_path = Path(":memory:")
            encryption_key = Fernet.generate_key()
            self._ephemeral_database = sqlite3.connect(
                ":memory:", autocommit=False, check_same_thread=False
            )
            self._ephemeral_database.execute("PRAGMA foreign_keys = ON")
        else:
            self.directory = directory or Path.home() / ".filo"
            if (self.directory / LEGACY_THERAPIST_DATABASE).exists():
                # Checked before mkdir, chmod, or key access so a Therapist store stays untouched.
                raise MemoryError(
                    "This directory holds a Therapist store, which Filo never reads or modifies. "
                    "Use a new data directory."
                )
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            os.chmod(self.directory, 0o700)
            self.database_path = self.directory / "filo.db"
            encryption_key = self._load_or_create_key()
        self._cipher = Fernet(encryption_key)
        self._hash_key = encryption_key
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        if self._ephemeral:
            with self.transaction():
                self._apply_schema(fresh=True)
            return
        # The whole check, any migration and the schema statements run in one transaction that
        # holds the write lock from the start, so no other process can migrate, write, or settle
        # a recovery copy between reading the marker and acting on it.
        database = sqlite3.connect(
            self.database_path, autocommit=True, timeout=BUSY_TIMEOUT_SECONDS
        )
        try:
            database.execute("PRAGMA foreign_keys = ON")
            try:
                database.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise MemoryError(
                    "Another Filo process is updating the data store. Try again shortly."
                ) from error
            self._transaction = database
            try:
                self._open_locked(database)
                database.execute("COMMIT")
            except BaseException:
                if database.in_transaction:
                    database.execute("ROLLBACK")
                self._clear_caches()
                raise
            finally:
                self._transaction = None
        finally:
            database.close()

    def _open_locked(self, database: sqlite3.Connection) -> None:
        tables = {
            row[0]
            for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if not tables:
            self._apply_schema(fresh=True)
            return
        generation = _schema_generation(
            self._read_state("schema_version") if "state" in tables else None
        )
        if generation is None or generation > SCHEMA_GENERATION:
            # Anything without a known marker, including a store written by a newer Filo, is
            # rejected before a table is created, so it is left unmodified.
            raise MemoryError(
                "This data store uses an unknown or newer schema. Use the Filo version that "
                "wrote it, or a new data directory."
            )
        if generation < SCHEMA_GENERATION:
            self._migrate(database, generation)
        self._apply_schema(fresh=False)
        # Verified and about to commit under the lock, which any migrating process would hold:
        # a copy left now, by this or an interrupted process, has served its purpose. SQLite's
        # journal still rolls back a commit cut short after this point.
        self._discard_migration_backup()

    def _apply_schema(self, *, fresh: bool) -> None:
        # Individual statements, unlike executescript, stay inside one transaction, so the
        # tables and the schema marker commit together or not at all.
        with self._connect() as database:
            for statement in SCHEMA_STATEMENTS.split(";"):
                if statement.strip():
                    database.execute(statement)
            if fresh:
                self._write_state("schema_version", SCHEMA_VERSION)
            # Earlier filo-1 development stores indexed user messages as "message"; rounds
            # replaced them, and nothing else would ever delete those vectors.
            database.execute("DELETE FROM semantic_index WHERE entity_type = 'message'")

    @property
    def migration_backup_path(self) -> Path:
        return self.directory / MIGRATION_BACKUP

    def _migrate(self, database: sqlite3.Connection, generation: int) -> None:
        """Bring an older store forward inside the caller's write transaction.

        A recovery copy is taken first with SQLite's online backup API. SQLite refuses to back up
        from a connection with an open write transaction, so a second, reading connection takes
        it; the write lock already held keeps every other writer out. The steps then run, and the
        result must pass integrity and foreign-key checks and decrypt its new marker before the
        caller commits. Any failure rolls the whole transaction back, so the store is never
        replaced from the copy: the copy only matters if the process dies mid-commit, and it holds
        the same encrypted payloads as the store.
        """
        try:
            self._write_backup()
            database.execute("PRAGMA defer_foreign_keys = ON")
            for step in range(generation, SCHEMA_GENERATION):
                MIGRATIONS[step](self, database)
            self._write_state("schema_version", SCHEMA_VERSION)
            (integrity,) = database.execute("PRAGMA integrity_check").fetchone()
            if integrity != "ok" or database.execute("PRAGMA foreign_key_check").fetchall():
                raise MemoryError("The migrated store failed verification.")
            if self._read_state("schema_version") != SCHEMA_VERSION:
                raise MemoryError("The migrated store failed verification.")
        except BaseException as error:
            self._discard_migration_backup()
            raise MemoryError("Updating the data store failed; it was left unchanged.") from error

    def _write_backup(self) -> None:
        backup = self.migration_backup_path
        partial = backup.with_name(f"{backup.name}.partial")
        partial.unlink(missing_ok=True)
        # Created with owner-only permissions before SQLite writes anything into it.
        os.close(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        source = sqlite3.connect(f"{self.database_path.resolve().as_uri()}?mode=ro", uri=True)
        target = sqlite3.connect(partial)
        try:
            source.backup(target)
        finally:
            source.close()
            target.close()
        os.replace(partial, backup)

    def _discard_migration_backup(self) -> None:
        backup = self.migration_backup_path
        for path in (backup, backup.with_name(f"{backup.name}.partial")):
            path.unlink(missing_ok=True)

    def load_app_state(self) -> AppState:
        payload = self._read_state("app")
        return AppState() if payload is None else AppState.model_validate_json(payload)

    def save_app_state(self, state: AppState) -> None:
        self._write_state("app", state.model_dump_json().encode())

    def record_failure(self, kind: str, error_class: str, now: datetime | None = None) -> None:
        """Keep the last failed turn's kind and exception class, never its content."""
        payload = {"kind": kind, "error_class": error_class, "at": _iso(now or _now())}
        self._write_state("last_failure", json.dumps(payload).encode())

    def last_failure(self) -> dict[str, str] | None:
        payload = self._read_state("last_failure")
        return None if payload is None else json.loads(payload)

    def load_formulation(self) -> CaseFormulation:
        payload = self._read_state("formulation")
        return (
            CaseFormulation() if payload is None else CaseFormulation.model_validate_json(payload)
        )

    def save_formulation(self, formulation: CaseFormulation, now: datetime | None = None) -> None:
        existing_focus = self.load_formulation().accepted_focus
        if formulation.accepted_focus != existing_focus:
            raise ValueError("Use set_focus with exact current-user evidence to accept a focus.")
        self._save_formulation(formulation, now)

    def _save_formulation(self, formulation: CaseFormulation, now: datetime | None = None) -> None:
        formulation.last_reviewed_at = _iso(now)
        self._write_state("formulation", formulation.model_dump_json().encode())

    def set_focus(
        self,
        focus: str,
        *,
        accepted: bool,
        evidence_quote: str | None = None,
        evidence_text: str | None = None,
        now: datetime | None = None,
    ) -> CaseFormulation:
        """Persist a proposed focus or an exact user-accepted focus.

        The conversation tool validates accepted wording early so the model can retry. This store
        check is the final backstop for callers added outside that tool path.
        """
        focus = _normalized(focus)
        if not focus:
            raise ValueError("Focus cannot be empty.")
        if accepted:
            if evidence_text is None:
                raise ValueError("An accepted focus requires the current user message.")
            if not _supported_quote(focus, evidence_text) or not _supported_quote(
                evidence_quote, evidence_text
            ):
                raise ValueError(
                    "An accepted focus and its evidence must be exact current-user text."
                )
            if not all(
                quote_keeps_clause_polarity(quoted, evidence_text)
                for quoted in (focus, evidence_quote)
                if quoted
            ):
                raise ValueError("An accepted focus may not quote across its negation.")
        formulation = self.load_formulation()
        if accepted:
            formulation.accepted_focus = focus
            formulation.proposed_focus = None
        else:
            formulation.proposed_focus = focus
        self._save_formulation(formulation, now)
        return formulation

    def save_formulation_links(
        self,
        links: dict[str, list[str]],
        *,
        proposed_focus: str | None = None,
        merge_existing: bool = False,
        remove_links: dict[str, list[str]] | None = None,
        now: datetime | None = None,
    ) -> CaseFormulation:
        active = {item.id: item for item in self.list_claims()}
        existing = self.load_formulation()
        if merge_existing:
            remove_links = remove_links or {}
            links = {
                field_name: list(
                    dict.fromkeys(
                        [
                            *(
                                item_id
                                for item_id in existing.evidence.get(field_name, [])
                                if item_id not in remove_links.get(field_name, [])
                            ),
                            *links.get(field_name, []),
                        ]
                    )
                )[:5]
                for field_name in FORMULATION_FIELDS
            }
        formulation = CaseFormulation(
            accepted_focus=existing.accepted_focus,
            proposed_focus=proposed_focus,
        )
        for field_name in FORMULATION_FIELDS:
            ids = []
            for item_id in links.get(field_name, [])[:5]:
                item = active.get(item_id)
                if item is None or not _claim_allowed_in_formulation(item, field_name):
                    continue
                ids.append(item_id)
            if ids:
                setattr(
                    formulation, field_name, [_formulation_text(active[item_id]) for item_id in ids]
                )
                formulation.evidence[field_name] = ids
        self.save_formulation(formulation, now)
        return formulation

    def start_session(self, now: datetime | None = None) -> SessionRecord:
        timestamp = _iso(now)
        session = SessionRecord(
            id=uuid4().hex[:12], started_at=timestamp, last_activity_at=timestamp
        )
        with self._connect() as database:
            database.execute(
                "INSERT INTO sessions(id, started_at, last_activity_at, payload) "
                "VALUES (?, ?, ?, ?)",
                (session.id, timestamp, timestamp, self._encrypt_model(session)),
            )
        return session

    def active_session(self) -> SessionRecord | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT payload FROM sessions WHERE ended_at IS NULL "
                "ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
        return None if row is None else self._decrypt_model(row[0], SessionRecord)

    def session_expired(self, session: SessionRecord, now: datetime | None = None) -> bool:
        return (now or _now()) - datetime.fromisoformat(session.last_activity_at) >= SESSION_GAP

    def save_session(self, session: SessionRecord) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE sessions SET last_activity_at = ?, ended_at = ?, payload = ? WHERE id = ?",
                (
                    session.last_activity_at,
                    session.ended_at,
                    self._encrypt_model(session),
                    session.id,
                ),
            )

    def close_session(
        self,
        session: SessionRecord,
        *,
        summary: str = "",
        themes: list[str] | None = None,
        user_defined_concerns: list[str] | None = None,
        meaningful_changes: list[str] | None = None,
        interventions_discussed: list[str] | None = None,
        tried: list[str] | None = None,
        outcomes: list[str] | None = None,
        unwanted_effects: list[str] | None = None,
        process_feedback: list[str] | None = None,
        support_choices: list[str] | None = None,
        open_questions: list[str] | None = None,
        risk_notes: list[str] | None = None,
        consolidation_error: str | None = None,
        end_reason: SessionEndReason | None = None,
        now: datetime | None = None,
    ) -> SessionRecord:
        session.ended_at = _iso(now)
        session.last_activity_at = session.ended_at
        session.summary = summary
        session.themes = themes or []
        session.user_defined_concerns = user_defined_concerns or []
        session.meaningful_changes = meaningful_changes or []
        session.interventions_discussed = interventions_discussed or []
        session.tried = tried or []
        session.outcomes = outcomes or []
        session.unwanted_effects = unwanted_effects or []
        session.process_feedback = process_feedback or []
        session.support_choices = support_choices or []
        session.open_questions = open_questions or []
        session.risk_notes = risk_notes or []
        session.consolidation_error = consolidation_error
        session.end_reason = end_reason
        self.save_session(session)
        return session

    def list_sessions(self, limit: int | None = None) -> list[SessionRecord]:
        sql = "SELECT payload FROM sessions ORDER BY started_at DESC"
        parameters: tuple[int, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (limit,)
        with self._connect() as database:
            rows = database.execute(sql, parameters).fetchall()
        return [self._decrypt_model(row[0], SessionRecord) for row in rows]

    def save_turn(
        self,
        session: SessionRecord,
        user_text: str,
        assistant_text: str,
        model_messages: list[ModelMessage],
        now: datetime | None = None,
        *,
        turn_metadata: dict[str, object] | None = None,
    ) -> int:
        timestamp = _iso(now)
        session.last_activity_at = timestamp
        assistant_payload: dict[str, object] = {
            "content": assistant_text,
            "model_messages": json.loads(ModelMessagesTypeAdapter.dump_json(model_messages)),
        }
        if turn_metadata:
            assistant_payload["turn_metadata"] = turn_metadata
        with self._connect() as database:
            cursor = database.execute(
                "INSERT INTO messages(session_id, role, created_at, payload) "
                "VALUES (?, 'user', ?, ?)",
                (session.id, timestamp, self._encrypt_json({"content": user_text})),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("SQLite did not return an ID for the inserted user message.")
            user_message_id = cursor.lastrowid
            database.execute(
                "INSERT INTO messages(session_id, role, created_at, payload) "
                "VALUES (?, 'assistant', ?, ?)",
                (session.id, timestamp, self._encrypt_json(assistant_payload)),
            )
            database.execute(
                "UPDATE sessions SET last_activity_at = ?, payload = ? WHERE id = ?",
                (timestamp, self._encrypt_model(session), session.id),
            )
        return user_message_id

    def load_session_history(self, session_id: str, limit: int | None = None) -> list[ModelMessage]:
        sql = (
            "SELECT payload FROM messages WHERE session_id = ? AND role = 'assistant' "
            "ORDER BY id DESC"
        )
        parameters: tuple[str, int] | tuple[str] = (session_id,)
        if limit is not None:
            sql += " LIMIT ?"
            parameters = (session_id, limit)
        with self._connect() as database:
            rows = database.execute(sql, parameters).fetchall()
        groups = [
            ModelMessagesTypeAdapter.validate_python(
                self._decrypt_json(row[0]).get("model_messages", [])
            )
            for row in reversed(rows)
        ]
        if limit is None:
            return [message for group in groups for message in group]
        selected: list[list[ModelMessage]] = []
        used = 0
        for group in reversed(groups):
            if selected and used + len(group) > limit:
                break
            if len(group) <= limit:
                selected.append(group)
                used += len(group)
        return [message for group in reversed(selected) for message in group]

    def session_messages(self, session_id: str, turn_limit: int = 50) -> list[tuple[str, str]]:
        if turn_limit < 1:
            raise ValueError("Turn limit must be positive.")
        with self._connect() as database:
            rows = database.execute(
                "SELECT role, payload FROM messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, turn_limit * 2),
            ).fetchall()
        return [
            (role, self._decrypt_json(payload).get("content", ""))
            for role, payload in reversed(rows)
        ]

    def session_transcript(self, session_id: str, limit_chars: int | None = None) -> str:
        with self._connect() as database:
            rows = database.execute(
                "SELECT role, payload FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        lines = [
            f"{role}: {self._decrypt_json(payload).get('content', '')}" for role, payload in rows
        ]
        if limit_chars is None:
            return "\n".join(lines)
        turns = ["\n".join(lines[index : index + 2]) for index in range(0, len(lines), 2)]
        selected: list[str] = []
        used = 0
        for turn in reversed(turns):
            if selected and used + len(turn) + 1 > limit_chars:
                break
            if len(turn) <= limit_chars:
                selected.append(turn)
                used += len(turn) + 1
        return "\n".join(reversed(selected))

    def add_pending_extraction(self, message_id: int, now: datetime | None = None) -> None:
        """Mark a user message whose direct reports are still to be extracted."""
        with self._connect() as database:
            database.execute(
                "INSERT OR IGNORE INTO pending_extractions(message_id, created_at) VALUES (?, ?)",
                (message_id, _iso(now)),
            )

    def pending_extractions(self) -> list[tuple[int, str, int]]:
        """Every marker as (message ID, session ID, failed attempts), oldest message first."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT pending_extractions.message_id, messages.session_id, "
                "pending_extractions.attempts FROM pending_extractions "
                "JOIN messages ON messages.id = pending_extractions.message_id "
                "ORDER BY pending_extractions.message_id"
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]

    def claim_pending_extraction(self, message_id: int) -> bool:
        """Remove a marker inside the caller's transaction; False if another run removed it.

        The delete takes the write lock first, so a concurrent run of the same marker waits and
        then finds nothing to claim instead of writing the same reports twice.
        """
        with self._connect() as database:
            cursor = database.execute(
                "DELETE FROM pending_extractions WHERE message_id = ?", (message_id,)
            )
        return cursor.rowcount == 1

    def record_extraction_failure(self, message_id: int, error_class: str) -> None:
        with self._connect() as database:
            database.execute(
                "UPDATE pending_extractions SET attempts = attempts + 1, last_error = ? "
                "WHERE message_id = ?",
                (error_class, message_id),
            )

    def extraction_input(self, message_id: int) -> tuple[SessionRecord, str, str | None, str]:
        """Return the session, user text, previous assistant reply, and time of a user message."""
        with self._connect() as database:
            row = database.execute(
                "SELECT session_id, created_at, payload FROM messages "
                "WHERE id = ? AND role = 'user'",
                (message_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"No user message {message_id}.")
            previous = database.execute(
                "SELECT payload FROM messages WHERE session_id = ? AND role = 'assistant' "
                "AND id < ? ORDER BY id DESC LIMIT 1",
                (row[0], message_id),
            ).fetchone()
            session = database.execute(
                "SELECT payload FROM sessions WHERE id = ?", (row[0],)
            ).fetchone()
        reply = self._decrypt_json(previous[0]).get("content") if previous else None
        return (
            self._decrypt_model(session[0], SessionRecord),
            self._decrypt_json(row[2]).get("content", ""),
            reply,
            row[1],
        )

    def withhold_user_reports(self, session_id: str) -> None:
        with self.transaction(), self._connect() as database:
            row = database.execute(
                "SELECT payload FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return
            session = self._decrypt_model(row[0], SessionRecord)
            session.user_reports_withheld = True
            self.save_session(session)

    def corrected_wording(self, message_id: int) -> list[str]:
        """Quotes and superseded wording of claims corrected by one message."""
        wording: list[str] = []
        for item in self.list_claims(include_inactive=True):
            corrections = [
                reference.quote
                for reference in item.evidence
                if reference.message_id == message_id
                and reference.quote
                and reference.relation in {EvidenceRelation.CORRECTS, EvidenceRelation.CONTRADICTS}
            ]
            if corrections:
                wording.extend([*corrections, *item.superseded_content])
        return list(dict.fromkeys(wording))

    def add_user_reports(
        self,
        reports: list[UserReport],
        evidence_message_id: int,
        evidence_text: str,
        now: datetime | None = None,
    ) -> list[MemoryItem]:
        if len(reports) > 2:
            raise ValueError("At most two user reports may be recorded per turn.")
        saved: list[MemoryItem] = []
        existing = self.list_claims(include_inactive=True)
        for report in reports:
            if report.merge_into_id and not any(
                item.id == report.merge_into_id
                and item.lifecycle is ClaimLifecycle.ACTIVE
                and item.origin is ClaimOrigin.USER_STATEMENT
                and item.kind is report.kind
                for item in existing
            ):
                raise ValueError("A merge target must be an active compatible user report.")
            quote = _normalized(report.evidence_quote)
            if not _supported_quote(quote, evidence_text):
                raise ValueError("A user report requires an exact quote from the current message.")
            if not quote_keeps_clause_polarity(quote, evidence_text):
                raise ValueError(
                    "A user report may not quote across the negation that qualified it."
                )
            content = _normalized(report.content)
            if (
                _evidence_quality(content, quote) is EvidenceQuality.GROUNDED_PARAPHRASE
                and report.kind not in PARAPHRASABLE_KINDS
            ):
                raise ValueError(
                    "Only a preference or a pattern may restate its quote; a fact or an event "
                    "must use the user's exact wording."
                )
            _require_grounded_paraphrase(content, quote, "a user report")
            _require_quoted_aliases(report.aliases, evidence_text)
            if report.valid_from is not None:
                observed = self._message_time(evidence_message_id)
                _require_quoted_time(
                    report.valid_from,
                    evidence_text,
                    datetime.fromisoformat(observed) if observed else now or _now(),
                )
            duplicate = next(
                (
                    item
                    for item in existing
                    if item.lifecycle is ClaimLifecycle.ACTIVE
                    and item.origin is ClaimOrigin.USER_STATEMENT
                    and item.kind is report.kind
                    and (
                        item.id == report.merge_into_id
                        or item.content.casefold() == content.casefold()
                        # Matching on content alone is sound because a restatement reuses the
                        # words of its quote. Matching on the shared quote instead would merge
                        # two distinct claims drawn from one sentence, and would swallow a user
                        # re-asserting wording they had previously corrected.
                        or _near_duplicate(item.content, content)
                    )
                ),
                None,
            )
            timestamp = _iso(now)
            evidence = EvidenceRef(
                message_id=evidence_message_id,
                quote=quote,
                relation=EvidenceRelation.SUPPORTS,
                quality=_evidence_quality(content, quote),
                recorded_at=timestamp,
            )
            if duplicate:
                duplicate.last_seen_at = timestamp
                if evidence_message_id not in {item.message_id for item in duplicate.evidence}:
                    # Quality describes the record this evidence lands on, which keeps its own
                    # existing content, not the incoming report's wording.
                    duplicate.evidence.append(
                        evidence.model_copy(
                            update={"quality": _evidence_quality(duplicate.content, quote)}
                        )
                    )
                duplicate.aliases = list(dict.fromkeys([*duplicate.aliases, *report.aliases]))[:5]
                duplicate.valid_from = duplicate.valid_from or report.valid_from
                duplicate.about_others = duplicate.about_others or report.about_others
                self._save_claim(duplicate)
                saved.append(duplicate)
                continue
            item = MemoryItem(
                id=uuid4().hex[:12],
                kind=report.kind,
                content=content,
                origin=ClaimOrigin.USER_STATEMENT,
                fit=ClaimFit.NOT_APPLICABLE,
                lifecycle=ClaimLifecycle.ACTIVE,
                evidence=[evidence],
                aliases=report.aliases,
                first_seen_at=timestamp,
                last_seen_at=timestamp,
                observed_at=self._message_time(evidence_message_id) or timestamp,
                valid_from=report.valid_from,
                about_others=report.about_others,
            )
            for other in existing:
                if (
                    other.lifecycle is ClaimLifecycle.ACTIVE
                    and other.kind is item.kind
                    and _claims_conflict(other.content, item.content)
                ):
                    item.conflict_ids.append(other.id)
                    other.conflict_ids = list(dict.fromkeys([*other.conflict_ids, item.id]))
                    self._save_claim(other)
            self._save_claim(item)
            existing.append(item)
            saved.append(item)
        return saved

    def add_hypothesis(
        self,
        content: str,
        *,
        linked_claim_ids: list[str],
        evidence_message_ids: list[int],
        aliases: list[str] | None = None,
        evidence_message_id: int | None = None,
        evidence_quote: str | None = None,
        evidence_text: str | None = None,
        now: datetime | None = None,
    ) -> MemoryItem:
        content = _normalized(content)
        if not content:
            raise ValueError("Hypothesis cannot be empty.")
        active = {item.id: item for item in self.list_claims()}
        if set(linked_claim_ids) - set(active):
            raise ValueError("Hypothesis links must reference available active claims.")
        _require_quoted_aliases(aliases or [], evidence_text)
        timestamp = _iso(now)
        evidence = [
            reference.model_copy(update={"relation": EvidenceRelation.SUPPORTS})
            for item_id in linked_claim_ids
            for reference in active[item_id].evidence
            if reference.message_id in evidence_message_ids
        ]
        if evidence_message_id is not None and evidence_quote and evidence_text:
            if not _supported_quote(evidence_quote, evidence_text):
                raise ValueError("A hypothesis quote must come from the current message.")
            if not quote_keeps_clause_polarity(evidence_quote, evidence_text):
                raise ValueError(
                    "A hypothesis may not quote across the negation that qualified it."
                )
            evidence.append(
                EvidenceRef(
                    message_id=evidence_message_id,
                    quote=_normalized(evidence_quote),
                    relation=EvidenceRelation.SUPPORTS,
                    quality=EvidenceQuality.EXACT_QUOTE,
                    recorded_at=timestamp,
                )
            )
        if not evidence:
            raise ValueError(
                "Hypothesis evidence must resolve to linked claims or an exact current quote."
            )
        duplicate = next(
            (
                item
                for item in active.values()
                if item.origin is ClaimOrigin.AGENT_HYPOTHESIS
                and item.lifecycle is ClaimLifecycle.ACTIVE
                and _near_duplicate(item.content, content)
            ),
            None,
        )
        if duplicate:
            duplicate.last_seen_at = timestamp
            duplicate.linked_claim_ids = list(
                dict.fromkeys([*duplicate.linked_claim_ids, *linked_claim_ids])
            )[:10]
            self._save_claim(duplicate)
            return duplicate
        item = MemoryItem(
            id=uuid4().hex[:12],
            kind=MemoryKind.HYPOTHESIS,
            content=content,
            origin=ClaimOrigin.AGENT_HYPOTHESIS,
            fit=ClaimFit.NOT_REVIEWED,
            lifecycle=ClaimLifecycle.ACTIVE,
            evidence=evidence,
            aliases=aliases or [],
            linked_claim_ids=linked_claim_ids,
            first_seen_at=timestamp,
            last_seen_at=timestamp,
            observed_at=(
                self._message_time(evidence_message_id) if evidence_message_id is not None else None
            )
            or timestamp,
        )
        self._save_claim(item)
        return item

    def correct_claim(
        self,
        correction: ClaimCorrection,
        evidence_message_id: int,
        evidence_text: str,
        now: datetime | None = None,
    ) -> MemoryItem:
        with self.transaction():
            self._discard_telegram_output()
            item = self._get_claim(correction.memory_id)
            if item.lifecycle is not ClaimLifecycle.ACTIVE:
                raise ValueError("Only an active claim can be corrected.")
            if correction.replacement_quote and item.origin is ClaimOrigin.AGENT_HYPOTHESIS:
                raise ValueError(
                    "An agent hypothesis cannot become a user statement through correction; "
                    "review it or supersede it without a replacement."
                )
            if not _supported_quote(correction.correction_quote, evidence_text):
                raise ValueError("A correction requires an exact current-message quote.")
            if correction.replacement_quote and not _supported_quote(
                correction.replacement_quote, evidence_text
            ):
                raise ValueError("A replacement requires an exact current-message quote.")
            # The correction path needs this most of all: a replacement mined out of its negation
            # writes the inversion straight into the claim it was meant to fix.
            for quoted, label in (
                (correction.correction_quote, "correction"),
                (correction.replacement_quote, "replacement"),
            ):
                if quoted and not quote_keeps_clause_polarity(quoted, evidence_text):
                    raise ValueError(
                        f"A {label} may not quote across the negation that qualified it."
                    )
            timestamp = _iso(now)
            old = item.content
            # A restated claim carries wording that differs from its content, so the quotes that
            # supported the superseded content must be retired with it. Otherwise the corrected
            # wording keeps returning through retrieval and derived text.
            replacement_wording = _normalized(correction.replacement_quote or "")
            retained = {old.casefold(), replacement_wording.casefold()}
            superseded_quotes = [
                reference.quote
                for reference in item.evidence
                if reference.quote
                and reference.relation is EvidenceRelation.SUPPORTS
                and reference.quote.casefold() not in retained
            ]
            relation = (
                EvidenceRelation.CORRECTS
                if correction.replacement_quote
                else EvidenceRelation.CONTRADICTS
            )
            item.evidence.append(
                EvidenceRef(
                    message_id=evidence_message_id,
                    quote=_normalized(correction.correction_quote),
                    relation=relation,
                    quality=EvidenceQuality.EXACT_QUOTE,
                    recorded_at=timestamp,
                )
            )
            if correction.replacement_quote:
                item.evidence.append(
                    EvidenceRef(
                        message_id=evidence_message_id,
                        quote=_normalized(correction.replacement_quote),
                        relation=EvidenceRelation.CORRECTS,
                        quality=EvidenceQuality.EXACT_QUOTE,
                        recorded_at=timestamp,
                    )
                )
            item.superseded_content.extend([old, *superseded_quotes])
            item.aliases = []
            item.last_seen_at = timestamp
            item.last_reviewed_at = timestamp
            item.superseded_at = timestamp
            if correction.replacement_quote:
                item.content = _normalized(correction.replacement_quote)
                item.origin = ClaimOrigin.USER_STATEMENT
                item.fit = ClaimFit.NOT_APPLICABLE
                # The replacement is a new observation, and the old date described the old claim.
                item.observed_at = self._message_time(evidence_message_id) or timestamp
                item.valid_from = None
            else:
                item.lifecycle = ClaimLifecycle.SUPERSEDED
                item.conflict_ids = []
            self._save_claim(item)
            self._delete_semantic_entities("claim", [item.id])
            self._delete_semantic_entities(
                "round", [str(reference.message_id) for reference in item.evidence]
            )
            self._rebuild_claim_conflicts()
            self._refresh_formulation_links()
            replacement = item.content if correction.replacement_quote else ""
            # Longest first: replacing the shorter stored content before the longer quote it was
            # drawn from would fragment the derived text and leave the quote unmatchable.
            for wording in sorted({old, *superseded_quotes}, key=len, reverse=True):
                self._invalidate_derived_text(wording, replacement)
            # A later round that repeats the superseded wording would bring it straight back as an
            # excerpt, although it is not the claim's evidence.
            self._forget_rounds(
                self._rounds_carrying([old, *superseded_quotes])
                | {
                    reference.message_id
                    for reference in item.evidence
                    if reference.relation is EvidenceRelation.SUPPORTS
                }
            )
            self._clear_pending_hypothesis(item.id)
            return item

    def review_hypotheses(
        self,
        reviews: list[HypothesisReview],
        evidence_message_id: int,
        evidence_text: str,
        now: datetime | None = None,
    ) -> tuple[list[MemoryItem], list[MemoryItem]]:
        ids = [review.memory_id for review in reviews]
        if len(ids) != len(set(ids)):
            raise ValueError("Each hypothesis needs a separate review.")
        reviewed: list[MemoryItem] = []
        accepted_reports: list[MemoryItem] = []
        for review in reviews:
            if review.fit in {ClaimFit.NOT_APPLICABLE, ClaimFit.NOT_REVIEWED}:
                raise ValueError("A hypothesis review requires an explicit fit result.")
            if not _supported_quote(review.evidence_quote, evidence_text):
                raise ValueError("Each hypothesis review requires its own exact evidence quote.")
            if review.accepted_wording_quote and not _supported_quote(
                review.accepted_wording_quote, evidence_text
            ):
                raise ValueError("Accepted wording must be an exact current-message quote.")
            for quoted, label in (
                (review.evidence_quote, "review"),
                (review.accepted_wording_quote, "accepted wording"),
            ):
                if quoted and not quote_keeps_clause_polarity(quoted, evidence_text):
                    raise ValueError(
                        f"A {label} quote may not cross the negation that qualified it."
                    )
            item = self._get_claim(review.memory_id)
            if (
                item.origin is not ClaimOrigin.AGENT_HYPOTHESIS
                or item.lifecycle is not ClaimLifecycle.ACTIVE
            ):
                raise ValueError("Only an active agent hypothesis can be reviewed.")
            timestamp = _iso(now)
            item.fit = review.fit
            item.last_reviewed_at = timestamp
            item.last_seen_at = timestamp
            item.evidence.append(
                EvidenceRef(
                    message_id=evidence_message_id,
                    quote=_normalized(review.evidence_quote),
                    relation=EvidenceRelation.REVIEWS,
                    quality=EvidenceQuality.EXACT_QUOTE,
                    recorded_at=timestamp,
                )
            )
            self._save_claim(item)
            reviewed.append(item)
            if review.fit is ClaimFit.PARTLY_FITS and review.accepted_wording_quote:
                accepted_reports.extend(
                    self.add_user_reports(
                        [
                            UserReport(
                                kind=MemoryKind.PATTERN,
                                content=review.accepted_wording_quote,
                                evidence_quote=review.accepted_wording_quote,
                            )
                        ],
                        evidence_message_id,
                        evidence_text,
                        now,
                    )
                )
            self._clear_pending_hypothesis(item.id)
        self._refresh_formulation_links()
        return reviewed, accepted_reports

    def forget_claim(self, item_id: str, now: datetime | None = None) -> MemoryItem:
        with self.transaction():
            self._discard_telegram_output()
            return self._forget_claim(self._get_claim(item_id), now)

    def _forget_claim(self, item: MemoryItem, now: datetime | None) -> MemoryItem:
        item.lifecycle = ClaimLifecycle.ARCHIVED
        item.conflict_ids = []
        item.last_seen_at = _iso(now)
        self._save_claim(item)
        self._delete_semantic_entities("claim", [item.id])
        self._rebuild_claim_conflicts()
        self._refresh_formulation_links()
        # Derived text quotes what the user actually wrote, which for a restated claim is not
        # the stored content, so forgetting has to reach both. Longest first, or removing the
        # shorter content leaves the longer quote unmatchable.
        wordings = {item.content, *(ref.quote for ref in item.evidence if ref.quote)}
        for wording in sorted(wordings, key=len, reverse=True):
            self._invalidate_derived_text(wording, "", forgotten_id=item.id)
        self._forget_rounds(
            {reference.message_id for reference in item.evidence} | self._rounds_carrying(wordings)
        )
        self._clear_pending_hypothesis(item.id)
        return item

    def has_user_quote(self, quote: str, *, before_id: int | None = None) -> bool:
        """Whether an archived user message contains this exact wording."""
        sql = "SELECT payload FROM messages WHERE role = 'user'"
        parameters: tuple[int, ...] = ()
        if before_id is not None:
            sql += " AND id < ?"
            parameters = (before_id,)
        with self._connect() as database:
            rows = database.execute(sql, parameters).fetchall()
        return any(
            _supported_quote(quote, self._decrypt_json(payload).get("content", ""))
            for (payload,) in rows
        )

    def forget_content(
        self,
        quotes: list[str],
        request_quote: str,
        evidence_message_id: int,
        evidence_text: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        """Forget archived content the user asked about, whether or not it ever became a claim.

        Each quote must be exact wording from an earlier user message, and the request itself an
        exact quote of the current one, so a refusal cannot be mined into a request. Forgetting
        suppresses every round that carried the wording, archives claims built on it, and clears
        the reflection of every closed session that contained such a round, because a summary can
        paraphrase a story without repeating any of its words. The archive itself is kept.
        """
        if not _supported_quote(request_quote, evidence_text):
            raise ValueError("A forget request requires an exact current-message quote.")
        if not quote_keeps_clause_polarity(request_quote, evidence_text):
            raise ValueError(
                "A forget request may not quote across the negation that qualified it."
            )
        wordings = [_normalized(quote) for quote in quotes]
        if not wordings:
            raise ValueError("Name at least one quote to forget.")
        for wording in wordings:
            if len(re.findall(r"\w+", wording)) < MIN_FORGET_QUOTE_WORDS:
                raise ValueError(
                    f"Each quote to forget needs at least {MIN_FORGET_QUOTE_WORDS} words."
                )
            if not self.has_user_quote(wording, before_id=evidence_message_id):
                raise ValueError("Each quote to forget must come from an earlier user message.")
        with self.transaction():
            # Not counted as a discard: this turn's own reply is queued after it and must not be
            # mistaken for a view rendered before a deletion.
            self._discard_telegram_output(count=False)
            forgotten = [
                item
                for item in self.list_claims()
                if any(
                    _carries(wording, text) or _carries(text, wording)
                    for wording in wordings
                    for text in [item.content, *(ref.quote for ref in item.evidence if ref.quote)]
                )
            ]
            for item in forgotten:
                self._forget_claim(item, now)
            for wording in sorted(wordings, key=len, reverse=True):
                self._invalidate_derived_text(wording, "")
            # The request names what it asks to forget, so its own round goes too.
            rounds = self._rounds_carrying(wordings) | {evidence_message_id}
            self._forget_rounds(rounds)
        return {"rounds": len(rounds), "claims": [item.id for item in forgotten]}

    def _forget_rounds(self, round_ids: set[int]) -> None:
        """Suppress rounds and every reflection that may paraphrase them."""
        self._suppress_rounds(round_ids)
        session_ids = {item.session_id for item in self._round_refs() if item.id in round_ids}
        for session in self.list_sessions():
            if session.id not in session_ids:
                continue
            if session.ended_at:
                _clear_session_reflection(session)
                self._delete_semantic_entities("session", [session.id])
            else:
                session.reflection_withheld = True
            self.save_session(session)

    def reflection_withheld(self, session_id: str) -> bool:
        with self._connect() as database:
            row = database.execute(
                "SELECT payload FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return row is not None and self._decrypt_model(row[0], SessionRecord).reflection_withheld

    def round_suppressed(self, round_id: int) -> bool:
        return round_id in self._suppressed_round_ids()

    def list_claims(self, include_inactive: bool = False) -> list[MemoryItem]:
        sql = "SELECT payload FROM memory_items"
        parameters: tuple[str, ...] = ()
        if not include_inactive:
            sql += " WHERE lifecycle = ?"
            parameters = (ClaimLifecycle.ACTIVE.value,)
        sql += " ORDER BY last_seen_at DESC"
        with self._connect() as database:
            rows = database.execute(sql, parameters).fetchall()
        return [self._decrypt_model(row[0], MemoryItem) for row in rows]

    def claims_as_of(self, moment: datetime) -> list[MemoryItem]:
        """Claims the store held as true at ``moment``: observed by then, not yet superseded.

        A corrected claim's current wording counts from the correction, which resets
        ``observed_at``; its earlier wording is not reconstructed. Forgotten claims never count.
        An inspection query only: retrieval never returns superseded wording.
        """
        return [
            item
            for item in self.list_claims(include_inactive=True)
            if item.lifecycle is not ClaimLifecycle.ARCHIVED
            and datetime.fromisoformat(item.observed_at or item.first_seen_at) <= moment
            and (
                item.lifecycle is ClaimLifecycle.ACTIVE
                or (
                    item.superseded_at is not None
                    and moment < datetime.fromisoformat(item.superseded_at)
                )
            )
        ]

    def record_process_preference(
        self,
        content: str,
        evidence_quote: str,
        evidence_message_id: int,
        evidence_text: str,
        now: datetime | None = None,
    ) -> ProcessPreference:
        if not _supported_quote(evidence_quote, evidence_text):
            raise ValueError("Process feedback requires an exact evidence quote.")
        if not quote_keeps_clause_polarity(evidence_quote, evidence_text):
            raise ValueError(
                "Process feedback may not quote across the negation that qualified it."
            )
        _require_grounded_paraphrase(content, evidence_quote, "a process preference")
        timestamp = _iso(now)
        existing = next(
            (
                item
                for item in self.list_process_preferences()
                if _near_duplicate(item.content, content)
            ),
            None,
        )
        if existing:
            existing.updated_at = timestamp
            self._save_process_preference(existing)
            return existing
        item = ProcessPreference(
            id=uuid4().hex[:12],
            content=_normalized(content),
            evidence=EvidenceRef(
                message_id=evidence_message_id,
                quote=_normalized(evidence_quote),
                relation=EvidenceRelation.SUPPORTS,
                quality=_evidence_quality(content, evidence_quote),
                recorded_at=timestamp,
            ),
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._save_process_preference(item)
        return item

    def list_process_preferences(self) -> list[ProcessPreference]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT payload FROM process_preferences ORDER BY updated_at DESC"
            ).fetchall()
        return [self._decrypt_model(row[0], ProcessPreference) for row in rows]

    def record_support_choice(
        self,
        content: str,
        evidence_quote: str,
        evidence_message_id: int,
        evidence_text: str,
        *,
        barrier: str | None = None,
        preference: str | None = None,
        now: datetime | None = None,
    ) -> SupportChoice:
        if not _supported_quote(evidence_quote, evidence_text):
            raise ValueError("A support choice requires exact evidence.")
        if not quote_keeps_clause_polarity(evidence_quote, evidence_text):
            raise ValueError(
                "A support choice may not quote across the negation that qualified it."
            )
        _require_grounded_paraphrase(content, evidence_quote, "a support choice")
        if barrier and not _supported_quote(barrier, evidence_text):
            raise ValueError("A support barrier requires exact evidence.")
        if preference and not _supported_quote(preference, evidence_text):
            raise ValueError("A support preference requires exact evidence.")
        for quoted, label in ((barrier, "barrier"), (preference, "preference")):
            if quoted and not quote_keeps_clause_polarity(quoted, evidence_text):
                raise ValueError(
                    f"A support {label} may not quote across the negation that qualified it."
                )
        timestamp = _iso(now)
        item = SupportChoice(
            id=uuid4().hex[:12],
            content=_normalized(content),
            evidence=EvidenceRef(
                message_id=evidence_message_id,
                quote=_normalized(evidence_quote),
                relation=EvidenceRelation.SUPPORTS,
                quality=_evidence_quality(content, evidence_quote),
                recorded_at=timestamp,
            ),
            barrier=_normalized(barrier) if barrier else None,
            preference=_normalized(preference) if preference else None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        self._save_support_choice(item)
        return item

    def list_support_choices(self) -> list[SupportChoice]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT payload FROM support_choices ORDER BY updated_at DESC"
            ).fetchall()
        return [self._decrypt_model(row[0], SupportChoice) for row in rows]

    def create_intervention(
        self,
        *,
        skill: str,
        description: str,
        state: InterventionState,
        linked_claim_ids: list[str],
        evidence_message_id: int,
        consent_quote: str | None = None,
        evidence_text: str | None = None,
        prediction: str | None = None,
        planned_for: str | None = None,
        context: str | None = None,
        follow_up_information: str | None = None,
        now: datetime | None = None,
    ) -> InterventionRecord:
        if state not in {InterventionState.OFFERED, InterventionState.AGREED}:
            raise ValueError("A new intervention must be offered or agreed.")
        _require_consent_quote(consent_quote, evidence_text)
        if any(
            item.skill == skill and _near_duplicate(item.description, description)
            for item in self.list_interventions(active_only=True)
        ):
            raise ValueError("Update the existing similar intervention instead of duplicating it.")
        timestamp = _iso(now)
        active_ids = {item.id for item in self.list_claims()}
        record = InterventionRecord(
            id=uuid4().hex[:12],
            skill=skill,
            description=_normalized(description),
            linked_claim_ids=[item for item in linked_claim_ids if item in active_ids][:5],
            state=state,
            offered_at=timestamp,
            consent_evidence=(
                EvidenceRef(
                    message_id=evidence_message_id,
                    quote=_normalized(consent_quote),
                    relation=EvidenceRelation.SUPPORTS,
                    quality=EvidenceQuality.EXACT_QUOTE,
                    recorded_at=timestamp,
                )
                if consent_quote
                else None
            ),
            evidence_message_ids=[evidence_message_id],
            prediction=_normalized(prediction) if prediction else None,
            planned_for=_normalized(planned_for) if planned_for else None,
            context=_normalized(context) if context else None,
            follow_up_information=(
                _normalized(follow_up_information) if follow_up_information else None
            ),
            updated_at=timestamp,
        )
        self._save_intervention(record)
        return record

    def update_intervention(
        self,
        record_id: str,
        *,
        state: InterventionState,
        evidence_message_id: int,
        description: str | None = None,
        linked_claim_ids: list[str] | None = None,
        consent_quote: str | None = None,
        evidence_text: str | None = None,
        prediction: str | None = None,
        planned_for: str | None = None,
        context: str | None = None,
        outcome: str | None = None,
        user_appraisal: str | None = None,
        unwanted_effects: str | None = None,
        decision: InterventionDecision | None = None,
        follow_up_information: str | None = None,
        now: datetime | None = None,
    ) -> InterventionRecord:
        record = self._get_intervention(record_id)
        if not valid_intervention_transition(record.state, state):
            raise ValueError(f"Invalid intervention transition: {record.state} -> {state}")
        _require_consent_quote(consent_quote, evidence_text)
        record.state = state
        for field_name, value in (
            ("description", description),
            ("prediction", prediction),
            ("planned_for", planned_for),
            ("context", context),
            ("outcome", outcome),
            ("user_appraisal", user_appraisal),
            ("unwanted_effects", unwanted_effects),
            ("follow_up_information", follow_up_information),
        ):
            if value:
                setattr(record, field_name, _normalized(value))
        if linked_claim_ids is not None:
            active_ids = {item.id for item in self.list_claims()}
            record.linked_claim_ids = [item for item in linked_claim_ids if item in active_ids][:5]
        if consent_quote:
            record.consent_evidence = EvidenceRef(
                message_id=evidence_message_id,
                quote=_normalized(consent_quote),
                relation=EvidenceRelation.REVIEWS,
                quality=EvidenceQuality.EXACT_QUOTE,
                recorded_at=_iso(now),
            )
        if decision:
            record.decision = decision
        if evidence_message_id not in record.evidence_message_ids:
            record.evidence_message_ids.append(evidence_message_id)
        record.updated_at = _iso(now)
        self._save_intervention(record)
        return record

    def list_interventions(self, active_only: bool = False) -> list[InterventionRecord]:
        sql = "SELECT payload FROM interventions"
        parameters: tuple[str, ...] = ()
        if active_only:
            sql += " WHERE state IN (?, ?, ?, ?)"
            parameters = (
                InterventionState.OFFERED.value,
                InterventionState.AGREED.value,
                InterventionState.TRIED.value,
                InterventionState.NOT_TRIED.value,
            )
        sql += " ORDER BY updated_at DESC"
        with self._connect() as database:
            rows = database.execute(sql, parameters).fetchall()
        return [self._decrypt_model(row[0], InterventionRecord) for row in rows]

    def retrieve_case_context(
        self,
        query: str,
        *,
        allow_index_write: bool = True,
        now: datetime | None = None,
        follow_up_before: str | None = None,
    ) -> CaseContextResult:
        """Bounded case data for one query.

        ``follow_up_before`` is the start of a session on its first turn: experiments the person
        agreed to before it and has not reported on are then listed in ``follow_up_due``.
        """
        current = now or _now()
        if not self._ephemeral:
            policy = self.load_app_state().retention_policy
            if policy != RetentionPolicy():
                self.apply_retention(policy)
        # One query embedding serves every entity type in this retrieval.
        query_vector = self._query_vector(query)
        query_tokens = _tokens(query)
        claims = self.list_claims()
        semantic_scores = self._semantic_text_scores(
            query_vector,
            "claim",
            {item.id: " ".join([item.content, *item.aliases]) for item in claims},
            allow_index_write=allow_index_write,
        )
        app_state = self.load_app_state()
        pending_hypothesis_id = app_state.pending_hypothesis_id
        formulation = self.load_formulation()
        formulation_fields: dict[str, list[str]] = {}
        for field_name, ids in formulation.evidence.items():
            for item_id in ids:
                formulation_fields.setdefault(item_id, []).append(field_name)
        ranked: list[ContextClaim] = []
        for index, item in enumerate(claims):
            semantic = semantic_scores.get(item.id)
            lexical = (
                min(len(query_tokens & _tokens(" ".join([item.content, *item.aliases]))), 3) / 3
            )
            stale = _is_stale_hypothesis(item)
            pinned = item.id == pending_hypothesis_id or bool(item.conflict_ids)
            relevant = (
                pinned
                or lexical > 0
                or (semantic is not None and semantic >= MIN_SEMANTIC_RELEVANCE)
            )
            if not relevant:
                continue
            ranked.append(
                ContextClaim(
                    id=item.id,
                    kind=item.kind,
                    content=item.content,
                    origin=item.origin,
                    fit=item.fit,
                    lifecycle=item.lifecycle,
                    evidence=[
                        reference
                        for reference in item.evidence
                        if reference.quote not in item.superseded_content
                    ],
                    first_seen_at=item.first_seen_at,
                    last_seen_at=item.last_seen_at,
                    last_reviewed_at=item.last_reviewed_at,
                    observed_at=item.observed_at,
                    valid_from=item.valid_from,
                    about_others=item.about_others,
                    formulation_fields=formulation_fields.get(item.id, []),
                    conflict_ids=item.conflict_ids,
                    semantic_relevance=semantic,
                    lexical_relevance=lexical,
                    recency_rank=index,
                    stale=stale,
                )
            )
        ranked.sort(
            key=lambda item: (
                item.id == pending_hypothesis_id,
                bool(item.conflict_ids),
                _context_claim_score(item),
                item.last_seen_at,
            ),
            reverse=True,
        )
        rejected = [
            item
            for item in ranked
            if item.origin is ClaimOrigin.AGENT_HYPOTHESIS and item.fit is ClaimFit.DOES_NOT_FIT
        ]
        ranked = [item for item in ranked if item not in rejected]
        user_reports = [item for item in ranked if item.origin is ClaimOrigin.USER_STATEMENT][:15]
        hypotheses = [
            item
            for item in ranked
            if item.origin is ClaimOrigin.AGENT_HYPOTHESIS
            and (
                not item.stale
                or item.id == pending_hypothesis_id
                or _context_claim_score(item) >= 0.5
            )
        ][:8]
        visible = {item.id: item for item in [*user_reports, *hypotheses]}
        for item in list(visible.values()):
            for conflict_id in item.conflict_ids:
                if conflict_id in visible:
                    continue
                conflict = next((value for value in ranked if value.id == conflict_id), None)
                if conflict:
                    visible[conflict_id] = conflict
                    (
                        user_reports
                        if conflict.origin is ClaimOrigin.USER_STATEMENT
                        else hypotheses
                    ).append(conflict)
        conflicts = []
        seen_pairs: set[tuple[str, str]] = set()
        for item in visible.values():
            for other_id in item.conflict_ids:
                pair = (min(item.id, other_id), max(item.id, other_id))
                if pair in seen_pairs or other_id not in visible:
                    continue
                seen_pairs.add(pair)
                conflicts.append(
                    ClaimConflict(
                        claim_ids=list(pair),
                        claims=[visible[pair[0]], visible[pair[1]]],
                    )
                )
        # Claims are index keys that point back to the rounds they came from: a matching claim
        # lifts its own evidence rounds even when the round text itself scores lower.
        claim_round_scores: dict[int, float] = {}
        for item in ranked:
            for reference in item.evidence:
                claim_round_scores[reference.message_id] = max(
                    claim_round_scores.get(reference.message_id, 0.0), _context_claim_score(item)
                )
        follow_up_due = (
            [
                item.id
                for item in self.list_interventions(active_only=True)
                if item.state is InterventionState.AGREED
                and not item.outcome
                and item.updated_at < follow_up_before
            ][:3]
            if follow_up_before
            else []
        )
        interventions = self._relevant_interventions(
            query,
            query_vector,
            app_state.pending_intervention_id,
            allow_index_write=allow_index_write,
            pinned=set(follow_up_due),
        )
        interventions.sort(key=lambda item: item.id not in follow_up_due)
        latest, sessions = self._relevant_sessions(
            query, query_vector, allow_index_write=allow_index_write
        )
        excerpts = self._relevant_excerpts(
            query, query_vector, claim_round_scores, allow_index_write=allow_index_write
        )
        support_choices = self._relevant_support_choices(
            query, query_vector, allow_index_write=allow_index_write
        )
        preferences = self.list_process_preferences()
        preference_scores = self._semantic_text_scores(
            query_vector,
            "preference",
            {item.id: item.content for item in preferences},
            allow_index_write=allow_index_write,
        )
        # Ranked by relevance to the turn, newest first among equals, rather than by recency alone.
        preferences.sort(
            key=lambda item: (
                _hybrid_text_score(query, item.content, preference_scores.get(item.id)),
                item.updated_at,
            ),
            reverse=True,
        )
        return CaseContextResult(
            today=current.date().isoformat(),
            latest_session=latest,
            elapsed_since_latest_session=(
                _elapsed(current - datetime.fromisoformat(latest.ended_at))
                if latest is not None and latest.ended_at
                else None
            ),
            accepted_focus=formulation.accepted_focus,
            proposed_focus=formulation.proposed_focus,
            user_reports=user_reports[:20],
            hypotheses=hypotheses[:10],
            rejected_hypotheses=rejected[:5],
            conflicts=conflicts[:5],
            relevant_sessions=sessions[:3],
            active_interventions=interventions[:5],
            follow_up_due=follow_up_due,
            process_preferences=[
                ContextPreference(
                    id=item.id,
                    content=item.content,
                    evidence=item.evidence,
                    updated_at=item.updated_at,
                    semantic_relevance=preference_scores.get(item.id),
                )
                for item in preferences[:10]
            ],
            support_choices=support_choices[:10],
            relevant_excerpts=excerpts[:5],
        )

    def _relevant_support_choices(
        self, query: str, query_vector: np.ndarray | None, *, allow_index_write: bool
    ) -> list[ContextSupportChoice]:
        items = self.list_support_choices()
        texts = {
            item.id: " ".join(
                value for value in (item.content, item.barrier, item.preference) if value
            )
            for item in items
        }
        scores = self._semantic_text_scores(
            query_vector, "support", texts, allow_index_write=allow_index_write
        )
        ranked = sorted(
            items,
            key=lambda item: (
                _hybrid_text_score(query, texts[item.id], scores.get(item.id)),
                item.updated_at,
            ),
            reverse=True,
        )
        return [
            ContextSupportChoice.model_validate(item, from_attributes=True)
            for item in ranked
            if _lexical_score(query, texts[item.id]) > 0
            or scores.get(item.id, 0.0) >= MIN_SEMANTIC_RELEVANCE
        ]

    def _relevant_interventions(
        self,
        query: str,
        query_vector: np.ndarray | None,
        pending_id: str | None,
        *,
        allow_index_write: bool,
        pinned: set[str] | None = None,
    ) -> list[ContextIntervention]:
        items = self.list_interventions(active_only=True)
        text = {
            item.id: " ".join(
                value
                for value in (
                    item.description,
                    item.prediction,
                    item.planned_for,
                    item.context,
                    item.outcome,
                    item.user_appraisal,
                    item.unwanted_effects,
                    item.follow_up_information,
                )
                if value
            )
            for item in items
        }
        scores = self._semantic_text_scores(
            query_vector, "intervention", text, allow_index_write=allow_index_write
        )
        items.sort(
            key=lambda item: (
                item.id == pending_id,
                _hybrid_text_score(query, text[item.id], scores.get(item.id)),
                item.updated_at,
            ),
            reverse=True,
        )
        return [
            ContextIntervention(
                id=item.id,
                skill=item.skill,
                description=item.description,
                state=item.state,
                prediction=item.prediction,
                planned_for=item.planned_for,
                outcome=item.outcome,
                user_appraisal=item.user_appraisal,
                unwanted_effects=item.unwanted_effects,
                decision=item.decision,
                follow_up_information=item.follow_up_information,
                offered_at=item.offered_at,
                updated_at=item.updated_at,
                semantic_relevance=scores.get(item.id),
            )
            for item in items
            if item.id == pending_id
            or item.id in (pinned or set())
            or _lexical_score(query, text[item.id]) > 0
            or scores.get(item.id, 0.0) >= MIN_SEMANTIC_RELEVANCE
            or item.unwanted_effects is not None
        ]

    def _relevant_sessions(
        self, query: str, query_vector: np.ndarray | None, *, allow_index_write: bool
    ) -> tuple[ContextSession | None, list[ContextSession]]:
        """Return the most recent closed session, always, and the relevant earlier ones.

        Recency and relevance are separate questions: the long-gap check and "last time" both
        need the session that actually came last, whatever the current message is about.
        """
        closed = [item for item in self.list_sessions() if item.ended_at]
        latest = max(closed, key=lambda item: item.ended_at or "", default=None)
        sessions = [item for item in closed if item is not latest]
        texts = {item.id: " ".join([item.summary, *_reflection_values(item)]) for item in sessions}
        scores = self._semantic_text_scores(
            query_vector, "session", texts, allow_index_write=allow_index_write
        )
        sessions.sort(
            key=lambda item: (
                _hybrid_text_score(query, texts[item.id], scores.get(item.id)),
                item.ended_at or "",
            ),
            reverse=True,
        )
        selected = [
            item
            for item in sessions
            if _is_relevant(_lexical_score(query, texts[item.id]), scores.get(item.id))
        ]
        return (
            None if latest is None else _context_session(latest, None),
            [_context_session(item, scores.get(item.id)) for item in selected],
        )

    def _relevant_excerpts(
        self,
        query: str,
        query_vector: np.ndarray | None,
        claim_round_scores: dict[int, float],
        *,
        allow_index_write: bool,
    ) -> list[ContextExcerpt]:
        excluded = self._excluded_round_ids()
        refs = {item.id: item for item in self._round_refs() if item.id not in excluded}
        # A round never changes once written, so its ID and timestamp identify its content
        # without decrypting it; both are plaintext columns already, so the key reveals nothing
        # an HMAC would hide. Newest first: the embedding budget goes to recent rounds.
        expected = {
            str(item.id): f"round:{item.id}:{item.created_at}"
            for item in sorted(refs.values(), key=lambda ref: ref.id, reverse=True)
        }
        scores = self._semantic_scores(
            query_vector,
            "round",
            expected,
            lambda ids: {
                str(item.id): item.text
                for item in self._decrypt_rounds([refs[int(entity_id)] for entity_id in ids])
            },
            allow_index_write=allow_index_write,
            embed_limit=MAX_ROUND_EMBEDDINGS_PER_RETRIEVAL,
        )
        # Only a bounded candidate set is decrypted for lexical scoring: the best semantic
        # matches, the most recent rounds (which also covers rounds not yet embedded), and the
        # rounds that matching claims point to.
        best = sorted(scores, key=scores.__getitem__, reverse=True)[:ROUND_LEXICAL_CANDIDATES]
        candidate_ids = {
            *(int(item) for item in best),
            *sorted(refs, reverse=True)[:ROUND_LEXICAL_CANDIDATES],
            *(item for item in claim_round_scores if item in refs),
        }
        rounds = self._cached_rounds([refs[item] for item in sorted(candidate_ids)])
        query_tokens = _tokens(query)
        candidates: list[tuple[float, int, _Round, float | None, int]] = []
        for item in rounds.values():
            semantic = scores.get(str(item.id))
            overlap = len(query_tokens & item.tokens)
            pointer = claim_round_scores.get(item.id)
            if pointer is None and not _is_relevant(overlap, semantic):
                continue
            lexical = min(overlap, 3) / 3
            score = lexical if semantic is None else 0.7 * max(0.0, semantic) + 0.3 * lexical
            candidates.append((max(score, pointer or 0.0), item.id, item, semantic, overlap))
        candidates.sort(key=lambda value: (value[0], value[1]), reverse=True)
        return [
            ContextExcerpt(
                message_id=item.id,
                quote=item.user,
                assistant_reply=item.assistant,
                created_at=item.created_at,
                semantic_relevance=semantic,
                lexical_relevance=min(overlap, 3) / 3,
            )
            for _score, _id, item, semantic, overlap in candidates[:5]
        ]

    def consolidation_rounds(self, session_id: str) -> list[tuple[str, str, str]]:
        """Dated rounds of one session that consolidation may summarize.

        Rounds suppressed by forgetting or correction are left out, so closing a session cannot
        write forgotten or superseded content back into its summary.
        """
        excluded = self._excluded_round_ids()
        refs = [
            item
            for item in self._round_refs()
            if item.session_id == session_id and item.id not in excluded
        ]
        return [(item.created_at, item.user, item.assistant) for item in self._decrypt_rounds(refs)]

    def consolidation_claims(self, session_id: str) -> list[MemoryItem]:
        """Active claims for consolidation: this session's own first, then the most recent."""
        session_rounds = {item.id for item in self._round_refs() if item.session_id == session_id}
        claims = self.list_claims()
        own = [
            item
            for item in claims
            if any(reference.message_id in session_rounds for reference in item.evidence)
        ]
        others = [item for item in claims if item not in own]
        return [*own, *others][:CONSOLIDATION_CLAIM_LIMIT]

    def apply_retention(
        self,
        policy: RetentionPolicy | None = None,
        *,
        dry_run: bool = False,
        now: datetime | None = None,
    ) -> dict[str, int]:
        policy = policy or self.load_app_state().retention_policy
        current = now or _now()
        message_ids: list[int] = []
        summaries: list[SessionRecord] = []
        hypotheses: list[MemoryItem] = []
        wellbeing_ids: list[str] = []
        with self._connect() as database:
            if policy.raw_message_days:
                cutoff = _iso(current - timedelta(days=policy.raw_message_days))
                message_ids = [
                    row[0]
                    for row in database.execute(
                        "SELECT id FROM messages WHERE created_at < ?", (cutoff,)
                    ).fetchall()
                ]
                # Wellbeing checks are raw self-reports, so they share the raw-message limit.
                wellbeing_ids = [
                    row[0]
                    for row in database.execute(
                        "SELECT id FROM wellbeing_checks WHERE created_at < ?", (cutoff,)
                    ).fetchall()
                ]
        if policy.session_summary_days:
            cutoff = current - timedelta(days=policy.session_summary_days)
            summaries = [
                item
                for item in self.list_sessions()
                if item.ended_at and datetime.fromisoformat(item.ended_at) < cutoff
            ]
        if policy.stale_hypothesis_days:
            cutoff = current - timedelta(days=policy.stale_hypothesis_days)
            hypotheses = [
                item
                for item in self.list_claims()
                if item.origin is ClaimOrigin.AGENT_HYPOTHESIS
                and item.fit in {ClaimFit.NOT_REVIEWED, ClaimFit.UNSURE}
                and datetime.fromisoformat(item.last_reviewed_at or item.last_seen_at) < cutoff
            ]
        result = {
            "messages": len(message_ids),
            "wellbeing_checks": len(wellbeing_ids),
            "session_summaries": len(summaries),
            "stale_hypotheses": len(hypotheses),
        }
        if dry_run:
            return result
        # A queued Telegram reply older than a cutoff that removed data may quote
        # it; replies queued since then, such as those a crash left behind, stay.
        removal_cutoffs = [
            current - timedelta(days=days)
            for days, removed in (
                (policy.raw_message_days, [*message_ids, *wellbeing_ids]),
                (policy.session_summary_days, summaries),
                (policy.stale_hypothesis_days, hypotheses),
            )
            if days and removed
        ]
        with self.transaction():
            if removal_cutoffs:
                self._discard_telegram_output(before=max(removal_cutoffs))
            self._delete_message_ids(message_ids)
            with self._connect() as database:
                database.executemany(
                    "DELETE FROM wellbeing_checks WHERE id = ?", [(item,) for item in wellbeing_ids]
                )
            for session in summaries:
                _clear_session_reflection(session)
                self.save_session(session)
                self._delete_semantic_entities("session", [session.id])
            for item in hypotheses:
                item.lifecycle = ClaimLifecycle.ARCHIVED
                self._save_claim(item)
                self._delete_semantic_entities("claim", [item.id])
            self._refresh_formulation_links()
            self._clean_pending_ids()
        return result

    def delete_session(self, session_id: str) -> bool:
        with self.transaction():
            with self._connect() as database:
                rows = database.execute(
                    "SELECT id FROM messages WHERE session_id = ?", (session_id,)
                ).fetchall()
                exists = database.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
            if exists is None:
                return False
            self._discard_telegram_output()
            self._delete_message_ids([row[0] for row in rows])
            with self._connect() as database:
                database.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            self._delete_semantic_entities("session", [session_id])
            self._refresh_formulation_links()
            self._clean_pending_ids()
            return True

    def delete_before(self, before: datetime) -> dict[str, int]:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        sessions = [
            item
            for item in self.list_sessions()
            if datetime.fromisoformat(item.ended_at or item.last_activity_at) < before
        ]
        deleted = sum(self.delete_session(item.id) for item in sessions)
        with self.transaction(), self._connect() as database:
            checks = database.execute(
                "DELETE FROM wellbeing_checks WHERE created_at < ?", (_iso(before),)
            ).rowcount
            if checks:
                self._discard_telegram_output()
        return {"sessions": deleted, "wellbeing_checks": checks}

    def export(self) -> dict[str, Any]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT id, session_id, role, created_at, payload FROM messages ORDER BY id"
            ).fetchall()
        messages = []
        for row in rows:
            payload = self._decrypt_json(row[4])
            message: dict[str, Any] = {
                "id": row[0],
                "session_id": row[1],
                "role": row[2],
                "created_at": row[3],
                "content": payload.get("content", ""),
            }
            if payload.get("turn_metadata"):
                message["turn_metadata"] = payload["turn_metadata"]
            tool_exchanges = []
            for model_message in payload.get("model_messages", []):
                for part in model_message.get("parts", []):
                    part_kind = part.get("part_kind")
                    if part_kind not in {"tool-call", "tool-return", "retry-prompt"}:
                        continue
                    if part_kind == "retry-prompt" and not part.get("tool_name"):
                        continue
                    content = part.get("args") if part_kind == "tool-call" else part.get("content")
                    if part_kind == "tool-call" and isinstance(content, str):
                        with suppress(json.JSONDecodeError):
                            content = json.loads(content)
                    exchange = {
                        "direction": "input" if part_kind == "tool-call" else "output",
                        "tool_name": part.get("tool_name"),
                        "content": content,
                    }
                    if part_kind != "tool-call":
                        exchange["outcome"] = part.get("outcome", "retry")
                    tool_exchanges.append(exchange)
            if tool_exchanges:
                message["tool_exchanges"] = tool_exchanges
            messages.append(message)
        return {
            "schema_version": SCHEMA_VERSION.decode(),
            "app": self.load_app_state().model_dump(mode="json"),
            "case_formulation": self.load_formulation().model_dump(mode="json"),
            "claims": [
                item.model_dump(mode="json") for item in self.list_claims(include_inactive=True)
            ],
            "process_preferences": [
                item.model_dump(mode="json") for item in self.list_process_preferences()
            ],
            "support_choices": [
                item.model_dump(mode="json") for item in self.list_support_choices()
            ],
            "interventions": [item.model_dump(mode="json") for item in self.list_interventions()],
            "sessions": [session.model_dump(mode="json") for session in self.list_sessions()],
            "messages": messages,
            "suppressed_round_ids": sorted(self._suppressed_round_ids()),
            "telegram_outbox": [
                item.model_dump(mode="json") for _id, item in self.list_telegram_output()
            ],
            "wellbeing_checks": [
                {**item.model_dump(mode="json"), "raw_score": item.raw_score}
                for item in self.list_wellbeing_checks()
            ],
        }

    def record_wellbeing_check(
        self, answers: list[int], now: datetime | None = None
    ) -> WellbeingCheck:
        check = WellbeingCheck(id=f"wb_{uuid4().hex[:10]}", created_at=_iso(now), answers=answers)
        with self._connect() as database:
            database.execute(
                "INSERT INTO wellbeing_checks(id, created_at, payload) VALUES (?, ?, ?)",
                (check.id, check.created_at, self._encrypt_model(check)),
            )
        return check

    def list_wellbeing_checks(self) -> list[WellbeingCheck]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT payload FROM wellbeing_checks ORDER BY created_at, id"
            ).fetchall()
        return [WellbeingCheck.model_validate(self._decrypt_json(row[0])) for row in rows]

    def forget_wellbeing_check(self, check_id: str) -> bool:
        with self.transaction(), self._connect() as database:
            deleted = database.execute(
                "DELETE FROM wellbeing_checks WHERE id = ?", (check_id,)
            ).rowcount
            if deleted:
                # A queued Telegram view may show this score.
                self._discard_telegram_output()
        return bool(deleted)

    def load_secret(self, name: str) -> bytes | None:
        return self._read_state(f"secret:{name}")

    def save_secret(self, name: str, payload: bytes) -> None:
        self._write_state(f"secret:{name}", payload)

    def delete_secret(self, name: str) -> None:
        with self._connect() as database:
            database.execute("DELETE FROM state WHERE name = ?", (f"secret:{name}",))

    def delete_all(self) -> None:
        # A recovery copy left by an interrupted migration is a full copy of the conversations.
        if not self._ephemeral:
            self._discard_migration_backup()
        with self.transaction(), self._connect() as database:
            # The discard counter lives in the state table, which is wiped here;
            # it must still move forward so a view rendered before the wipe is
            # not queued after it.
            epoch = self.telegram_output_epoch() + 1
            for table in (
                "semantic_index",
                "pending_extractions",
                "messages",
                "sessions",
                "memory_items",
                "interventions",
                "process_preferences",
                "support_choices",
                "telegram_outbox",
                "wellbeing_checks",
                "state",
            ):
                database.execute(f"DELETE FROM {table}")
            self._write_state("schema_version", SCHEMA_VERSION)
            self._write_state("telegram_output_epoch", str(epoch).encode())
        self._clear_caches()

    def _save_claim(self, item: MemoryItem) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO memory_items(id, kind, origin, fit, lifecycle, first_seen_at, "
                "last_seen_at, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET kind = excluded.kind, origin = excluded.origin, "
                "fit = excluded.fit, lifecycle = excluded.lifecycle, "
                "last_seen_at = excluded.last_seen_at, payload = excluded.payload",
                (
                    item.id,
                    item.kind.value,
                    item.origin.value,
                    item.fit.value,
                    item.lifecycle.value,
                    item.first_seen_at,
                    item.last_seen_at,
                    self._encrypt_model(item),
                ),
            )

    def _save_intervention(self, item: InterventionRecord) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO interventions(id, state, updated_at, payload) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET state = excluded.state, "
                "updated_at = excluded.updated_at, payload = excluded.payload",
                (item.id, item.state.value, item.updated_at, self._encrypt_model(item)),
            )

    def _save_process_preference(self, item: ProcessPreference) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO process_preferences(id, updated_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, "
                "payload = excluded.payload",
                (item.id, item.updated_at, self._encrypt_model(item)),
            )

    def _save_support_choice(self, item: SupportChoice) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO support_choices(id, updated_at, payload) VALUES (?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET updated_at = excluded.updated_at, "
                "payload = excluded.payload",
                (item.id, item.updated_at, self._encrypt_model(item)),
            )

    def _get_claim(self, item_id: str) -> MemoryItem:
        with self._connect() as database:
            row = database.execute(
                "SELECT payload FROM memory_items WHERE id = ?", (item_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown claim: {item_id}")
        return self._decrypt_model(row[0], MemoryItem)

    def _get_intervention(self, record_id: str) -> InterventionRecord:
        with self._connect() as database:
            row = database.execute(
                "SELECT payload FROM interventions WHERE id = ?", (record_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown intervention: {record_id}")
        return self._decrypt_model(row[0], InterventionRecord)

    def _round_refs(self) -> list[_RoundRef]:
        """Every archived round in ID order, from plaintext columns only."""
        with self._connect() as database:
            rows = database.execute(
                "SELECT id, session_id, role, created_at FROM messages ORDER BY id"
            ).fetchall()
        refs = []
        for index, (message_id, session_id, role, created_at) in enumerate(rows):
            if role != "user":
                continue
            following = rows[index + 1] if index + 1 < len(rows) else None
            reply_id = (
                following[0]
                if following and following[2] == "assistant" and following[1] == session_id
                else None
            )
            refs.append(_RoundRef(message_id, session_id, created_at, reply_id))
        return refs

    def _decrypt_rounds(self, refs: list[_RoundRef]) -> Iterator[_Round]:
        """Decrypt rounds in batches, without caching them."""
        for start in range(0, len(refs), 500):
            batch = refs[start : start + 500]
            wanted = [
                message_id
                for ref in batch
                for message_id in (ref.id, ref.reply_id)
                if message_id is not None
            ]
            with self._connect() as database:
                payloads = dict(
                    database.execute(
                        "SELECT id, payload FROM messages WHERE id IN "
                        f"({','.join('?' for _ in wanted)})",
                        wanted,
                    ).fetchall()
                )
            for ref in batch:
                if ref.id not in payloads:
                    continue
                user = self._decrypt_json(payloads[ref.id]).get("content", "")
                assistant = (
                    self._decrypt_json(payloads[ref.reply_id]).get("content", "")
                    if ref.reply_id is not None and ref.reply_id in payloads
                    else ""
                )
                yield _Round(
                    id=ref.id,
                    session_id=ref.session_id,
                    created_at=ref.created_at,
                    user=user,
                    assistant=assistant,
                    tokens=frozenset(_tokens(f"{user}\n{assistant}")),
                )

    def _cached_rounds(self, refs: list[_RoundRef]) -> dict[int, _Round]:
        """Decrypt a bounded set of rounds through a small LRU of recently used ones.

        A cached round is reused only if its timestamp still matches: an ID can come back with
        other content after a rolled-back transaction.
        """
        found: dict[int, _Round] = {}
        missing: list[_RoundRef] = []
        with self._cache_lock:
            for ref in refs:
                cached = self._round_cache.get(ref.id)
                if cached is not None and cached.created_at == ref.created_at:
                    self._round_cache.move_to_end(ref.id)
                    found[ref.id] = cached
                else:
                    missing.append(ref)
        loaded = {item.id: item for item in self._decrypt_rounds(missing)}
        found.update(loaded)
        with self._cache_lock:
            self._round_cache.update(loaded)
            while len(self._round_cache) > ROUND_TEXT_CACHE_SIZE:
                self._round_cache.popitem(last=False)
        return found

    def _clear_caches(self) -> None:
        with self._cache_lock:
            self._vector_tables.clear()
            self._round_cache.clear()

    def _message_time(self, message_id: int) -> str | None:
        with self._connect() as database:
            row = database.execute(
                "SELECT created_at FROM messages WHERE id = ?", (message_id,)
            ).fetchone()
        return None if row is None else row[0]

    def _excluded_round_ids(self) -> set[int]:
        """Rounds no retrieval or consolidation may return: forgotten or superseded provenance."""
        return self._suppressed_round_ids() | {
            reference.message_id
            for item in self.list_claims(include_inactive=True)
            if item.lifecycle is not ClaimLifecycle.ACTIVE or item.superseded_content
            for reference in item.evidence
        }

    def _suppressed_round_ids(self) -> set[int]:
        payload = self._read_state("suppressed_rounds")
        return set() if payload is None else set(json.loads(payload))

    def _suppress_rounds(self, round_ids: set[int]) -> None:
        if not round_ids:
            return
        suppressed = self._suppressed_round_ids() | round_ids
        self._write_state("suppressed_rounds", json.dumps(sorted(suppressed)).encode())
        self._delete_semantic_entities("round", [str(item) for item in round_ids])
        # A pending split-mode extraction would otherwise record the forgotten wording later.
        with self._connect() as database:
            database.executemany(
                "DELETE FROM pending_extractions WHERE message_id = ?",
                ((item,) for item in round_ids),
            )

    def _rounds_carrying(self, wordings: Iterable[str]) -> set[int]:
        """Rounds whose user message or assistant reply repeats any of these wordings.

        A wording shorter than three words is too generic to match by content ("tired" occurs in
        "retired"); its own evidence rounds are still suppressed by provenance.
        """
        wordings = [
            wording
            for wording in wordings
            if wording and len(re.findall(r"\w+", wording)) >= MIN_FORGET_QUOTE_WORDS
        ]
        return {
            item.id
            for item in self._decrypt_rounds(self._round_refs())
            if any(
                _carries(wording, item.user) or _carries(wording, item.assistant)
                for wording in wordings
            )
        }

    def _query_vector(self, query: str) -> np.ndarray | None:
        if not self._embedder or not self._embedding_model or not query.strip():
            return None
        try:
            return _unit(self._embedder.embed_query_sync(query).embeddings[0])
        except Exception as error:
            raise MemoryError(
                "Semantic memory is unavailable. Run `filo setup` and try again."
            ) from error

    def _semantic_text_scores(
        self,
        query_vector: np.ndarray | None,
        entity_type: str,
        texts: dict[str, str],
        *,
        allow_index_write: bool,
    ) -> dict[str, float]:
        return self._semantic_scores(
            query_vector,
            entity_type,
            {entity_id: self._content_hash(text) for entity_id, text in texts.items()},
            lambda ids: {entity_id: texts[entity_id] for entity_id in ids},
            allow_index_write=allow_index_write,
        )

    def _semantic_scores(
        self,
        query_vector: np.ndarray | None,
        entity_type: str,
        expected: dict[str, str],
        texts_for: Callable[[list[str]], dict[str, str]],
        *,
        allow_index_write: bool,
        embed_limit: int | None = None,
    ) -> dict[str, float]:
        """Score every expected entity, loading or embedding only what the table lacks.

        ``expected`` maps entity IDs to content hashes, in the order to embed missing ones.
        Rows the table holds for anything not expected, or under another hash, are dropped, so
        deletion, suppression and correction reach the in-process table without a callback.
        """
        embedder = self._embedder
        if query_vector is None or embedder is None:
            return {}
        try:
            with self._cache_lock:
                table = self._vector_tables.setdefault(entity_type, _VectorTable())
                for entity_id in [
                    item for item in table.ids if expected.get(item) != table.hashes[item]
                ]:
                    table.remove(entity_id)
                missing = [item for item in expected if item not in table.rows]
            found: set[str] = set()
            loaded: dict[str, np.ndarray] = {}
            capacity = len(table.ids) + len(missing)
            for start in range(0, len(missing), 500):
                batch = missing[start : start + 500]
                with self._connect() as database:
                    rows = database.execute(
                        "SELECT entity_id, content_hash, dimensions, payload FROM semantic_index "
                        "WHERE model = ? AND entity_type = ? AND entity_id IN "
                        f"({','.join('?' for _ in batch)})",
                        (self._embedding_model, entity_type, *batch),
                    ).fetchall()
                batch_loaded: dict[str, np.ndarray] = {}
                for entity_id, content_hash, dimensions, payload in rows:
                    if expected[entity_id] != content_hash:
                        continue
                    raw = self._decrypt(payload)
                    # Packed little-endian float32; any other layout is rebuilt below.
                    if len(raw) == dimensions * 4:
                        unit = _unit(np.frombuffer(raw, dtype="<f4"))
                        if unit is not None:
                            batch_loaded[entity_id] = unit
                # Straight into the matrix, so a cold start never holds the vectors twice.
                self._put_vectors(table, expected, batch_loaded, capacity)
                found.update(batch_loaded)
            embed = [item for item in missing if item not in found][:embed_limit]
            if embed:
                texts = texts_for(embed)
                embed = [item for item in embed if item in texts]
                result = embedder.embed_documents_sync([texts[item] for item in embed])
                rows_to_write = []
                for entity_id, vector in zip(embed, result.embeddings, strict=True):
                    packed = np.asarray(vector, dtype="<f4")
                    unit = _unit(packed)
                    if unit is None:
                        continue
                    loaded[entity_id] = unit
                    rows_to_write.append(
                        (
                            entity_type,
                            entity_id,
                            self._embedding_model,
                            expected[entity_id],
                            packed.size,
                            self._cipher.encrypt(packed.tobytes()),
                        )
                    )
                if allow_index_write and rows_to_write:
                    with self._connect() as database:
                        database.executemany(
                            "INSERT INTO semantic_index(entity_type, entity_id, model, "
                            "content_hash, dimensions, payload) VALUES (?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(entity_type, entity_id) DO UPDATE SET "
                            "model = excluded.model, content_hash = excluded.content_hash, "
                            "dimensions = excluded.dimensions, payload = excluded.payload",
                            rows_to_write,
                        )
            self._put_vectors(table, expected, loaded, capacity)
            with self._cache_lock:
                return {
                    entity_id: score
                    for entity_id, score in table.scores(query_vector).items()
                    if entity_id in expected
                }
        except MemoryError:
            raise
        except Exception as error:
            raise MemoryError(
                "Semantic memory is unavailable. Run `filo setup` and try again."
            ) from error

    def _put_vectors(
        self,
        table: _VectorTable,
        expected: dict[str, str],
        vectors: dict[str, np.ndarray],
        capacity: int,
    ) -> None:
        if not vectors:
            return
        with self._cache_lock:
            table.reserve(
                max(capacity, len(table.ids) + len(vectors)), next(iter(vectors.values())).size
            )
            for entity_id, unit in vectors.items():
                table.put(entity_id, expected[entity_id], unit)

    def _delete_semantic_entities(self, entity_type: str, entity_ids: list[str]) -> None:
        if not entity_ids:
            return
        with self._connect() as database:
            database.executemany(
                "DELETE FROM semantic_index WHERE entity_type = ? AND entity_id = ?",
                ((entity_type, entity_id) for entity_id in entity_ids),
            )

    def _delete_message_ids(self, message_ids: list[int]) -> None:
        if not message_ids:
            return
        message_set = set(message_ids)
        with self._connect() as database:
            session_ids = {
                row[0]
                for row in database.execute(
                    f"SELECT DISTINCT session_id FROM messages WHERE id IN "
                    f"({','.join('?' for _ in message_ids)})",
                    message_ids,
                ).fetchall()
            }
            database.executemany(
                "DELETE FROM messages WHERE id = ?", ((item,) for item in message_ids)
            )
            database.executemany(
                "DELETE FROM pending_extractions WHERE message_id = ?",
                ((item,) for item in message_ids),
            )
        self._delete_semantic_entities("round", [str(item) for item in message_ids])
        for claim in self.list_claims(include_inactive=True):
            claim.evidence = [
                reference for reference in claim.evidence if reference.message_id not in message_set
            ]
            if claim.evidence:
                self._save_claim(claim)
            else:
                with self._connect() as database:
                    database.execute("DELETE FROM memory_items WHERE id = ?", (claim.id,))
                self._delete_semantic_entities("claim", [claim.id])
        for record in self.list_interventions():
            record.evidence_message_ids = [
                item for item in record.evidence_message_ids if item not in message_set
            ]
            if record.consent_evidence and record.consent_evidence.message_id in message_set:
                record.consent_evidence = None
            if not record.evidence_message_ids:
                record.description = "Content removed by retention or selective deletion."
                record.prediction = None
                record.planned_for = None
                record.context = None
                record.outcome = None
                record.user_appraisal = None
                record.unwanted_effects = None
                record.follow_up_information = None
                record.state = InterventionState.STOPPED
            self._save_intervention(record)
        for item in self.list_process_preferences():
            if item.evidence.message_id in message_set:
                with self._connect() as database:
                    database.execute("DELETE FROM process_preferences WHERE id = ?", (item.id,))
                self._delete_semantic_entities("preference", [item.id])
        for item in self.list_support_choices():
            if item.evidence.message_id in message_set:
                with self._connect() as database:
                    database.execute("DELETE FROM support_choices WHERE id = ?", (item.id,))
                self._delete_semantic_entities("support", [item.id])
        for session in self.list_sessions():
            if session.id in session_ids:
                _clear_session_reflection(session)
                self.save_session(session)
                self._delete_semantic_entities("session", [session.id])

    def _content_hash(self, content: str) -> str:
        return hmac.new(self._hash_key, content.encode(), hashlib.sha256).hexdigest()

    def _refresh_formulation_links(self) -> None:
        formulation = self.load_formulation()
        if not formulation.evidence:
            return
        self.save_formulation_links(
            formulation.evidence,
            proposed_focus=formulation.proposed_focus,
        )

    def _rebuild_claim_conflicts(self) -> None:
        claims = self.list_claims()
        for claim in claims:
            claim.conflict_ids = []
        for index, left in enumerate(claims):
            for right in claims[index + 1 :]:
                if left.kind is right.kind and _claims_conflict(left.content, right.content):
                    left.conflict_ids.append(right.id)
                    right.conflict_ids.append(left.id)
        for claim in claims:
            self._save_claim(claim)

    def _invalidate_derived_text(
        self, old: str, new: str, *, forgotten_id: str | None = None
    ) -> None:
        formulation = self.load_formulation()
        for field_name in ("accepted_focus", "proposed_focus"):
            value = getattr(formulation, field_name)
            if value:
                setattr(formulation, field_name, _replace_derived(value, old, new) or None)
        self._save_formulation(formulation)
        stopped: set[str] = set()
        for intervention in self.list_interventions():
            if forgotten_id:
                intervention.linked_claim_ids = [
                    item for item in intervention.linked_claim_ids if item != forgotten_id
                ]
            for field_name in (
                "description",
                "prediction",
                "planned_for",
                "context",
                "outcome",
                "user_appraisal",
                "unwanted_effects",
                "follow_up_information",
            ):
                value = getattr(intervention, field_name)
                if value:
                    setattr(intervention, field_name, _replace_derived(value, old, new) or None)
            if not intervention.description:
                intervention.description = "Content removed by user request."
                intervention.state = InterventionState.STOPPED
                stopped.add(intervention.id)
            self._save_intervention(intervention)
        for session in self.list_sessions():
            session.summary = _replace_derived(session.summary, old, new)
            for field_name in SESSION_REFLECTION_LIST_FIELDS:
                setattr(
                    session,
                    field_name,
                    [
                        updated
                        for value in getattr(session, field_name)
                        if (updated := _replace_derived(value, old, new))
                    ],
                )
            self.save_session(session)
        if stopped:
            app = self.load_app_state()
            if app.pending_intervention_id in stopped:
                app.pending_intervention_id = None
                self.save_app_state(app)

    def _clear_pending_hypothesis(self, item_id: str) -> None:
        app = self.load_app_state()
        if app.pending_hypothesis_id == item_id:
            app.pending_hypothesis_id = None
            self.save_app_state(app)

    def queue_telegram_output(
        self,
        deliveries: list[TelegramDelivery],
        *,
        update_offset: int | None = None,
        consent_version: str | None = None,
        rendered_at_epoch: int | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Queue replies and record the update that produced them in one transaction.

        With ``rendered_at_epoch``, nothing is written and False is returned if a
        deletion discarded queued output after the replies were rendered, since
        they may quote what it removed.
        """
        with self.transaction():
            if rendered_at_epoch is not None and rendered_at_epoch != self.telegram_output_epoch():
                return False
            with self._connect() as database:
                database.executemany(
                    "INSERT INTO telegram_outbox(created_at, payload) VALUES (?, ?)",
                    [(_iso(now), self._encrypt_model(item)) for item in deliveries],
                )
            if update_offset is None and consent_version is None:
                return True
            app = self.load_app_state()
            if update_offset is not None:
                app.telegram_update_offset = max(app.telegram_update_offset or 0, update_offset)
            if consent_version is not None:
                app.telegram_consent_version = consent_version
            self.save_app_state(app)
        return True

    def telegram_output_epoch(self) -> int:
        """Count of deletions that discarded queued Telegram output."""
        payload = self._read_state("telegram_output_epoch")
        return 0 if payload is None else int(payload)

    def list_telegram_output(
        self, limit: int | None = None, *, after_id: int = 0
    ) -> list[tuple[int, TelegramDelivery]]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT id, payload FROM telegram_outbox WHERE id > ? ORDER BY id LIMIT ?",
                (after_id, -1 if limit is None else limit),
            ).fetchall()
        return [
            (row[0], TelegramDelivery.model_validate_json(self._decrypt(row[1]))) for row in rows
        ]

    def remove_telegram_output(self, entry_id: int) -> None:
        with self._connect() as database:
            database.execute("DELETE FROM telegram_outbox WHERE id = ?", (entry_id,))

    def _discard_telegram_output(
        self, before: datetime | None = None, *, count: bool = True
    ) -> None:
        # An undelivered Telegram reply can quote what is being deleted, and
        # nothing ties a queued message to the records it mentions, so a
        # deletion drops every queued reply that could predate it.
        if count:
            self._write_state(
                "telegram_output_epoch", str(self.telegram_output_epoch() + 1).encode()
            )
        with self._connect() as database:
            if before is None:
                database.execute("DELETE FROM telegram_outbox")
            else:
                database.execute(
                    "DELETE FROM telegram_outbox WHERE created_at < ?", (_iso(before),)
                )

    def _clean_pending_ids(self) -> None:
        app = self.load_app_state()
        claim_ids = {item.id for item in self.list_claims()}
        intervention_ids = {item.id for item in self.list_interventions(active_only=True)}
        changed = False
        if app.pending_hypothesis_id not in claim_ids:
            app.pending_hypothesis_id = None
            changed = True
        if app.pending_intervention_id not in intervention_ids:
            app.pending_intervention_id = None
            changed = True
        if changed:
            self.save_app_state(app)

    def _load_or_create_key(self) -> bytes:
        path = self.directory / "memory.key"
        try:
            key = path.read_bytes()
        except FileNotFoundError:
            key = Fernet.generate_key()
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as key_file:
                key_file.write(key)
        os.chmod(path, 0o600)
        return key

    @property
    def _transaction(self) -> sqlite3.Connection | None:
        return getattr(self._local, "transaction", None)

    @_transaction.setter
    def _transaction(self, database: sqlite3.Connection | None) -> None:
        self._local.transaction = database

    @contextmanager
    def transaction(self) -> Generator[None]:
        with self._ephemeral_lock if self._ephemeral else nullcontext():
            yield from self._transaction_scope()

    def _transaction_scope(self) -> Iterator[None]:
        if self._transaction is not None:
            yield
            return
        if self._ephemeral_database is not None:
            self._transaction = self._ephemeral_database
            try:
                with self._ephemeral_database:
                    yield
            except BaseException:
                # SQLite hands a rolled-back row ID out again, so nothing read inside the
                # transaction may outlive it.
                self._clear_caches()
                raise
            finally:
                self._transaction = None
            return
        # BEGIN IMMEDIATE takes the write lock before the first read. A deferred
        # transaction that reads and then writes cannot wait for a concurrent
        # writer (the Telegram listener and a CLI command share this file):
        # SQLite fails the lock upgrade at once with "database is locked"
        # instead of honoring the busy timeout.
        database = sqlite3.connect(
            self.database_path, autocommit=True, timeout=BUSY_TIMEOUT_SECONDS
        )
        self._transaction = database
        try:
            database.execute("PRAGMA foreign_keys = ON")
            database.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                if database.in_transaction:
                    database.execute("ROLLBACK")
                self._clear_caches()
                raise
            database.execute("COMMIT")
        finally:
            self._transaction = None
            database.close()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection]:
        with self._ephemeral_lock if self._ephemeral else nullcontext():
            yield from self._connection_scope()

    def _connection_scope(self) -> Iterator[sqlite3.Connection]:
        if self._transaction is not None:
            yield self._transaction
            return
        database = self._ephemeral_database or self._open_connection()
        try:
            with database:
                yield database
        finally:
            if not self._ephemeral:
                database.close()

    def _open_connection(self) -> sqlite3.Connection:
        database = sqlite3.connect(
            self.database_path, autocommit=True, timeout=BUSY_TIMEOUT_SECONDS
        )
        # The pragma is a no-op inside a transaction, so it is set before leaving autocommit.
        database.execute("PRAGMA foreign_keys = ON")
        database.autocommit = False
        return database

    def _read_state(self, name: str) -> bytes | None:
        with self._connect() as database:
            row = database.execute("SELECT payload FROM state WHERE name = ?", (name,)).fetchone()
        return None if row is None else self._decrypt(row[0])

    def _write_state(self, name: str, payload: bytes) -> None:
        with self._connect() as database:
            database.execute(
                "INSERT INTO state(name, payload) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET payload = excluded.payload, "
                "updated_at = CURRENT_TIMESTAMP",
                (name, self._cipher.encrypt(payload)),
            )

    def _decrypt(self, payload: bytes) -> bytes:
        try:
            return self._cipher.decrypt(payload)
        except InvalidToken as error:
            raise MemoryError("The local key cannot decrypt this memory database.") from error

    def _encrypt_json(self, value: Any) -> bytes:
        return self._cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def _decrypt_json(self, payload: bytes) -> dict[str, Any]:
        return json.loads(self._decrypt(payload))

    def _encrypt_model(self, value: BaseModel) -> bytes:
        return self._cipher.encrypt(value.model_dump_json().encode())

    def _decrypt_model(self, payload: bytes, model: type[BaseModel]) -> Any:
        return model.model_validate_json(self._decrypt(payload))


def _claim_allowed_in_formulation(item: MemoryItem, field_name: str) -> bool:
    if item.lifecycle is not ClaimLifecycle.ACTIVE or _is_stale_hypothesis(item):
        return False
    if item.origin is ClaimOrigin.USER_STATEMENT:
        return field_name not in {"open_hypotheses", "shared_hypotheses"}
    if item.fit is ClaimFit.DOES_NOT_FIT:
        return False
    if field_name == "open_hypotheses":
        return item.fit in {ClaimFit.NOT_REVIEWED, ClaimFit.UNSURE, ClaimFit.PARTLY_FITS}
    if field_name == "shared_hypotheses":
        return item.fit in {ClaimFit.FITS, ClaimFit.PARTLY_FITS}
    return False


def _formulation_text(item: MemoryItem) -> str:
    # A claim about another person stays in the formulation, attributed to the user who told it.
    return f"User's account: {item.content}" if item.about_others else item.content


def _is_stale_hypothesis(item: MemoryItem, now: datetime | None = None) -> bool:
    if item.origin is not ClaimOrigin.AGENT_HYPOTHESIS or item.fit not in {
        ClaimFit.NOT_REVIEWED,
        ClaimFit.UNSURE,
    }:
        return False
    reviewed = datetime.fromisoformat(item.last_reviewed_at or item.last_seen_at)
    return (now or _now()) - reviewed >= timedelta(days=DEFAULT_STALE_HYPOTHESIS_DAYS)


def _context_claim_score(item: ContextClaim) -> float:
    semantic = max(0.0, item.semantic_relevance or 0.0)
    return 0.7 * semantic + 0.3 * item.lexical_relevance


# Kana, CJK ideographs and Thai are written without spaces between words.
_UNSPACED_SCRIPT = re.compile("[\u3040-\u30ff\u3400-\u9fff\u0e00-\u0e7f]")


def _tokens(text: str) -> set[str]:
    normalized = text.casefold()
    tokens = {token for token in re.findall(r"\w+", normalized) if len(token) >= 4}
    if _UNSPACED_SCRIPT.search(normalized):
        characters = "".join(
            character
            for character in normalized
            if unicodedata.category(character)[0] in {"L", "M", "N"}
        )
        tokens.update(characters[index : index + 2] for index in range(max(0, len(characters) - 1)))
    return tokens


def _lexical_score(query: str, text: str) -> int:
    return len(_tokens(query) & _tokens(text))


def _hybrid_text_score(query: str, text: str, semantic_score: float | None) -> float:
    lexical = min(_lexical_score(query, text), 3) / 3
    if semantic_score is None:
        return lexical
    return (0.7 * max(0.0, semantic_score)) + (0.3 * lexical)


def _unit(vector: Any) -> np.ndarray | None:
    array = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(array))
    if array.ndim != 1 or not array.size or not norm or not np.isfinite(norm):
        return None
    return array / norm


def _near_duplicate(left: str, right: str) -> bool:
    if set(re.findall(r"\d+", left)) != set(re.findall(r"\d+", right)):
        return False
    if _has_broad_negation(left) != _has_broad_negation(right):
        return False
    if _named_tokens(left) != _named_tokens(right):
        return False
    normalized_left = " ".join(re.findall(r"\w+", left.casefold()))
    normalized_right = " ".join(re.findall(r"\w+", right.casefold()))
    return SequenceMatcher(None, normalized_left, normalized_right).ratio() >= 0.9


def _claims_conflict(left: str, right: str) -> bool:
    overlap = _lexical_score(left, right)
    if overlap == 0:
        return False
    different_numbers = set(re.findall(r"\d+", left)) != set(re.findall(r"\d+", right))
    opposite_negation = _has_negation(left) != _has_negation(right)
    distinct_names = _named_tokens(left) != _named_tokens(right)
    return different_numbers or opposite_negation or distinct_names


def _named_tokens(value: str) -> set[str]:
    ignored = {
        "I",
        "The",
        "A",
        "An",
        "My",
        "Il",
        "Lo",
        "La",
        "Un",
        "Una",
        "Io",
        "Mi",
    }
    return {
        token.casefold()
        for token in re.findall(r"\b[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'-]+\b", value)
        if token not in ignored
    }


_APOSTROPHES = str.maketrans({"\u2019": "'", "\u02bc": "'", "\u00b4": "'"})

# `\w+n't` covers every English contraction at once, including the ones an explicit list keeps
# missing: don't, won't, haven't, wouldn't, shouldn't.
_NEGATION = re.compile(r"\b(non|not|never|cannot|no|\w+n't)\b")
# "mai" belongs here rather than with plain negation: "meglio che mai" is an ordinary positive
# statement, and treating it as a contradiction marker would invent conflicts between claims.
_WEAK_NEGATION = re.compile(
    r"\b(mai|none|nothing|nobody|without|hardly|rarely|neither|nor|niente|nulla|nessun|"
    r"nessuno|nessuna|senza|né|neanche|nemmeno|neppure)\b"
)


def _has_negation(value: str) -> bool:
    """Detect plain sentence negation.

    Deliberately narrow. This decides whether two claims contradict each other, and words like
    "senza" or "without" appear in ordinary positive statements — "dormo bene senza problemi" is
    not the negation of "dormo bene".
    """
    return bool(_NEGATION.search(_fold_apostrophes(value)))


def _fold_apostrophes(value: str) -> str:
    """Case-fold and normalise typographic apostrophes before matching negation.

    Phones and messaging apps substitute U+2019 by default, which would otherwise hide every
    English contraction from these patterns while leaving the text visually identical.
    """
    return value.casefold().translate(_APOSTROPHES)


def _has_broad_negation(value: str) -> bool:
    """Detect any polarity marker, including ones that only weaken a statement.

    Used where a missed negation is worse than a spurious one: deciding whether a shortened
    restatement still says what the user said.
    """
    return _negation_count(value) > 0


def _negation_count(value: str) -> int:
    """Count polarity markers rather than merely detecting one.

    A clause can negate twice — "non bevo alcol e non fumo" — and dropping one marker inverts a
    conjunct while leaving the sentence negative overall, which a boolean check would wave through.
    """
    folded = _fold_apostrophes(value)
    return len(_NEGATION.findall(folded)) + len(_WEAK_NEGATION.findall(folded))


def _supported_quote(quote: str | None, evidence: str) -> bool:
    if not quote:
        return False
    return _normalized(quote).casefold() in _normalized(evidence).casefold()


# Coordinating conjunctions and dashes divide clauses as surely as punctuation does. Without them
# "bevo alcol" could be quoted out of "non bevo alcol e non fumo", where the conjunct it belongs to
# is negative even though the sentence keeps a negation either way.
_CLAUSE_BOUNDARY = re.compile(
    r"[.!?;,\n]|\s[-\u2013\u2014]\s|\b(?:e|ed|o|oppure|ma|per\u00f2|and|or|but)\b",
    flags=re.IGNORECASE,
)


def quote_keeps_clause_polarity(quote: str, evidence: str) -> bool:
    """Reject a quote mined out of the clause that negated it.

    "è insonnia cronica" is a genuine substring of "non è insonnia cronica, è solo stress", so
    being an exact quote is not by itself enough to make it something the user said. A quote
    spanning several clauses is checked one fragment at a time against the clause it came from,
    since a partial leading fragment can shed the words that qualified it.
    """
    clauses = [clause for clause in _CLAUSE_BOUNDARY.split(_normalized(evidence)) if clause.strip()]
    for fragment in _CLAUSE_BOUNDARY.split(_normalized(quote)):
        if not fragment.strip():
            continue
        source = next(
            (clause for clause in clauses if fragment.strip().casefold() in clause.casefold()),
            None,
        )
        if source is not None and _negation_count(source) != _negation_count(fragment):
            return False
    return True


def _evidence_quality(content: str, quote: str) -> EvidenceQuality:
    """Classify a claim by whether its content repeats its evidence quote verbatim.

    The quote itself is validated against the current message by the caller, so a paraphrase is
    still anchored to words the user actually wrote.
    """
    if _normalized(content).casefold() == _normalized(quote).casefold():
        return EvidenceQuality.EXACT_QUOTE
    return EvidenceQuality.GROUNDED_PARAPHRASE


def is_grounded_paraphrase(content: str, quote: str) -> bool:
    """Check that a restatement only drops and reorders the words of its quote.

    Restating is extractive rather than generative: the content must be a subsequence of the
    quote, so it may only drop words and never reorder or substitute them. Allowing any subset
    would let a reordering invert exactly what the user denied — "non è insonnia cronica, è solo
    stress" rearranges into "non è solo stress, è insonnia cronica" using the same words.

    Polarity is checked separately, because dropping one word is enough to reverse it. A quote
    whose clauses do not agree in polarity cannot be shortened at all: dropping across the boundary
    would attach the negation of one clause to the verb of another, turning "non voglio dormire,
    voglio lavorare" into "non voglio lavorare".
    """
    content_words = re.findall(r"\w+", _normalized(content).casefold())
    quote_words = re.findall(r"\w+", _normalized(quote).casefold())
    remaining = iter(quote_words)
    return (
        len(quote_words) >= MIN_PARAPHRASE_QUOTE_WORDS
        and len(content_words) <= len(quote_words)
        and _has_uniform_polarity(quote)
        and all(word in remaining for word in content_words)
        and _negation_count(content) == _negation_count(quote)
    )


def _has_uniform_polarity(value: str) -> bool:
    clauses = [clause for clause in _CLAUSE_BOUNDARY.split(value) if clause.strip()]
    return len({_has_broad_negation(clause) for clause in clauses}) <= 1


def _require_grounded_paraphrase(content: str, quote: str, subject: str) -> None:
    if _evidence_quality(content, quote) is EvidenceQuality.EXACT_QUOTE:
        return
    if not is_grounded_paraphrase(content, quote):
        raise ValueError(
            f"A restatement of {subject} must stay inside its quote: no added number or name, "
            "no changed negation, no more words than the quote, and a quote of at least "
            f"{MIN_PARAPHRASE_QUOTE_WORDS} words."
        )


def _require_quoted_aliases(aliases: list[str], evidence: str | None) -> None:
    """Hold retrieval aliases to the same standard as content.

    Aliases never surface as claim text, but they are durable, they steer lexical retrieval, and
    nothing else constrains them — an alias saying the opposite of its claim would pull that claim
    into exactly the conversations it contradicts.
    """
    for alias in aliases:
        if evidence is None or not _supported_quote(alias, evidence):
            raise ValueError("Each alias must be an exact quote from the current message.")
        if not quote_keeps_clause_polarity(alias, evidence):
            raise ValueError("An alias may not quote across the negation that qualified it.")


def _require_consent_quote(quote: str | None, evidence: str | None) -> None:
    """Hold consent to the same standard as every other durable quote.

    Consent is the one field where a mined quote is worst: "sono d'accordo a provare l'esercizio"
    lifted out of "non sono d'accordo a provare l'esercizio" records a refusal as agreement.
    """
    if not quote:
        return
    if evidence is None:
        raise ValueError("An intervention consent quote requires the message it came from.")
    if not _supported_quote(quote, evidence):
        raise ValueError("An intervention consent quote must come from the current message.")
    if not quote_keeps_clause_polarity(quote, evidence):
        raise ValueError("Consent may not be quoted across the negation that qualified it.")


def _replace_derived(value: str, old: str, new: str) -> str:
    """Replace superseded wording, without rewriting text that is already the replacement.

    A replacement routinely contains the wording it replaces — every correction by negation does,
    turning "I drink coffee late" into "I do not drink coffee late". Substituting blindly would
    then match inside the text just written and duplicate it, so occurrences of the replacement
    are held aside while the rest of the value is rewritten.
    """
    if new:
        segments = re.split(f"({re.escape(new)})", value, flags=re.IGNORECASE)
        rewritten = "".join(
            segment if index % 2 else re.sub(re.escape(old), new, segment, flags=re.IGNORECASE)
            for index, segment in enumerate(segments)
        ).strip()
    else:
        rewritten = re.sub(re.escape(old), new, value, flags=re.IGNORECASE).strip()
    if rewritten != value:
        return rewritten
    if new and _normalized(new).casefold() in _normalized(value).casefold():
        # Already carries the replacement, from an earlier wording of the same correction. The
        # lexical fallback would read the overlap as stale text and discard a correct field.
        return value
    if _lexical_score(old, value) >= 2:
        return ""
    return value


def _clear_session_reflection(session: SessionRecord) -> None:
    session.summary = ""
    for field_name in SESSION_REFLECTION_LIST_FIELDS:
        setattr(session, field_name, [])


def _reflection_values(session: SessionRecord) -> list[str]:
    return [
        value
        for field_name in SESSION_REFLECTION_LIST_FIELDS
        for value in getattr(session, field_name)
    ]


def _context_session(session: SessionRecord, relevance: float | None) -> ContextSession:
    return ContextSession(
        id=session.id,
        started_at=session.started_at,
        ended_at=session.ended_at,
        summary=session.summary,
        themes=session.themes,
        outcomes=session.outcomes,
        unwanted_effects=session.unwanted_effects,
        process_feedback=session.process_feedback,
        support_choices=session.support_choices,
        risk_notes=session.risk_notes,
        semantic_relevance=relevance,
    )


def _elapsed(delta: timedelta) -> str:
    """Coarse elapsed time for the model; the absolute dates sit beside it in the envelope."""
    seconds = max(0, int(delta.total_seconds()))
    for unit, size in (("day", 86_400), ("hour", 3_600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'s' if count != 1 else ''}"
    return "less than a minute"


def _is_relevant(lexical_overlap: int, semantic: float | None) -> bool:
    return (
        semantic is not None and semantic >= MIN_SEMANTIC_RELEVANCE
    ) or lexical_overlap >= MIN_LEXICAL_OVERLAP


def _carries(wording: str, text: str) -> bool:
    """Whether text repeats a wording, verbatim or with most of its content words."""
    if _supported_quote(wording, text):
        return True
    wanted = _tokens(wording)
    if len(wanted) < 3:
        return False
    return len(wanted & _tokens(text)) / len(wanted) >= ROUND_CARRY_COVERAGE


_MONTHS = {
    name: number
    for number, names in enumerate(
        (
            ("january", "gennaio"),
            ("february", "febbraio"),
            ("march", "marzo"),
            ("april", "aprile"),
            ("may", "maggio"),
            ("june", "giugno"),
            ("july", "luglio"),
            ("august", "agosto"),
            ("september", "settembre"),
            ("october", "ottobre"),
            ("november", "novembre"),
            ("december", "dicembre"),
        ),
        start=1,
    )
    for name in names
}
_SEASONS = {
    "spring": "spring",
    "primavera": "spring",
    "summer": "summer",
    "estate": "summer",
    "autumn": "autumn",
    "fall": "autumn",
    "autunno": "autumn",
    "winter": "winter",
    "inverno": "winter",
}
_SEASON_START_MONTH = {"spring": 3, "summer": 6, "autumn": 9, "winter": 12}
_MONTH_NAME = "|".join(_MONTHS)
_DAY_ANCHOR = re.compile(
    r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}(?:/\d{2,4})?\b"
    rf"|\b\d{{1,2}}\s+(?:di\s+)?(?:{_MONTH_NAME})\b|\b(?:{_MONTH_NAME})\s+\d{{1,2}}\b"
    r"|\b(?:yesterday|today|tonight|ieri|oggi|stamattina|stasera|stanotte)\b"
    r"|\b(?:monday|tuesday|wednesday|thursday|friday|saturday|sunday|luned[iì]|marted[iì]"
    r"|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)\b"
    r"|\b\d+\s+(?:days?|giorni)\s+(?:ago|fa)\b"
)
_MONTH_ANCHOR = re.compile(
    rf"\b(?:{_MONTH_NAME})\b|\b\d{{4}}-\d{{2}}\b"
    r"|\b(?:last|this)\s+month\b|\bmese\s+scorso\b|\bquesto\s+mese\b"
    r"|\b\d+\s+(?:months?|mesi)\s+(?:ago|fa)\b"
)
_SEASON_ANCHOR = re.compile(rf"\b(?:{'|'.join(_SEASONS)})\b")
_YEAR_ANCHOR = re.compile(
    r"\b\d{4}\b|\b(?:last|this)\s+year\b|\banno\s+scorso\b|\bquest'anno\b"
    r"|\b\d+\s+(?:years?|anni)\s+(?:ago|fa)\b"
)
_PRECISION_RANK = {
    TimePrecision.YEAR: 0,
    TimePrecision.SEASON: 1,
    TimePrecision.MONTH: 2,
    TimePrecision.DAY: 3,
}


def _supported_precision(quote: str) -> TimePrecision | None:
    """The finest precision a time expression itself carries, or None if it dates nothing.

    "Lately" or "for a while" carry no date at all, "last spring" a season, "in March" a month.
    A recorded precision may be coarser than this, never finer.
    """
    folded = _fold_apostrophes(quote)
    for precision, anchor in (
        (TimePrecision.DAY, _DAY_ANCHOR),
        (TimePrecision.MONTH, _MONTH_ANCHOR),
        (TimePrecision.SEASON, _SEASON_ANCHOR),
        (TimePrecision.YEAR, _YEAR_ANCHOR),
    ):
        if anchor.search(folded):
            return precision
    return None


def _period_start(expression: TimeExpression) -> datetime:
    value = expression.value or ""
    year = int(value[:4])
    if expression.precision is TimePrecision.DAY:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC)
    if expression.precision is TimePrecision.MONTH:
        return datetime(year, int(value[5:7]), 1, tzinfo=UTC)
    if expression.precision is TimePrecision.SEASON:
        return datetime(year, _SEASON_START_MONTH[value[5:]], 1, tzinfo=UTC)
    return datetime(year, 1, 1, tzinfo=UTC)


def time_expression_problem(
    expression: TimeExpression, evidence: str, message_time: datetime
) -> str | None:
    """Explain why a time expression cannot be recorded, or return None if it can.

    Shared by the conversation tool and the extractor, where the model can retry, and the
    store, where it is the backstop. The value may only resolve what the quote says, against
    the date of the message that said it.
    """
    if not _supported_quote(expression.quote, evidence):
        return "valid_from.quote must be an exact span of the current user message."
    if not quote_keeps_clause_polarity(expression.quote, evidence):
        return "valid_from.quote may not be taken out of the negation that qualified it."
    supported = _supported_precision(expression.quote)
    if supported is None:
        return (
            "valid_from.quote names no date, month, season or year; leave valid_from unset "
            "for a vague phrase such as 'lately'."
        )
    if _PRECISION_RANK[expression.precision] > _PRECISION_RANK[supported]:
        return (
            f"valid_from.precision {expression.precision.value} is finer than the quote "
            f"supports ({supported.value}); use {supported.value} or coarser."
        )
    if expression.value is None:
        return None
    folded = _fold_apostrophes(expression.quote)
    message_date = message_time.astimezone(UTC) if message_time.tzinfo else message_time
    message_date = message_date.replace(tzinfo=UTC)
    if _period_start(expression) > message_date:
        return "valid_from.value lies after the message that dated it."
    months = {number for name, number in _MONTHS.items() if re.search(rf"\b{name}\b", folded)}
    if (
        months
        and expression.precision in {TimePrecision.MONTH, TimePrecision.DAY}
        and int(expression.value[5:7]) not in months
    ):
        return "valid_from.value must keep the month the user named."
    seasons = {season for name, season in _SEASONS.items() if re.search(rf"\b{name}\b", folded)}
    if (
        seasons
        and expression.precision is TimePrecision.SEASON
        and expression.value[5:] not in seasons
    ):
        return "valid_from.value must keep the season the user named."
    if re.search(r"\b\d{4}\b", folded):
        return None
    year = int(expression.value[:4])
    ago = re.search(r"\b(\d+)\s+(?:years?|anni)\s+(?:ago|fa)\b", folded)
    if ago:
        expected = {message_date.year - int(ago.group(1))}
    elif re.search(r"\blast\s+year\b|\banno\s+scorso\b", folded):
        expected = {message_date.year - 1}
    elif re.search(r"\bthis\s+year\b|\bquest'anno\b", folded):
        expected = {message_date.year}
    else:
        # A relative expression without a year ("last spring", "in March") resolves against the
        # message date: this year or the one before, never an arbitrary one.
        expected = {message_date.year, message_date.year - 1}
    if year not in expected:
        return "valid_from.value must resolve the quote against the message date."
    relative_days = {"yesterday": 1, "ieri": 1, "today": 0, "oggi": 0, "tonight": 0}
    for word, offset in relative_days.items():
        if (
            re.search(rf"\b{word}\b", folded)
            and expression.precision is TimePrecision.DAY
            and expression.value != (message_date - timedelta(days=offset)).date().isoformat()
        ):
            return "valid_from.value must resolve the quote against the message date."
    return None


def _require_quoted_time(expression: TimeExpression, evidence: str, message_time: datetime) -> None:
    problem = time_expression_problem(expression, evidence, message_time)
    if problem:
        raise ValueError(problem)


def _schema_generation(marker: bytes | None) -> int | None:
    match = SCHEMA_MARKER.fullmatch(marker or b"")
    return int(match.group(1)) if match else None
