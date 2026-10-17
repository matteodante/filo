"""Maintainer benchmark (W0.6): recall@k of production retrieval with the real embedding model.

Builds an in-memory store from the synthetic labeled set in `tests/cases/retrieval_benchmark.yaml`
(topic sessions with their rounds, summaries and some claims, plus near misses and filler), asks
every labeled query through `MemoryStore.retrieve_case_context`, and reports, for claims, rounds
(excerpts) and sessions:

- hit@k and recall@k of what the envelope actually returns, after the relevance floor and the
  per-type cut;
- the same for raw cosine ranking over every item, which isolates the embedding model;
- for each candidate floor, the share of relevant pairs kept and of irrelevant pairs let through.

It also replays `tests/cases/multilingual_semantic_memory.yaml` as rounds, one fresh store per
case. All data is invented. Requires the model from `filo setup`; nothing is sent over the network.

    HF_HUB_OFFLINE=1 uv run python scripts/benchmark_retrieval.py
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from filo.cli import DEFAULT_EMBEDDING_MODEL, _default_embedder
from filo.memory import MIN_SEMANTIC_RELEVANCE, MemoryKind, MemoryStore, UserReport

CASES = Path(__file__).resolve().parents[1] / "tests" / "cases"
START = datetime(2025, 10, 1, 18, tzinfo=UTC)
FLOORS = (0.30, 0.35, 0.40, 0.45, 0.50)


def _store(embedder: object) -> MemoryStore:
    return MemoryStore(
        ephemeral=True,
        embedding_model=DEFAULT_EMBEDDING_MODEL,
        embedder=embedder,  # type: ignore[arg-type]
    )


def _session(store: MemoryStore, rounds: list[dict[str, str]], summary: str, when: datetime):
    session = store.start_session(when)
    ids = [
        store.save_turn(session, item["user"], item["assistant"], [], when + timedelta(minutes=n))
        for n, item in enumerate(rounds)
    ]
    store.close_session(session, summary=summary, now=when + timedelta(hours=1))
    return session.id, ids


def build(store: MemoryStore, data: dict) -> dict[str, dict[str, set[str]]]:
    """Write the labeled archive and return, per topic, its relevant IDs by entity type."""
    filler = [
        {"user": f"{subject} {detail}", "assistant": data["filler"]["assistant"]}
        for subject, detail in itertools.product(
            data["filler"]["subjects"], data["filler"]["details"]
        )
    ]
    distractors = iter([*data["near_misses"], *filler])
    labels: dict[str, dict[str, set[str]]] = {}
    when = START
    for topic in data["topics"]:
        session_id, round_ids = _session(store, topic["rounds"], topic["summary"], when)
        labels[topic["id"]] = {
            "session": {session_id},
            "round": {str(item) for item in round_ids},
            "claim": set(),
        }
        if claim := topic.get("claim"):
            (item,) = store.add_user_reports(
                [
                    UserReport(
                        kind=MemoryKind(claim["kind"]),
                        content=claim["quote"],
                        evidence_quote=claim["quote"],
                    )
                ],
                round_ids[0],
                topic["rounds"][0]["user"],
                when,
            )
            labels[topic["id"]]["claim"] = {item.id}
        when += timedelta(days=10)
        # Small talk between topics; the last session is small talk too, so no labeled session
        # is the always-present latest one.
        _session(store, [next(distractors), next(distractors)], "Small talk.", when)
        when += timedelta(days=10)
    rest = list(distractors)
    while rest:
        _session(store, rest[:4], "Small talk.", when)
        rest = rest[4:]
        when += timedelta(days=3)
    return labels


def _returned(context: object) -> dict[str, list[str]]:
    return {
        "claim": [item.id for item in context.user_reports],  # type: ignore[attr-defined]
        "round": [str(item.message_id) for item in context.relevant_excerpts],  # type: ignore[attr-defined]
        "session": [item.id for item in context.relevant_sessions],  # type: ignore[attr-defined]
    }


def _rank_metrics(ranked: list[str], relevant: set[str], ks: tuple[int, ...]) -> dict[str, float]:
    result: dict[str, float] = {}
    for k in ks:
        top = set(ranked[:k])
        result[f"hit@{k}"] = float(bool(top & relevant))
        result[f"recall@{k}"] = len(top & relevant) / len(relevant)
    return result


def _mean(rows: list[dict[str, float]]) -> dict[str, float]:
    return {key: float(np.mean([row[key] for row in rows])) for key in rows[0]} if rows else {}


def _report(title: str, rows: dict[str, list[dict[str, float]]]) -> None:
    print(f"\n{title}")
    for entity, values in rows.items():
        cells = ", ".join(f"{key} {value:.2f}" for key, value in _mean(values).items())
        print(f"  {entity:8} n={len(values):3}  {cells}")


def benchmark(embedder: object, data: dict) -> None:
    store = _store(embedder)
    labels = build(store, data)
    now = START + timedelta(days=400)
    # Retrieval embeds at most 64 new rounds per call, so index everything before measuring.
    for _ in range(4):
        store.retrieve_case_context("warm up", now=now)
    ks = (1, 3, 5)
    envelope: dict[str, list[dict[str, float]]] = {"claim": [], "round": [], "session": []}
    raw: dict[str, list[dict[str, float]]] = {"claim": [], "round": [], "session": []}
    noise: dict[str, list[int]] = {"claim": [], "round": [], "session": []}
    pairs: dict[str, tuple[list[float], list[float]]] = {
        entity: ([], []) for entity in ("claim", "round", "session")
    }
    excerpt_by_claim: dict[bool, list[dict[str, float]]] = {True: [], False: []}
    for topic in data["topics"]:
        relevant = labels[topic["id"]]
        for query in topic["queries"]:
            context = store.retrieve_case_context(query, now=now)
            returned = _returned(context)
            vector = store._query_vector(query)
            for entity in envelope:
                if not relevant[entity]:
                    continue
                envelope[entity].append(_rank_metrics(returned[entity], relevant[entity], ks))
                noise[entity].append(len(set(returned[entity]) - relevant[entity]))
                scores = store._vector_tables[entity].scores(vector)
                ranked = sorted(scores, key=scores.__getitem__, reverse=True)
                raw[entity].append(_rank_metrics(ranked, relevant[entity], ks))
                for entity_id, score in scores.items():
                    pairs[entity][entity_id not in relevant[entity]].append(score)
            excerpt_by_claim[bool(relevant["claim"])].append(envelope["round"][-1])
    _report("Envelope (production retrieval, after floor and cut)", envelope)
    for entity, values in noise.items():
        print(f"  {entity:8} irrelevant items returned per query: {np.mean(values):.2f}")
    print(
        "  rounds of topics with a claim:    "
        + ", ".join(f"{k} {v:.2f}" for k, v in _mean(excerpt_by_claim[True]).items())
    )
    print(
        "  rounds of topics without a claim: "
        + ", ".join(f"{k} {v:.2f}" for k, v in _mean(excerpt_by_claim[False]).items())
    )
    _report("Raw cosine ranking over every item", raw)
    print(f"\nRelevance floor (production value {MIN_SEMANTIC_RELEVANCE:.2f})")
    for entity, (related, unrelated) in pairs.items():
        print(f"  {entity}: {len(related)} relevant pairs, {len(unrelated)} irrelevant pairs")
        for floor in FLOORS:
            kept = np.mean(np.asarray(related) >= floor)
            through = np.mean(np.asarray(unrelated) >= floor)
            print(
                f"    floor {floor:.2f}: relevant kept {kept:.1%}, irrelevant through {through:.1%}"
            )


def multilingual(embedder: object) -> None:
    cases = yaml.safe_load((CASES / "multilingual_semantic_memory.yaml").read_text())["cases"]
    first = 0
    above_floor = 0
    for case in cases:
        store = _store(embedder)
        inputs = case["inputs"]
        _session(
            store,
            [{"user": text, "assistant": "Ok."} for text in [*inputs["distractors"]]],
            "",
            START,
        )
        _, (memory_id,) = _session(
            store, [{"user": inputs["memory"], "assistant": "Ok."}], "", START + timedelta(days=1)
        )
        _session(store, [{"user": "Ciao.", "assistant": "Ciao."}], "", START + timedelta(days=2))
        context = store.retrieve_case_context(inputs["query"], now=START + timedelta(days=3))
        excerpts = context.relevant_excerpts
        first += bool(excerpts) and excerpts[0].message_id == memory_id
        score = next(
            (item.semantic_relevance for item in excerpts if item.message_id == memory_id), None
        )
        above_floor += score is not None and score >= MIN_SEMANTIC_RELEVANCE
    print(
        f"\nMultilingual rounds ({len(cases)} cross-lingual cases): memory round first in "
        f"{first}, above the floor in {above_floor}"
    )


def main() -> None:
    embedder = _default_embedder(local_files_only=True)
    data = yaml.safe_load((CASES / "retrieval_benchmark.yaml").read_text())
    benchmark(embedder, data)
    multilingual(embedder)


if __name__ == "__main__":
    main()
