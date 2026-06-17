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
    print(f"  AUTO-LABELED     : {gate.n_auto} items ({pct}% of dataset) at conf >= {gate.threshold:.3f}")
    print(f"  routed to human  : {gate.n_queue} items")
    print(f"  gold set         : {gate.n_gold} items")
    print(f"  calibration ECE  : {gate.ece_before:.3f} -> {gate.ece_after:.3f} (lower is better)")
    print(f"\n  >> Auto-labeled {pct}% of the data at >= {gate.target_precision:.0%} precision; "
          f"a human only touches the remaining {gate.n_queue}.")


def cmd_demo(args):
    settings = Settings.from_env()
    settings.db_path = args.db
    storage = Storage(settings.db_path)
    taxonomy, gate = appmod.bootstrap_demo(storage, settings,
                                            target_precision=args.target)
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
    gold = appmod.load_gold(args.gold, args.dataset) if args.gold else None
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
                              settings.target_precision, settings, log_events=False)
    report = build_quality_report(storage, args.dataset, taxonomy, gate)
    print(json.dumps(to_dict(report), indent=2))
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
                              settings.target_precision, settings, log_events=False)
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
