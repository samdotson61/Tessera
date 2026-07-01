"""Gold growth from human corrections + bootstrap coverage CI."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.engine.metrics import bootstrap_coverage_ci
from tessera.pipeline import record_human_action, calibrate_and_gate
from tessera.quality import build_quality_report


class TestGoldGrowth(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "g.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95)
        self.routed = [p for p in self.storage.get_predictions("demo") if p.routed]
        gold = self.storage.get_gold("demo")
        self.ungolded = [p for p in self.routed if p.item_id not in gold]

    def tearDown(self):
        self.storage.close()

    def test_accept_and_edit_grow_gold(self):
        before = len(self.storage.get_gold("demo"))
        record_human_action(self.storage, self.taxonomy, self.ungolded[0].item_id,
                            "accept", grow_gold=True)
        record_human_action(self.storage, self.taxonomy, self.ungolded[1].item_id,
                            "edit", label=self.taxonomy.labels[0], grow_gold=True)
        gold = self.storage.get_gold("demo")
        self.assertEqual(len(gold), before + 2)
        self.assertEqual(gold[self.ungolded[1].item_id], self.taxonomy.labels[0])
        self.assertEqual(self.storage.count_gold_by_source("demo").get("human"), 2)

    def test_reject_does_not_grow_gold(self):
        before = len(self.storage.get_gold("demo"))
        record_human_action(self.storage, self.taxonomy, self.routed[0].item_id,
                            "reject", grow_gold=True)
        self.assertEqual(len(self.storage.get_gold("demo")), before)

    def test_seed_gold_never_overwritten(self):
        gold = self.storage.get_gold("demo")
        seeded = [p for p in self.routed if p.item_id in gold]
        if not seeded:
            self.skipTest("no routed item with seed gold in this run")
        target = seeded[0]
        wrong = next(l for l in self.taxonomy.labels if l != gold[target.item_id])
        record_human_action(self.storage, self.taxonomy, target.item_id,
                            "edit", label=wrong, grow_gold=True)
        self.assertEqual(self.storage.get_gold("demo")[target.item_id],
                         gold[target.item_id])   # seed row untouched
        self.assertEqual(self.storage.count_gold_by_source("demo").get("human", 0), 0)

    def test_grown_gold_feeds_next_calibration(self):
        for p in self.routed:
            record_human_action(self.storage, self.taxonomy, p.item_id, "accept",
                                grow_gold=True)
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        self.assertEqual(gate.n_gold, len(self.storage.get_gold("demo")))
        self.assertGreater(gate.n_gold, self.gate.n_gold)

    def test_default_off_in_record_human_action(self):
        before = len(self.storage.get_gold("demo"))
        record_human_action(self.storage, self.taxonomy, self.routed[0].item_id, "accept")
        self.assertEqual(len(self.storage.get_gold("demo")), before)

    def test_quality_report_flags_grown_gold(self):
        record_human_action(self.storage, self.taxonomy, self.ungolded[0].item_id,
                            "accept", grow_gold=True)
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        report = build_quality_report(self.storage, "demo", self.taxonomy, gate)
        self.assertTrue(any("grown from human review" in c for c in report.caveats))


class TestBootstrapCI(unittest.TestCase):
    def test_ci_brackets_point_estimate_and_is_deterministic(self):
        confs = [0.95] * 30 + [0.5] * 10
        correct = [True] * 30 + [False] * 10
        lo, hi = bootstrap_coverage_ci(confs, correct, 0.95)
        self.assertLessEqual(lo, 0.75)
        self.assertGreaterEqual(hi, 0.75)
        self.assertLess(lo, hi)
        self.assertEqual((lo, hi), bootstrap_coverage_ci(confs, correct, 0.95))

    def test_empty_gold(self):
        self.assertEqual(bootstrap_coverage_ci([], [], 0.95), (0.0, 0.0))

    def test_gate_result_carries_ci(self):
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "ci.db"))
        storage = Storage(settings.db_path)
        _, gate = bootstrap_demo(storage, settings, target_precision=0.95)
        self.assertIsNotNone(gate.coverage_ci)
        lo, hi = gate.coverage_ci
        self.assertTrue(0.0 <= lo <= hi <= 1.0)
        storage.close()


if __name__ == "__main__":
    unittest.main()
