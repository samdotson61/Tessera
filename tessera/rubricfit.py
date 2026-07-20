"""Session-zero rubric calibration (docs/case-study-issue-triage.md, lesson 1).

Before any labeling engagement, measure how well a DRAFT rubric reproduces
the partner's own labeling convention: run the configured labeler over a
stratified sample of their historically labeled items and diff against their
labels. The disagreement patterns are the kickoff agenda — each cluster is a
convention the rubric text doesn't carry yet.

Measured origin: a careful annotator following a phrasing-based rubric
agreed with VS Code maintainers only 69.5% of the time, invisible to every
internal metric; rewriting one rubric paragraph moved the machine-readable
agreement from ~72% to ~83% and partner-gold coverage from 11% to 91%.

Agreement here measures rubric-fit THROUGH the configured model, so it is
capped by model ability — treat the patterns as the signal, the absolute
number as a floor.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile

from .app import ingest
from .pipeline import run_labeling_pass
from .storage import Storage


def stratified_by_label(authority: dict, n: int) -> list:
    """Up to n item ids, round-robin across the AUTHORITY labels so the check
    covers their whole convention, not just the majority class. Deterministic
    (sorted ids per class)."""
    by_label = {}
    for iid, lab in sorted(authority.items()):
        by_label.setdefault(lab, []).append(iid)
    order = sorted(by_label)
    out = []
    while len(out) < n and any(by_label[l] for l in order):
        for lab in order:
            if by_label[lab] and len(out) < n:
                out.append(by_label[lab].pop(0))
    return out


def check(items, authority, taxonomy, labelers, n=100, workers=8):
    """Label a stratified sample of authority-labeled items under the draft
    rubric and diff. Returns a report dict:

      n, agreement, per_label {label: (ok, total)},
      patterns [((authority_label, predicted_label), [item_id, ...]), ...],
      n_no_signal
    """
    by_id = {it.id: it for it in items}
    usable = {iid: lab for iid, lab in authority.items() if iid in by_id}
    picked = stratified_by_label(usable, n)
    # rehome the sample under this check's own dataset id — callers' items may
    # belong to any dataset
    sample = [dataclasses.replace(by_id[iid], dataset_id="rubricfit")
              for iid in picked]

    tmp = os.path.join(tempfile.mkdtemp(), "rubricfit.db")
    st = Storage(tmp)
    ingest(st, "rubricfit", "rubricfit", sample, taxonomy, None)
    preds = {p.item_id: p
             for p in run_labeling_pass(st, "rubricfit", taxonomy, labelers,
                                        workers=workers)}
    st.close()

    per_label, patterns = {}, {}
    ok = no_signal = 0
    for iid in picked:
        want = usable[iid]
        p = preds.get(iid)
        got = p.label if p else None
        if p is not None and "labelers no-signal]" in (p.rationale or ""):
            no_signal += 1
        o, t = per_label.get(want, (0, 0))
        hit = got == want
        per_label[want] = (o + (1 if hit else 0), t + 1)
        if hit:
            ok += 1
        else:
            patterns.setdefault((want, got), []).append(iid)
    return {
        "n": len(picked),
        "agreement": (ok / len(picked)) if picked else 0.0,
        "per_label": per_label,
        "patterns": sorted(patterns.items(), key=lambda kv: -len(kv[1])),
        "n_no_signal": no_signal,
    }


def print_report(report, items, max_examples=3, width=140):
    by_id = {it.id: it for it in items}
    print("\n=== rubric-check: draft rubric vs the partner's own labels ===")
    print(f"  sample            : {report['n']} items (stratified across their labels)")
    print(f"  agreement         : {report['agreement']:.1%}  "
          "(rubric-fit through the configured model — a floor, not a ceiling)")
    for lab, (o, t) in sorted(report["per_label"].items()):
        print(f"    {lab:<20} {o}/{t} ({o / t:.0%})" if t else f"    {lab:<20} —")
    if report["n_no_signal"]:
        print(f"  !! NO SIGNAL      : {report['n_no_signal']} item(s) got no usable model "
              "answer — fix the serving stack before trusting this number "
              "(reasoning mode? llama-server: --chat-template-kwargs "
              "'{\"enable_thinking\":false}')")
    if not report["patterns"]:
        print("  no disagreements in the sample — grow the sample before celebrating.")
        return
    print("\n  disagreement patterns (their label <- rubric read) — the kickoff agenda:")
    for (want, got), ids in report["patterns"]:
        print(f"  - they say {want!r}, the rubric reads {got!r}: {len(ids)} item(s)")
        for iid in ids[:max_examples]:
            text = " ".join(by_id[iid].text.split())[:width]
            print(f"      {iid}: {text}")
    print("\n  next: rewrite the rubric where patterns cluster, re-run, and co-label "
          "the residue in the kickoff (docs/case-study-issue-triage.md §6).")
