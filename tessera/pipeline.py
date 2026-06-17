"""The core loop orchestrator (docs/03, docs/08 Phase 0/1).

run_labeling_pass:     model(s) label every item -> stored predictions
calibrate_and_gate:    fit calibration on gold, compute coverage@precision,
                       auto-apply the confident majority, queue the rest
record_human_action:   apply a human accept/edit/reject and log the flywheel event
"""
from __future__ import annotations

import time

from .schemas import Prediction, Event, GateResult, HumanAction
from .engine import confidence as conf_mod
from .engine.verify import deterministic_checks
from .engine.calibration import fit_calibrator, cross_val_metrics
from .engine.metrics import coverage_at_precision, ece
from .engine.gating import apply_gate


def run_labeling_pass(storage, dataset_id, taxonomy, labelers):
    """Label every item in the dataset and store predictions. Returns the predictions."""
    items = storage.get_items(dataset_id)
    preds = []
    for it in items:
        t0 = time.time()
        outputs = [lab.label(it, taxonomy) for lab in labelers]
        label, raw, agreement, votes, dist = conf_mod.ensemble(outputs)
        violations = deterministic_checks(label, taxonomy)
        rationale = outputs[0].rationale if outputs else ""
        if violations:
            # A failed rubric check forces the item to a human regardless of model confidence.
            raw = 0.0
            rationale = f"FAILED CHECK: {'; '.join(violations)}"
        p = Prediction(
            item_id=it.id, dataset_id=dataset_id, taxonomy_id=taxonomy.id,
            label=label, confidence_raw=raw, agreement=agreement, rationale=rationale,
            votes=votes, distribution=dist,
            source="+".join(l.model_id for l in labelers))
        storage.upsert_prediction(p)
        preds.append(p)
        _ = (time.time() - t0)  # latency available for telemetry
    return preds


def _gold_arrays(predictions_by_item, gold, use_calibrated):
    confs, correct = [], []
    for item_id, true_label in gold.items():
        p = predictions_by_item.get(item_id)
        if not p:
            continue
        confs.append(p.confidence() if use_calibrated else p.confidence_raw)
        correct.append(p.label == true_label)
    return confs, correct


def calibrate_and_gate(storage, dataset_id, taxonomy, target_precision, settings, log_events=True):
    """Fit calibration on gold, gate all predictions, auto-apply + log. Returns GateResult.

    log_events=False recomputes the gate without re-appending auto-apply events
    (used when re-gating an already-processed dataset, e.g. for a report).
    """
    preds = storage.get_predictions(dataset_id)
    gold = storage.get_gold(dataset_id)
    by_item = {p.item_id: p for p in preds}

    raw_confs, correct = _gold_arrays(by_item, gold, use_calibrated=False)
    ece_before = ece(raw_confs, correct) if raw_confs else 0.0

    calib = fit_calibrator(settings.calibration, raw_confs, correct,
                           settings.min_gold_for_calibration)
    for p in preds:
        p.confidence_calibrated = calib.transform(p.confidence_raw)
        storage.upsert_prediction(p)

    by_item = {p.item_id: p for p in preds}
    cal_confs, cal_correct = _gold_arrays(by_item, gold, use_calibrated=True)

    # Choose the operating threshold and the reported precision/ECE at the SAME
    # operating point. Prefer cross-validation (honest, out-of-sample); the gate
    # then deploys that same threshold, so coverage and achieved_precision are
    # coherent. Fall back to in-sample only when the gold set is too small for CV.
    cv = cross_val_metrics(settings.calibration, raw_confs, correct, target_precision,
                           min_points=settings.min_gold_for_calibration)
    if cv is not None:
        threshold = cv["threshold"]
        achieved = cv["achieved"]
        ece_after = cv["ece"]
        cross_validated = True
    else:
        threshold, _gold_cov, achieved = coverage_at_precision(
            cal_confs, cal_correct, target_precision)
        ece_after = ece(cal_confs, cal_correct) if cal_confs else 0.0
        cross_validated = False

    n_auto, n_queue = apply_gate(preds, threshold)
    full_coverage = n_auto / len(preds) if preds else 0.0

    if log_events:
        storage.delete_auto_events(dataset_id)  # idempotent: replace prior auto events
    for p in preds:
        storage.upsert_prediction(p)
        if p.auto_applied:
            storage.set_final_label(p.item_id, p.label)
            if log_events:
                storage.append_event(_event_for(p, taxonomy, routed=False))

    return GateResult(
        target_precision=target_precision, threshold=threshold, coverage=full_coverage,
        achieved_precision=achieved, n_auto=n_auto, n_queue=n_queue,
        n_gold=len(cal_confs), ece_before=ece_before, ece_after=ece_after,
        cross_validated=cross_validated)


def _event_for(p, taxonomy, routed, human_action=None, final_label="__model__"):
    if final_label == "__model__":
        final_label = p.label if not routed else None
    return Event(
        item_id=p.item_id, dataset_id=p.dataset_id, model_id=p.source,
        model_label=p.label, model_rationale=p.rationale,
        confidence_raw=p.confidence_raw, confidence_calibrated=p.confidence_calibrated,
        ensemble_votes=p.votes, routed_to_human=routed,
        route_reason=("low_confidence" if routed else ""),
        human_action=human_action, final_label=final_label,
        taxonomy_version=taxonomy.version, rubric_snapshot=taxonomy.guidelines[:200])


def record_human_action(storage, taxonomy, item_id, action, label=None, annotator="human"):
    """Apply a human decision to a queued item and log the event. Returns the final label."""
    p = storage.get_prediction(item_id)
    if p is None:
        raise ValueError(f"no prediction for item {item_id}")
    if action == HumanAction.ACCEPT.value:
        final = p.label
    elif action == HumanAction.EDIT.value:
        final = label or p.label
    elif action == HumanAction.REJECT.value:
        final = None
    else:
        raise ValueError(f"unknown action: {action}")

    if final is not None:
        storage.set_final_label(item_id, final)
    e = _event_for(p, taxonomy, routed=True, human_action=action, final_label=final)
    e.annotator_id = annotator
    storage.append_event(e)
    p.routed = False  # resolved; leaves the queue
    storage.upsert_prediction(p)
    return final
