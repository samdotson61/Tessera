"""The dataset quality report (docs/04).

This is the artifact that lets a team trust and ship the auto-labels: per-class
precision on the gold set, coverage@precision, calibration error, and explicit
caveats about where auto-labeling is NOT trusted.
"""
from __future__ import annotations

from .schemas import QualityReport
from .engine.metrics import precision_recall_f1


def build_quality_report(storage, dataset_id, taxonomy, gate_result):
    preds = storage.get_predictions(dataset_id)
    gold = storage.get_gold(dataset_id)
    by_item = {p.item_id: p for p in preds}

    y_true, y_pred = [], []
    for item_id, true_label in gold.items():
        p = by_item.get(item_id)
        if not p:
            continue
        y_true.append(true_label)
        y_pred.append(p.label)
    prf = precision_recall_f1(y_true, y_pred, taxonomy.labels)
    per_label = {k: round(v["precision"], 3) for k, v in prf["per_label"].items()}

    caveats = []
    if gate_result.n_gold < 20:
        caveats.append(f"Small gold set ({gate_result.n_gold} items); precision estimate is noisy. "
                       "Grow the gold set for a tighter guarantee.")
    if gate_result.coverage == 0:
        caveats.append("No items met the precision target; everything was routed to humans. "
                       "Lower the target, improve the rubric, or add gold.")
    caveats.append("Rare classes and out-of-distribution items are never auto-applied without audit.")
    caveats.append("Threshold is fit on the gold set; achieved precision/ECE are cross-validated "
                   "estimates. Validate with ongoing audit sampling (docs/04).")

    return QualityReport(
        dataset_id=dataset_id, taxonomy_version=taxonomy.version,
        target_precision=gate_result.target_precision,
        threshold=round(gate_result.threshold, 4),
        coverage=round(gate_result.coverage, 4),
        achieved_precision=round(gate_result.achieved_precision, 4),
        per_label_precision=per_label, n_items=len(preds),
        n_auto=gate_result.n_auto, n_queue=gate_result.n_queue, n_gold=gate_result.n_gold,
        ece=round(gate_result.ece_after, 4), caveats=caveats)
