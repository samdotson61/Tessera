"""Undo of human actions + the reliability-diagram data."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.engine.metrics import reliability_bins
from tessera.flywheel import event_stats
from tessera.pipeline import record_human_action, undo_last_human_action
from tessera.quality import build_quality_report


class TestUndo(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "u.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95)
        gold = self.storage.get_gold("demo")
        self.routed = [p for p in self.storage.get_predictions("demo")
                       if p.routed and p.item_id not in gold]

    def tearDown(self):
        self.storage.close()

    def test_undo_reverts_accept(self):
        target = self.routed[0]
        gold_before = len(self.storage.get_gold("demo"))
        record_human_action(self.storage, self.taxonomy, target.item_id, "accept",
                            grow_gold=True)
        undone = undo_last_human_action(self.storage, "demo")
        self.assertEqual(undone, target.item_id)
        self.assertIsNone(self.storage.get_item(target.item_id).final_label)
        self.assertTrue(self.storage.get_prediction(target.item_id).routed)  # back in queue
        self.assertEqual(len(self.storage.get_gold("demo")), gold_before)    # grown gold dropped
        self.assertEqual(event_stats(self.storage, "demo")["accept"], 0)     # event removed

    def test_undo_is_lifo(self):
        a, b = self.routed[0], self.routed[1]
        record_human_action(self.storage, self.taxonomy, a.item_id, "accept")
        record_human_action(self.storage, self.taxonomy, b.item_id, "reject")
        self.assertEqual(undo_last_human_action(self.storage, "demo"), b.item_id)
        self.assertEqual(undo_last_human_action(self.storage, "demo"), a.item_id)
        self.assertIsNone(undo_last_human_action(self.storage, "demo"))

    def test_undo_nothing_returns_none(self):
        self.assertIsNone(undo_last_human_action(self.storage, "demo"))

    def test_undo_preserves_auto_events(self):
        record_human_action(self.storage, self.taxonomy, self.routed[0].item_id, "accept")
        before = self.storage.counts("demo")["events"]
        undo_last_human_action(self.storage, "demo")
        self.assertEqual(self.storage.counts("demo")["events"], before - 1)


class TestReliabilityBins(unittest.TestCase):
    def test_bins_reflect_accuracy(self):
        confs = [0.95] * 10 + [0.55] * 10
        correct = [True] * 10 + [True] * 5 + [False] * 5
        bins = reliability_bins(confs, correct)
        self.assertEqual(len(bins), 2)                       # only non-empty bins
        hi = next(b for b in bins if b["lo"] == 0.9)
        lo = next(b for b in bins if b["lo"] == 0.5)
        self.assertEqual(hi["accuracy"], 1.0)
        self.assertEqual(lo["accuracy"], 0.5)
        self.assertEqual(hi["count"], 10)

    def test_report_carries_bins(self):
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "r.db"))
        storage = Storage(settings.db_path)
        taxonomy, gate = bootstrap_demo(storage, settings, target_precision=0.95)
        report = build_quality_report(storage, "demo", taxonomy, gate)
        self.assertTrue(report.reliability_bins)
        total = sum(b["count"] for b in report.reliability_bins)
        self.assertEqual(total, gate.n_gold)
        storage.close()


if __name__ == "__main__":
    unittest.main()
