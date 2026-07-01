"""Evaluation metrics, including the north-star coverage@precision.

See docs/04. coverage@precision answers: "at a confidence threshold that holds the
target precision on the gold set, what fraction of items can we auto-apply?"
"""
from __future__ import annotations


def precision_recall_f1(y_true, y_pred, labels=None):
    labels = labels or sorted(set(y_true) | set(y_pred))
    per = {}
    for lab in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if p == lab and t == lab)
        fp = sum(1 for t, p in zip(y_true, y_pred) if p == lab and t != lab)
        fn = sum(1 for t, p in zip(y_true, y_pred) if p != lab and t == lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per[lab] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
    accuracy = (sum(1 for t, p in zip(y_true, y_pred) if t == p) / len(y_true)) if y_true else 0.0
    macro_p = (sum(v["precision"] for v in per.values()) / len(per)) if per else 0.0
    return {"per_label": per, "accuracy": accuracy, "macro_precision": macro_p}


def coverage_at_precision(confs, correct, target_precision):
    """Find the highest-coverage confidence threshold whose precision on the gold
    set is >= target_precision.

    Returns (threshold, coverage, achieved_precision) measured on this labeled set.
    We scan each distinct confidence value as a candidate threshold t and evaluate
    the set {items with confidence >= t} (so ties are handled consistently — the
    same rule the gate later applies to the full dataset). Among thresholds whose
    selected-set precision clears the target, we keep the one with the largest
    coverage. If none clears it, coverage is 0.
    """
    pairs = list(zip(confs, [1 if c else 0 for c in correct]))
    n = len(pairs)
    if n == 0:
        return (1.01, 0.0, 0.0)
    best = None  # (threshold, coverage, precision) maximizing coverage
    for t in sorted(set(confs), reverse=True):
        sel = [c for cf, c in pairs if cf >= t]
        if not sel:
            continue
        prec = sum(sel) / len(sel)
        cov = len(sel) / n
        if prec >= target_precision and (best is None or cov > best[1]):
            best = (t, cov, prec)
    if best is None:
        return (1.01, 0.0, 0.0)
    return best


def bootstrap_coverage_ci(confs, correct, target_precision, n_boot=200, seed=0):
    """Percentile bootstrap CI for coverage@precision.

    Each resample redraws the gold set with replacement and re-runs the whole
    threshold-selection procedure, so the interval reflects how much the
    reported coverage depends on which gold items we happened to label.
    Returns (lo, hi) at 2.5/97.5 percentiles; (0.0, 0.0) with no data.
    """
    import random
    n = len(confs)
    if n == 0:
        return (0.0, 0.0)
    rng = random.Random(seed)
    covs = []
    for _ in range(n_boot):
        idx = [rng.randrange(n) for _ in range(n)]
        _, cov, _ = coverage_at_precision([confs[i] for i in idx],
                                          [correct[i] for i in idx], target_precision)
        covs.append(cov)
    covs.sort()
    lo = covs[max(0, int(0.025 * n_boot) - 1)]
    hi = covs[min(n_boot - 1, int(0.975 * n_boot))]
    return (lo, hi)


def ece(confs, correct, n_bins=10):
    """Expected Calibration Error: average |confidence - accuracy| weighted by bin size."""
    if not confs:
        return 0.0
    sums = [0.0] * n_bins
    conf_sums = [0.0] * n_bins
    counts = [0] * n_bins
    for c, ok in zip(confs, correct):
        b = min(n_bins - 1, int(c * n_bins))
        sums[b] += 1.0 if ok else 0.0
        conf_sums[b] += c
        counts[b] += 1
    n = len(confs)
    total = 0.0
    for i in range(n_bins):
        if counts[i]:
            acc = sums[i] / counts[i]
            avg_conf = conf_sums[i] / counts[i]
            total += (counts[i] / n) * abs(acc - avg_conf)
    return total
