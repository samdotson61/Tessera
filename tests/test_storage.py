"""Persistence round-trip tests."""
import os
import tempfile
import unittest

from tessera.storage import Storage
from tessera.schemas import Dataset, Item, Taxonomy, Prediction, GoldItem, Event


class TestStorage(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = os.path.join(self.dir, "t.db")
        self.s = Storage(self.db)

    def tearDown(self):
        self.s.close()

    def test_items_and_taxonomy_roundtrip(self):
        self.s.add_dataset(Dataset("d", "demo"))
        self.s.add_items([Item("i1", "d", "hello", {"k": 1}), Item("i2", "d", "world")])
        self.s.add_taxonomy(Taxonomy("t", "tax", labels=["a", "b"], definitions={"a": "x"}))
        items = self.s.get_items("d")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].meta, {"k": 1})
        tax = self.s.get_taxonomy("t")
        self.assertEqual(tax.labels, ["a", "b"])
        self.assertEqual(tax.definitions["a"], "x")

    def test_prediction_roundtrip(self):
        self.s.add_dataset(Dataset("d"))
        p = Prediction("i1", "d", "t", "a", 0.8, confidence_calibrated=0.7,
                       votes={"m1": "a"}, distribution={"a": 0.8, "b": 0.2},
                       auto_applied=True, routed=False, source="m1")
        self.s.upsert_prediction(p)
        got = self.s.get_prediction("i1")
        self.assertEqual(got.label, "a")
        self.assertEqual(got.distribution["a"], 0.8)
        self.assertTrue(got.auto_applied)
        self.assertFalse(got.routed)

    def test_gold_and_events(self):
        self.s.add_dataset(Dataset("d"))
        self.s.add_gold([GoldItem("i1", "d", "a")])
        self.assertEqual(self.s.get_gold("d"), {"i1": "a"})
        rowid = self.s.append_event(Event("i1", "d", model_label="a", human_action="accept",
                                          final_label="a", routed_to_human=True))
        self.assertGreater(rowid, 0)
        events = self.s.get_events("d")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].human_action, "accept")

    def test_null_gate_flags_roundtrip(self):
        # auto_applied/routed default to None (un-gated) and survive a round-trip.
        self.s.add_dataset(Dataset("d"))
        self.s.upsert_prediction(Prediction("i1", "d", "t", "a", 0.5))
        got = self.s.get_prediction("i1")
        self.assertIsNone(got.auto_applied)
        self.assertIsNone(got.routed)


if __name__ == "__main__":
    unittest.main()
