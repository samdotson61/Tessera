"""Active-learning router (docs/04 / docs/03; upgraded per docs/08 Phase 2).

Orders the human review queue so each label maximally improves the system.

"confidence" mode (default) is ascending-confidence order, round-robined
across predicted labels — most uncertain first. It is the default because the
harness said so: on the first A/B (oracle reviewer, AG News local run, equal
86-review budgets) it found 21 model errors to cluster mode's 17. docs/08
Phase 2 pre-committed this rule: add router factors only when the measurement
shows they help.

"cluster" mode (experimental, TESSERA_ROUTER=cluster) scores each routed item
by the Phase 2 formula — uncertainty x informativeness x representativeness:

  uncertainty        1 - calibrated confidence
  informativeness    1 + ensemble/sample disagreement (1 - agreement)
  representativeness sqrt(cluster size) — labeling an item that speaks for a
                     big cluster teaches more than an outlier

— then interleaves clusters (best cluster first, one item per cluster per
pass) so a reviewer is never shown a run of near-identical items. Its
diversity benefit for human reviewers is real but unmeasurable by an oracle;
re-run the A/B (scripts/simulate_review.py --router) on your own data before
switching.
"""
from __future__ import annotations

import math

from .embed import embed, leader_clusters


def _confidence_order(routed):
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


def order_queue(predictions, items=None, mode="cluster"):
    routed = [p for p in predictions if p.routed]
    if mode != "cluster" or not items:
        return _confidence_order(routed)
    texts = {}
    for p in routed:
        it = items.get(p.item_id)
        if it is None:
            return _confidence_order(routed)   # incomplete item map: fall back
        texts[p.item_id] = it.render()

    clusters = leader_clusters({iid: embed(t) for iid, t in texts.items()})
    sizes = {}
    for ci in clusters.values():
        sizes[ci] = sizes.get(ci, 0) + 1

    def score(p):
        uncertainty = 1.0 - p.confidence()
        informativeness = 1.0 + (1.0 - p.agreement)
        representativeness = math.sqrt(sizes[clusters[p.item_id]])
        return uncertainty * informativeness * representativeness

    by_cluster = {}
    for p in sorted(routed, key=score, reverse=True):
        by_cluster.setdefault(clusters[p.item_id], []).append(p)
    # Interleave clusters, best-scoring cluster first — diversity without
    # abandoning priority.
    queues = sorted(by_cluster.values(), key=lambda q: score(q[0]), reverse=True)
    out = []
    while queues:
        for q in queues:
            out.append(q.pop(0))
        queues = [q for q in queues if q]
    return out
