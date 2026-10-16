"""Maintainer tool: measure where the semantic relevance floor separates related from unrelated.

Runs the pinned local embedding model over a small synthetic Italian/English set of queries and
memory-like items (short claims and user/assistant rounds) on eight everyday topics. Every
query-item pair on the same topic counts as related, every other pair as unrelated, including
pairs that are equally emotional but about something else. It prints, for each candidate floor,
the share of related pairs kept and of unrelated pairs let through. The data is invented; no real
conversation is used. Requires the model from `filo setup`; nothing is sent over the network.

    HF_HUB_OFFLINE=1 uv run python scripts/calibrate_semantic_floor.py
"""

from __future__ import annotations

import numpy as np
from huggingface_hub import snapshot_download
from pydantic_ai import Embedder

from filo.cli import DEFAULT_EMBEDDING_REPO, DEFAULT_EMBEDDING_REVISION

TOPICS = {
    "work_calls": (
        ["I have to call my manager tomorrow and I'm dreading it", "Devo telefonare al capo"],
        [
            "Phone calls with my manager feel difficult",
            "Rimando sempre le chiamate di lavoro",
            "user: I avoided the call with my boss again\nassistant: What happened right before?",
        ],
    ),
    "sleep": (
        ["I couldn't sleep again last night", "Stanotte non ho chiuso occhio"],
        [
            "I wake up at 4am and can't fall back asleep",
            "Dormo male da quando ho cambiato turno",
            "user: the insomnia is back\nassistant: How many nights this week?",
        ],
    ),
    "sister": (
        ["My sister and I had another argument", "Ho litigato di nuovo con mia sorella"],
        [
            "My sister criticizes my choices in front of our parents",
            "Con mia sorella finisce sempre in lite",
            "user: Giulia shouted at me at dinner\nassistant: What did you do afterwards?",
        ],
    ),
    "running": (
        ["I went for a run and felt better", "Sono tornato a correre al parco"],
        [
            "Running in the morning helps my mood",
            "Correre mi schiarisce le idee",
            "user: I ran 5km today\nassistant: How did you feel after?",
        ],
    ),
    "money": (
        ["I'm worried about paying rent this month", "Non so se riesco a pagare l'affitto"],
        [
            "Debt from my credit card keeps growing",
            "Ho paura di non arrivare a fine mese",
            "user: the bank called about the overdraft\nassistant: What options do you see?",
        ],
    ),
    "grief": (
        ["I miss my grandmother so much", "Mi manca tanto la nonna"],
        [
            "My grandmother died last spring",
            "Da quando è morta la nonna sono triste",
            "user: it's been a year since grandma passed\nassistant: Anniversaries bring it back.",
        ],
    ),
    "exam": (
        ["The exam is next week and I haven't studied", "Ho l'esame di diritto venerdì"],
        [
            "I procrastinate studying until the night before",
            "Studiare mi blocca, apro il libro e lo richiudo",
            "user: I failed the statistics exam\nassistant: What do you make of it?",
        ],
    ),
    "cooking": (
        ["I tried a new recipe tonight", "Ho cucinato la lasagna per gli amici"],
        [
            "Cooking for others makes me feel useful",
            "Mi piace cucinare la domenica",
            "user: I baked bread\nassistant: How did it turn out?",
        ],
    ),
}


def _unit(rows: object) -> np.ndarray:
    array = np.asarray(rows, dtype=np.float32)
    return array / np.linalg.norm(array, axis=-1, keepdims=True)


def main() -> None:
    path = snapshot_download(
        DEFAULT_EMBEDDING_REPO, revision=DEFAULT_EMBEDDING_REVISION, local_files_only=True
    )
    embedder = Embedder(f"sentence-transformers:{path}")
    items = [(topic, text) for topic, (_, texts) in TOPICS.items() for text in texts]
    vectors = _unit(embedder.embed_documents_sync([text for _, text in items]).embeddings)
    related: list[float] = []
    unrelated: list[float] = []
    for topic, (queries, _) in TOPICS.items():
        for query in queries:
            scores = vectors @ _unit(embedder.embed_query_sync(query).embeddings[0])
            for (item_topic, _), score in zip(items, scores.tolist(), strict=True):
                (related if item_topic == topic else unrelated).append(score)
    print(f"related pairs: {len(related)}, unrelated pairs: {len(unrelated)}")
    print(f"unrelated max {max(unrelated):.3f}, p95 {np.percentile(unrelated, 95):.3f}")
    for floor in (0.2, 0.3, 0.35, 0.4, 0.45, 0.5):
        kept = np.mean(np.asarray(related) >= floor)
        passed = np.mean(np.asarray(unrelated) >= floor)
        print(f"floor {floor:.2f}: related kept {kept:.1%}, unrelated let through {passed:.1%}")


if __name__ == "__main__":
    main()
