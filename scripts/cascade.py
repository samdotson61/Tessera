"""The organization-scale cascade, measured end to end.

Tier 0: the trained specialist labels everything at its own calibrated gate
        (microseconds per item).
Tier 1: the local LLM labels only what Tier 0 routed, at its own gate.
Tier 2: whatever both tiers route is the human queue.

Each tier calibrates against the gold it can see and is validated against
held-back truth. This is the shape that makes a massive corpus feasible: the
specialist prices the easy mass, the LLM prices the hard middle, humans price
the tail — and corrections retrain Tier 0.

Usage:
  python scripts/cascade.py --data items.jsonl --taxonomy tax.json \
      --gold gold.jsonl --truth truth.jsonl [--target 0.95] \
      [--llm-url http://127.0.0.1:8091/v1/chat/completions] [--model q]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, ".")
from tessera.app import ingest, load_gold, load_items, load_taxonomy   # noqa: E402
from tessera.config import Settings                                    # noqa: E402
from tessera.engine.specialist import Specialist, SpecialistLabeler    # noqa: E402
from tessera.labelers import LLMLabeler                                # noqa: E402
from tessera.pipeline import calibrate_and_gate, run_labeling_pass     # noqa: E402
from tessera.storage import Storage                                    # noqa: E402


def tier(storage, dataset_id, taxonomy, labelers, settings, subset=None):
    """Label (a subset of) the dataset with the given labelers and gate."""
    if subset is not None:
        all_items = storage.get_items(dataset_id)
        keep = {it.id for it in all_items} & subset
        # temp view: label only the subset by running the pass on a filtered db
    run_labeling_pass(storage, dataset_id, taxonomy, labelers, workers=4)
    return calibrate_and_gate(storage, dataset_id, taxonomy,
                              settings.target_precision, settings)


def true_stats(storage, dataset_id, truth):
    preds = storage.get_predictions(dataset_id)
    auto = [p for p in preds if p.auto_applied]
    ok = sum(1 for p in auto if p.label == truth.get(p.item_id))
    return len(auto), (ok / len(auto) if auto else float("nan"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--taxonomy", required=True)
    ap.add_argument("--gold", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--target", type=float, default=0.95)
    ap.add_argument("--llm-url", default="")
    ap.add_argument("--model", default="q")
    args = ap.parse_args()

    truth = {json.loads(l)["id"]: json.loads(l)["label"]
             for l in open(args.truth) if l.strip()}
    taxonomy = load_taxonomy(args.taxonomy)
    items = load_items(args.data, "cascade")
    gold = load_gold(args.gold, "cascade", items=items)
    settings = Settings(target_precision=args.target, cache_path="none")
    tmp = tempfile.mkdtemp()

    # ---- Tier 0: specialist ----
    # Train/calibrate SPLIT: the gate must be calibrated on gold the
    # specialist never trained on, or training memorization masquerades as
    # confidence and the gate passes everything (measured: cv_est 100%).
    gold_map = {g.item_id: g.label for g in gold}
    by_id = {it.id: it for it in items}
    ids = sorted(gold_map)
    train_ids = set(ids[::2])
    calib_gold = [g for g in gold if g.item_id not in train_ids]
    st0 = Storage(os.path.join(tmp, "t0.db"))
    ingest(st0, "cascade", "cascade", items, taxonomy, calib_gold)
    t0 = time.time()
    spec = Specialist(taxonomy.labels).train(
        [by_id[i].render() for i in train_ids if i in by_id],
        [gold_map[i] for i in train_ids if i in by_id])
    run_labeling_pass(st0, "cascade", taxonomy, [SpecialistLabeler(spec)], workers=1)
    g0 = calibrate_and_gate(st0, "cascade", taxonomy, args.target, settings)
    t0_wall = time.time() - t0
    n0, p0 = true_stats(st0, "cascade", truth)
    routed0 = {p.item_id for p in st0.get_predictions("cascade") if p.routed}
    print(f"Tier 0 (specialist: trained on {len(train_ids)} gold, "
          f"calibrated on the other {len(calib_gold)}): {t0_wall:.1f}s total")
    print(f"  auto {n0}/{len(items)} ({g0.coverage:.1%})  cv_est {g0.achieved_precision:.1%}  "
          f"TRUE {p0:.1%}  routed on {len(routed0)}")
    st0.close()

    if not args.llm_url:
        print("no --llm-url: stopping after Tier 0")
        return

    # ---- Tier 1: LLM on the residue only ----
    residue = [it for it in items if it.id in routed0]
    res_gold = [g for g in gold if g.item_id in routed0]
    st1 = Storage(os.path.join(tmp, "t1.db"))
    ingest(st1, "cascade", "cascade", residue, taxonomy, res_gold)
    t1 = time.time()
    llm = LLMLabeler("openai", "", model=args.model, n_samples=1,
                     base_url=args.llm_url, logprobs=True)
    run_labeling_pass(st1, "cascade", taxonomy, [llm], workers=4)
    g1 = calibrate_and_gate(st1, "cascade", taxonomy, args.target, settings)
    t1_wall = time.time() - t1
    n1, p1 = true_stats(st1, "cascade", truth)
    print(f"Tier 1 (LLM on {len(residue)} residue items, {len(res_gold)} gold): {t1_wall:.1f}s")
    print(f"  auto {n1}/{len(residue)} ({g1.coverage:.1%})  cv_est {g1.achieved_precision:.1%}  "
          f"TRUE {p1:.1%}")

    total_auto = n0 + n1
    total_ok = (round(n0 * p0) if n0 else 0) + (round(n1 * p1) if n1 else 0)
    prec = f"{total_ok/total_auto:.1%}" if total_auto else "—"
    print(f"\nCASCADE: {total_auto}/{len(items)} auto ({total_auto/len(items):.1%} coverage) "
          f"at {prec} TRUE precision, "
          f"{len(items)-total_auto} to humans, wall {t0_wall+t1_wall:.1f}s")
    st1.close()


if __name__ == "__main__":
    main()
