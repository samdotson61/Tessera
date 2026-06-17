"""Unit tests for the accuracy/trust engine math."""
import unittest

from tessera.schemas import Prediction, Taxonomy, LabelOutput
from tessera.engine.metrics import precision_recall_f1, coverage_at_precision, ece
from tessera.engine.calibration import IsotonicCalibrator, fit_calibrator, cross_val_metrics
from tessera.engine.confidence import ensemble, self_consistency
from tessera.engine.gating import apply_gate, choose_threshold
from tessera.engine.router import order_queue
from tessera.engine.goldset import stratified_sample
from tessera.engine.verify import deterministic_checks, judge


def _pred(item_id, label, conf, routed=None, calibrated=None):
    return Prediction(item_id=item_id, dataset_id="d", taxonomy_id="t", label=label,
                      confidence_raw=conf, confidence_calibrated=calibrated, routed=routed)


class TestMetrics(unittest.TestCase):
    def test_precision_recall_f1(self):
        y_true = ["a", "a", "b", "b"]
        y_pred = ["a", "b", "b", "b"]
        r = precision_recall_f1(y_true, y_pred, ["a", "b"])
        self.assertAlmostEqual(r["per_label"]["a"]["precision"], 1.0)   # 1 pred a, correct
        self.assertAlmostEqual(r["per_label"]["a"]["recall"], 0.5)      # 1 of 2 true a found
        self.assertAlmostEqual(r["per_label"]["b"]["precision"], 2 / 3)
        self.assertAlmostEqual(r["accuracy"], 0.75)

    def test_coverage_at_precision_basic(self):
        confs = [0.9, 0.8, 0.7, 0.6]
        correct = [1, 1, 0, 1]
        thr, cov, prec = coverage_at_precision(confs, correct, 0.9)
        self.assertEqual(thr, 0.8)        # accept top two
        self.assertAlmostEqual(cov, 0.5)
        self.assertAlmostEqual(prec, 1.0)

    def test_coverage_at_precision_handles_ties(self):
        thr, cov, prec = coverage_at_precision([0.5, 0.5, 0.5], [1, 1, 0], 0.6)
        self.assertEqual(thr, 0.5)        # threshold includes all tied items consistently
        self.assertAlmostEqual(cov, 1.0)
        self.assertAlmostEqual(prec, 2 / 3)

    def test_coverage_at_precision_none_satisfy(self):
        thr, cov, prec = coverage_at_precision([0.5, 0.4], [0, 0], 0.9)
        self.assertEqual(cov, 0.0)
        self.assertGreater(thr, 1.0)      # sentinel: nothing auto-applies

    def test_ece(self):
        self.assertAlmostEqual(ece([1.0, 1.0], [True, True]), 0.0)
        self.assertAlmostEqual(ece([1.0, 1.0], [False, False]), 1.0)


class TestCalibration(unittest.TestCase):
    def test_isotonic_monotonic(self):
        confs = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
        correct = [0, 0, 0, 1, 0, 1, 1, 1, 1, 1]
        cal = IsotonicCalibrator().fit(confs, correct)
        outs = [cal.transform(c) for c in confs]
        for a, b in zip(outs, outs[1:]):
            self.assertLessEqual(a, b + 1e-9)             # non-decreasing
        for o in outs:
            self.assertGreaterEqual(o, 0.0)
            self.assertLessEqual(o, 1.0)

    def test_identity_fallback_when_few_points(self):
        cal = fit_calibrator("isotonic", [0.5, 0.6], [1, 0], min_points=10)
        self.assertEqual(cal.transform(0.42), 0.42)       # identity passthrough

    def test_calibration_reduces_ece_on_miscalibrated_data(self):
        # Overconfident model: confidence 0.9 but only ~50% correct.
        confs = [0.9] * 20
        correct = [i % 2 == 0 for i in range(20)]
        before = ece(confs, correct)
        cal = fit_calibrator("isotonic", confs, correct, min_points=5)
        after = ece([cal.transform(c) for c in confs], correct)
        self.assertLess(after, before)

    def test_cross_val_metrics_runs(self):
        confs = [i / 40 for i in range(40)]
        correct = [c > 0.5 for c in confs]
        cv = cross_val_metrics("isotonic", confs, correct, 0.9, k=5, min_points=10)
        self.assertIsNotNone(cv)
        self.assertIn("ece", cv)
        self.assertGreaterEqual(cv["coverage"], 0.0)


class TestConfidence(unittest.TestCase):
    def test_ensemble_mean_and_agreement(self):
        a = LabelOutput("m1", {"x": 0.8, "y": 0.2})
        b = LabelOutput("m2", {"x": 0.6, "y": 0.4})
        label, raw, agreement, votes, dist = ensemble([a, b])
        self.assertEqual(label, "x")
        self.assertAlmostEqual(raw, 0.7)               # mean mass on winner
        self.assertAlmostEqual(agreement, 1.0)         # both pick x
        self.assertAlmostEqual(sum(dist.values()), 1.0)

    def test_ensemble_disagreement(self):
        a = LabelOutput("m1", {"x": 0.7, "y": 0.3})
        b = LabelOutput("m2", {"x": 0.2, "y": 0.8})
        label, raw, agreement, votes, dist = ensemble([a, b])
        self.assertEqual(agreement, 0.5)               # models split

    def test_self_consistency_deterministic(self):
        d = {"x": 0.9, "y": 0.1}
        self.assertEqual(self_consistency(d, n=20, seed=1), self_consistency(d, n=20, seed=1))


class TestGatingRouterGold(unittest.TestCase):
    def test_apply_gate_partitions(self):
        preds = [_pred("a", "x", 0.9, calibrated=0.9), _pred("b", "x", 0.3, calibrated=0.3)]
        n_auto, n_q = apply_gate(preds, 0.8)
        self.assertEqual((n_auto, n_q), (1, 1))
        self.assertTrue(preds[0].auto_applied)
        self.assertTrue(preds[1].routed)

    def test_router_orders_uncertain_first_with_diversity(self):
        preds = [
            _pred("a", "x", 0.2, routed=True), _pred("b", "x", 0.1, routed=True),
            _pred("c", "y", 0.15, routed=True),
            _pred("d", "x", 0.95, routed=False),   # not routed -> excluded
        ]
        order = order_queue(preds)
        self.assertNotIn("d", [p.item_id for p in order])
        # most-uncertain per label first, round-robin across labels
        self.assertEqual(order[0].item_id, "b")    # x's most uncertain
        self.assertEqual(order[1].item_id, "c")    # then y's
        self.assertEqual(len(order), 3)

    def test_stratified_sample_spreads_labels(self):
        preds = [_pred(f"x{i}", "x", 0.5) for i in range(10)] + \
                [_pred(f"y{i}", "y", 0.5) for i in range(10)]
        sample = stratified_sample(preds, 4, seed=0)
        labels = ["x" if s.startswith("x") else "y" for s in sample]
        self.assertEqual(labels.count("x"), 2)
        self.assertEqual(labels.count("y"), 2)


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tax = Taxonomy(id="t", name="n", labels=["a", "b"])

    def test_deterministic_checks(self):
        self.assertEqual(deterministic_checks("a", self.tax), [])
        self.assertTrue(deterministic_checks("zzz", self.tax))
        self.assertTrue(deterministic_checks("", self.tax))

    def test_judge(self):
        self.assertTrue(judge("a", 0.9, self.tax)[0])
        self.assertFalse(judge("a", 0.05, self.tax)[0])    # below confidence floor
        self.assertFalse(judge("zzz", 0.9, self.tax)[0])   # not in taxonomy


class TestCalibrationHonesty(unittest.TestCase):
    def test_cv_ece_exceeds_in_sample_on_noisy_data(self):
        # Uninformative confidences (correctness independent of confidence) — the case
        # where in-sample isotonic looks perfect but is dishonest. CV must expose it.
        import random
        from tessera.engine.metrics import ece
        rng = random.Random(0)
        confs = [rng.random() for _ in range(80)]
        correct = [rng.random() < 0.7 for _ in range(80)]   # base rate, no signal
        cal = fit_calibrator("isotonic", confs, correct, min_points=5)
        in_sample_ece = ece([cal.transform(c) for c in confs], correct)
        cv = cross_val_metrics("isotonic", confs, correct, 0.9, k=5, min_points=10)
        self.assertIsNotNone(cv)
        self.assertLess(in_sample_ece, 0.05)                # in-sample looks great...
        self.assertGreater(cv["ece"], in_sample_ece + 0.05)  # ...CV reveals the truth

    def test_cross_val_returns_none_for_small_gold(self):
        self.assertIsNone(cross_val_metrics("isotonic", [0.5] * 12, [True] * 12, 0.9,
                                            k=5, min_points=10))  # 12 < max(2k, min+k)=15


class TestHistogramCalibrator(unittest.TestCase):
    def test_histogram_transform(self):
        confs = [0.95] * 6 + [0.15] * 4
        correct = [True] * 6 + [False] * 4         # global accuracy 0.6
        cal = fit_calibrator("histogram", confs, correct, min_points=5)
        self.assertEqual(cal.to_dict()["kind"], "histogram")
        self.assertEqual(cal.transform(1.0), 1.0)   # top bin, all correct (no IndexError)
        self.assertEqual(cal.transform(0.15), 0.0)  # bin all wrong
        self.assertAlmostEqual(cal.transform(0.55), 0.6)  # empty bin -> global accuracy


class TestEngineEdgeCases(unittest.TestCase):
    def test_empty_inputs(self):
        self.assertEqual(coverage_at_precision([], [], 0.9), (1.01, 0.0, 0.0))
        self.assertEqual(ece([], []), 0.0)
        self.assertEqual(ensemble([]), (None, 0.0, 0.0, {}, {}))

    def test_gate_boundary_and_raw_fallback(self):
        at_threshold = _pred("eq", "x", 0.0, calibrated=0.8)   # calibrated == threshold
        raw_only = _pred("raw", "x", 0.85)                     # calibrated None -> uses raw
        n_auto, n_q = apply_gate([at_threshold, raw_only], 0.8)
        self.assertEqual((n_auto, n_q), (2, 0))
        self.assertTrue(at_threshold.auto_applied)             # >= is inclusive
        self.assertTrue(raw_only.auto_applied)                 # raw fallback works


if __name__ == "__main__":
    unittest.main()
