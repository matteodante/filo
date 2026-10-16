"""Encrypted longitudinal case memory backed by standard-library SQLite."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Embedder, ModelMessage, ModelMessagesTypeAdapter

SESSION_GAP = timedelta(hours=8)
LOCAL_OLLAMA_EMBEDDINGS_URL = "http://localhost:11434/v1"
# Filo stores start their own schema lineage; future forward migrations build on this marker.
SCHEMA_VERSION = b"filo-1"
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
"""
DEFAULT_STALE_HYPOTHESIS_DAYS = 180
MIN_SEMANTIC_RELEVANCE = 0.2


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


class EvidenceRef(StrictModel):
    message_id: int
    quote: str | None = Field(default=None, max_length=500)
    relation: EvidenceRelation
    quality: EvidenceQuality
    recorded_at: str


class UserReport(StrictModel):
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=500)
    evidence_quote: str = Field(min_length=1, max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=5)
    merge_into_id: str | None = None


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
    consolidation_error: str | None = None
    end_reason: SessionEndReason | None = None
    context_warning_sent: bool = False
    last_context_tokens: int = Field(default=0, ge=0)


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
    ended_at: str | None
    summary: str
    themes: list[str]
    outcomes: list[str]
    unwanted_effects: list[str]
    process_feedback: list[str]
    support_choices: list[str]
    semantic_relevance: float | None = None


class ContextIntervention(StrictModel):
    id: str
    skill: str
    description: str
    state: InterventionState
    prediction: str | None
    outcome: str | None
    user_appraisal: str | None
    unwanted_effects: str | None
    decision: InterventionDecision
    follow_up_information: str | None
    updated_at: str
    semantic_relevance: float | None = None


class ContextPreference(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef


class ContextSupportChoice(StrictModel):
    id: str
    content: str
    evidence: EvidenceRef
    barrier: str | None
    preference: str | None


class ContextExcerpt(StrictModel):
    message_id: int
    quote: str
    created_at: str
    semantic_relevance: float | None = None
    lexical_relevance: float = 0.0


class CaseContextEnvelope(StrictModel):
    schema_version: int = 1
    accepted_focus: str | None = None
    proposed_focus: str | None = None
    user_reports: list[ContextClaim] = Field(default_factory=list)
    hypotheses: list[ContextClaim] = Field(default_factory=list)
    conflicts: list[ClaimConflict] = Field(default_factory=list)
    relevant_sessions: list[ContextSession] = Field(default_factory=list)
    active_interventions: list[ContextIntervention] = Field(default_factory=list)
    process_preferences: list[ContextPreference] = Field(default_factory=list)
    support_choices: list[ContextSupportChoice] = Field(default_factory=list)
    relevant_excerpts: list[ContextExcerpt] = Field(default_factory=list)


class CaseContextResult(CaseContextEnvelope):
    pass


class MemoryError(RuntimeError):
    pass


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
        self._transaction: sqlite3.Connection | None = None
        self._embedding_model = embedding_model or ("injected" if embedder else None)
        self._embedder = embedder or (Embedder(embedding_model) if embedding_model else None)
        if ephemeral:
            self.directory = Path(":memory:")
            self.database_path = Path(":memory:")
            encryption_key = Fernet.generate_key()
            self._ephemeral_database = sqlite3.connect(":memory:", autocommit=False)
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
        with self._connect() as database:
            tables = {
                row[0]
                for row in database.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            }
        existing_version = self._read_state("schema_version") if "state" in tables else None
        if tables and existing_version != SCHEMA_VERSION:
            # Only an empty database may be initialized. Anything else without the current marker
            # is rejected before a table is created, so an unknown store is left unmodified.
            raise MemoryError(
                "This data store uses an incompatible schema. Use a new data directory or delete "
                "the old store; migration is not supported."
            )
        # Individual statements, unlike executescript, stay inside one transaction, so the
        # tables and the schema marker commit together or not at all.
        with self.transaction(), self._connect() as database:
            for statement in SCHEMA_STATEMENTS.split(";"):
                if statement.strip():
                    database.execute(statement)
            if not tables:
                self._write_state("schema_version", SCHEMA_VERSION)

    def load_app_state(self) -> AppState:
        payload = self._read_state("app")
        return AppState() if payload is None else AppState.model_validate_json(payload)

    def save_app_state(self, state: AppState) -> None:
        self._write_state("app", state.model_dump_json().encode())

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
                setattr(formulation, field_name, [active[item_id].content for item_id in ids])
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
            if correction.replacement_quote:
                item.content = _normalized(correction.replacement_quote)
                item.origin = ClaimOrigin.USER_STATEMENT
                item.fit = ClaimFit.NOT_APPLICABLE
            else:
                item.lifecycle = ClaimLifecycle.SUPERSEDED
                item.conflict_ids = []
            self._save_claim(item)
            self._delete_semantic_entities("claim", [item.id])
            self._delete_semantic_entities(
                "message", [str(reference.message_id) for reference in item.evidence]
            )
            self._rebuild_claim_conflicts()
            self._refresh_formulation_links()
            replacement = item.content if correction.replacement_quote else ""
            # Longest first: replacing the shorter stored content before the longer quote it was
            # drawn from would fragment the derived text and leave the quote unmatchable.
            for wording in sorted({old, *superseded_quotes}, key=len, reverse=True):
                self._invalidate_derived_text(wording, replacement)
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
            item = self._get_claim(item_id)
            item.lifecycle = ClaimLifecycle.ARCHIVED
            item.conflict_ids = []
            item.last_seen_at = _iso(now)
            self._save_claim(item)
            self._delete_semantic_entities("claim", [item.id])
            self._delete_semantic_entities(
                "message", [str(reference.message_id) for reference in item.evidence]
            )
            self._rebuild_claim_conflicts()
            self._refresh_formulation_links()
            # Derived text quotes what the user actually wrote, which for a restated claim is not
            # the stored content, so forgetting has to reach both. Longest first, or removing the
            # shorter content leaves the longer quote unmatchable.
            wordings = {item.content, *(ref.quote for ref in item.evidence if ref.quote)}
            for wording in sorted(wordings, key=len, reverse=True):
                self._invalidate_derived_text(wording, "", forgotten_id=item.id)
            self._clear_pending_hypothesis(item.id)
            return item

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
        self, query: str, *, allow_index_write: bool = True
    ) -> CaseContextResult:
        if not self._ephemeral:
            policy = self.load_app_state().retention_policy
            if policy != RetentionPolicy():
                self.apply_retention(policy)
        claims = self.list_claims()
        semantic_scores = self._semantic_scores(query, claims, allow_index_write=allow_index_write)
        app_state = self.load_app_state()
        pending_hypothesis_id = app_state.pending_hypothesis_id
        ranked: list[ContextClaim] = []
        for index, item in enumerate(claims):
            semantic = semantic_scores.get(item.id)
            lexical = min(_lexical_score(query, " ".join([item.content, *item.aliases])), 3) / 3
            stale = _is_stale_hypothesis(item)
            pinned = item.id == pending_hypothesis_id or bool(item.conflict_ids)
            relevant = (
                pinned
                or lexical > 0
                or (semantic is not None and semantic >= MIN_SEMANTIC_RELEVANCE)
            )
            if not relevant:
                continue
            if item.origin is ClaimOrigin.AGENT_HYPOTHESIS and item.fit is ClaimFit.DOES_NOT_FIT:
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
        interventions = self._relevant_interventions(
            query,
            app_state.pending_intervention_id,
            allow_index_write=allow_index_write,
        )
        sessions = self._relevant_sessions(query, allow_index_write=allow_index_write)
        excerpts = self._relevant_excerpts(query, allow_index_write=allow_index_write)
        support_choices = self._relevant_support_choices(query, allow_index_write=allow_index_write)
        formulation = self.load_formulation()
        return CaseContextResult(
            accepted_focus=formulation.accepted_focus,
            proposed_focus=formulation.proposed_focus,
            user_reports=user_reports[:20],
            hypotheses=hypotheses[:10],
            conflicts=conflicts[:5],
            relevant_sessions=sessions[:3],
            active_interventions=interventions[:5],
            process_preferences=[
                ContextPreference(id=item.id, content=item.content, evidence=item.evidence)
                for item in self.list_process_preferences()[:10]
            ],
            support_choices=support_choices[:10],
            relevant_excerpts=excerpts[:5],
        )

    def _relevant_support_choices(
        self, query: str, *, allow_index_write: bool
    ) -> list[ContextSupportChoice]:
        items = self.list_support_choices()
        texts = {
            item.id: " ".join(
                value for value in (item.content, item.barrier, item.preference) if value
            )
            for item in items
        }
        scores = self._semantic_text_scores(
            query, "support", texts, allow_index_write=allow_index_write
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
        pending_id: str | None,
        *,
        allow_index_write: bool,
    ) -> list[ContextIntervention]:
        items = self.list_interventions(active_only=True)
        text = {
            item.id: " ".join(
                value
                for value in (
                    item.description,
                    item.prediction,
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
            query, "intervention", text, allow_index_write=allow_index_write
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
                outcome=item.outcome,
                user_appraisal=item.user_appraisal,
                unwanted_effects=item.unwanted_effects,
                decision=item.decision,
                follow_up_information=item.follow_up_information,
                updated_at=item.updated_at,
                semantic_relevance=scores.get(item.id),
            )
            for item in items
            if item.id == pending_id
            or _lexical_score(query, text[item.id]) > 0
            or scores.get(item.id, 0.0) >= MIN_SEMANTIC_RELEVANCE
            or item.unwanted_effects is not None
        ]

    def _relevant_sessions(self, query: str, *, allow_index_write: bool) -> list[ContextSession]:
        sessions = [item for item in self.list_sessions() if item.ended_at]
        texts = {
            item.id: " ".join(
                [
                    item.summary,
                    *item.themes,
                    *item.user_defined_concerns,
                    *item.meaningful_changes,
                    *item.outcomes,
                    *item.unwanted_effects,
                    *item.process_feedback,
                    *item.support_choices,
                    *item.open_questions,
                ]
            )
            for item in sessions
        }
        scores = self._semantic_text_scores(
            query, "session", texts, allow_index_write=allow_index_write
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
            if _lexical_score(query, texts[item.id]) > 0
            or scores.get(item.id, 0.0) >= MIN_SEMANTIC_RELEVANCE
        ]
        if sessions and sessions[0] not in selected:
            selected.append(sessions[0])
        return [
            ContextSession(
                id=item.id,
                ended_at=item.ended_at,
                summary=item.summary,
                themes=item.themes,
                outcomes=item.outcomes,
                unwanted_effects=item.unwanted_effects,
                process_feedback=item.process_feedback,
                support_choices=item.support_choices,
                semantic_relevance=scores.get(item.id),
            )
            for item in selected
        ]

    def _relevant_excerpts(self, query: str, *, allow_index_write: bool) -> list[ContextExcerpt]:
        excluded_message_ids = {
            reference.message_id
            for item in self.list_claims(include_inactive=True)
            if item.lifecycle is not ClaimLifecycle.ACTIVE or item.superseded_content
            for reference in item.evidence
        }
        candidates = self._archive_candidates(excluded_message_ids)
        scores = self._semantic_text_scores(
            query,
            "message",
            {str(item[0]): item[2] for item in candidates},
            allow_index_write=allow_index_write,
        )
        ranked = sorted(
            candidates,
            key=lambda item: (
                _hybrid_text_score(query, item[2], scores.get(str(item[0]))),
                item[0],
            ),
            reverse=True,
        )
        return [
            ContextExcerpt(
                message_id=message_id,
                quote=text,
                created_at=created_at,
                semantic_relevance=scores.get(str(message_id)),
                lexical_relevance=min(_lexical_score(query, text), 3) / 3,
            )
            for message_id, created_at, text in ranked
            if _lexical_score(query, text) > 0
            or scores.get(str(message_id), 0.0) >= MIN_SEMANTIC_RELEVANCE
        ]

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
        with self._connect() as database:
            if policy.raw_message_days:
                cutoff = _iso(current - timedelta(days=policy.raw_message_days))
                message_ids = [
                    row[0]
                    for row in database.execute(
                        "SELECT id FROM messages WHERE created_at < ?", (cutoff,)
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
            "session_summaries": len(summaries),
            "stale_hypotheses": len(hypotheses),
        }
        if dry_run:
            return result
        with self.transaction():
            self._delete_message_ids(message_ids)
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
        return {"sessions": deleted}

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
        }

    def load_secret(self, name: str) -> bytes | None:
        return self._read_state(f"secret:{name}")

    def save_secret(self, name: str, payload: bytes) -> None:
        self._write_state(f"secret:{name}", payload)

    def delete_secret(self, name: str) -> None:
        with self._connect() as database:
            database.execute("DELETE FROM state WHERE name = ?", (f"secret:{name}",))

    def delete_all(self) -> None:
        with self._connect() as database:
            for table in (
                "semantic_index",
                "messages",
                "sessions",
                "memory_items",
                "interventions",
                "process_preferences",
                "support_choices",
                "state",
            ):
                database.execute(f"DELETE FROM {table}")
        self._write_state("schema_version", SCHEMA_VERSION)

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

    def _archive_candidates(self, excluded_message_ids: set[int]) -> list[tuple[int, str, str]]:
        with self._connect() as database:
            rows = database.execute(
                "SELECT id, created_at, payload FROM messages "
                "WHERE role = 'user' ORDER BY id DESC LIMIT 1000"
            ).fetchall()
        return [
            (message_id, created_at, self._decrypt_json(payload).get("content", ""))
            for message_id, created_at, payload in rows
            if message_id not in excluded_message_ids
        ]

    def _semantic_scores(
        self,
        query: str,
        claims: list[MemoryItem],
        *,
        allow_index_write: bool,
    ) -> dict[str, float]:
        return self._semantic_text_scores(
            query,
            "claim",
            {item.id: " ".join([item.content, *item.aliases]) for item in claims},
            allow_index_write=allow_index_write,
        )

    def _semantic_text_scores(
        self,
        query: str,
        entity_type: str,
        texts: dict[str, str],
        *,
        allow_index_write: bool,
    ) -> dict[str, float]:
        if not self._embedder or not self._embedding_model or not query.strip() or not texts:
            return {}
        try:
            expected = {entity_id: self._content_hash(text) for entity_id, text in texts.items()}
            with self._connect() as database:
                rows = database.execute(
                    "SELECT entity_id, content_hash, dimensions, payload FROM semantic_index "
                    "WHERE model = ? AND entity_type = ?",
                    (self._embedding_model, entity_type),
                ).fetchall()
            vectors: dict[str, list[float]] = {}
            for entity_id, content_hash, dimensions, payload in rows:
                vector = self._decrypt_json(payload)["vector"]
                if expected.get(entity_id) == content_hash and len(vector) == dimensions:
                    vectors[entity_id] = vector
            missing = {
                entity_id: text for entity_id, text in texts.items() if entity_id not in vectors
            }
            if missing:
                result = self._embedder.embed_documents_sync(list(missing.values()))
                for entity_id, vector in zip(missing, result.embeddings, strict=True):
                    vectors[entity_id] = list(vector)
                if allow_index_write:
                    with self._connect() as database:
                        for entity_id in missing:
                            stored = vectors[entity_id]
                            database.execute(
                                "INSERT INTO semantic_index(entity_type, entity_id, model, "
                                "content_hash, dimensions, payload) VALUES (?, ?, ?, ?, ?, ?) "
                                "ON CONFLICT(entity_type, entity_id) DO UPDATE SET "
                                "model = excluded.model, content_hash = excluded.content_hash, "
                                "dimensions = excluded.dimensions, payload = excluded.payload",
                                (
                                    entity_type,
                                    entity_id,
                                    self._embedding_model,
                                    expected[entity_id],
                                    len(stored),
                                    self._encrypt_json({"vector": stored}),
                                ),
                            )
            query_vector = list(self._embedder.embed_query_sync(query).embeddings[0])
            return {
                item_id: similarity
                for item_id, vector in vectors.items()
                if (similarity := _cosine_similarity(query_vector, vector)) is not None
            }
        except Exception as error:
            raise MemoryError(
                "Semantic memory is unavailable. Run `filo setup` and try again."
            ) from error

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
        self._delete_semantic_entities("message", [str(item) for item in message_ids])
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
        for item in self.list_support_choices():
            if item.evidence.message_id in message_set:
                with self._connect() as database:
                    database.execute("DELETE FROM support_choices WHERE id = ?", (item.id,))
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
            for field_name in (
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
            ):
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

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._transaction is not None:
            yield
            return
        database = self._ephemeral_database or self._open_connection()
        self._transaction = database
        try:
            with database:
                yield
        finally:
            self._transaction = None
            if not self._ephemeral:
                database.close()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
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
        database = sqlite3.connect(self.database_path, autocommit=False)
        database.execute("PRAGMA foreign_keys = ON")
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


def _tokens(text: str) -> set[str]:
    normalized = text.casefold()
    tokens = {token for token in re.findall(r"\w+", normalized) if len(token) >= 4}
    if any(
        "\u3040" <= character <= "\u30ff"
        or "\u3400" <= character <= "\u9fff"
        or "\u0e00" <= character <= "\u0e7f"
        for character in normalized
    ):
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


def _cosine_similarity(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return None
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


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
    session.themes = []
    session.user_defined_concerns = []
    session.meaningful_changes = []
    session.interventions_discussed = []
    session.tried = []
    session.outcomes = []
    session.unwanted_effects = []
    session.process_feedback = []
    session.support_choices = []
    session.open_questions = []
