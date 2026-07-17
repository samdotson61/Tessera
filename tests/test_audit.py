"""Audit sampling: shipped labels get verified, auto-region errors reach gold."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.engine.audit import audit_pick
from tessera.flywheel import audit_stats
from tessera.pipeline import calibrate_and_gate, record_human_action, undo_last_human_action
from tessera.quality import build_quality_report


class TestAuditPick(unittest.TestCase):
    def test_deterministic_and_rate_zero_off(self):
        self.assertEqual(audit_pick("d", "i1", 0.5), audit_pick("d", "i1", 0.5))
        self.assertFalse(audit_pick("d", "i1", 0.0))

    def test_rate_approximation(self):
        n = sum(audit_pick("ds", f"item{i}", 0.1) for i in range(2000))
        self.assertTrue(120 < n < 280, f"~10% expected, got {n}/2000")


class TestAuditGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "a.db"), audit_rate=0.30)
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95)
        self.preds = self.storage.get_predictions("demo")
        self.audits = [p for p in self.preds if p.audit]

    def tearDown(self):
        self.storage.close()

    def test_audit_subset_of_auto_and_coverage_unchanged(self):
        self.assertGreater(len(self.audits), 0)
        self.assertTrue(all(p.auto_applied for p in self.audits))
        self.assertEqual(self.gate.n_audit_pending, len(self.audits))
        base = Settings(db_path=os.path.join(self.dir, "b.db"), audit_rate=0.0)
        st2 = Storage(base.db_path)
        _, g2 = bootstrap_demo(st2, base, target_precision=0.95)
        self.assertEqual(self.gate.n_auto, g2.n_auto)        # label still ships
        self.assertEqual(self.gate.coverage, g2.coverage)
        st2.close()

    def test_audit_items_finalized_and_in_counts(self):
        for p in self.audits:
            self.assertEqual(self.storage.get_item(p.item_id).final_label, p.label)
        self.assertEqual(self.storage.counts("demo")["audit_pending"], len(self.audits))

    def test_accept_confirms_and_grows_gold(self):
        gold = self.storage.get_gold("demo")
        target = next(p for p in self.audits if p.item_id not in gold)
        record_human_action(self.storage, self.taxonomy, target.item_id, "accept",
                            grow_gold=True)
        p = self.storage.get_prediction(target.item_id)
        self.assertFalse(p.audit)
        self.assertTrue(p.auto_applied)                       # still shipped
        self.assertEqual(self.storage.get_item(target.item_id).final_label, target.label)
        self.assertEqual(self.storage.get_gold("demo")[target.item_id], target.label)
        s = audit_stats(self.storage, "demo")
        self.assertEqual((s["n_audited"], s["n_confirmed"]), (1, 1))
        self.assertEqual(s["audit_precision"], 1.0)

    def test_edit_overturns_shipped_label(self):
        gold = self.storage.get_gold("demo")
        target = next(p for p in self.audits if p.item_id not in gold)
        wrong = next(l for l in self.taxonomy.labels if l != target.label)
        record_human_action(self.storage, self.taxonomy, target.item_id, "edit",
                            label=wrong, grow_gold=True)
        self.assertEqual(self.storage.get_item(target.item_id).final_label, wrong)
        self.assertEqual(self.storage.get_gold("demo")[target.item_id], wrong)
        self.assertEqual(audit_stats(self.storage, "demo")["audit_precision"], 0.0)
        # a re-gate must not clobber the human's final with the model label
        calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        self.assertEqual(self.storage.get_item(target.item_id).final_label, wrong)

    def test_reject_unships_and_routes(self):
        target = self.audits[0]
        record_human_action(self.storage, self.taxonomy, target.item_id, "reject")
        p = self.storage.get_prediction(target.item_id)
        self.assertIsNone(self.storage.get_item(target.item_id).final_label)
        self.assertFalse(p.auto_applied)
        self.assertTrue(p.routed)

    def test_regate_keeps_pending_set_and_skips_reviewed(self):
        before = {p.item_id for p in self.audits}
        record_human_action(self.storage, self.taxonomy, self.audits[0].item_id, "accept")
        calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        after = {p.item_id for p in self.storage.get_predictions("demo") if p.audit}
        self.assertEqual(after, before - {self.audits[0].item_id})   # stable, no re-audit

    def test_undo_restores_audit_pending(self):
        target = self.audits[0]
        record_human_action(self.storage, self.taxonomy, target.item_id, "edit",
                            label=self.taxonomy.labels[0], grow_gold=True)
        undone = undo_last_human_action(self.storage, "demo")
        self.assertEqual(undone, target.item_id)
        p = self.storage.get_prediction(target.item_id)
        self.assertTrue(p.audit)
        self.assertTrue(p.auto_applied)
        self.assertEqual(self.storage.get_item(target.item_id).final_label, target.label)
        self.assertEqual(audit_stats(self.storage, "demo")["n_audited"], 0)

    def test_quality_report_carries_audit(self):
        rep = build_quality_report(self.storage, "demo", self.taxonomy, self.gate)
        self.assertEqual(rep.n_audit_pending, len(self.audits))
        self.assertTrue(any("await audit review" in c for c in rep.caveats))
        record_human_action(self.storage, self.taxonomy, self.audits[0].item_id, "accept")
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        rep = build_quality_report(self.storage, "demo", self.taxonomy, gate)
        self.assertEqual(rep.n_audited, 1)
        self.assertEqual(rep.audit_precision, 1.0)
        self.assertTrue(any("Audit sample:" in c for c in rep.caveats))


if __name__ == "__main__":
    unittest.main()
