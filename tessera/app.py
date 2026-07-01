"""High-level facade: file loaders + end-to-end orchestration used by the CLI/server."""
from __future__ import annotations

import json
import os

from .schemas import Dataset, Item, Taxonomy, GoldItem
from .storage import Storage
from .labelers import make_labelers
from .pipeline import run_labeling_pass, calibrate_and_gate


def repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sample_dir() -> str:
    """Bundled sample dataset, resolved relative to the installed package so it
    works from a pip-installed wheel (not just a source checkout)."""
    pkg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample_data")
    if os.path.isdir(pkg):
        return pkg
    return os.path.join(repo_root(), "sample_data")  # source-tree fallback


def load_taxonomy(path) -> Taxonomy:
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return Taxonomy(
        id=d.get("id", "tax"), name=d.get("name", "taxonomy"),
        version=int(d.get("version", 1)),
        label_type=d.get("label_type", "classification"),
        labels=d["labels"], definitions=d.get("definitions", {}),
        guidelines=d.get("guidelines", ""))


def load_items(path, dataset_id) -> list:
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            items.append(Item(id=str(d["id"]), dataset_id=dataset_id,
                              text=d["text"], meta=d.get("meta", {})))
    return items


def load_gold(path, dataset_id) -> list:
    gold = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            gold.append(GoldItem(item_id=str(d["id"]), dataset_id=dataset_id, label=d["label"]))
    return gold


def ingest(storage, dataset_id, name, items, taxonomy, gold=None):
    storage.add_dataset(Dataset(id=dataset_id, name=name))
    storage.add_taxonomy(taxonomy)
    storage.add_items(items)
    if gold:
        storage.add_gold(gold)


def run_full(storage, dataset_id, taxonomy, settings, target_precision=None):
    """Run the labeling pass then calibrate + gate. Returns GateResult."""
    target = target_precision if target_precision is not None else settings.target_precision
    labelers = make_labelers(settings)
    run_labeling_pass(storage, dataset_id, taxonomy, labelers, workers=settings.workers)
    return calibrate_and_gate(storage, dataset_id, taxonomy, target, settings)


def bootstrap_demo(storage, settings, dataset_id="demo", target_precision=None):
    """Load the bundled sample dataset and run the full loop. Returns (taxonomy, GateResult)."""
    sd = sample_dir()
    taxonomy = load_taxonomy(os.path.join(sd, "taxonomy.json"))
    items = load_items(os.path.join(sd, "intents.jsonl"), dataset_id)
    gold = load_gold(os.path.join(sd, "gold.jsonl"), dataset_id)
    ingest(storage, dataset_id, "Support intents (sample)", items, taxonomy, gold)
    gate = run_full(storage, dataset_id, taxonomy, settings, target_precision)
    return taxonomy, gate
