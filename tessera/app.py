"""High-level facade: file loaders + end-to-end orchestration used by the CLI/server."""
from __future__ import annotations

import json
import os

from .schemas import Dataset, Item, Taxonomy, GoldItem
from .storage import Storage
from .engine.embed import dedup_groups
from .engine.specialist import SpecialistLabeler, train_consensus
from .labelers import make_labelers
from .labelers.judge import make_judge
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
            meta = dict(d.get("meta", {}))
            if "response_a" in d and "response_b" in d:   # pairwise/preference rows
                meta["response_a"] = d["response_a"]
                meta["response_b"] = d["response_b"]
            items.append(Item(id=str(d["id"]), dataset_id=dataset_id,
                              text=d.get("text", d.get("prompt", "")), meta=meta))
    return items


def load_gold(path, dataset_id, items=None) -> list:
    """Load gold rows. Classification/pairwise rows carry {id, label}; span rows
    carry {id, spans: [...]} where each span is either {start, end, type} or a
    quote {text, type} resolved against the item text (requires items)."""
    from .engine import spans as spans_mod
    by_id = {it.id: it for it in items} if items else {}
    gold = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            if "spans" in d:
                rows = d["spans"]
                quoted = [s for s in rows if "start" not in s]
                offset = [s for s in rows if "start" in s]
                if quoted:
                    it = by_id.get(str(d["id"]))
                    if it is None:
                        raise ValueError(f"gold row {d['id']} quotes span text but the "
                                         "item is not loaded; pass items to load_gold")
                    resolved, unresolved = spans_mod.resolve_quoted(quoted, it.text)
                    if unresolved:
                        raise ValueError(f"gold row {d['id']}: quote(s) not found in "
                                         f"item text: {unresolved}")
                    offset.extend(resolved)
                label = spans_mod.canonical(offset)
            else:
                label = d["label"]
            gold.append(GoldItem(item_id=str(d["id"]), dataset_id=dataset_id, label=label))
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
    examples = None
    if getattr(settings, "fewshot", 0):
        gold = storage.get_gold(dataset_id)
        items = {it.id: it for it in storage.get_items(dataset_id)}
        examples = [(items[iid].render(), lab) for iid, lab in gold.items() if iid in items]
    labelers = make_labelers(settings, examples=examples)

    # Consensus gate (docs/04): the Tier-0 specialist joins the ensemble,
    # trained only on the TRAIN half of the trusted labels (the gate then
    # calibrates on the other half — see calibrate_and_gate's leak guard).
    # Disagreement with the LLM flattens confidence and routes; measured, the
    # agreement subset ran 95.5% true against 66.4% where they disagreed.
    if getattr(settings, "specialist", False):
        spec, _n = train_consensus(storage, dataset_id, taxonomy,
                                   getattr(settings, "specialist_min_train", 10))
        if spec is not None:
            labelers = list(labelers) + [SpecialistLabeler(spec)]

    # Near-duplicate propagation (docs/05): only cluster representatives — and
    # everything holding gold — hit the LLM; members mirror their rep at the
    # gate. On redundant corpora this multiplies throughput by the duplication
    # factor without leaving the audit universe.
    only_ids = None
    prop = float(getattr(settings, "propagate", 0) or 0)
    if prop > 0:
        items = storage.get_items(dataset_id)
        groups = dedup_groups({it.id: it.render() for it in items}, prop,
                              force_reps=set(storage.get_gold(dataset_id)))
        storage.set_clusters(dataset_id, groups)
        only_ids = {it.id for it in items} - set(groups)
    else:
        storage.set_clusters(dataset_id, {})

    run_labeling_pass(storage, dataset_id, taxonomy, labelers,
                      workers=settings.workers, only_ids=only_ids)
    return calibrate_and_gate(storage, dataset_id, taxonomy, target, settings,
                              judge=make_judge(settings))


def bootstrap_demo(storage, settings, dataset_id="demo", target_precision=None,
                   sample="intents"):
    """Load a bundled sample dataset and run the full loop. Returns (taxonomy, GateResult).

    sample: "intents" (classification) or "pairwise" (A/B response preference).
    """
    sd = sample_dir()
    if sample == "pairwise":
        taxonomy = load_taxonomy(os.path.join(sd, "pairwise_taxonomy.json"))
        items = load_items(os.path.join(sd, "pairwise.jsonl"), dataset_id)
        gold = load_gold(os.path.join(sd, "pairwise_gold.jsonl"), dataset_id)
        name = "Response preference (sample)"
    elif sample == "span":
        taxonomy = load_taxonomy(os.path.join(sd, "span_taxonomy.json"))
        items = load_items(os.path.join(sd, "span.jsonl"), dataset_id)
        gold = load_gold(os.path.join(sd, "span_gold.jsonl"), dataset_id, items=items)
        name = "Entity spans (sample)"
    else:
        taxonomy = load_taxonomy(os.path.join(sd, "taxonomy.json"))
        items = load_items(os.path.join(sd, "intents.jsonl"), dataset_id)
        gold = load_gold(os.path.join(sd, "gold.jsonl"), dataset_id)
        name = "Support intents (sample)"
    ingest(storage, dataset_id, name, items, taxonomy, gold)
    gate = run_full(storage, dataset_id, taxonomy, settings, target_precision)
    return taxonomy, gate
