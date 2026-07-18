"""Consensus gate: the specialist joins the ensemble, calibration stays leak-safe."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.engine.specialist import consensus_split, train_consensus
from tessera.pipeline import calibrate_and_gate
from tessera.schemas import Item, Taxonomy, GoldItem
from tessera.storage import Storage
from tessera.app import ingest, run_full

BILLING = "refund my invoice charge payment bill overcharge"
TECH = "crash error login bug website broken page slow"
TAX = Taxonomy(id="t", name="t", labels=["billing", "technical"],
               definitions={"billing": BILLING, "technical": TECH},
               guidelines="route money issues to billing, product issues to technical")


def _dataset(n=24):
    items, gold = [], []
    for i in range(n):
        lab = "billing" if i % 2 == 0 else "technical"
        text = (f"item {i}: please {BILLING}" if lab == "billing"
                else f"item {i}: the {TECH}")
        items.append(Item(id=f"i{i:02d}", dataset_id="d", text=text))
        gold.append(GoldItem(item_id=f"i{i:02d}", dataset_id="d", label=lab))
    return items, gold


class TestConsensusSplit(unittest.TestCase):
    def test_deterministic_and_covers_both_halves(self):
        sides = [consensus_split("d", f"i{i}") for i in range(200)]
        self.assertEqual(sides, [consensus_split("d", f"i{i}") for i in range(200)])
        self.assertIn("train", sides)
        self.assertIn("calib", sides)
        n_train = sides.count("train")
        self.assertTrue(60 < n_train < 140, f"~half expected, got {n_train}/200")

    def test_train_consensus_refuses_thin_or_single_label_corpora(self):
        d = tempfile.mkdtemp()
        st = Storage(os.path.join(d, "t.db"))
        items, gold = _dataset(4)
        ingest(st, "d", "d", items, TAX, gold)
        spec, n = train_consensus(st, "d", TAX, min_train=10)
        self.assertIsNone(spec)          # too few training examples
        self.assertLess(n, 10)
        pair_tax = Taxonomy(id="p", name="p", label_type="pairwise", labels=["A", "B"])
        self.assertEqual(train_consensus(st, "d", pair_tax, min_train=1), (None, 0))
        st.close()

    def test_train_consensus_trains_only_on_train_half(self):
        d = tempfile.mkdtemp()
        st = Storage(os.path.join(d, "t.db"))
        items, gold = _dataset(24)
        ingest(st, "d", "d", items, TAX, gold)
        spec, n = train_consensus(st, "d", TAX, min_train=4)
        self.assertIsNotNone(spec)
        expected = sum(1 for g in gold if consensus_split("d", g.item_id) == "train")
        self.assertEqual(n, expected)
        dist = spec.predict("refund the invoice charge")
        self.assertEqual(max(dist, key=dist.get), "billing")
        st.close()


class TestConsensusGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(
            db_path=os.path.join(self.dir, "c.db"), specialist=True,
            specialist_min_train=4, min_gold_for_calibration=4, audit_rate=0.0,
            cache_path="none")
        self.storage = Storage(self.settings.db_path)
        self.items, self.gold = _dataset(24)
        ingest(self.storage, "d", "d", self.items, TAX, self.gold)
        self.gate = run_full(self.storage, "d", TAX, self.settings,
                             target_precision=0.8)

    def tearDown(self):
        self.storage.close()

    def test_specialist_votes_in_the_ensemble(self):
        preds = self.storage.get_predictions("d")
        self.assertTrue(all(
            any(m.startswith("specialist") for m in p.votes) for p in preds))
        self.assertTrue(all("specialist" in p.source for p in preds))

    def test_calibration_excludes_the_train_half(self):
        n_calib = sum(1 for g in self.gold
                      if consensus_split("d", g.item_id) == "calib")
        self.assertEqual(self.gate.n_gold, n_calib)
        self.assertLess(self.gate.n_gold, len(self.gold))

    def test_regate_detects_specialist_and_stays_leak_safe(self):
        # A report-time re-gate has no settings.specialist context to trust —
        # it must detect the specialist from the stored votes.
        plain = Settings(db_path=self.settings.db_path, audit_rate=0.0,
                         min_gold_for_calibration=4, cache_path="none")
        g2 = calibrate_and_gate(self.storage, "d", TAX, 0.8, plain,
                                log_events=False)
        self.assertEqual(g2.n_gold, self.gate.n_gold)

    def test_disabled_specialist_uses_all_gold(self):
        d2 = os.path.join(self.dir, "plain.db")
        st2 = Storage(d2)
        ingest(st2, "d", "d", self.items, TAX, self.gold)
        s2 = Settings(db_path=d2, specialist=False, min_gold_for_calibration=4,
                      audit_rate=0.0, cache_path="none")
        g2 = run_full(st2, "d", TAX, s2, target_precision=0.8)
        self.assertEqual(g2.n_gold, len(self.gold))
        st2.close()


if __name__ == "__main__":
    unittest.main()
