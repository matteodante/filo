"""The optional WHO-5 wellbeing check.

The five statements, the answer scale and the scoring are reproduced verbatim from the WHO
sources cited in `THIRD_PARTY_NOTICES.md`. The score is shown as a number and a trend, never
with a clinical cutoff or label: this is self-tracking, not screening or diagnosis.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from filo.memory import MemoryStore, WellbeingCheck

OFFER_INTERVAL = timedelta(days=14)
TREND_LENGTH = 6

# The World Health Organization-Five Well-Being Index (WHO-5). Geneva: World Health Organization;
# 2024. License: CC BY-NC-SA 3.0 IGO.
INTRO_EN = (
    "Please indicate for each of the five statements which is closest to how you have been "
    "feeling over the last two weeks. Notice that higher numbers mean better well-being."
)
ITEMS_EN = (
    "I have felt cheerful and in good spirits",
    "I have felt calm and relaxed",
    "I have felt active and vigorous",
    "I woke up feeling fresh and rested",
    "My daily life has been filled with things that interest me",
)
SCALE_EN = (
    "All of the time",
    "Most of the time",
    "More than half of the time",
    "Less than half of the time",
    "Some of the time",
    "At no time",
)
# Italian translation by the Psychiatric Research Unit, WHO Collaborating Centre in Mental
# Health, Frederiksborg General Hospital, published by WHO with the WHO-5. WHO did not create it;
# the English version is the binding one.
INTRO_IT = (
    "Per ciascuna delle cinque affermazioni, la preghiamo di indicare la risposta che più si "
    "avvicina a come si è sentito/a nelle ultime due settimane. I numeri più alti corrispondono "
    "ad un maggior stato di benessere."
)
ITEMS_IT = (
    "Mi sono sentito/a allegro/a e di buon umore",
    "Mi sono sentito/a calmo/a e rilassato/a",
    "Mi sono sentito/a attivo/a ed energico/a",
    "Mi sono svegliato/a sentendomi fresco/a e riposato/a",
    "La mia vita di tutti i giorni è stata piena di cose che mi interessano",
)
SCALE_IT = (
    "Sempre",
    "La maggior parte del tempo",
    "Più della metà del tempo",
    "Meno della metà del tempo",
    "A volte",
    "Mai",
)

_TEXT = {
    "en": {
        "title": "WELLBEING CHECK (WHO-5)",
        "scale": "Answer each statement from 5 to 0:",
        "record": "To record a check, send /wellbeing followed by your five answers, e.g. "
        "/wellbeing 3 4 2 3 4",
        "not_diagnosis": "This is a personal record, not a diagnosis or a health assessment.",
        "enabled": "Reminders: on, at most every two weeks (/wellbeing off to stop).",
        "disabled": "Reminders: off (/wellbeing on for a reminder at most every two weeks).",
        "turned_on": "Wellbeing reminders are on: at most one every two weeks.",
        "turned_off": "Wellbeing reminders are off. Your past checks are kept.",
        "recorded": "Recorded: {raw}/25, or {percentage} out of 100.",
        "trend": "Your recent checks (0 to 100, higher means better well-being):",
        "none": "No checks recorded yet.",
        "invalid": "Send five whole numbers from 0 to 5, one per statement, e.g. "
        "/wellbeing 3 4 2 3 4",
        "invite": "Optional: Filo can keep a short wellbeing check (WHO-5, five statements, "
        "about a minute), with a reminder at most every two weeks. Send /wellbeing to see it "
        "and /wellbeing on to enable reminders. I will not ask again.",
        "remind": "It has been two weeks: /wellbeing shows your optional wellbeing check.",
        "attribution": "WHO-5 © World Health Organization 2024, CC BY-NC-SA 3.0 IGO. "
        "WHO does not endorse Filo.",
    },
    "it": {
        "title": "CONTROLLO DEL BENESSERE (WHO-5)",
        "scale": "Rispondi a ogni affermazione da 5 a 0:",
        "record": "Per registrare un controllo, invia /wellbeing seguito dalle cinque risposte, "
        "per esempio /wellbeing 3 4 2 3 4",
        "not_diagnosis": "È una registrazione personale, non una diagnosi né una valutazione "
        "di salute.",
        "enabled": "Promemoria: attivi, al massimo ogni due settimane (/wellbeing off per "
        "fermarli).",
        "disabled": "Promemoria: spenti (/wellbeing on per un promemoria al massimo ogni due "
        "settimane).",
        "turned_on": "Promemoria attivi: al massimo uno ogni due settimane.",
        "turned_off": "Promemoria spenti. I controlli già registrati restano.",
        "recorded": "Registrato: {raw}/25, cioè {percentage} su 100.",
        "trend": "I tuoi ultimi controlli (da 0 a 100, più alto vuol dire più benessere):",
        "none": "Nessun controllo registrato finora.",
        "invalid": "Invia cinque numeri interi da 0 a 5, uno per affermazione, per esempio "
        "/wellbeing 3 4 2 3 4",
        "invite": "Facoltativo: Filo può tenere un breve controllo del benessere (WHO-5, cinque "
        "affermazioni, circa un minuto), con un promemoria al massimo ogni due settimane. Invia "
        "/wellbeing per vederlo e /wellbeing on per attivare i promemoria. Non te lo chiederò "
        "di nuovo.",
        "remind": "Sono passate due settimane: /wellbeing mostra il tuo controllo facoltativo del "
        "benessere.",
        "attribution": "WHO-5 © World Health Organization 2024, CC BY-NC-SA 3.0 IGO. Traduzione "
        "italiana: Psychiatric Research Unit, WHO Collaborating Centre in Mental Health, "
        "Frederiksborg General Hospital; non realizzata dall'OMS. L'OMS non approva Filo.",
    },
}


def _language(locale: str) -> str:
    return "it" if locale.lower().startswith("it") else "en"


def parse_answers(argument: str) -> list[int] | None:
    """Five answers from 0 to 5, separated by spaces or commas or written as five digits."""
    parts = re.split(r"[\s,;]+", argument.strip())
    if len(parts) == 1 and re.fullmatch(r"[0-5]{5}", parts[0]):
        parts = list(parts[0])
    if len(parts) != 5 or not all(re.fullmatch(r"[0-5]", part) for part in parts):
        return None
    return [int(part) for part in parts]


def trend_lines(checks: list[WellbeingCheck], locale: str) -> list[str]:
    text = _TEXT[_language(locale)]
    if not checks:
        return [text["none"]]
    lines = [text["trend"]]
    recent = checks[-TREND_LENGTH:]
    previous = checks[-TREND_LENGTH - 1] if len(checks) > TREND_LENGTH else None
    for check in recent:
        change = f" ({check.percentage - previous.percentage:+d})" if previous is not None else ""
        lines.append(f"• {check.created_at[:10]} · {check.id}: {check.percentage}{change}")
        previous = check
    return lines


def render_form(locale: str) -> str:
    language = _language(locale)
    text = _TEXT[language]
    intro, items, scale = (
        (INTRO_IT, ITEMS_IT, SCALE_IT) if language == "it" else (INTRO_EN, ITEMS_EN, SCALE_EN)
    )
    lines = [text["title"], intro, ""]
    lines += [f"{index}. {item}" for index, item in enumerate(items, start=1)]
    lines += ["", text["scale"]]
    lines += [f"{5 - index} = {label}" for index, label in enumerate(scale)]
    return "\n".join(lines)


def wellbeing_command(
    argument: str | None,
    *,
    checks: MemoryStore,
    settings: MemoryStore,
    locale: str,
    now: datetime | None = None,
) -> str:
    """Handle /wellbeing, /wellbeing on|off, and /wellbeing with five answers.

    ``checks`` holds the records (the conversation store, in memory in ephemeral mode) and
    ``settings`` the opt-in (the configuration store).
    """
    text = _TEXT[_language(locale)]
    argument = (argument or "").strip().lower()
    if argument in {"on", "off"}:
        state = settings.load_app_state()
        state.wellbeing_enabled = argument == "on"
        state.wellbeing_invited = True
        settings.save_app_state(state)
        return text["turned_on" if argument == "on" else "turned_off"]
    if argument:
        answers = parse_answers(argument)
        if answers is None:
            return text["invalid"]
        check = checks.record_wellbeing_check(answers, now)
        return "\n".join(
            [
                text["recorded"].format(raw=check.raw_score, percentage=check.percentage),
                "",
                *trend_lines(checks.list_wellbeing_checks(), locale),
                "",
                text["not_diagnosis"],
            ]
        )
    enabled = settings.load_app_state().wellbeing_enabled
    return "\n".join(
        [
            render_form(locale),
            "",
            text["record"],
            "",
            *trend_lines(checks.list_wellbeing_checks(), locale),
            "",
            text["enabled" if enabled else "disabled"],
            text["not_diagnosis"],
            text["attribution"],
        ]
    )


def wellbeing_offer(
    *,
    checks: MemoryStore,
    settings: MemoryStore,
    locale: str,
    now: datetime | None = None,
) -> str | None:
    """A notice to show at a calm moment, or None.

    The check is offered unprompted once. After that it is offered only when the person turned
    reminders on, and at most every two weeks counting from both the last offer and the last check.
    """
    now = now or datetime.now(UTC)
    text = _TEXT[_language(locale)]
    state = settings.load_app_state()
    if not state.wellbeing_invited:
        state.wellbeing_invited = True
        state.wellbeing_offered_at = now.isoformat()
        settings.save_app_state(state)
        return text["invite"]
    if not state.wellbeing_enabled:
        return None
    recorded = checks.list_wellbeing_checks()
    last = max(
        [
            datetime.fromisoformat(value)
            for value in (
                state.wellbeing_offered_at,
                recorded[-1].created_at if recorded else None,
            )
            if value
        ],
        default=None,
    )
    if last is not None and now - last < OFFER_INTERVAL:
        return None
    state.wellbeing_offered_at = now.isoformat()
    settings.save_app_state(state)
    return text["remind"]
