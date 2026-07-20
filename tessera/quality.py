"""The dataset quality report (docs/04).

This is the artifact that lets a team trust and ship the auto-labels: per-class
precision on the gold set, coverage@precision, calibration error, and explicit
caveats about where auto-labeling is NOT trusted.
"""
from __future__ import annotations

from .schemas import QualityReport
from .engine.metrics import precision_recall_f1, reliability_bins
from .flywheel import audit_stats


def build_quality_report(storage, dataset_id, taxonomy, gate_result):
    preds = storage.get_predictions(dataset_id)
    gold = storage.get_gold(dataset_id)
    by_item = {p.item_id: p for p in preds}

    y_true, y_pred, g_confs, g_correct = [], [], [], []
    for item_id, true_label in gold.items():
        p = by_item.get(item_id)
        if not p:
            continue
        y_true.append(true_label)
        y_pred.append(p.label)
        g_confs.append(p.confidence())
        g_correct.append(p.label == true_label)
    if taxonomy.label_type == "span":
        # Span-level precision per entity type (item-level exact match drives
        # calibration; this is the finer-grained view a buyer reads).
        from .engine.spans import corpus_per_type_precision
        per_label = {k: round(v, 3) for k, v in corpus_per_type_precision(
            list(zip(y_pred, y_true)), taxonomy.labels).items()}
    else:
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
    audit = audit_stats(storage, dataset_id)
    n_audit_pending = sum(1 for p in preds if p.audit)
    if audit["n_audited"]:
        line = (f"Audit sample: {audit['n_confirmed']}/{audit['n_audited']} shipped labels "
                f"confirmed ({audit['audit_precision']:.1%}) — the production check of the SLA.")
        if audit["audit_precision"] < gate_result.target_precision:
            line += (" BELOW the target: auto-region errors are real; overturned labels have "
                     "entered gold — re-gate, and consider a stricter target or better rubric.")
        caveats.append(line)
    elif n_audit_pending:
        caveats.append(f"{n_audit_pending} auto-applied item(s) await audit review; the SLA is "
                       "unverified in production until the audit sample is worked.")
    n_human_gold = storage.count_gold_by_source(dataset_id).get("human", 0)
    if n_human_gold:
        caveats.append(f"{n_human_gold} gold item(s) were grown from human review decisions; "
                       "they over-represent low-confidence items, so per-band calibration "
                       "there is better sampled but the seed gold remains the unbiased core.")
    spec_active = any(any(str(m).startswith("specialist") for m in p.votes) for p in preds)
    if spec_active:
        caveats.append("Consensus specialist is in the ensemble: disagreement with the model "
                       "flattens confidence and routes. Calibration used only the half of the "
                       "trusted labels the specialist never trained on (leak guard); n_gold "
                       "reflects that half.")
    if gate_result.n_propagated:
        caveats.append(f"{gate_result.n_propagated} auto label(s) are near-duplicate "
                       "propagations: the member mirrors its representative's label and was "
                       "never labeled directly. Propagated labels stay in the audit universe.")
    if gate_result.autopilot_level:
        caveats.append(f"AUTOPILOT level {gate_result.autopilot_level}: audit evidence breached "
                       f"the target, so the gate ran at an effective target of "
                       f"{gate_result.effective_target:.1%} (allowed error halved per level). "
                       "Coverage reflects the tightened gate.")
    if gate_result.n_no_signal:
        share = gate_result.n_no_signal / max(1, len(preds))
        line = (f"{gate_result.n_no_signal} item(s) ({share:.0%}) had a labeler return NO "
                "usable signal (error or empty answer).")
        if share > 0.05:
            line += (" CHECK THE SERVING STACK before trusting this run — a reasoning-mode "
                     "model can answer with empty content on every long item while the "
                     "remaining ensemble members keep the numbers looking plausible "
                     "(llama-server: --reasoning-budget 0).")
        caveats.append(line)
    if gate_result.n_judge_vetoed:
        caveats.append(f"LLM judge vetoed {gate_result.n_judge_vetoed} auto-apply candidate(s) "
                       "to the human queue; reported coverage is post-veto.")
    if gate_result.cross_validated:
        caveats.append("Achieved precision/ECE are cross-validated, out-of-sample estimates at the "
                       "deployed threshold. Validate with ongoing audit sampling (docs/04).")
    else:
        caveats.append("Gold set too small for cross-validation: achieved precision/ECE are "
                       "IN-SAMPLE (optimistic). Grow the gold set before trusting these numbers.")

    return QualityReport(
        dataset_id=dataset_id, taxonomy_version=taxonomy.version,
        target_precision=gate_result.target_precision,
        threshold=round(gate_result.threshold, 4),
        coverage=round(gate_result.coverage, 4),
        achieved_precision=round(gate_result.achieved_precision, 4),
        per_label_precision=per_label, n_items=len(preds),
        n_auto=gate_result.n_auto, n_queue=gate_result.n_queue, n_gold=gate_result.n_gold,
        ece=round(gate_result.ece_after, 4),
        coverage_ci=([round(v, 4) for v in gate_result.coverage_ci]
                     if gate_result.coverage_ci else None),
        reliability_bins=reliability_bins(g_confs, g_correct),
        n_audit_pending=n_audit_pending, n_audited=audit["n_audited"],
        audit_precision=(round(audit["audit_precision"], 4)
                         if audit["audit_precision"] is not None else None),
        n_propagated=gate_result.n_propagated,
        autopilot_level=gate_result.autopilot_level,
        effective_target=gate_result.effective_target,
        n_no_signal=gate_result.n_no_signal,
        caveats=caveats)
