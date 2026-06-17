"""Active-learning router (docs/04 / docs/03).

Orders the human review queue so each label maximally improves the system:
most-uncertain first, round-robined across predicted labels for diversity so a
reviewer isn't stuck on one class. (Embedding-based representativeness is the
production upgrade; predicted label is the cheap MVP proxy for a cluster.)
"""
from __future__ import annotations


def order_queue(predictions):
    routed = [p for p in predictions if p.routed]
    buckets = {}
    for p in sorted(routed, key=lambda x: x.confidence()):  # ascending = most uncertain first
        buckets.setdefault(p.label, []).append(p)
    queues = list(buckets.values())
    out = []
    while queues:
        for q in queues:
            out.append(q.pop(0))
        queues = [q for q in queues if q]
    return out
