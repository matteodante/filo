from datetime import UTC, datetime

from test_longitudinal_memory import _store

from filo.memory import MemoryKind, UserReport


def test_multilingual_meaning_equivalent_retrieval(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session(datetime.now(UTC))
    text = "Le chiamate di lavoro mi mettono in difficoltà"
    message_id = store.save_turn(session, text, "Ti ascolto.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text)],
        message_id,
        text,
    )[0]
    context = store.retrieve_case_context("work phone calls")
    assert context.user_reports[0].id == claim.id
