"""Confidence-gated auto-apply (docs/04).

Above the threshold -> auto-applied; below -> routed to a human. The threshold is
chosen on the gold set to hold the target precision.
"""
from __future__ import annotations

from .metrics import coverage_at_precision


def choose_threshold(gold_confs, gold_correct, target_precision):
    """Return (threshold, gold_coverage, achieved_precision)."""
    return coverage_at_precision(gold_confs, gold_correct, target_precision)


def apply_gate(predictions, threshold):
    """Mark each prediction auto_applied (conf >= threshold) or routed. Mutates in place."""
    n_auto = n_queue = 0
    for p in predictions:
        if p.confidence() >= threshold:
            p.auto_applied = True
            p.routed = False
            n_auto += 1
        else:
            p.auto_applied = False
            p.routed = True
            n_queue += 1
    return n_auto, n_queue
