"""Phase 2: cluster-aware router + run-over-run instrumentation."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.engine.embed import embed, cosine, leader_clusters
from tessera.engine.router import order_queue
from tessera.pipeline import calibrate_and_gate, record_human_action


class TestEmbed(unittest.TestCase):
    def test_cosine_similarity_ranks_related_text_higher(self):
        a = embed("refund charge invoice billing problem")
        b = embed("billing invoice refund overcharge")
        c = embed("the app crashes with an error on login")
        self.assertGreater(cosine(a, b), cosine(a, c))
        self.assertAlmostEqual(cosine(a, a), 1.0, places=6)

    def test_leader_clusters_deterministic_and_grouping(self):
        vecs = {f"i{k}": embed(t) for k, t in enumerate(
            ["refund my invoice charge", "invoice refund charge please",
             "app error crash login", "login crash error app broken",
             "totally unrelated gardening tulips"])}
        c1 = leader_clusters(vecs)
        self.assertEqual(c1, leader_clusters(vecs))
        self.assertEqual(c1["i0"], c1["i1"])
        self.assertEqual(c1["i2"], c1["i3"])
        self.assertNotEqual(c1["i0"], c1["i2"])


class TestRouter(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "r.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95)
        self.preds = self.storage.get_predictions("demo")
        self.items = {it.id: it for it in self.storage.get_items("demo")}

    def tearDown(self):
        self.storage.close()

    def test_cluster_mode_orders_all_routed_most_valuable_first(self):
        q = order_queue(self.preds, items=self.items, mode="cluster")
        routed = [p for p in self.preds if p.routed]
        self.assertEqual({p.item_id for p in q}, {p.item_id for p in routed})
        # top of the queue is high-uncertainty: no high-confidence item leads
        self.assertLessEqual(q[0].confidence(), min(p.confidence() for p in routed) + 0.25)

    def test_confidence_mode_matches_legacy_order(self):
        q = order_queue(self.preds, mode="confidence")
        self.assertEqual(len(q), self.gate.n_queue)
        self.assertEqual(q[0].confidence(), min(p.confidence() for p in q))

    def test_missing_items_falls_back(self):
        q1 = order_queue(self.preds, items=None, mode="cluster")
        q2 = order_queue(self.preds, mode="confidence")
        self.assertEqual([p.item_id for p in q1], [p.item_id for p in q2])


class TestRunHistory(unittest.TestCase):
    def test_runs_recorded_and_touch_count_grows(self):
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "h.db"))
        storage = Storage(settings.db_path)
        taxonomy, gate = bootstrap_demo(storage, settings, target_precision=0.95)
        runs = storage.get_runs("demo")
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["human_touches"], 0)
        self.assertAlmostEqual(runs[0]["coverage"], gate.coverage, places=6)
        routed = [p for p in storage.get_predictions("demo") if p.routed]
        record_human_action(storage, taxonomy, routed[0].item_id, "accept")
        calibrate_and_gate(storage, "demo", taxonomy, 0.95, settings)
        runs = storage.get_runs("demo")
        self.assertEqual(len(runs), 2)
        self.assertEqual(runs[-1]["human_touches"], 1)
        # report path (log_events=False) must NOT append a run
        calibrate_and_gate(storage, "demo", taxonomy, 0.95, settings, log_events=False)
        self.assertEqual(len(storage.get_runs("demo")), 2)
        storage.close()


if __name__ == "__main__":
    unittest.main()
