"""A recap of recent weeks, drawn only from stored records and linked to them by ID.

Nothing here is generated: every line comes from a session summary, a claim, an intervention, or a
wellbeing check, so a recap cannot say more than the memory holds.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from filo.memory import (
    ClaimFit,
    ClaimLifecycle,
    ClaimOrigin,
    InterventionState,
    MemoryStore,
)

DEFAULT_RECAP_WEEKS = 4
MAX_RECAP_WEEKS = 52
MAX_ITEMS = 10


class RecapSession(BaseModel):
    id: str
    started_at: str
    summary: str
    themes: list[str]


class RecapTheme(BaseModel):
    theme: str
    session_ids: list[str]


class RecapExperiment(BaseModel):
    id: str
    description: str
    state: InterventionState
    planned_for: str | None
    prediction: str | None
    outcome: str | None
    user_appraisal: str | None
    unwanted_effects: str | None
    updated_at: str


class RecapClaim(BaseModel):
    id: str
    content: str
    fit: ClaimFit
    at: str
    evidence_message_ids: list[int]


class RecapWellbeing(BaseModel):
    id: str
    at: str
    raw_score: int
    percentage: int


class Recap(BaseModel):
    weeks: int = Field(ge=1, le=MAX_RECAP_WEEKS)
    since: str
    sessions: list[RecapSession]
    recurring_themes: list[RecapTheme]
    experiments: list[RecapExperiment]
    new_reports: list[RecapClaim]
    reviewed_hypotheses: list[RecapClaim]
    corrected: list[RecapClaim]
    wellbeing: list[RecapWellbeing]


def build_recap(store: MemoryStore, weeks: int, now: datetime | None = None) -> Recap:
    weeks = max(1, min(weeks, MAX_RECAP_WEEKS))
    since = ((now or datetime.now(UTC)) - timedelta(weeks=weeks)).isoformat()
    sessions = [
        item
        for item in store.list_sessions()
        if (item.ended_at or item.last_activity_at) >= since and (item.summary or item.themes)
    ]
    sessions.sort(key=lambda item: item.started_at)
    theme_sessions: dict[str, list[str]] = {}
    for session in sessions:
        for theme in dict.fromkeys(session.themes):
            theme_sessions.setdefault(theme, []).append(session.id)
    recurring = Counter({theme: len(ids) for theme, ids in theme_sessions.items()})
    claims = store.list_claims(include_inactive=True)

    def recap_claim(item, at: str) -> RecapClaim:
        return RecapClaim(
            id=item.id,
            content=item.content,
            fit=item.fit,
            at=at,
            evidence_message_ids=[reference.message_id for reference in item.evidence],
        )

    return Recap(
        weeks=weeks,
        since=since,
        sessions=[
            RecapSession(
                id=item.id, started_at=item.started_at, summary=item.summary, themes=item.themes
            )
            for item in sessions[-MAX_ITEMS:]
        ],
        recurring_themes=[
            RecapTheme(theme=theme, session_ids=theme_sessions[theme])
            for theme, count in recurring.most_common(MAX_ITEMS)
            if count > 1
        ],
        experiments=[
            RecapExperiment.model_validate(item, from_attributes=True)
            for item in sorted(store.list_interventions(), key=lambda item: item.updated_at)
            if item.updated_at >= since
        ][-MAX_ITEMS:],
        new_reports=[
            recap_claim(item, item.first_seen_at)
            for item in claims
            if item.origin is ClaimOrigin.USER_STATEMENT
            and item.lifecycle is ClaimLifecycle.ACTIVE
            and item.first_seen_at >= since
        ][-MAX_ITEMS:],
        reviewed_hypotheses=[
            recap_claim(item, item.last_reviewed_at)
            for item in claims
            if item.origin is ClaimOrigin.AGENT_HYPOTHESIS
            and item.last_reviewed_at
            and item.last_reviewed_at >= since
        ][-MAX_ITEMS:],
        corrected=[
            recap_claim(item, item.superseded_at)
            for item in claims
            if item.superseded_at and item.superseded_at >= since
        ][-MAX_ITEMS:],
        wellbeing=[
            RecapWellbeing(
                id=item.id,
                at=item.created_at,
                raw_score=item.raw_score,
                percentage=item.percentage,
            )
            for item in store.list_wellbeing_checks()
            if item.created_at >= since
        ][-MAX_ITEMS:],
    )


def render_recap(recap: Recap) -> str:
    lines = [f"RECAP · last {recap.weeks} week{'s' if recap.weeks != 1 else ''}"]
    if not any(
        (
            recap.sessions,
            recap.experiments,
            recap.new_reports,
            recap.reviewed_hypotheses,
            recap.corrected,
            recap.wellbeing,
        )
    ):
        lines.append("Nothing recorded in this period yet.")
        return "\n".join(lines)
    if recap.recurring_themes:
        lines.append("\nRecurring themes")
        lines += [
            f"• {item.theme} ({len(item.session_ids)} sessions: {', '.join(item.session_ids)})"
            for item in recap.recurring_themes
        ]
    if recap.sessions:
        lines.append("\nSessions")
        lines += [
            f"• {item.started_at[:10]} · {item.id}: {item.summary or ', '.join(item.themes)}"
            for item in recap.sessions
        ]
    if recap.experiments:
        lines.append("\nWhat you tried")
        for item in recap.experiments:
            details = [f"• {item.id} · {item.state.value}: {item.description}"]
            for label, value in (
                ("planned for", item.planned_for),
                ("expected", item.prediction),
                ("outcome", item.outcome),
                ("your view", item.user_appraisal),
                ("unwanted effects", item.unwanted_effects),
            ):
                if value:
                    details.append(f"  {label}: {value}")
            lines += details
    for title, items in (
        ("New things you told me", recap.new_reports),
        ("Readings you reviewed", recap.reviewed_hypotheses),
        ("Corrected", recap.corrected),
    ):
        if items:
            lines.append(f"\n{title}")
            lines += [
                f"• {item.at[:10]} · {item.id}: {item.content}"
                + (f" ({item.fit.value})" if title == "Readings you reviewed" else "")
                for item in items
            ]
    if recap.wellbeing:
        lines.append("\nWellbeing checks (WHO-5, 0 to 100)")
        lines += [f"• {item.at[:10]}: {item.percentage}" for item in recap.wellbeing]
    return "\n".join(lines)
