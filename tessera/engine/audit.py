"""Audit sampling (docs/04, Layer 5).

A deterministic ~audit_rate slice of the AUTO-APPLIED items is routed to a
human anyway: the label still ships (coverage is unchanged), but the human
verdict checks the precision SLA in production and feeds auto-region errors
back into gold. Without it, gold grown from the review queue can only describe
the routed region — the gate's own errors live above the threshold, where a
reviewer never looks. (Measured: an oracle reviewer working the entire routed
queue never moved coverage or corrected an optimistic CV estimate.)

Selection hashes (dataset_id, item_id): per-item stable, so re-gates keep the
same audit set and there is no RNG state to store.
"""
from __future__ import annotations

import hashlib


def audit_pick(dataset_id: str, item_id: str, rate: float) -> bool:
    """Deterministically pick ~rate of items for audit."""
    if rate <= 0:
        return False
    h = hashlib.sha256(f"audit|{dataset_id}|{item_id}".encode()).digest()
    return int.from_bytes(h[:8], "big") / 2 ** 64 < rate
