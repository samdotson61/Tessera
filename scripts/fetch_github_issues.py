"""Fetch labeled GitHub issues as a Tessera triage corpus (stdlib only).

Builds items.jsonl (id, text = title + body) and truth.jsonl (id, label =
the repo's own maintainer-applied label, held back for validation) from any
public repo whose issues carry exactly one of the labels you name. This is
how the issue-triage case study corpus was built (docs/case-study §1).

Usage:
  python scripts/fetch_github_issues.py --repo microsoft/vscode \
      --labels 'bug,feature-request,*question' --per-label 170 \
      [--since 2026-01-01] [--out data/triage]

Unauthenticated requests are rate-limited (10 searches/min); set
GITHUB_TOKEN for headroom. Label names with '*' need the quotes.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import time
import urllib.parse
import urllib.request


def search(repo, label, since, page, token):
    q = f'repo:{repo} is:issue label:"{label}"' + (f" created:>={since}" if since else "")
    url = ("https://api.github.com/search/issues?"
           + urllib.parse.urlencode({"q": q, "per_page": 100, "page": page,
                                     "sort": "created", "order": "desc"}))
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        **({"Authorization": f"Bearer {token}"} if token else {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--labels", required=True,
                    help="comma list; each fetched issue must carry exactly ONE of these")
    ap.add_argument("--per-label", type=int, default=150)
    ap.add_argument("--since", default="", help="created:>= date filter (YYYY-MM-DD)")
    ap.add_argument("--out", default=".", help="output directory")
    ap.add_argument("--max-chars", type=int, default=1400)
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    labels = [l.strip() for l in args.labels.split(",") if l.strip()]
    kind = set(labels)
    slug = {l: l.lstrip("*") for l in labels}   # '*question' -> 'question'
    items, truth, seen = [], [], set()
    for label in labels:
        got, page = 0, 1
        while got < args.per_label and page <= 5:
            data = search(args.repo, label, args.since, page, token)
            rows = data.get("items", [])
            if not rows:
                break
            for it in rows:
                names = {l["name"] for l in it.get("labels", [])}
                if len(names & kind) != 1 or it["number"] in seen:
                    continue    # exactly one target label; no duplicates
                seen.add(it["number"])
                body = (it.get("body") or "").strip().replace("\r", "")
                iid = f"gh{it['number']}"
                items.append({"id": iid,
                              "text": (it["title"].strip() + "\n\n" + body)[:args.max_chars]})
                truth.append({"id": iid, "label": slug[label]})
                got += 1
                if got >= args.per_label:
                    break
            page += 1
            time.sleep(2 if token else 7)
        print(f"{slug[label]}: {got}")

    random.Random(7).shuffle(items)   # decouple file order from class
    os.makedirs(args.out, exist_ok=True)
    for name, rows in (("items.jsonl", items), ("truth.jsonl", truth)):
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            for d in rows:
                f.write(json.dumps(d) + "\n")
    print(f"{len(items)} items -> {args.out}/items.jsonl "
          f"(truth.jsonl holds the maintainer labels — don't peek until validation)")


if __name__ == "__main__":
    main()
