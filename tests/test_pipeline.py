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


if __name__ == "__main__":
    unittest.main()
