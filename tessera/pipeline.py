"""The core loop orchestrator (docs/03, docs/08 Phase 0/1).

run_labeling_pass:     model(s) label every item -> stored predictions
calibrate_and_gate:    fit calibration on gold, compute coverage@precision,
                       auto-apply the confident majority, queue the rest
record_human_action:   apply a human accept/edit/reject and log the flywheel event
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from math import comb

from .schemas import Prediction, Event, GateResult, GoldItem, HumanAction
from .engine import confidence as conf_mod
from .engine.audit import audit_pick
from .engine.specialist import consensus_split
from .engine.verify import deterministic_checks
from .engine.calibration import fit_calibrator, cross_val_metrics
from .engine.metrics import coverage_at_precision, ece, bootstrap_coverage_ci
from .engine.gating import apply_gate
from .flywheel import audit_stats


_NO_SIGNAL = ("LLM error:", "no label mass in logprobs", "logprobs unavailable")


def _predict_item(it, dataset_id, taxonomy, labelers):
    outputs = [lab.label(it, taxonomy) for lab in labelers]
    label, raw, agreement, votes, dist = conf_mod.ensemble(outputs)
    violations = deterministic_checks(label, taxonomy, item=it)
    rationale = outputs[0].rationale if outputs else ""
    # A labeler that errored or whose answer carried no label signal returned
    # a uniform distribution — the ensemble silently runs on the remaining
    # members. Mark it loudly: a high no-signal share means the serving stack
    # is broken (measured: a reasoning-mode model answered 434/438 items with
    # empty content and every downstream number looked plausible).
    dead = sum(1 for o in outputs if any(m in (o.rationale or "") for m in _NO_SIGNAL))
    if dead:
        rationale = f"[{dead}/{len(outputs)} labelers no-signal] {rationale}"
    if violations:
        # A failed rubric check forces the item to a human regardless of model confidence.
        raw = 0.0
        rationale = f"FAILED CHECK: {'; '.join(violations)}"
    return Prediction(
        item_id=it.id, dataset_id=dataset_id, taxonomy_id=taxonomy.id,
        label=label, confidence_raw=raw, agreement=agreement, rationale=rationale,
        votes=votes, distribution=dist,
        source="+".join(l.model_id for l in labelers))


def run_labeling_pass(storage, dataset_id, taxonomy, labelers, workers=1, only_ids=None):
    """Label every item in the dataset and store predictions. Returns the predictions.

    workers > 1 labels items concurrently (LLM calls are network-bound); results
    are stored in dataset order from the main thread either way.
    only_ids (near-duplicate propagation) restricts the pass to the given item
    ids — cluster representatives — leaving members to mirror them at the gate.
    """
    items = storage.get_items(dataset_id)
    if only_ids is not None:
        items = [it for it in items if it.id in only_ids]
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


def _binom_cdf(k, n, p):
    """P(X <= k) for X ~ Binomial(n, p) — exact, stdlib."""
    q = 1.0 - p
    return min(1.0, sum(comb(n, i) * (p ** i) * (q ** (n - i)) for i in range(k + 1)))


def _autopilot_level(storage, dataset_id, settings, target, allow_update):
    """Closed-loop gate control from audit evidence (docs/04).

    Each tightening level halves the allowed error (effective target
    1 - (1-t)/2^L, L capped at 3). The controller judges only the audits that
    arrived since its last adjustment: a breach (exact one-sided binomial test,
    p < target at 95% confidence) raises the level; a window back at/above the
    target lowers it. Inconclusive windows keep accumulating. Report-only
    re-gates read the level without consuming evidence.
    """
    if not getattr(settings, "autopilot", False):
        return 0
    level = int(storage.get_kv(dataset_id, "autopilot_level", "0") or 0)
    if not allow_update:
        return level
    audits = [e for e in storage.get_events(dataset_id)
              if e.routed_to_human and e.route_reason == "audit"]
    marked = int(storage.get_kv(dataset_id, "autopilot_n", "0") or 0)
    window = audits[marked:]
    if len(window) < getattr(settings, "autopilot_min_audits", 20):
        return level
    k = sum(1 for e in window if e.human_action == "accept")
    n = len(window)
    if _binom_cdf(k, n, target) < 0.05:
        new_level = min(level + 1, 3)
    elif (k / n) >= target and level > 0:
        new_level = level - 1
    else:
        return level   # inconclusive: leave the window open to accumulate
    storage.set_kv(dataset_id, "autopilot_level", new_level)
    storage.set_kv(dataset_id, "autopilot_n", len(audits))
    return new_level


def calibrate_and_gate(storage, dataset_id, taxonomy, target_precision, settings,
                       log_events=True, judge=None):
    """Fit calibration on gold, gate all predictions, auto-apply + log. Returns GateResult.

    log_events=False recomputes the gate without re-appending auto-apply events
    (used when re-gating an already-processed dataset, e.g. for a report).
    judge (optional, docs/04 Layer 3) reviews each auto-apply candidate and can
    veto it back to the human queue — it narrows the auto set, never widens it.

    Two trust features hook in here without extra plumbing, both detected from
    the data so report-time re-gates stay consistent with the run that made it:
    - Consensus specialist: if a specialist voted in the ensemble, the half of
      the trusted labels it trained on is excluded from calibration (leak guard).
    - Near-duplicate propagation: members in the clusters table mirror their
      representative's label and gate state instead of being gated directly.
    """
    all_preds = storage.get_predictions(dataset_id)
    gold = storage.get_gold(dataset_id)
    clusters = storage.get_clusters(dataset_id)

    # Direct predictions calibrate and gate; member rows are derived data,
    # rebuilt from their representative every gate.
    direct = [p for p in all_preds
              if p.source != "propagated" and p.item_id not in clusters]
    by_item = {p.item_id: p for p in direct}

    spec_active = any(any(str(m).startswith("specialist") for m in p.votes)
                      for p in direct)
    if spec_active:
        gold = {iid: lab for iid, lab in gold.items()
                if consensus_split(dataset_id, iid) == "calib"}

    raw_confs, correct = _gold_arrays(by_item, gold, use_calibrated=False)
    ece_before = ece(raw_confs, correct) if raw_confs else 0.0

    # Autopilot (docs/04 closed loop): fresh audit evidence may tighten the
    # target the gate actually runs at. The promise reported is still the base.
    level = _autopilot_level(storage, dataset_id, settings, target_precision, log_events)
    effective_target = 1.0 - (1.0 - target_precision) * (0.5 ** level)

    calib = fit_calibrator(settings.calibration, raw_confs, correct,
                           settings.min_gold_for_calibration)
    for p in direct:
        p.confidence_calibrated = calib.transform(p.confidence_raw)

    cal_confs, cal_correct = _gold_arrays(by_item, gold, use_calibrated=True)

    # Choose the operating threshold and the reported precision/ECE at the SAME
    # operating point. Prefer cross-validation (honest, out-of-sample); the gate
    # then deploys that same threshold, so coverage and achieved_precision are
    # coherent. Fall back to in-sample only when the gold set is too small for CV.
    cv = cross_val_metrics(settings.calibration, raw_confs, correct, effective_target,
                           min_points=settings.min_gold_for_calibration)
    if cv is not None:
        threshold = cv["threshold"]
        achieved = cv["achieved"]
        ece_after = cv["ece"]
        cross_validated = True
        # CI from the out-of-fold values, so it inherits the honest estimate.
        ci = bootstrap_coverage_ci(cv["oof"], correct, effective_target)
    else:
        threshold, _gold_cov, achieved = coverage_at_precision(
            cal_confs, cal_correct, effective_target)
        ece_after = ece(cal_confs, cal_correct) if cal_confs else 0.0
        cross_validated = False
        ci = bootstrap_coverage_ci(cal_confs, cal_correct, effective_target) \
            if cal_confs else None

    apply_gate(direct, threshold)

    n_vetoed = 0
    if judge is not None:
        items = {it.id: it for it in storage.get_items(dataset_id)}
        for p in direct:
            if not p.auto_applied:
                continue
            it = items.get(p.item_id)
            ok, reason = judge.review(it, taxonomy, p.label) if it else (True, "")
            if not ok:
                p.auto_applied = False
                p.routed = True
                p.rationale = f"JUDGE VETO: {reason} | {p.rationale}"
                n_vetoed += 1

    events = storage.get_events(dataset_id)
    human_resolved = {e.item_id for e in events
                      if e.routed_to_human and e.final_label is not None}
    human_touched = {e.item_id for e in events if e.routed_to_human}
    already_audited = {e.item_id for e in events
                       if e.routed_to_human and e.route_reason == "audit"}

    # Near-duplicate propagation: every untouched member is rebuilt as a mirror
    # of its representative — same label, same confidence, same gate state — so
    # re-gates keep the whole group in lockstep. A member a human has touched
    # is emancipated: it keeps its own row and is never mirrored again.
    members, member_final, emancipated = [], {}, []
    n_propagated = 0
    if clusters:
        item_finals = {it.id: it.final_label for it in storage.get_items(dataset_id)}
        old_rows = {p.item_id: p for p in all_preds if p.item_id in clusters}
        for mid, (rid, sim) in sorted(clusters.items()):
            rp = by_item.get(rid)
            if mid in human_touched or rp is None:
                if mid in old_rows:
                    emancipated.append(old_rows[mid])
                continue
            members.append(Prediction(
                item_id=mid, dataset_id=dataset_id, taxonomy_id=rp.taxonomy_id,
                label=rp.label, confidence_raw=rp.confidence_raw,
                confidence_calibrated=rp.confidence_calibrated,
                agreement=rp.agreement,
                rationale=f"PROPAGATED from {rid} (cosine {sim:.2f})",
                votes={}, distribution=dict(rp.distribution),
                auto_applied=rp.auto_applied, routed=rp.routed,
                source="propagated"))
            if rp.auto_applied:
                n_propagated += 1
            if rid in human_resolved:
                member_final[mid] = item_finals.get(rid)
    preds = direct + members + emancipated

    # A human already decided these: a re-gate must not put them back in the
    # queue (found dogfooding — the queue count claimed work that was done).
    for p in preds:
        if p.routed and p.item_id in human_resolved:
            p.routed = False

    # Audit sampling (docs/04): a deterministic ~audit_rate slice of the auto
    # set — propagated members included — is ALSO routed for human verification.
    # The label still ships (coverage unchanged), but the verdict checks the
    # SLA in production and feeds auto-region errors into gold, the one region
    # queue review never sees.
    n_audit = 0
    audit_rate = getattr(settings, "audit_rate", 0.0)
    for p in preds:
        p.audit = bool(p.auto_applied and audit_rate > 0
                       and p.item_id not in already_audited
                       and p.item_id not in human_resolved
                       and audit_pick(dataset_id, p.item_id, audit_rate))
        if p.audit:
            n_audit += 1

    n_auto = sum(1 for p in preds if p.auto_applied)
    n_queue = sum(1 for p in preds if p.routed)
    full_coverage = n_auto / len(preds) if preds else 0.0
    n_no_signal = sum(1 for p in direct if "labelers no-signal]" in (p.rationale or ""))

    if log_events:
        storage.delete_auto_events(dataset_id)  # idempotent: replace prior auto events
    for p in preds:
        storage.upsert_prediction(p)
        if p.auto_applied:
            if p.item_id in member_final:         # rep was corrected by a human
                storage.set_final_label(p.item_id, member_final[p.item_id])
            elif p.item_id not in human_resolved:  # an audit edit outranks the model label
                storage.set_final_label(p.item_id, p.label)
            if log_events:
                storage.append_event(_event_for(p, taxonomy, routed=False))
        elif p.routed and p.item_id not in human_resolved:
            storage.set_final_label(p.item_id, None)
        elif p.item_id in member_final:            # member of a queue-resolved rep
            storage.set_final_label(p.item_id, member_final[p.item_id])

    gate = GateResult(
        target_precision=target_precision, threshold=threshold, coverage=full_coverage,
        achieved_precision=achieved, n_auto=n_auto, n_queue=n_queue,
        n_gold=len(cal_confs), ece_before=ece_before, ece_after=ece_after,
        cross_validated=cross_validated, n_judge_vetoed=n_vetoed,
        n_audit_pending=n_audit, coverage_ci=(list(ci) if ci else None),
        n_propagated=n_propagated, autopilot_level=level,
        effective_target=(effective_target if level else None),
        n_no_signal=n_no_signal)
    if log_events:
        # Run-over-run instrumentation (docs/08 Phase 2): coverage up, human
        # effort down — every gating run appends one row.
        human_touches = sum(1 for e in events if e.routed_to_human)
        storage.append_run(dataset_id, gate, human_touches)
    return gate


def sync_cluster_members(storage, dataset_id, rep_item_id):
    """Mirror a representative's current state onto its propagated members.

    Called after a human action (or undo) touching a near-duplicate group so
    the group resolves — or un-resolves — together without waiting for a
    re-gate: accepting a rep bulk-accepts its members, an audit reject un-ships
    them, an undo puts them all back. Members with their own human events are
    emancipated and never synced. Returns the number of members updated.
    """
    clusters = storage.get_clusters(dataset_id)
    member_ids = [m for m, (r, _s) in clusters.items() if r == rep_item_id]
    if not member_ids:
        return 0
    rp = storage.get_prediction(rep_item_id)
    rep_item = storage.get_item(rep_item_id)
    if rp is None or rep_item is None:
        return 0
    n = 0
    for mid in sorted(member_ids):
        if storage.get_human_events_for_item(mid):
            continue   # emancipated
        sim = clusters[mid][1]
        old = storage.get_prediction(mid)
        storage.upsert_prediction(Prediction(
            item_id=mid, dataset_id=dataset_id, taxonomy_id=rp.taxonomy_id,
            label=rp.label, confidence_raw=rp.confidence_raw,
            confidence_calibrated=rp.confidence_calibrated, agreement=rp.agreement,
            rationale=f"PROPAGATED from {rep_item_id} (cosine {sim:.2f})",
            votes={}, distribution=dict(rp.distribution),
            auto_applied=rp.auto_applied, routed=rp.routed,
            audit=bool(old and old.audit and rp.auto_applied),
            source="propagated"))
        storage.set_final_label(mid, rep_item.final_label if not rp.routed else None)
        n += 1
    return n


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
        _sync_if_clustered(storage, dataset_id, e.item_id)
        return e.item_id
    remaining = storage.get_human_events_for_item(e.item_id)
    storage.set_final_label(e.item_id, remaining[-1].final_label if remaining else None)
    if p is not None:
        p.routed = True
        p.auto_applied = False
        storage.upsert_prediction(p)
    _sync_if_clustered(storage, dataset_id, e.item_id)
    return e.item_id


def _sync_if_clustered(storage, dataset_id, item_id):
    """After a human action or undo: keep the item's near-duplicate group in
    lockstep. A representative syncs its members; an un-done member falls back
    under its representative's mirror."""
    clusters = storage.get_clusters(dataset_id)
    if not clusters:
        return
    if item_id in clusters:
        sync_cluster_members(storage, dataset_id, clusters[item_id][0])
    elif any(rep == item_id for rep, _s in clusters.values()):
        sync_cluster_members(storage, dataset_id, item_id)


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
    # Resolving a near-duplicate representative resolves (or un-ships) its
    # whole group: the reviewer's one keypress is the cluster's bulk action.
    _sync_if_clustered(storage, p.dataset_id, item_id)
    return final
