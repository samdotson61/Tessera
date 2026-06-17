"""Gold-set bootstrap sampling (docs/04, Layer 5).

Pick a small set of items for a human to label as ground truth, stratified across
predicted labels so the gold set covers the label space rather than over-sampling
the majority class.
"""
from __future__ import annotations

import random


def stratified_sample(predictions, n, seed=0):
    """Return up to n item_ids, spread across predicted labels."""
    rng = random.Random(seed)
    buckets = {}
    for p in predictions:
        buckets.setdefault(p.label, []).append(p.item_id)
    for ids in buckets.values():
        rng.shuffle(ids)
    labels = list(buckets)
    out = []
    i = 0
    while len(out) < n and any(buckets[l] for l in labels):
        l = labels[i % len(labels)]
        if buckets[l]:
            out.append(buckets[l].pop())
        i += 1
    return out[:n]
