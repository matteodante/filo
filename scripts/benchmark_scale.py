"""Maintainer benchmark (W0.6): scale and latency on a generated three-year archive.

Writes an encrypted on-disk store of template-generated sessions (no model call; all content is
invented), indexes it through ordinary retrievals with the real local embedding model, and
reports at a few archive sizes:

- write and indexing time, and database size;
- store open time;
- envelope build latency per message (retrieval plus bounded JSON), cold after opening and warm;
- the envelope's size and item counts, to check that context stays bounded.

Requires the model from `filo setup`; nothing is sent over the network. Defaults are 150 sessions
of about 30 rounds each over three years.

    HF_HUB_OFFLINE=1 uv run python scripts/benchmark_scale.py [--sessions 150] [--rounds 30]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filo.chat import MAX_CONTEXT_CHARS, _bounded_context_json
from filo.cli import DEFAULT_EMBEDDING_MODEL, _default_embedder
from filo.memory import MemoryKind, MemoryStore, UserReport

START = datetime(2023, 10, 1, 19, tzinfo=UTC)
PEOPLE = [
    "Giulia",
    "Marco",
    "Paolo",
    "Luca",
    "Anna",
    "Sara",
    "my sister",
    "my manager",
    "mia madre",
]
TOPICS = {
    "work": [
        "I postponed the call with {person} about the report again",
        "Oggi in ufficio {person} mi ha chiesto il progetto entro {day}",
        "The meeting on {day} went badly and I said nothing",
    ],
    "sleep": [
        "I woke up at {hour} and couldn't fall back asleep",
        "Stanotte ho dormito {number} ore",
        "I stayed up until {hour} scrolling on my phone",
    ],
    "family": [
        "{person} called on {day} and we argued about money",
        "A pranzo con {person} ho evitato di parlare del lavoro",
        "I visited {person} at the hospital for {number} hours",
    ],
    "health": [
        "I ran {number} kilometres in the park",
        "Ho saltato la palestra per la {number}a volta",
        "My back hurt all of {day}",
    ],
    "money": [
        "The rent went up by {number} euros",
        "Ho speso {number} euro senza pensarci",
        "I paid the credit card bill late on {day}",
    ],
    "friends": [
        "{person} cancelled our dinner on {day}",
        "Sono uscito con {person} e mi sono divertito",
        "I didn't answer {person}'s messages for {number} days",
    ],
}
REPLIES = [
    "What happened right before?",
    "Come ti sei sentito dopo?",
    "That sounds like a lot to carry.",
    "Cosa ti ha colpito di più?",
    "Which part of it stays with you now?",
]
QUERIES = [
    "Ho di nuovo rimandato la telefonata con il capo",
    "I can't sleep and I'm exhausted",
    "My sister and I fought again",
    "Ho ricominciato a correre",
    "I'm worried about money this month",
    "A friend let me down",
    "Mi sento solo la sera",
    "The report is late and I feel guilty",
    "Non riesco a dire di no a mia madre",
    "My back is better since I walk more",
]
DAYS = ["Monday", "Tuesday", "Friday", "sabato", "domenica", "lunedì"]


def _round(rng: random.Random, topic: str) -> tuple[str, str]:
    template = rng.choice(TOPICS[topic])
    text = template.format(
        person=rng.choice(PEOPLE),
        day=rng.choice(DAYS),
        hour=f"{rng.randint(1, 5)}am",
        number=rng.randint(2, 300),
    )
    detail = rng.choice(["", " and it bothered me", ", non so perché", " again this week"])
    return f"{text}{detail}.", rng.choice(REPLIES)


def write_sessions(store: MemoryStore, rng: random.Random, first: int, last: int, args) -> int:
    rounds = 0
    span = timedelta(days=3 * 365) / args.sessions
    for index in range(first, last):
        when = START + span * index
        session = store.start_session(when)
        topics = rng.sample(sorted(TOPICS), 2)
        count = max(1, round(rng.gauss(args.rounds, args.rounds / 3)))
        for turn in range(count):
            user, assistant = _round(rng, topics[turn % 2])
            message_id = store.save_turn(
                session, user, assistant, [], when + timedelta(minutes=2 * turn)
            )
            if turn == 0 and index % 3 == 0:
                store.add_user_reports(
                    [UserReport(kind=MemoryKind.PATTERN, content=user, evidence_quote=user)],
                    message_id,
                    user,
                    when,
                )
        store.close_session(
            session,
            summary=f"Talked about {topics[0]} and {topics[1]}.",
            themes=topics,
            now=when + timedelta(minutes=2 * count + 5),
        )
        rounds += count
    return rounds


def index_all(store: MemoryStore, now: datetime) -> tuple[float, int]:
    """Index every round through ordinary retrievals, which embed at most 64 new rounds each."""
    started = time.perf_counter()
    calls = 0
    while True:
        with store._connect() as database:
            (pending,) = database.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'user' AND CAST(id AS TEXT) NOT IN "
                "(SELECT entity_id FROM semantic_index WHERE entity_type = 'round')"
            ).fetchone()
        if not pending:
            return time.perf_counter() - started, calls
        store.retrieve_case_context("index the archive", now=now)
        calls += 1


def envelope(store: MemoryStore, query: str, now: datetime) -> tuple[float, str, object]:
    started = time.perf_counter()
    context = store.retrieve_case_context(query, now=now)
    payload = _bounded_context_json(context)
    return time.perf_counter() - started, payload, context


def _size(directory: Path) -> float:
    return sum(path.stat().st_size for path in directory.glob("filo.db*")) / 1e6


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=150)
    parser.add_argument("--rounds", type=int, default=30, help="mean rounds per session")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    started = time.perf_counter()
    embedder = _default_embedder(local_files_only=True)
    embedder.embed_query_sync("load the model")
    print(f"model load and first embedding: {time.perf_counter() - started:.1f}s")
    timings = []
    for query in QUERIES * 2:
        started = time.perf_counter()
        embedder.embed_query_sync(query)
        timings.append(time.perf_counter() - started)
    print(f"query embedding alone: p50 {statistics.median(timings) * 1e3:.0f} ms")

    checkpoints = sorted({max(1, args.sessions // 15), args.sessions // 3, args.sessions})
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory)
        store = MemoryStore(path, embedding_model=DEFAULT_EMBEDDING_MODEL, embedder=embedder)
        written = rounds = 0
        print(
            "\nsessions rounds claims | write s | index s rounds/s | db MB | open ms | "
            "cold ms | warm p50 p95 ms | envelope chars (bound) | "
            "excerpts, sessions, claims: raw>sent"
        )
        for checkpoint in checkpoints:
            started = time.perf_counter()
            new_rounds = write_sessions(store, rng, written, checkpoint, args)
            write_seconds = time.perf_counter() - started
            rounds += new_rounds
            written = checkpoint
            now = START + timedelta(days=3 * 365 + 1)
            index_seconds, _calls = index_all(store, now)
            size = _size(path)
            del store
            opens = []
            for _ in range(3):
                started = time.perf_counter()
                store = MemoryStore(
                    path, embedding_model=DEFAULT_EMBEDDING_MODEL, embedder=embedder
                )
                opens.append(time.perf_counter() - started)
            cold, _payload, _context = envelope(store, QUERIES[0], now)
            warm = []
            for query in QUERIES * 2:
                seconds, payload, context = envelope(store, query, now)
                warm.append(seconds)
            raw = len(context.model_dump_json())  # type: ignore[attr-defined]
            bounded = json.loads(payload)
            claims = len(store.list_claims())
            print(
                f"{checkpoint:8} {rounds:6} {claims:6} | {write_seconds:7.1f} | "
                f"{index_seconds:7.1f} {new_rounds / index_seconds:8.1f} | {size:5.1f} | "
                f"{statistics.median(opens) * 1e3:7.0f} | {cold * 1e3:7.0f} | "
                f"{statistics.median(warm) * 1e3:8.0f} "
                f"{statistics.quantiles(warm, n=20)[-1] * 1e3:4.0f} | "
                f"{len(payload):6} raw {raw:6} ({MAX_CONTEXT_CHARS}) | "
                + " ".join(
                    f"{len(context.__dict__[name])}>{len(bounded[name])}"
                    for name in ("relevant_excerpts", "relevant_sessions", "user_reports")
                )
            )


if __name__ == "__main__":
    main()
