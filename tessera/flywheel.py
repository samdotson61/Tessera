"""Data flywheel utilities (docs/05).

The event log is the proprietary asset. These helpers turn finalized labels into
training pairs for Phase-B distillation and summarize the human-action stream that
proves the loop is tightening.
"""
from __future__ import annotations


def export_training_pairs(storage, dataset_id):
    """Supervised pairs (text -> final label) from every finalized item.

    These — especially the human EDITs — are the corpus a per-customer labeler is
    later distilled on (docs/05, Phase B).
    """
    pairs = []
    for it in storage.get_items(dataset_id):
        if it.final_label:
            pairs.append({"id": it.id, "text": it.text, "label": it.final_label})
    return pairs


def audit_stats(storage, dataset_id):
    """Completed audit reviews: how often the human confirmed the shipped label.

    audit_precision is the production check of the SLA — measured on a random
    sample of the auto-applied set, not on gold the calibrator has seen.
    """
    events = [e for e in storage.get_events(dataset_id)
              if e.routed_to_human and e.route_reason == "audit"]
    confirmed = sum(1 for e in events if e.human_action == "accept")
    return {"n_audited": len(events), "n_confirmed": confirmed,
            "audit_precision": (confirmed / len(events)) if events else None}


def event_stats(storage, dataset_id):
    """Counts of how items were resolved — the raw material for 'effort dropping'."""
    events = storage.get_events(dataset_id)
    stats = {"total": len(events), "auto_applied": 0,
             "accept": 0, "edit": 0, "reject": 0, "routed": 0}
    for e in events:
        if not e.routed_to_human:
            stats["auto_applied"] += 1
        else:
            stats["routed"] += 1
            if e.human_action in ("accept", "edit", "reject"):
                stats[e.human_action] += 1
    return stats
