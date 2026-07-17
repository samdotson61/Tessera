"""Verification pass (docs/04, Layer 3).

Deterministic rubric checks catch the dumb errors for free (label must be in the
taxonomy, non-empty). An LLM-as-judge is the production add-on; here we expose a
lightweight judge that flags violations or very-low-confidence outputs. A judge
shares blind spots with the labeler, so deterministic checks are the trustworthy
floor — see the correlated-error caveat in docs/04.
"""
from __future__ import annotations


def deterministic_checks(label, taxonomy, item=None):
    """Return a list of violation strings (empty = passes).

    For span taxonomies the label is a canonical span-set JSON; bounds, order,
    overlap, and entity types are validated against the item text (an empty
    span list is a legitimate annotation, not a violation).
    """
    if taxonomy.label_type == "span":
        from . import spans as spans_mod
        if label is None:
            return ["empty label"]
        try:
            parsed = spans_mod.parse(label)
        except (ValueError, KeyError, TypeError) as e:
            return [f"unparsable span annotation: {e}"]
        text = item.render() if item is not None else ""
        return spans_mod.validate(parsed, text, set(taxonomy.labels)) if item is not None else []
    violations = []
    if not label:
        violations.append("empty label")
    elif label not in taxonomy.labels:
        violations.append(f"label '{label}' not in taxonomy")
    return violations


def judge(label, confidence, taxonomy, floor=0.2):
    """Return (ok, reason). Fails on rubric violations or confidence below floor."""
    viol = deterministic_checks(label, taxonomy)
    if viol:
        return (False, "; ".join(viol))
    if confidence < floor:
        return (False, f"confidence {confidence:.2f} below floor {floor}")
    return (True, "")
