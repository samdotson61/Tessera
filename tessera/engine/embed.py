"""Cheap text embeddings + clustering (stdlib) for the active-learning router.

Hashed bag-of-words vectors (feature hashing into a fixed number of buckets,
L2-normalized) and greedy leader clustering. Deliberately simple: at MVP scale
(hundreds to thousands of routed items) this is fast, deterministic, and good
enough to keep a reviewer from being shown five near-identical items in a row.
The production upgrade is a real embedding model + vector DB (docs/03 §6).
"""
from __future__ import annotations

import hashlib
import math
import re

_WORD = re.compile(r"[a-z0-9']+")


def embed(text: str, dim: int = 256) -> dict:
    """Sparse hashed bag-of-words vector {bucket: weight}, L2-normalized."""
    v = {}
    for w in _WORD.findall(text.lower()):
        b = int.from_bytes(hashlib.sha256(w.encode()).digest()[:4], "big") % dim
        v[b] = v.get(b, 0.0) + 1.0
    norm = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {b: x / norm for b, x in v.items()}


def cosine(a: dict, b: dict) -> float:
    if len(b) < len(a):
        a, b = b, a
    return sum(x * b.get(k, 0.0) for k, x in a.items())


def dedup_groups(rendered: dict, threshold: float, force_reps=(), dim: int = 256) -> dict:
    """Near-duplicate grouping for label propagation: {member_id: (rep_id, sim)}.

    Greedy leader pass at a HIGH cosine threshold — propagation is only sound
    between texts that say essentially the same thing (0.9+ recommended).
    force_reps (gold items) are seeded as leaders first so anything with a
    trusted label is always labeled directly and never propagated over.
    Deterministic (sorted ids); representatives have no row in the result."""
    vecs = {iid: embed(t, dim) for iid, t in rendered.items()}
    forced = [i for i in sorted(set(force_reps)) if i in vecs]
    leaders = [(i, vecs[i]) for i in forced]
    out = {}
    seeded = set(forced)
    for iid in sorted(vecs):
        if iid in seeded:
            continue
        v = vecs[iid]
        for rid, lv in leaders:
            s = cosine(v, lv)
            if s >= threshold:
                out[iid] = (rid, s)
                break
        else:
            leaders.append((iid, v))
    return out


def leader_clusters(vectors: dict, threshold: float = 0.35) -> dict:
    """Greedy leader clustering: {id: cluster_index}. Deterministic (sorted ids);
    an item joins the first leader within the cosine threshold, else founds a
    new cluster."""
    leaders = []   # (cluster_index, vector)
    out = {}
    for iid in sorted(vectors):
        v = vectors[iid]
        for ci, lv in leaders:
            if cosine(v, lv) >= threshold:
                out[iid] = ci
                break
        else:
            ci = len(leaders)
            leaders.append((ci, v))
            out[iid] = ci
    return out
