"""Validate a finished run against held-back ground truth.

The quality report promises a precision SLA calibrated on the gold sample.
This script checks whether that promise actually held on the items the
calibrator never saw — the honest, out-of-sample validation of the headline
coverage@precision number.

Usage:  python scripts/validate_run.py --db agnews.db --dataset agnews \
            --truth data/agnews/truth.jsonl [--gold data/agnews/gold.jsonl]
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, ".")
from tessera.storage import Storage   # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--truth", required=True, help="JSONL of {id, label} for all items")
    ap.add_argument("--gold", help="gold JSONL; if given, also reports the unseen-only split")
    args = ap.parse_args()

    truth = {}
    with open(args.truth, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                d = json.loads(line)
                truth[str(d["id"])] = d["label"]
    gold_ids = set()
    if args.gold:
        with open(args.gold, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    gold_ids.add(str(json.loads(line)["id"]))

    storage = Storage(args.db)
    preds = storage.get_predictions(args.dataset)
    auto = [p for p in preds if p.auto_applied]

    def precision(subset):
        scored = [(p.label == truth[p.item_id]) for p in subset if p.item_id in truth]
        return (sum(scored) / len(scored), len(scored)) if scored else (0.0, 0)

    print(f"items: {len(preds)}   auto-applied: {len(auto)} "
          f"({len(auto) / len(preds):.1%} coverage)   routed: {len(preds) - len(auto)}")
    prec, n = precision(auto)
    print(f"TRUE precision of the auto-applied set (all {n} with truth): {prec:.2%}")
    if gold_ids:
        unseen = [p for p in auto if p.item_id not in gold_ids]
        prec_u, n_u = precision(unseen)
        print(f"TRUE precision on auto-applied items the calibrator NEVER saw "
              f"({n_u} items): {prec_u:.2%}")
    wrong = [(p.item_id, p.label, truth[p.item_id]) for p in auto
             if p.item_id in truth and p.label != truth[p.item_id]]
    if wrong:
        print("auto-applied errors:")
        for iid, got, want in wrong[:20]:
            print(f"  {iid}: predicted {got}, truth {want}")
    storage.close()


if __name__ == "__main__":
    main()
