from datetime import UTC, datetime, timedelta

from test_longitudinal_memory import _store

from filo.memory import MemoryKind, UserReport


def test_semantic_index_is_encrypted_reused_and_reindexed_after_correction(tmp_path) -> None:
    store = _store(tmp_path)
    session = store.start_session(datetime.now(UTC) - timedelta(days=5))
    text = "Le telefonate con il responsabile mi pesano"
    message_id = store.save_turn(session, text, "Capisco.", [])
    claim = store.add_user_reports(
        [UserReport(kind=MemoryKind.EVENT, content=text, evidence_quote=text)],
        message_id,
        text,
    )[0]
    assert store.retrieve_case_context("a difficult call").user_reports[0].id == claim.id
    assert text.encode() not in store.database_path.read_bytes()
