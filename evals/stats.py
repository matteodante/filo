"""Small-sample statistics for evaluation results, using only the standard library."""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence

Z95 = 1.959963984540054


def wilson(successes: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval; (0, 1) when there is no observation."""
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def _percentile(values: Sequence[float], q: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values)


def cluster_bootstrap_mean(
    observations: Iterable[tuple[str, float]],
    *,
    resamples: int = 5_000,
    seed: int = 20261016,
) -> tuple[float, float, float]:
    """Mean with a 95% percentile interval, resampling whole clusters (personas, scenarios)."""
    clusters: dict[str, list[float]] = defaultdict(list)
    for cluster, value in observations:
        clusters[cluster].append(value)
    if not clusters:
        return math.nan, math.nan, math.nan
    keys = sorted(clusters)
    point = _mean([value for key in keys for value in clusters[key]])
    rng = random.Random(seed)
    estimates = []
    for _ in range(resamples):
        sample = [value for key in rng.choices(keys, k=len(keys)) for value in clusters[key]]
        estimates.append(_mean(sample))
    return point, _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def paired_cluster_difference(
    observations: Iterable[tuple[str, str, float]],
    arm_a: str,
    arm_b: str,
    *,
    resamples: int = 5_000,
    seed: int = 20261016,
) -> tuple[float, float, float]:
    """Mean(arm_a) - mean(arm_b) with a 95% interval, resampling clusters with both arms."""
    by_cluster: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for cluster, arm, value in observations:
        by_cluster[cluster][arm].append(value)
    keys = sorted(key for key, arms in by_cluster.items() if arms[arm_a] and arms[arm_b])
    if not keys:
        return math.nan, math.nan, math.nan

    def difference(selected: list[str]) -> float:
        a = [value for key in selected for value in by_cluster[key][arm_a]]
        b = [value for key in selected for value in by_cluster[key][arm_b]]
        return _mean(a) - _mean(b)

    rng = random.Random(seed)
    estimates = [difference(rng.choices(keys, k=len(keys))) for _ in range(resamples)]
    return difference(keys), _percentile(estimates, 0.025), _percentile(estimates, 0.975)


def pass_hat_k(results: dict[str, list[bool]], k: int) -> float:
    """Unbiased pass^k from tau-bench: mean over tasks of C(c, k) / C(n, k)."""
    values = []
    for trials in results.values():
        n, c = len(trials), sum(trials)
        if n < k:
            raise ValueError(f"pass^{k} needs at least {k} trials per task, got {n}.")
        values.append(math.comb(c, k) / math.comb(n, k))
    return _mean(values) if values else math.nan


def rogan_gladen(observed: float, sensitivity: float, specificity: float) -> float:
    """Correct an observed judge pass rate for judge error, clipped to [0, 1]."""
    youden = sensitivity + specificity - 1
    if youden <= 0:
        raise ValueError("The judge is no better than chance; correction is undefined.")
    return min(1.0, max(0.0, (observed + specificity - 1) / youden))


def tpr_tnr(pairs: Iterable[tuple[bool, bool]]) -> tuple[float, float, int, int]:
    """Judge (predicted, human) pairs, with human as truth: TPR, TNR, positives, negatives."""
    tp = fn = tn = fp = 0
    for predicted, truth in pairs:
        if truth:
            tp, fn = tp + predicted, fn + (not predicted)
        else:
            tn, fp = tn + (not predicted), fp + predicted
    positives, negatives = tp + fn, tn + fp
    return (
        tp / positives if positives else math.nan,
        tn / negatives if negatives else math.nan,
        positives,
        negatives,
    )


def cohen_kappa(first: Sequence[str], second: Sequence[str]) -> float:
    """Agreement between two labelings of the same items beyond chance."""
    if len(first) != len(second) or not first:
        raise ValueError("Kappa needs two non-empty labelings of equal length.")
    n = len(first)
    observed = sum(a == b for a, b in zip(first, second, strict=True)) / n
    categories = set(first) | set(second)
    expected = sum((first.count(c) / n) * (second.count(c) / n) for c in categories)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)
