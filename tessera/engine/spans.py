"""Span/NER label support (docs/08 Phase 1, third beachhead label type).

A span item's label is a SET of (start, end, type) annotations over the item
text. The whole engine stays per-item: the set is serialized to one canonical
JSON string (sorted, minimal) that flows through Prediction.label, gold, final
labels, events, and exports unchanged — two annotations are equal iff their
canonical strings are equal.

Confidence follows the docs/08 guidance to start simple: each labeler/sample
votes for one whole span-set, the existing ensemble/self-consistency averaging
turns identical sets into high confidence and any disagreement (a missed
entity, a boundary difference, a type confusion) into a flatter distribution
that routes to a human. Deterministic validators are the floor: bounds, order,
overlap, and entity types are checked before anything can auto-apply.
"""
from __future__ import annotations

import json


def canonical(spans) -> str:
    """Serialize spans to the canonical label string: sorted, minimal JSON."""
    norm = sorted({(int(s["start"]), int(s["end"]), str(s["type"])) for s in spans})
    return json.dumps([{"start": a, "end": b, "type": t} for a, b, t in norm],
                      separators=(",", ":"))


def parse(label: str):
    """Canonical (or near-canonical) label string -> list of span dicts."""
    if not label:
        return []
    data = json.loads(label)
    if isinstance(data, dict):
        data = data.get("spans", [])
    return [{"start": int(s["start"]), "end": int(s["end"]), "type": str(s["type"])}
            for s in data]


def validate(spans, text: str, allowed_types) -> list:
    """Deterministic rubric checks. Returns violation strings (empty = passes)."""
    v = []
    seen = []
    for s in spans:
        a, b, t = s["start"], s["end"], s["type"]
        if t not in allowed_types:
            v.append(f"span type '{t}' not in taxonomy")
        if not (0 <= a < b <= len(text)):
            v.append(f"span [{a},{b}) out of bounds for text of length {len(text)}")
            continue
        if not text[a:b].strip():
            v.append(f"span [{a},{b}) covers only whitespace")
        for a2, b2 in seen:
            if a < b2 and a2 < b:
                v.append(f"span [{a},{b}) overlaps [{a2},{b2})")
                break
        seen.append((a, b))
    return v


def resolve_quoted(quoted_spans, text: str):
    """Resolve LLM-quoted spans ({"text": ..., "type": ...}) to offsets.

    Models are unreliable with character offsets, so the span prompt asks for
    the exact quoted substring instead; each quote is located in the item text
    (first unclaimed occurrence). Returns (spans, unresolved_quotes).
    """
    spans, unresolved, claimed = [], [], []
    for q in quoted_spans:
        needle = str(q.get("text", ""))
        if not needle:
            unresolved.append(needle)
            continue
        pos = 0
        placed = False
        while True:
            i = text.find(needle, pos)
            if i < 0:
                break
            j = i + len(needle)
            if all(not (i < b and a < j) for a, b in claimed):
                spans.append({"start": i, "end": j, "type": str(q.get("type", ""))})
                claimed.append((i, j))
                placed = True
                break
            pos = i + 1
        if not placed:
            unresolved.append(needle)
    return spans, unresolved


def corpus_per_type_precision(label_pairs, types):
    """Span-level precision per entity type over (pred_label, gold_label) pairs."""
    tp = {t: 0 for t in types}
    n_pred = {t: 0 for t in types}
    for pred_label, gold_label in label_pairs:
        gset = {(s["start"], s["end"], s["type"]) for s in parse(gold_label)}
        for s in parse(pred_label):
            t = s["type"]
            if t not in n_pred:
                continue
            n_pred[t] += 1
            if (s["start"], s["end"], s["type"]) in gset:
                tp[t] += 1
    return {t: (tp[t] / n_pred[t]) if n_pred[t] else 0.0 for t in types}


def per_type_prf(pred_spans, gold_spans, types):
    """Span-level (exact-match) precision/recall/F1 per entity type."""
    out = {}
    for t in types:
        p = {(s["start"], s["end"]) for s in pred_spans if s["type"] == t}
        g = {(s["start"], s["end"]) for s in gold_spans if s["type"] == t}
        tp = len(p & g)
        prec = tp / len(p) if p else (1.0 if not g else 0.0)
        rec = tp / len(g) if g else 1.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[t] = {"precision": prec, "recall": rec, "f1": f1, "support": len(g)}
    return out
