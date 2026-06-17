"""End-to-end loop tests on the bundled sample dataset."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.pipeline import record_human_action
from tessera.flywheel import export_training_pairs, event_stats
from tessera.quality import build_quality_report
from tessera.export import export_jsonl


class TestPipelineEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "demo.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings, target_precision=0.95)

    def tearDown(self):
        self.storage.close()

    def test_gate_partitions_all_items(self):
        n_items = self.storage.counts("demo")["items"]
        self.assertEqual(n_items, 48)
        self.assertEqual(self.gate.n_auto + self.gate.n_queue, n_items)
        self.assertGreater(self.gate.n_auto, 0)
        self.assertGreater(self.gate.n_queue, 0)            # ambiguous items routed, not all auto
        self.assertGreaterEqual(self.gate.coverage, 0.0)
        self.assertLessEqual(self.gate.coverage, 1.0)

    def test_precision_target_respected(self):
        # Cross-validated achieved precision should meet the target on this clean sample.
        self.assertGreaterEqual(self.gate.achieved_precision, 0.95)

    def test_clear_item_auto_ambiguous_routed(self):
        clear = self.storage.get_prediction("t01")     # obvious billing
        self.assertTrue(clear.auto_applied)
        # at least one of the deliberately-ambiguous items is routed
        routed_ids = {p.item_id for p in self.storage.get_predictions("demo") if p.routed}
        self.assertTrue(routed_ids & {"t38", "t40", "t41", "t43", "t44", "t45", "t46"})

    def test_auto_applied_logged_and_finalized(self):
        counts = self.storage.counts("demo")
        self.assertEqual(counts["auto_applied"], self.gate.n_auto)
        self.assertEqual(counts["finalized"], self.gate.n_auto)   # only auto so far
        self.assertEqual(counts["events"], self.gate.n_auto)      # one event per auto-apply

    def test_human_action_finalizes_and_logs(self):
        routed = [p for p in self.storage.get_predictions("demo") if p.routed]
        self.assertTrue(routed)
        target = routed[0]
        final = record_human_action(self.storage, self.taxonomy, target.item_id,
                                    "edit", label=self.taxonomy.labels[0])
        self.assertEqual(final, self.taxonomy.labels[0])
        self.assertEqual(self.storage.get_item(target.item_id).final_label, self.taxonomy.labels[0])
        self.assertFalse(self.storage.get_prediction(target.item_id).routed)   # left the queue
        stats = event_stats(self.storage, "demo")
        self.assertEqual(stats["edit"], 1)

    def test_reject_does_not_finalize_but_logs(self):
        routed = [p for p in self.storage.get_predictions("demo") if p.routed]
        target = routed[0]
        final = record_human_action(self.storage, self.taxonomy, target.item_id, "reject")
        self.assertIsNone(final)                                            # nothing finalized
        self.assertIsNone(self.storage.get_item(target.item_id).final_label)
        self.assertFalse(self.storage.get_prediction(target.item_id).routed)  # left the queue
        self.assertEqual(event_stats(self.storage, "demo")["reject"], 1)
        # rejected items must NOT enter the training corpus
        pair_ids = {p["id"] for p in export_training_pairs(self.storage, "demo")}
        self.assertNotIn(target.item_id, pair_ids)

    def test_regate_is_idempotent(self):
        from tessera.pipeline import calibrate_and_gate
        before = self.storage.counts("demo")["events"]
        calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        after = self.storage.counts("demo")["events"]
        self.assertEqual(before, after)          # auto events replaced, not duplicated

    def test_demo_gold_is_cross_validated(self):
        self.assertTrue(self.gate.cross_validated)   # 24 gold >= CV minimum

    def test_coverage_and_precision_share_threshold(self):
        # Coherence: coverage>0 with the target met, and the deployed threshold's
        # auto-applied gold set actually clears the target in-sample too.
        self.assertGreater(self.gate.coverage, 0.0)
        gold = self.storage.get_gold("demo")
        preds = {p.item_id: p for p in self.storage.get_predictions("demo")}
        sel = [(preds[i].label == lab) for i, lab in gold.items()
               if preds[i].confidence() >= self.gate.threshold]
        if sel:
            self.assertGreaterEqual(sum(sel) / len(sel), self.gate.target_precision)

    def test_quality_report_and_export(self):
        report = build_quality_report(self.storage, "demo", self.taxonomy, self.gate)
        self.assertEqual(report.n_items, 48)
        self.assertTrue(report.caveats)
        self.assertTrue(set(report.per_label_precision) <= set(self.taxonomy.labels))
        out = os.path.join(self.dir, "labels.jsonl")
        n = export_jsonl(self.storage, "demo", out)
        self.assertEqual(n, self.gate.n_auto)            # only auto-applied finalized so far
        pairs = export_training_pairs(self.storage, "demo")
        self.assertEqual(len(pairs), self.gate.n_auto)


class TestSmallGoldLabeling(unittest.TestCase):
    """With too little gold for CV, the result must be flagged in-sample, not 'CV'."""
    def test_small_gold_not_cross_validated(self):
        from tessera.schemas import Dataset, Item, Taxonomy, GoldItem
        from tessera.app import run_full
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "s.db"))
        storage = Storage(settings.db_path)
        tax = Taxonomy(id="t", name="t", labels=["a", "b"],
                       definitions={"a": "alpha apple", "b": "beta banana"})
        items = [Item(id=f"i{i}", dataset_id="s",
                      text=("apple alpha" if i % 2 == 0 else "banana beta")) for i in range(12)]
        gold = [GoldItem(item_id=f"i{i}", dataset_id="s",
                         label=("a" if i % 2 == 0 else "b")) for i in range(12)]  # 12 < 15
        storage.add_dataset(Dataset("s")); storage.add_taxonomy(tax)
        storage.add_items(items); storage.add_gold(gold)
        gate = run_full(storage, "s", tax, settings)
        self.assertFalse(gate.cross_validated)
        storage.close()


if __name__ == "__main__":
    unittest.main()
