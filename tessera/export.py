"""Export finalized labels (docs/03 Export service).

Writes a JSONL of every finalized item (auto-applied or human-resolved) ready to
feed a training pipeline.
"""
from __future__ import annotations

import json


def export_jsonl(storage, dataset_id, path):
    """Write one JSON object per finalized item. Returns the count written."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for it in storage.get_items(dataset_id):
            if it.final_label is None:
                continue
            p = storage.get_prediction(it.id)
            rec = {
                "id": it.id,
                "text": it.text,
                "label": it.final_label,
                "source": "auto" if (p and p.auto_applied) else "human",
                "confidence": (round(p.confidence(), 4) if p else None),
            }
            f.write(json.dumps(rec) + "\n")
            n += 1
    return n
