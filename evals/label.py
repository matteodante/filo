"""Blind, randomized maintainer labeling of judged items, with judge agreement statistics.

Usage:
  uv run python -m evals.label RUN_DIR --labels labels.json [--count 60] [--retest 0.2]
  uv run python -m evals.label RUN_DIR --labels labels.json --stats
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.datasets import load_personas, load_safety
from evals.judge import blind
from evals.memory import PROBE_CRITERIA, PROCESS_CRITERIA
from evals.runs import read_jsonl
from evals.stats import cohen_kappa, tpr_tnr
from filo.evaluation import ReviewerFields

LABELS = {"p": "pass", "f": "fail", "u": "unknown"}


def _item_id(family: str, material: dict[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps([family, material], sort_keys=True, ensure_ascii=False).encode()
    )
    return digest.hexdigest()[:16]


def collect_items(run_dir: Path) -> list[dict[str, Any]]:
    """Every judged item in a run, with the judge verdict kept apart from what is shown."""
    items: list[dict[str, Any]] = []
    probes = {probe.id: probe for persona in load_personas().personas for probe in persona.probes}
    for record in read_jsonl(run_dir / "memory.jsonl"):
        for probe_record in record["probes"]:
            probe = probes[probe_record["probe"]]
            material = {
                "question": probe.question,
                "reference": {
                    "expected": probe.expected,
                    "forbidden": probe.forbidden,
                    "earlier_assistant_reply": probe_record.get("reference_reply"),
                },
                "answer": probe_record["answer"],
            }
            for name, criterion in PROBE_CRITERIA.items():
                if name in probe_record:
                    items.append(_item(f"probe:{name}", criterion, material, probe_record[name]))
        for process in record["process"]:
            material = {"session": record["transcripts"][process["session"]]}
            for name, criterion in PROCESS_CRITERIA.items():
                items.append(_item(f"process:{name}", criterion, material, process[name]))
    criteria = {
        (scenario.id, criterion.id): criterion.text
        for scenario in load_safety().scenarios
        for criterion in scenario.criteria
    }
    for record in read_jsonl(run_dir / "safety.jsonl"):
        material = {"language": record["locale"], "transcript": record["transcript"]}
        for criterion_id, verdict in record["judgments"].items():
            text = criteria[(record["scenario"], criterion_id)]
            items.append(_item(f"safety:{record['category']}", text, material, verdict))
    return items


def _item(family: str, criterion: str, material: dict[str, Any], verdict: Any) -> dict[str, Any]:
    shown = {"criterion": criterion, "material": material}
    return {
        "id": _item_id(family, shown),
        "family": family,
        "shown": shown,
        "judge": verdict["verdict"],
    }


def build_queue(
    items: list[dict[str, Any]], count: int, retest_share: float, seed: int
) -> list[str]:
    """Random sample plus hidden repeats for test-retest agreement, shuffled together."""
    rng = random.Random(seed)
    ids = sorted({item["id"] for item in items})
    sample = rng.sample(ids, min(count, len(ids)))
    repeats = rng.sample(sample, round(len(sample) * retest_share))
    queue = sample + repeats
    rng.shuffle(queue)
    # A repeat never directly follows its first showing.
    for index in range(1, len(queue)):
        if queue[index] == queue[index - 1]:
            swap = (index + 1) % len(queue)
            queue[index], queue[swap] = queue[swap], queue[index]
    return queue


def label_items(
    items: list[dict[str, Any]],
    labels_path: Path,
    count: int,
    retest_share: float,
    seed: int,
    ask: Callable[[str], str] = input,
    show: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Interactive loop that saves after every answer and resumes where it stopped."""
    by_id = {item["id"]: item for item in items}
    if labels_path.exists():
        state = json.loads(labels_path.read_text(encoding="utf-8"))
    else:
        state = {
            "seed": seed,
            "queue": build_queue(items, count, retest_share, seed),
            "labels": [],
            "reviewer": ReviewerFields().model_dump(),
        }
    if not state["reviewer"]["name"]:
        state["reviewer"]["name"] = ask("Reviewer name: ").strip() or None
    for position in range(len(state["labels"]), len(state["queue"])):
        item = by_id[state["queue"][position]]
        show(f"\n[{position + 1}/{len(state['queue'])}]")
        show(blind(json.dumps(item["shown"], ensure_ascii=False, indent=1)))
        answer = ""
        while answer not in LABELS and answer != "q":
            answer = ask("pass (p) / fail (f) / unknown (u) / quit (q): ").strip().lower()
        if answer == "q":
            break
        state["labels"].append(
            {"id": item["id"], "label": LABELS[answer], "at": datetime.now(UTC).isoformat()}
        )
        state["reviewer"]["reviewed_at"] = datetime.now(UTC).isoformat()
        labels_path.write_text(json.dumps(state, indent=1) + "\n", encoding="utf-8")
    return state


def agreement(items: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    """Per-family judge TPR/TNR against the first human label, and test-retest kappa."""
    by_id = {item["id"]: item for item in items}
    first: dict[str, str] = {}
    retest: list[tuple[str, str]] = []
    for entry in state["labels"]:
        if entry["id"] in first:
            retest.append((first[entry["id"]], entry["label"]))
        else:
            first[entry["id"]] = entry["label"]
    families: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for item_id, label in first.items():
        item = by_id.get(item_id)
        if item is None or label == "unknown" or item["judge"] == "unknown":
            continue
        pair = (item["judge"] == "pass", label == "pass")
        families[item["family"]].append(pair)
        families["all"].append(pair)
    result: dict[str, Any] = {"labeled": len(first), "retest_pairs": len(retest)}
    for family, pairs in sorted(families.items()):
        tpr, tnr, positives, negatives = tpr_tnr(pairs)
        result[family] = {"tpr": tpr, "tnr": tnr, "positives": positives, "negatives": negatives}
    if retest:
        result["test_retest_kappa"] = cohen_kappa(
            [pair[0] for pair in retest], [pair[1] for pair in retest]
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.label", description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--count", type=int, default=60)
    parser.add_argument("--retest", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args(argv)
    items = collect_items(args.run_dir)
    if not items:
        parser.error("no judged items found in the run directory")
    if args.stats:
        state = json.loads(args.labels.read_text(encoding="utf-8"))
    else:
        state = label_items(items, args.labels, args.count, args.retest, args.seed)
    print(json.dumps(agreement(items, state), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
