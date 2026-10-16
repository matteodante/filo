"""Forward schema migrations: ordered steps, a verified recovery copy, and unknown versions."""

import sqlite3
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from filo import memory
from filo.memory import MemoryError, MemoryStore


def _old_store(directory: Path) -> MemoryStore:
    """A filo-1 store holding one archived turn."""
    store = MemoryStore(directory)
    session = store.start_session()
    store.save_turn(session, "migration-marker user turn", "migration-marker reply", [])
    return store


def _next_generation(monkeypatch: pytest.MonkeyPatch, step) -> None:
    monkeypatch.setattr(memory, "SCHEMA_GENERATION", 2)
    monkeypatch.setattr(memory, "SCHEMA_VERSION", b"filo-2")
    monkeypatch.setattr(memory, "MIGRATIONS", {1: step})


def _messages(store: MemoryStore) -> list[str]:
    return [message["content"] for message in store.export()["messages"]]


def test_older_store_is_migrated_and_keeps_its_memory(tmp_path, monkeypatch) -> None:
    _old_store(tmp_path)

    def add_column(store: MemoryStore, database: sqlite3.Connection) -> None:
        database.execute("ALTER TABLE sessions ADD COLUMN migrated INTEGER NOT NULL DEFAULT 1")
        # A step can rewrite encrypted payloads through the store it receives.
        store._write_state("migrated", b"yes")

    _next_generation(monkeypatch, add_column)

    store = MemoryStore(tmp_path)

    assert _messages(store) == ["migration-marker user turn", "migration-marker reply"]
    assert store._read_state("schema_version") == b"filo-2"
    assert store._read_state("migrated") == b"yes"
    with sqlite3.connect(store.database_path) as database:
        columns = {row[1] for row in database.execute("PRAGMA table_info(sessions)")}
    assert "migrated" in columns
    assert not store.migration_backup_path.exists()
    # Opening again runs no step.
    _next_generation(monkeypatch, lambda store, database: pytest.fail("ran twice"))
    MemoryStore(tmp_path)


def test_failing_step_leaves_the_store_unchanged(tmp_path, monkeypatch) -> None:
    store = _old_store(tmp_path)
    before = store.export()

    def broken(store: MemoryStore, database: sqlite3.Connection) -> None:
        database.execute("DELETE FROM messages")
        raise RuntimeError("step failed")

    _next_generation(monkeypatch, broken)
    with pytest.raises(MemoryError, match="left unchanged"):
        MemoryStore(tmp_path)

    assert not store.migration_backup_path.exists()
    monkeypatch.undo()
    assert MemoryStore(tmp_path).export() == before


def test_migrated_store_failing_verification_is_rolled_back(tmp_path, monkeypatch) -> None:
    store = _old_store(tmp_path)
    before = store.export()

    def orphan(store: MemoryStore, database: sqlite3.Connection) -> None:
        database.execute(
            "INSERT INTO messages(session_id, role, created_at, payload) "
            "VALUES ('missing', 'user', '2026-01-01', x'00')"
        )

    _next_generation(monkeypatch, orphan)
    with pytest.raises(MemoryError, match="left unchanged"):
        MemoryStore(tmp_path)

    assert not store.migration_backup_path.exists()
    monkeypatch.undo()
    assert MemoryStore(tmp_path).export() == before


def test_recovery_copy_is_private_encrypted_and_consistent(tmp_path, monkeypatch) -> None:
    store = _old_store(tmp_path)
    seen: dict[str, object] = {}

    def inspect(store: MemoryStore, database: sqlite3.Connection) -> None:
        backup = store.migration_backup_path
        seen["mode"] = stat.S_IMODE(backup.stat().st_mode)
        seen["plaintext"] = b"migration-marker" in backup.read_bytes()
        copy = sqlite3.connect(backup)
        seen["messages"] = copy.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        copy.close()
        # A concurrent writer cannot slip a change between the copy and the steps.
        writer = sqlite3.connect(store.database_path, timeout=0.1)
        try:
            writer.execute("DELETE FROM messages")
        except sqlite3.OperationalError as error:
            seen["writer"] = str(error)
        finally:
            writer.close()

    _next_generation(monkeypatch, inspect)
    MemoryStore(tmp_path)

    assert seen == {
        "mode": 0o600,
        "plaintext": False,
        "messages": 2,
        "writer": "database is locked",
    }
    assert _messages(MemoryStore(tmp_path)) == [
        "migration-marker user turn",
        "migration-marker reply",
    ]
    assert not store.migration_backup_path.exists()


def _open_in_thread(directory: Path, outcomes: list[object]) -> threading.Thread:
    def run() -> None:
        try:
            MemoryStore(directory)
            outcomes.append("opened")
        except Exception as error:  # recorded, so the test asserts on it
            outcomes.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    return thread


def test_second_opener_waits_for_a_migration_and_runs_no_step(tmp_path, monkeypatch) -> None:
    _old_store(tmp_path)
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(store: MemoryStore, database: sqlite3.Connection) -> None:
        calls.append(1)
        started.set()
        release.wait(5)

    _next_generation(monkeypatch, slow)
    first: list[object] = []
    second: list[object] = []
    first_thread = _open_in_thread(tmp_path, first)
    assert started.wait(5)
    second_thread = _open_in_thread(tmp_path, second)
    time.sleep(0.5)  # long enough for the second opener to block on the write lock
    assert second == []
    release.set()
    first_thread.join(10)
    second_thread.join(10)

    assert (first, second, calls) == (["opened"], ["opened"], [1])
    assert MemoryStore(tmp_path)._read_state("schema_version") == b"filo-2"


def test_older_filo_opening_mid_migration_is_rejected_and_keeps_the_copy(
    tmp_path, monkeypatch
) -> None:
    """A process running the previous release must not act on the marker it read before."""
    _old_store(tmp_path)
    started = threading.Event()
    release = threading.Event()
    copy_present = []

    def slow(store: MemoryStore, database: sqlite3.Connection) -> None:
        started.set()
        release.wait(10)
        copy_present.append(store.migration_backup_path.exists())

    _next_generation(monkeypatch, slow)
    outcomes: list[object] = []
    migrating = _open_in_thread(tmp_path, outcomes)
    assert started.wait(5)
    older = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; from pathlib import Path; from filo.memory import MemoryStore\n"
            "try:\n    MemoryStore(Path(sys.argv[1]))\nexcept Exception as error:\n"
            "    print(error)",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    time.sleep(1.0)  # the older process is now waiting on the write lock
    release.set()
    migrating.join(10)
    output, _ = older.communicate(timeout=20)

    assert outcomes == ["opened"]
    assert copy_present == [True]
    assert "unknown or newer schema" in output


def test_older_store_in_a_relative_directory_is_migrated(tmp_path, monkeypatch) -> None:
    _old_store(tmp_path / "data")
    monkeypatch.chdir(tmp_path)
    _next_generation(monkeypatch, lambda store, database: None)

    store = MemoryStore(Path("data"))

    assert store._read_state("schema_version") == b"filo-2"
    assert not (tmp_path / "data" / "filo.db.pre-migration.partial").exists()


def test_leftover_recovery_copy_of_a_verified_store_is_deleted(tmp_path) -> None:
    store = _old_store(tmp_path)
    store.migration_backup_path.write_bytes(store.database_path.read_bytes())

    MemoryStore(tmp_path)

    assert not store.migration_backup_path.exists()


def test_newer_store_is_rejected_without_modification(tmp_path) -> None:
    store = _old_store(tmp_path)
    store._write_state("schema_version", b"filo-2")
    before = store.database_path.read_bytes()

    with pytest.raises(MemoryError, match="unknown or newer schema"):
        MemoryStore(tmp_path)

    assert store.database_path.read_bytes() == before


def test_delete_all_removes_a_surviving_recovery_copy(tmp_path) -> None:
    store = _old_store(tmp_path)
    partial = tmp_path / "filo.db.pre-migration.partial"
    store.migration_backup_path.write_bytes(store.database_path.read_bytes())
    partial.write_bytes(b"partial")

    store.delete_all()

    assert not store.migration_backup_path.exists()
    assert not partial.exists()


def test_plain_connections_enforce_foreign_keys(tmp_path) -> None:
    store = MemoryStore(tmp_path)
    with store._connect() as database:
        assert database.execute("PRAGMA foreign_keys").fetchone() == (1,)
