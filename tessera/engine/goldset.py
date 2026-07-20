"""Gold-set bootstrap sampling (docs/04, Layer 5).

Pick a small set of items for a human to label as ground truth, stratified across
predicted labels so the gold set covers the label space rather than over-sampling
the majority class.
"""
from __future__ import annotations

import random


def cluster_sample(rendered, n, exclude=(), threshold=0.35):
    """Cold-start gold sample when no predictions exist yet: hashed-BoW leader
    clusters over the corpus, then round-robin across clusters (largest first)
    so the sample spans the corpus's regions instead of over-sampling its
    majority shape. Deterministic (sorted ids). rendered: {item_id: text}."""
    from .embed import embed, leader_clusters
    skip = set(exclude)
    ids = [i for i in sorted(rendered) if i not in skip]
    if not ids:
        return []
    clusters = leader_clusters({i: embed(rendered[i]) for i in ids}, threshold)
    by_cluster = {}
    for iid in ids:
        by_cluster.setdefault(clusters[iid], []).append(iid)
    order = sorted(by_cluster, key=lambda c: (-len(by_cluster[c]), c))
    queues = [by_cluster[c] for c in order]
    out = []
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


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
