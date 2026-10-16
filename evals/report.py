"""Aggregate memory-study results into the preregistered comparisons, as Markdown.

Usage: uv run python -m evals.report RUN_DIR [--labels labels.json]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from evals.runs import read_jsonl
from evals.stats import cluster_bootstrap_mean, paired_cluster_difference, rogan_gladen, wilson

H3_SESSIONS = 4
H3_DAYS = 30


def _passed(verdict: dict[str, str]) -> float | None:
    return None if verdict["verdict"] == "unknown" else float(verdict["verdict"] == "pass")


def probe_observations(records: list[dict[str, Any]], criterion: str) -> list[dict[str, Any]]:
    """Flat (persona, arm, type, long_gap, value) rows; unknown verdicts are dropped."""
    rows = []
    for record in records:
        for probe in record["probes"]:
            if criterion not in probe or (value := _passed(probe[criterion])) is None:
                continue
            rows.append(
                {
                    "persona": record["persona_id"],
                    "arm": record["arm"],
                    "type": probe["type"],
                    "long_gap": probe["intervening_sessions"] >= H3_SESSIONS
                    or probe["gap_days"] > H3_DAYS,
                    "value": value,
                }
            )
    return rows


def _rate(rows: list[dict[str, Any]]) -> str:
    n = len(rows)
    passed = int(sum(row["value"] for row in rows))
    low, high = wilson(passed, n)
    return f"{passed}/{n} = {passed / n:.0%} [{low:.0%}, {high:.0%}]" if n else "n/a"


def _difference(rows: list[dict[str, Any]], a: str, b: str, invert: bool = False) -> str:
    sign = -1.0 if invert else 1.0
    point, low, high = paired_cluster_difference(
        ((row["persona"], row["arm"], sign * row["value"]) for row in rows), a, b
    )
    if math.isnan(point):
        return "n/a"
    return f"{point * 100:+.1f} pts [{low * 100:+.1f}, {high * 100:+.1f}]"


def markdown(records: list[dict[str, Any]], corrections: dict[str, tuple[float, float]]) -> str:
    correct = probe_observations(records, "correct")
    no_false = probe_observations(records, "no_false_memory")
    arms = sorted({row["arm"] for row in correct})
    lines = ["## Memory probes", "", "| Arm | Correct | No false memory |", "|---|---|---|"]
    for arm in arms:
        lines.append(
            f"| {arm} | {_rate([r for r in correct if r['arm'] == arm])} | "
            f"{_rate([r for r in no_false if r['arm'] == arm])} |"
        )
    lines += ["", "Wilson 95% intervals; N counts probe answers with a known verdict.", ""]
    lines += ["### Correct, by probe type", "", "| Type | " + " | ".join(arms) + " |"]
    lines.append("|---" * (len(arms) + 1) + "|")
    for probe_type in sorted({row["type"] for row in correct}):
        cells = [
            _rate([r for r in correct if r["type"] == probe_type and r["arm"] == arm])
            for arm in arms
        ]
        lines.append(f"| {probe_type} | " + " | ".join(cells) + " |")
    lines += ["", "### Preregistered comparisons (standard minus transcript_only)", ""]
    lines.append(f"- H1 probe accuracy: {_difference(correct, 'standard', 'transcript_only')}")
    lines.append(
        "- H2 false-memory rate: "
        f"{_difference(no_false, 'standard', 'transcript_only', invert=True)}"
    )
    for label, flag in (("long gap", True), ("short gap", False)):
        subset = [row for row in correct if row["long_gap"] is flag]
        difference = _difference(subset, "standard", "transcript_only")
        lines.append(f"- H3 accuracy, {label}: {difference}")
    process = [
        {"persona": record["persona_id"], "arm": record["arm"], "value": value}
        for record in records
        for session in record["process"]
        if (value := _passed(session["process_quality"])) is not None
    ]
    lines.append(f"- H4 process quality: {_difference(process, 'standard', 'transcript_only')}")
    lines += [
        "",
        "Differences are paired by persona with a persona-clustered bootstrap (5,000 resamples).",
        "",
        "## How much the sessions help (per session, by arm)",
        "",
        "| Criterion | " + " | ".join(arms) + " |",
        "|---" * (len(arms) + 1) + "|",
    ]
    for name in ("felt_understood", "useful_step", "uses_continuity", "follows_up"):
        rows = [
            {"persona": record["persona_id"], "arm": record["arm"], "value": value}
            for record in records
            for session in record["process"]
            if name in session and (value := _passed(session[name])) is not None
        ]
        if rows:
            cells = [_rate([r for r in rows if r["arm"] == arm]) for arm in arms]
            lines.append(f"| {name} | " + " | ".join(cells) + " |")
    for name, (sensitivity, specificity) in corrections.items():
        rows = correct if name == "probe:correct" else no_false
        for arm in arms:
            subset = [r["value"] for r in rows if r["arm"] == arm]
            if subset:
                observed = sum(subset) / len(subset)
                corrected = rogan_gladen(observed, sensitivity, specificity)
                lines.append(f"- Rogan-Gladen {name} {arm}: {observed:.0%} -> {corrected:.0%}")
    store = [record["store_metrics"] for record in records if record["store_metrics"]]
    if store:
        lines += ["", "## Store-level metrics (standard arm)", ""]
        for key in ("extraction_recall", "extraction_precision", "update_applied"):
            values = [(str(i), m[key]) for i, m in enumerate(store) if m[key] is not None]
            point, low, high = cluster_bootstrap_mean(values)
            lines.append(f"- {key}: {point:.0%} [{low:.0%}, {high:.0%}] (personas={len(values)})")
        lines.append(f"- unsupported claims: {sum(m['unsupported_claims'] for m in store)}")
        leaked = sum(bool(m["forgotten_terms_in_derived_text"]) for m in store)
        lines.append(f"- personas with forgotten terms in derived text: {leaked}/{len(store)}")
    return "\n".join(lines) + "\n"


def judge_operating_points(stats: dict[str, Any]) -> dict[str, tuple[float, float]]:
    """Judge sensitivity and specificity per probe criterion, when both are estimable."""
    return {
        family: (values["tpr"], values["tnr"])
        for family, values in stats.items()
        if family.startswith("probe:")
        and isinstance(values, dict)
        and not math.isnan(values["tpr"])
        and not math.isnan(values["tnr"])
        and values["tpr"] + values["tnr"] > 1
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.report", description=__doc__)
    parser.add_argument("run_dirs", type=Path, nargs="+")
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args(argv)
    records = [r for directory in args.run_dirs for r in read_jsonl(directory / "memory.jsonl")]
    corrections: dict[str, tuple[float, float]] = {}
    if args.labels is not None:
        from evals.label import agreement, collect_items

        items = [item for directory in args.run_dirs for item in collect_items(directory)]
        state = json.loads(args.labels.read_text(encoding="utf-8"))
        corrections = judge_operating_points(agreement(items, state))
    print(markdown(records, corrections))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
