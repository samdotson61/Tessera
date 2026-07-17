"""Simulate the review loop to measure the gold-growth coverage climb.

An oracle reviewer (held-back ground truth stands in for the human) works the
routed queue in router order, batch by batch; every decision grows the gold
set; the gate re-calibrates each round. This measures the *mechanism* docs/08
Phase 2 promises — coverage up, queue down, run over run — with zero spend.
It does NOT measure human effort realistically (the oracle never errs or
tires); say so when quoting numbers.

Usage:  python scripts/simulate_review.py --db run.db --dataset agnews \
            --truth data/agnews/truth.jsonl [--rounds 5] [--batch 50] [--target 0.95]

Tip: run on a COPY of the db — the simulation writes gold rows, events, and
final labels.
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")
from tessera.config import Settings                    # noqa: E402
from tessera.storage import Storage                    # noqa: E402
from tessera.engine.router import order_queue          # noqa: E402
from tessera.pipeline import calibrate_and_gate, record_human_action  # noqa: E402


def true_precision(preds, truth, exclude=()):
    scored = [(p.label == truth[p.item_id]) for p in preds
              if p.item_id in truth and p.item_id not in exclude]
    return (sum(scored) / len(scored), len(scored)) if scored else (float("nan"), 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--batch", type=int, default=50)
    ap.add_argument("--target", type=float, default=0.95)
    ap.add_argument("--audit-rate", type=float, default=0.0,
                    help="audit share of the auto set (0 = queue-only baseline)")
    ap.add_argument("--router", default="confidence", choices=["cluster", "confidence"],
                    help="review-queue ordering to simulate")
    args = ap.parse_args()

    truth = {}
    with open(args.truth, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                truth[str(d["id"])] = d["label"]

    storage = Storage(args.db)
    settings = Settings(db_path=args.db, target_precision=args.target,
                        audit_rate=args.audit_rate)
    preds = storage.get_predictions(args.dataset)
    taxonomy = storage.get_taxonomy(preds[0].taxonomy_id)

    reviewed = set()
    print(f"oracle reviewer on '{args.dataset}': {len(preds)} items, "
          f"{args.rounds} rounds x {args.batch} reviews, target {args.target:.0%}, "
          f"audit rate {args.audit_rate:.0%}")
    print(f"{'round':>5} {'gold':>5} {'thresh':>7} {'coverage':>9} {'cv_prec':>8} "
          f"{'true_auto':>10} {'true_nongold':>13} {'audit':>6} {'reviews':>8}")

    for rnd in range(args.rounds + 1):
        gate = calibrate_and_gate(storage, args.dataset, taxonomy, args.target, settings)
        preds = storage.get_predictions(args.dataset)
        auto = [p for p in preds if p.auto_applied]
        gold_ids = set(storage.get_gold(args.dataset))
        t_all, _ = true_precision(auto, truth)
        t_non, n_non = true_precision(auto, truth, exclude=gold_ids)
        print(f"{rnd:>5} {gate.n_gold:>5} {gate.threshold:>7.3f} {gate.coverage:>9.1%} "
              f"{gate.achieved_precision:>8.1%} {t_all:>10.1%} "
              f"{t_non:>12.1%}({n_non:>3}) {gate.n_audit_pending:>6} {len(reviewed):>8}")
        if rnd == args.rounds:
            break
        # Audit items first (they verify shipped labels — the SLA check), then
        # the routed queue in router order.
        item_map = {it.id: it for it in storage.get_items(args.dataset)}
        audits = sorted((p for p in preds if p.audit), key=lambda p: p.item_id)
        queue = audits + [p for p in order_queue(preds, items=item_map, mode=args.router)
                          if p.item_id not in reviewed]
        for p in queue[:args.batch]:
            want = truth.get(p.item_id)
            if want is None:
                continue
            action = "accept" if p.label == want else "edit"
            record_human_action(storage, taxonomy, p.item_id, action,
                                label=want, grow_gold=True, annotator="oracle-sim")
            reviewed.add(p.item_id)

    storage.close()


if __name__ == "__main__":
    main()
