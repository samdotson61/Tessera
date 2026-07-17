"""The core loop orchestrator (docs/03, docs/08 Phase 0/1).

run_labeling_pass:     model(s) label every item -> stored predictions
calibrate_and_gate:    fit calibration on gold, compute coverage@precision,
                       auto-apply the confident majority, queue the rest
record_human_action:   apply a human accept/edit/reject and log the flywheel event
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .schemas import Prediction, Event, GateResult, GoldItem, HumanAction
from .engine import confidence as conf_mod
from .engine.audit import audit_pick
from .engine.verify import deterministic_checks
from .engine.calibration import fit_calibrator, cross_val_metrics
from .engine.metrics import coverage_at_precision, ece, bootstrap_coverage_ci
from .engine.gating import apply_gate


def _predict_item(it, dataset_id, taxonomy, labelers):
    outputs = [lab.label(it, taxonomy) for lab in labelers]
    label, raw, agreement, votes, dist = conf_mod.ensemble(outputs)
    violations = deterministic_checks(label, taxonomy, item=it)
    rationale = outputs[0].rationale if outputs else ""
    if violations:
        # A failed rubric check forces the item to a human regardless of model confidence.
        raw = 0.0
        rationale = f"FAILED CHECK: {'; '.join(violations)}"
    return Prediction(
        item_id=it.id, dataset_id=dataset_id, taxonomy_id=taxonomy.id,
        label=label, confidence_raw=raw, agreement=agreement, rationale=rationale,
        votes=votes, distribution=dist,
        source="+".join(l.model_id for l in labelers))


def run_labeling_pass(storage, dataset_id, taxonomy, labelers, workers=1):
    """Label every item in the dataset and store predictions. Returns the predictions.

    workers > 1 labels items concurrently (LLM calls are network-bound); results
    are stored in dataset order from the main thread either way.
    """
    items = storage.get_items(dataset_id)
    if workers > 1 and len(items) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            preds = list(pool.map(
                lambda it: _predict_item(it, dataset_id, taxonomy, labelers), items))
    else:
        preds = [_predict_item(it, dataset_id, taxonomy, labelers) for it in items]
    for p in preds:
        storage.upsert_prediction(p)
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


def calibrate_and_gate(storage, dataset_id, taxonomy, target_precision, settings,
                       log_events=True, judge=None):
    """Fit calibration on gold, gate all predictions, auto-apply + log. Returns GateResult.

    log_events=False recomputes the gate without re-appending auto-apply events
    (used when re-gating an already-processed dataset, e.g. for a report).
    judge (optional, docs/04 Layer 3) reviews each auto-apply candidate and can
    veto it back to the human queue — it narrows the auto set, never widens it.
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
        # CI from the out-of-fold values, so it inherits the honest estimate.
        ci = bootstrap_coverage_ci(cv["oof"], correct, target_precision)
    else:
        threshold, _gold_cov, achieved = coverage_at_precision(
            cal_confs, cal_correct, target_precision)
        ece_after = ece(cal_confs, cal_correct) if cal_confs else 0.0
        cross_validated = False
        ci = bootstrap_coverage_ci(cal_confs, cal_correct, target_precision) \
            if cal_confs else None

    n_auto, n_queue = apply_gate(preds, threshold)

    n_vetoed = 0
    if judge is not None:
        items = {it.id: it for it in storage.get_items(dataset_id)}
        for p in preds:
            if not p.auto_applied:
                continue
            it = items.get(p.item_id)
            ok, reason = judge.review(it, taxonomy, p.label) if it else (True, "")
            if not ok:
                p.auto_applied = False
                p.routed = True
                p.rationale = f"JUDGE VETO: {reason} | {p.rationale}"
                n_vetoed += 1
        n_auto -= n_vetoed
        n_queue += n_vetoed
    full_coverage = n_auto / len(preds) if preds else 0.0

    # Items resolved by a human keep their final label; anything else that is now
    # routed must not stay finalized from a previous, looser gate.
    events = storage.get_events(dataset_id)
    human_resolved = {e.item_id for e in events
                      if e.routed_to_human and e.final_label is not None}
    already_audited = {e.item_id for e in events
                       if e.routed_to_human and e.route_reason == "audit"}

    # A human already decided these: a re-gate must not put them back in the
    # queue (found dogfooding — the queue count claimed work that was done).
    for p in preds:
        if p.routed and p.item_id in human_resolved:
            p.routed = False
            n_queue -= 1

    # Audit sampling (docs/04): a deterministic ~audit_rate slice of the auto
    # set is ALSO routed for human verification. The label still ships —
    # coverage is unchanged — but the verdict checks the SLA in production and
    # feeds auto-region errors into gold, the one region queue review never sees.
    n_audit = 0
    audit_rate = getattr(settings, "audit_rate", 0.0)
    for p in preds:
        p.audit = bool(p.auto_applied and audit_rate > 0
                       and p.item_id not in already_audited
                       and p.item_id not in human_resolved
                       and audit_pick(dataset_id, p.item_id, audit_rate))
        if p.audit:
            n_audit += 1

    if log_events:
        storage.delete_auto_events(dataset_id)  # idempotent: replace prior auto events
    for p in preds:
        storage.upsert_prediction(p)
        if p.auto_applied:
            if p.item_id not in human_resolved:   # an audit edit outranks the model label
                storage.set_final_label(p.item_id, p.label)
            if log_events:
                storage.append_event(_event_for(p, taxonomy, routed=False))
        elif p.routed and p.item_id not in human_resolved:
            storage.set_final_label(p.item_id, None)

    gate = GateResult(
        target_precision=target_precision, threshold=threshold, coverage=full_coverage,
        achieved_precision=achieved, n_auto=n_auto, n_queue=n_queue,
        n_gold=len(cal_confs), ece_before=ece_before, ece_after=ece_after,
        cross_validated=cross_validated, n_judge_vetoed=n_vetoed,
        n_audit_pending=n_audit, coverage_ci=(list(ci) if ci else None))
    if log_events:
        # Run-over-run instrumentation (docs/08 Phase 2): coverage up, human
        # effort down — every gating run appends one row.
        human_touches = sum(1 for e in events if e.routed_to_human)
        storage.append_run(dataset_id, gate, human_touches)
    return gate


def _event_for(p, taxonomy, routed, human_action=None, final_label="__model__", reason=None):
    if final_label == "__model__":
        final_label = p.label if not routed else None
    return Event(
        item_id=p.item_id, dataset_id=p.dataset_id, model_id=p.source,
        model_label=p.label, model_rationale=p.rationale,
        confidence_raw=p.confidence_raw, confidence_calibrated=p.confidence_calibrated,
        ensemble_votes=p.votes, routed_to_human=routed,
        route_reason=(reason or ("low_confidence" if routed else "")),
        human_action=human_action, final_label=final_label,
        taxonomy_version=taxonomy.version, rubric_snapshot=taxonomy.guidelines[:200])


def undo_last_human_action(storage, dataset_id):
    """Revert the most recent human accept/edit/reject on a dataset.

    The event is removed, human-grown gold for the item is dropped, the final
    label rolls back to the previous human decision (or none), and the item
    returns to the review queue. Returns the item_id, or None if nothing to undo.
    """
    e = storage.get_last_human_event(dataset_id)
    if e is None:
        return None
    storage.delete_event(e.id)
    storage.remove_gold(e.item_id, source="human")
    p = storage.get_prediction(e.item_id)
    if e.route_reason == "audit":
        # The item was auto-applied; the audit verdict is undone, so the model
        # label ships again and the item returns to the audit queue.
        storage.set_final_label(e.item_id, p.label if p else None)
        if p is not None:
            p.audit = True
            p.auto_applied = True
            p.routed = False
            storage.upsert_prediction(p)
        return e.item_id
    remaining = storage.get_human_events_for_item(e.item_id)
    storage.set_final_label(e.item_id, remaining[-1].final_label if remaining else None)
    if p is not None:
        p.routed = True
        p.auto_applied = False
        storage.upsert_prediction(p)
    return e.item_id


def record_human_action(storage, taxonomy, item_id, action, label=None, annotator="human",
                        grow_gold=False):
    """Apply a human decision to a queued item and log the event. Returns the final label.

    grow_gold=True also records the accepted/edited label as gold (source
    "human") — docs/04's gold-set growth, so calibration tightens run over run.
    Seed gold rows are never overwritten by grown ones.

    Audit items (auto-applied, flagged for verification) take the same actions:
    accept confirms the shipped label, edit overturns it (and the correction
    enters gold — the auto-region ground-truth injection), reject clears the
    label and demotes the item to the ordinary review queue.
    """
    p = storage.get_prediction(item_id)
    if p is None:
        raise ValueError(f"no prediction for item {item_id}")
    was_audit = bool(p.audit) and not p.routed
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
        if grow_gold and item_id not in storage.get_gold(p.dataset_id):
            storage.add_gold([GoldItem(item_id=item_id, dataset_id=p.dataset_id,
                                       label=final, source="human")])
    e = _event_for(p, taxonomy, routed=True, human_action=action, final_label=final,
                   reason="audit" if was_audit else None)
    e.annotator_id = annotator
    storage.append_event(e)
    p.audit = False
    if was_audit and action == HumanAction.REJECT.value:
        # The human says the shipped label is wrong and offers no replacement:
        # un-ship it and send the item through the ordinary review queue.
        storage.set_final_label(item_id, None)
        p.auto_applied = False
        p.routed = True
    else:
        p.routed = False  # resolved; leaves the queue
    storage.upsert_prediction(p)
    return final
