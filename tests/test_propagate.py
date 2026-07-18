"""Near-duplicate propagation: label once per group, mirror members, audit them all."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.engine.embed import dedup_groups
from tessera.pipeline import (calibrate_and_gate, record_human_action,
                              undo_last_human_action)
from tessera.schemas import Item, Taxonomy, GoldItem
from tessera.storage import Storage
from tessera.app import ingest, run_full

BILLING = "refund my invoice charge payment bill overcharge"
TECH = "crash error login bug website broken page slow"
TAX = Taxonomy(id="t", name="t", labels=["billing", "technical"],
               definitions={"billing": BILLING, "technical": TECH},
               guidelines="money issues are billing, product issues are technical")

DUP_A = f"please {BILLING} now"          # group A: 4 identical billing texts
DUP_B = f"the {TECH} again"              # group B: 4 identical technical texts


def _dataset():
    items = [Item(id=f"a{i}", dataset_id="d", text=DUP_A) for i in range(1, 5)]
    items += [Item(id=f"b{i}", dataset_id="d", text=DUP_B) for i in range(1, 5)]
    gold = [GoldItem(item_id="a2", dataset_id="d", label="billing")]  # inside group A
    for i in range(12):   # singleton gold so the calibrator has signal
        lab = "billing" if i % 2 == 0 else "technical"
        text = (f"case {i}: another {BILLING} question" if lab == "billing"
                else f"case {i}: another {TECH} report")
        items.append(Item(id=f"g{i:02d}", dataset_id="d", text=text))
        gold.append(GoldItem(item_id=f"g{i:02d}", dataset_id="d", label=lab))
    return items, gold


class TestDedupGroups(unittest.TestCase):
    def test_groups_exact_duplicates_and_forces_gold_reps(self):
        items, _ = _dataset()
        groups = dedup_groups({it.id: it.text for it in items}, 0.95,
                              force_reps={"a2"})
        # a2 (gold) leads group A; b1 (first sorted) leads group B
        self.assertEqual({m: r for m, (r, _s) in groups.items()},
                         {"a1": "a2", "a3": "a2", "a4": "a2",
                          "b2": "b1", "b3": "b1", "b4": "b1"})
        self.assertTrue(all(s >= 0.95 for _r, s in groups.values()))
        again = dedup_groups({it.id: it.text for it in items}, 0.95,
                             force_reps={"a2"})
        self.assertEqual(groups, again)   # deterministic


class TestPropagationGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(
            db_path=os.path.join(self.dir, "p.db"), propagate=0.95,
            min_gold_for_calibration=4, audit_rate=0.5, cache_path="none")
        self.storage = Storage(self.settings.db_path)
        self.items, self.gold = _dataset()
        ingest(self.storage, "d", "d", self.items, TAX, self.gold)
        self.gate = run_full(self.storage, "d", TAX, self.settings,
                             target_precision=0.8)
        self.preds = {p.item_id: p for p in self.storage.get_predictions("d")}

    def tearDown(self):
        self.storage.close()

    def _rep(self, member):
        return self.storage.get_clusters("d")[member][0]

    def test_members_mirror_their_representative(self):
        members = ["a1", "a3", "a4", "b2", "b3", "b4"]
        for mid in members:
            m, r = self.preds[mid], self.preds[self._rep(mid)]
            self.assertEqual(m.source, "propagated")
            self.assertEqual((m.label, m.auto_applied, m.routed),
                             (r.label, r.auto_applied, r.routed))
            self.assertEqual(m.confidence(), r.confidence())
            self.assertIn("PROPAGATED from", m.rationale)
        # representatives (incl. the forced gold rep) are direct model output
        for rid in ("a2", "b1"):
            self.assertNotEqual(self.preds[rid].source, "propagated")
            self.assertTrue(self.preds[rid].votes)
        self.assertEqual(self.storage.counts("d")["propagated"], 6)

    def test_coverage_counts_everyone_and_reports_propagation(self):
        self.assertEqual(self.gate.n_auto + self.gate.n_queue, len(self.items))
        auto_members = [m for m in ("a1", "a3", "a4", "b2", "b3", "b4")
                        if self.preds[m].auto_applied]
        self.assertEqual(self.gate.n_propagated, len(auto_members))

    def test_propagated_members_are_in_the_audit_universe(self):
        # rate 0.5 over six members: the hash slice must reach some of them
        audited = [m for m in ("a1", "a3", "a4", "b2", "b3", "b4")
                   if self.preds[m].audit]
        auto = [m for m in ("a1", "a3", "a4", "b2", "b3", "b4")
                if self.preds[m].auto_applied]
        if auto:   # audit only samples shipped labels
            self.assertTrue(audited, "no propagated member was audit-sampled")

    def test_bulk_accept_resolves_the_whole_group(self):
        rep = self.storage.get_prediction("b1")
        rep.auto_applied, rep.routed = False, True   # force the rep into the queue
        self.storage.upsert_prediction(rep)
        record_human_action(self.storage, TAX, "b1", "accept", grow_gold=False)
        for mid in ("b2", "b3", "b4"):
            self.assertEqual(self.storage.get_item(mid).final_label, rep.label)
            self.assertFalse(self.storage.get_prediction(mid).routed)

    def test_audit_reject_unships_the_whole_group(self):
        rep = self.storage.get_prediction("b1")
        rep.auto_applied, rep.routed, rep.audit = True, False, True
        self.storage.upsert_prediction(rep)
        record_human_action(self.storage, TAX, "b1", "reject", grow_gold=False)
        for mid in ("b1", "b2", "b3", "b4"):
            p = self.storage.get_prediction(mid)
            self.assertFalse(p.auto_applied)
            self.assertTrue(p.routed)
            self.assertIsNone(self.storage.get_item(mid).final_label)

    def test_undo_restores_the_group(self):
        rep = self.storage.get_prediction("b1")
        rep.auto_applied, rep.routed = False, True
        self.storage.upsert_prediction(rep)
        record_human_action(self.storage, TAX, "b1", "accept", grow_gold=False)
        undo_last_human_action(self.storage, "d")
        for mid in ("b2", "b3", "b4"):
            p = self.storage.get_prediction(mid)
            self.assertTrue(p.routed)
            self.assertIsNone(self.storage.get_item(mid).final_label)

    def test_human_touched_member_is_emancipated(self):
        record_human_action(self.storage, TAX, "b2", "edit", label="billing",
                            grow_gold=False)
        rep = self.storage.get_prediction("b1")
        rep.auto_applied, rep.routed = False, True
        self.storage.upsert_prediction(rep)
        record_human_action(self.storage, TAX, "b1", "accept", grow_gold=False)
        self.assertEqual(self.storage.get_item("b2").final_label, "billing")
        # a re-gate must respect the emancipation too
        calibrate_and_gate(self.storage, "d", TAX, 0.8, self.settings)
        self.assertEqual(self.storage.get_item("b2").final_label, "billing")
        for mid in ("b3", "b4"):   # untouched members still follow the rep
            self.assertEqual(self.storage.get_item(mid).final_label,
                             self.storage.get_item("b1").final_label)

    def test_regate_keeps_groups_in_lockstep(self):
        g2 = calibrate_and_gate(self.storage, "d", TAX, 0.8, self.settings)
        self.assertEqual(g2.n_propagated, self.gate.n_propagated)
        preds = {p.item_id: p for p in self.storage.get_predictions("d")}
        for mid in ("a1", "a3", "a4", "b2", "b3", "b4"):
            m, r = preds[mid], preds[self._rep(mid)]
            self.assertEqual((m.label, m.auto_applied, m.routed),
                             (r.label, r.auto_applied, r.routed))


if __name__ == "__main__":
    unittest.main()
