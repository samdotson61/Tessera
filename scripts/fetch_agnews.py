"""Fetch a real classification dataset (AG News test split) for a live Tessera run.

Pure stdlib, keyless: uses the public HuggingFace datasets-server rows API.
Writes to data/agnews/:

  items.jsonl      400 news snippets ({id, text})
  taxonomy.json    the 4-class AG News taxonomy with a written rubric
  gold.jsonl       stratified 120-item gold sample (30 per class) — what a
                   human would label in a real project
  truth.jsonl      ground truth for ALL items. NOT an input to the pipeline:
                   scripts/validate_run.py uses it after a run to check that
                   the precision SLA held on data the calibrator never saw.

Usage:  python scripts/fetch_agnews.py [--n 400] [--gold-per-class 30] [--out data/agnews]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import urllib.request

API = ("https://datasets-server.huggingface.co/rows"
       "?dataset=fancyzhx%2Fag_news&config=default&split=test&offset={offset}&length={length}")
LABELS = ["world", "sports", "business", "scitech"]   # class order of the dataset

TAXONOMY = {
    "id": "agnews-topics",
    "name": "AG News topics",
    "version": 1,
    "label_type": "classification",
    "labels": LABELS,
    "definitions": {
        "world": "international affairs, politics, government, diplomacy, war, elections, world leaders",
        "sports": "games, matches, teams, players, championships, leagues, olympics, scores, athletes",
        "business": "companies, markets, stocks, earnings, economy, trade, deals, prices, oil",
        "scitech": "science, technology, software, internet, research, space, computers, gadgets, telecom",
    },
    "guidelines": ("Classify each news snippet by its primary topic. A story about a "
                   "tech company's finances or stock is business; a story about its "
                   "products or research is scitech. Sports business (transfers, TV "
                   "deals) is sports when the subject is the sport itself."),
}


def fetch(n):
    rows = []
    offset = 0
    while len(rows) < n:
        length = min(100, n - len(rows))   # the API caps length at 100
        with urllib.request.urlopen(API.format(offset=offset, length=length), timeout=30) as r:
            body = json.loads(r.read())
        page = body.get("rows", [])
        if not page:
            break
        rows.extend(page)
        offset += len(page)
    return rows[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=400)
    ap.add_argument("--gold-per-class", type=int, default=30)
    ap.add_argument("--out", default="data/agnews")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = fetch(args.n)
    print(f"fetched {len(rows)} rows")

    items, truth, by_class = [], [], {l: [] for l in LABELS}
    for r in rows:
        iid = f"ag{r['row_idx']:05d}"
        label = LABELS[r["row"]["label"]]
        items.append({"id": iid, "text": r["row"]["text"]})
        truth.append({"id": iid, "label": label})
        by_class[label].append(iid)

    # Stratified gold: the ~30-per-class sample a human would label first.
    rng = random.Random(args.seed)
    gold_ids = set()
    for label, ids in by_class.items():
        rng.shuffle(ids)
        gold_ids |= set(ids[:args.gold_per_class])
    gold = [t for t in truth if t["id"] in gold_ids]

    def dump(name, records):
        path = os.path.join(args.out, name)
        with open(path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")
        print(f"wrote {len(records):4d} -> {path}")

    dump("items.jsonl", items)
    dump("gold.jsonl", gold)
    dump("truth.jsonl", truth)
    with open(os.path.join(args.out, "taxonomy.json"), "w", encoding="utf-8") as f:
        json.dump(TAXONOMY, f, indent=2)
    print(f"wrote taxonomy -> {os.path.join(args.out, 'taxonomy.json')}")
    counts = {l: len(v) for l, v in by_class.items()}
    print(f"class counts: {counts}")


if __name__ == "__main__":
    main()
