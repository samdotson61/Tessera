"""Confidence estimation: combine multiple labeler outputs into one prediction.

See docs/04 (Accuracy & Trust Engine), Layer 1. The ensemble distribution is the
mean of each model's distribution; raw confidence is the mass on the winning
label; agreement is the fraction of models whose own pick matches the winner
(the ensemble-disagreement signal used for routing).
"""
from __future__ import annotations

import random


def ensemble(outputs):
    """outputs: list[LabelOutput] -> (label, raw_confidence, agreement, votes, distribution)."""
    if not outputs:
        return (None, 0.0, 0.0, {}, {})
    labels = set()
    for o in outputs:
        labels |= set(o.distribution)
    mean = {l: sum(o.distribution.get(l, 0.0) for o in outputs) / len(outputs) for l in labels}
    z = sum(mean.values()) or 1.0
    mean = {l: v / z for l, v in mean.items()}
    label = max(mean, key=mean.get)
    raw = mean[label]
    votes = {o.model_id: o.top()[0] for o in outputs}
    agreement = sum(1 for v in votes.values() if v == label) / len(votes)
    return (label, raw, agreement, votes, mean)


def self_consistency(distribution, n=5, seed=0):
    """Illustrative helper: agreement among n seeded samples drawn from a distribution.

    Converges to the max probability as n grows; demonstrates the self-consistency
    confidence signal in a reproducible way. The production path samples real model
    completions instead.
    """
    if not distribution:
        return (None, 0.0)
    rng = random.Random(seed)
    labels = list(distribution)
    weights = [distribution[l] for l in labels]
    draws = rng.choices(labels, weights=weights, k=n)
    top = max(set(draws), key=draws.count)
    return (top, draws.count(top) / n)
