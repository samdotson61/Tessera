"""Command-line interface.

    python -m tessera demo                 # run the whole loop on the bundled sample
    python -m tessera demo --serve         # ...then open the review UI
    python -m tessera label --data d.jsonl --taxonomy t.json [--gold g.jsonl]
    python -m tessera report --dataset demo
    python -m tessera export --dataset demo --out labels.jsonl
    python -m tessera serve --dataset demo
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import Settings
from .labelers.judge import make_judge
from .storage import Storage
from .schemas import to_dict
from . import app as appmod
from .pipeline import calibrate_and_gate
from .quality import build_quality_report
from .export import export_jsonl
from .flywheel import export_training_pairs
from .server import serve


def _taxonomy_for_dataset(storage, dataset_id):
    preds = storage.get_predictions(dataset_id)
    if not preds:
        return None
    return storage.get_taxonomy(preds[0].taxonomy_id)


def _print_summary(gate, taxonomy=None):
    pct = round(gate.coverage * 100, 1)
    print("\n=== coverage@precision ===")
    est = "cross-validated" if gate.cross_validated else "IN-SAMPLE (gold too small for CV)"
    print(f"  target precision : {gate.target_precision:.2%}")
    print(f"  achieved         : {gate.achieved_precision:.2%}  ({est})")
    ci = ""
    if gate.coverage_ci:
        ci = f"  [gold coverage 95% CI: {gate.coverage_ci[0]:.1%}-{gate.coverage_ci[1]:.1%}]"
    print(f"  AUTO-LABELED     : {gate.n_auto} items ({pct}% of dataset) at conf >= {gate.threshold:.3f}{ci}")
    print(f"  routed to human  : {gate.n_queue} items")
    print(f"  gold set         : {gate.n_gold} items")
    print(f"  calibration ECE  : {gate.ece_before:.3f} -> {gate.ece_after:.3f} (lower is better)")
    if gate.n_propagated:
        print(f"  propagated       : {gate.n_propagated} near-duplicate members mirror "
              f"their representative's label")
    if gate.autopilot_level:
        print(f"  AUTOPILOT        : level {gate.autopilot_level} — audit breach; gating at "
              f"{gate.effective_target:.2%} effective target")
    print(f"\n  >> Auto-labeled {pct}% of the data at >= {gate.target_precision:.0%} precision; "
          f"a human only touches the remaining {gate.n_queue}.")


def cmd_demo(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    storage = Storage(settings.db_path)
    sample = "pairwise" if args.pairwise else ("span" if args.span else "intents")
    taxonomy, gate = appmod.bootstrap_demo(storage, settings,
                                            target_precision=args.target,
                                            sample=sample)
    _print_summary(gate, taxonomy)
    report = build_quality_report(storage, "demo", taxonomy, gate)
    print("\n=== quality report ===")
    print(json.dumps(to_dict(report), indent=2))
    if args.serve:
        serve(storage, "demo", taxonomy, settings, gate_result=gate)
    storage.close()


def cmd_label(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    if args.target is not None:
        settings.target_precision = args.target
    storage = Storage(settings.db_path)
    taxonomy = appmod.load_taxonomy(args.taxonomy)
    items = appmod.load_items(args.data, args.dataset)
    gold = appmod.load_gold(args.gold, args.dataset, items=items) if args.gold else None
    appmod.ingest(storage, args.dataset, args.dataset, items, taxonomy, gold)
    gate = appmod.run_full(storage, args.dataset, taxonomy, settings)
    _print_summary(gate, taxonomy)
    storage.close()


def cmd_report(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    storage = Storage(settings.db_path)
    taxonomy = _taxonomy_for_dataset(storage, args.dataset)
    if not taxonomy:
        print(f"no predictions for dataset '{args.dataset}'. Run `label` or `demo` first.")
        return 1
    gate = calibrate_and_gate(storage, args.dataset, taxonomy,
                              settings.target_precision, settings, log_events=False,
                              judge=make_judge(settings))
    report = build_quality_report(storage, args.dataset, taxonomy, gate)
    print(json.dumps(to_dict(report), indent=2))
    runs = storage.get_runs(args.dataset, limit=8)
    if runs:
        print("\n=== run history (coverage up, human effort visible) ===")
        print(f"{'at':>20} {'coverage':>9} {'gold':>5} {'queue':>6} {'touches':>8}")
        for r in runs:
            print(f"{r['at']:>20} {r['coverage']:>9.1%} {r['n_gold']:>5} "
                  f"{r['n_queue']:>6} {r['human_touches']:>8}")
    storage.close()
    return 0


def cmd_export(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    storage = Storage(settings.db_path)
    n = export_jsonl(storage, args.dataset, args.out)
    print(f"exported {n} finalized labels -> {args.out}")
    if args.pairs:
        pairs = export_training_pairs(storage, args.dataset)
        with open(args.pairs, "w", encoding="utf-8") as f:
            for p in pairs:
                f.write(json.dumps(p) + "\n")
        print(f"exported {len(pairs)} training pairs (flywheel) -> {args.pairs}")
    storage.close()


def cmd_serve(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    if args.port:
        settings.port = args.port
    storage = Storage(settings.db_path)
    taxonomy = _taxonomy_for_dataset(storage, args.dataset)
    if not taxonomy:
        print(f"no data for dataset '{args.dataset}'. Run `python -m tessera demo` first.")
        return 1
    gate = calibrate_and_gate(storage, args.dataset, taxonomy,
                              settings.target_precision, settings, log_events=False,
                              judge=make_judge(settings))
    serve(storage, args.dataset, taxonomy, settings, gate_result=gate)
    storage.close()
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="tessera", description="Cursor for data labeling (MVP).")
    p.add_argument("--db", default="tessera.db", help="SQLite db path")
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("demo", help="run the full loop on the bundled sample dataset")
    d.add_argument("--target", type=float, default=0.95, help="target precision")
    d.add_argument("--serve", action="store_true", help="open the review UI afterwards")
    d.add_argument("--pairwise", action="store_true",
                   help="use the bundled A/B response-preference sample instead of intents")
    d.add_argument("--span", action="store_true",
                   help="use the bundled entity-span (NER) sample instead of intents")
    d.set_defaults(func=cmd_demo)

    l = sub.add_parser("label", help="ingest a dataset + taxonomy and run the loop")
    l.add_argument("--data", required=True, help="items JSONL ({id,text})")
    l.add_argument("--taxonomy", required=True, help="taxonomy JSON")
    l.add_argument("--gold", help="gold JSONL ({id,label})")
    l.add_argument("--dataset", default="ds1", help="dataset id")
    l.add_argument("--target", type=float, default=None, help="target precision")
    l.set_defaults(func=cmd_label)

    r = sub.add_parser("report", help="print the quality report for a dataset")
    r.add_argument("--dataset", default="demo")
    r.set_defaults(func=cmd_report)

    e = sub.add_parser("export", help="export finalized labels to JSONL")
    e.add_argument("--dataset", default="demo")
    e.add_argument("--out", default="labels.jsonl")
    e.add_argument("--pairs", help="also export flywheel training pairs to this path")
    e.set_defaults(func=cmd_export)

    s = sub.add_parser("serve", help="open the keyboard-first review UI")
    s.add_argument("--dataset", default="demo")
    s.add_argument("--port", type=int, default=None)
    s.set_defaults(func=cmd_serve)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
