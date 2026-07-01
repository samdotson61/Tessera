"""Confidence calibration (pure Python).

Raw model confidence is not a probability of correctness. We fit a monotonic map
from raw confidence to empirical accuracy on the gold set so "0.9" really means
~90% correct. See docs/04, Layer 2. Isotonic regression (via Pool-Adjacent-
Violators) is the default; histogram binning and identity are alternatives.
"""
from __future__ import annotations

from bisect import bisect_right


def _pav(values, weights):
    """Weighted Pool-Adjacent-Violators on values sorted by x ascending.

    Returns one non-decreasing fitted value per input position (the isotonic fit of
    the weighted 0/1 correctness targets). Weights let us pool tied x-values.
    """
    stack = []  # blocks: [mean_value, weight, size]
    for val, wt in zip(values, weights):
        stack.append([float(val), float(wt), 1])
        while len(stack) >= 2 and stack[-2][0] >= stack[-1][0]:
            a = stack.pop()
            b = stack.pop()
            w = a[1] + b[1]
            stack.append([(a[0] * a[1] + b[0] * b[1]) / w, w, a[2] + b[2]])
    fitted = []
    for value, _w, size in stack:
        fitted.extend([value] * size)
    return fitted


class IdentityCalibrator:
    def transform(self, c: float) -> float:
        return c

    def to_dict(self):
        return {"kind": "identity"}


class IsotonicCalibrator:
    def __init__(self):
        self.xs = []   # sorted raw confidences
        self.ys = []   # monotonic calibrated values

    def fit(self, confs, correct):
        # Aggregate duplicate confidences into one point (sum of correctness, count),
        # so tied x-values are pooled rather than ordered arbitrarily.
        agg, cnt = {}, {}
        for c, ok in zip(confs, correct):
            agg[c] = agg.get(c, 0.0) + (1.0 if ok else 0.0)
            cnt[c] = cnt.get(c, 0) + 1
        self.xs = sorted(agg)
        ybar = [agg[x] / cnt[x] for x in self.xs]
        weights = [cnt[x] for x in self.xs]
        self.ys = _pav(ybar, weights)
        return self

    def transform(self, c: float) -> float:
        if not self.xs:
            return c
        # i in [0, len(xs)-1] since bisect_right <= len(xs) and len(ys)==len(xs).
        i = bisect_right(self.xs, c) - 1
        if i < 0:
            return self.ys[0]
        return self.ys[i]

    def to_dict(self):
        return {"kind": "isotonic", "n": len(self.xs)}


class HistogramCalibrator:
    def __init__(self, n_bins: int = 10):
        self.n_bins = n_bins
        self.bin_acc = [0.0] * n_bins
        self.global_acc = 0.0

    def fit(self, confs, correct):
        sums = [0.0] * self.n_bins
        counts = [0] * self.n_bins
        for c, ok in zip(confs, correct):
            b = min(self.n_bins - 1, int(c * self.n_bins))
            sums[b] += 1.0 if ok else 0.0
            counts[b] += 1
        self.global_acc = (sum(1 for ok in correct if ok) / len(correct)) if correct else 0.0
        self.bin_acc = [(sums[i] / counts[i]) if counts[i] else self.global_acc
                        for i in range(self.n_bins)]
        return self

    def transform(self, c: float) -> float:
        b = min(self.n_bins - 1, int(c * self.n_bins))
        return self.bin_acc[b]

    def to_dict(self):
        return {"kind": "histogram", "bins": self.bin_acc}


def fit_calibrator(kind: str, confs, correct, min_points: int = 10):
    """Fit a calibrator; fall back to identity when there is too little gold data."""
    if len(confs) < min_points:
        return IdentityCalibrator()
    if kind == "identity":
        return IdentityCalibrator()
    if kind == "histogram":
        return HistogramCalibrator().fit(confs, correct)
    return IsotonicCalibrator().fit(confs, correct)


def cross_val_metrics(kind, confs, correct, target_precision, k=5, min_points=10):
    """Out-of-sample coverage@precision + ECE via k-fold CV.

    Fitting calibration and measuring it on the same points is optimistic (it can
    report ECE 0 / precision 100%). This computes each gold item's calibrated
    confidence from a calibrator trained on the OTHER folds, then evaluates on
    those out-of-fold values — an honest estimate. Returns None if too little gold.
    """
    from .metrics import coverage_at_precision, ece
    n = len(confs)
    if n < max(2 * k, min_points + k):
        return None
    order = sorted(range(n), key=lambda i: confs[i])   # spread by confidence across folds
    folds = [[] for _ in range(k)]
    for pos, i in enumerate(order):
        folds[pos % k].append(i)
    oof = [0.0] * n
    for f in range(k):
        test = set(folds[f])
        tr_c = [confs[i] for i in range(n) if i not in test]
        tr_y = [correct[i] for i in range(n) if i not in test]
        cal = fit_calibrator(kind, tr_c, tr_y, min_points=1)
        for i in folds[f]:
            oof[i] = cal.transform(confs[i])
    thr, cov, prec = coverage_at_precision(oof, correct, target_precision)
    return {"threshold": thr, "coverage": cov, "achieved": prec, "ece": ece(oof, correct),
            "oof": oof}
