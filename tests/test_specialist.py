"""Tier-0 specialist: training, prediction, and gate integration."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.engine.specialist import Specialist, SpecialistLabeler, train_from_storage
from tessera.schemas import Item, Taxonomy


class TestSpecialist(unittest.TestCase):
    TEXTS = ["refund my invoice charge please", "the charge on my bill is wrong",
             "app crashes with an error on login", "the website is broken and slow",
             "invoice overcharge on my payment", "error crash on the login page"]
    LABELS = ["billing", "billing", "technical", "technical", "billing", "technical"]

    def test_learns_separable_classes_and_is_deterministic(self):
        s1 = Specialist(["billing", "technical"]).train(self.TEXTS, self.LABELS)
        s2 = Specialist(["billing", "technical"]).train(self.TEXTS, self.LABELS)
        d1 = s1.predict("please refund the invoice")
        self.assertEqual(max(d1, key=d1.get), "billing")
        d2 = s1.predict("login shows an error and crashes")
        self.assertEqual(max(d2, key=d2.get), "technical")
        self.assertAlmostEqual(sum(d1.values()), 1.0, places=6)
        self.assertEqual(d1, s2.predict("please refund the invoice"))

    def test_labeler_wraps_distribution(self):
        s = Specialist(["billing", "technical"]).train(self.TEXTS, self.LABELS)
        tax = Taxonomy(id="t", name="t", labels=["billing", "technical"])
        out = SpecialistLabeler(s).label(
            Item(id="i", dataset_id="d", text="refund the invoice charge"), tax)
        self.assertEqual(out.top()[0], "billing")
        self.assertIn("specialist head", out.rationale)

    def test_train_from_storage_uses_gold_and_human_finals(self):
        from tessera.storage import Storage
        from tessera.app import bootstrap_demo
        from tessera.pipeline import record_human_action
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "s.db"))
        st = Storage(settings.db_path)
        tax, _ = bootstrap_demo(st, settings, target_precision=0.95)
        routed = [p for p in st.get_predictions("demo")
                  if p.routed and p.item_id not in st.get_gold("demo")]
        record_human_action(st, tax, routed[0].item_id, "edit",
                            label=tax.labels[0], grow_gold=False)
        spec = train_from_storage(st, "demo", tax)
        self.assertEqual(sorted(spec.labels), sorted(tax.labels))
        d1 = spec.predict("I was charged twice, refund the invoice")
        self.assertEqual(max(d1, key=d1.get), "billing")
        st.close()


if __name__ == "__main__":
    unittest.main()
