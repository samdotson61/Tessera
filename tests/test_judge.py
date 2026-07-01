"""LLM-as-judge tests — a fake judge vetoes gate candidates (no network)."""
import json
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.labelers.judge import LLMJudge, make_judge
from tessera.pipeline import calibrate_and_gate
from tessera.quality import build_quality_report
from tessera.schemas import Item, Taxonomy


class VetoJudge:
    """Vetoes a fixed set of item ids (matched by item text marker)."""
    def __init__(self, veto_item_ids):
        self.veto = set(veto_item_ids)
        self.reviewed = []

    def review(self, item, taxonomy, label):
        self.reviewed.append(item.id)
        if item.id in self.veto:
            return (False, "does not follow the guidelines")
        return (True, "")


class TestJudgeGate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "j.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95)

    def tearDown(self):
        self.storage.close()

    def test_veto_routes_item_and_adjusts_counts(self):
        auto_ids = [p.item_id for p in self.storage.get_predictions("demo") if p.auto_applied]
        judge = VetoJudge(auto_ids[:2])
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95,
                                  self.settings, judge=judge)
        self.assertEqual(gate.n_judge_vetoed, 2)
        self.assertEqual(gate.n_auto, self.gate.n_auto - 2)
        self.assertEqual(gate.n_queue, self.gate.n_queue + 2)
        self.assertEqual(gate.n_auto + gate.n_queue, 48)
        for iid in auto_ids[:2]:
            p = self.storage.get_prediction(iid)
            self.assertTrue(p.routed)
            self.assertIn("JUDGE VETO", p.rationale)
            self.assertIsNone(self.storage.get_item(iid).final_label)  # un-finalized

    def test_judge_only_reviews_auto_candidates(self):
        judge = VetoJudge([])
        calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95,
                           self.settings, judge=judge)
        routed = {p.item_id for p in self.storage.get_predictions("demo") if p.routed}
        self.assertFalse(set(judge.reviewed) & routed)

    def test_veto_appears_in_quality_report_caveats(self):
        auto_ids = [p.item_id for p in self.storage.get_predictions("demo") if p.auto_applied]
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95,
                                  self.settings, judge=VetoJudge(auto_ids[:1]))
        report = build_quality_report(self.storage, "demo", self.taxonomy, gate)
        self.assertTrue(any("judge vetoed" in c.lower() for c in report.caveats))

    def test_regate_without_judge_restores_auto(self):
        auto_ids = [p.item_id for p in self.storage.get_predictions("demo") if p.auto_applied]
        calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95,
                           self.settings, judge=VetoJudge(auto_ids[:1]))
        gate = calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95, self.settings)
        self.assertEqual(gate.n_auto, self.gate.n_auto)
        self.assertEqual(gate.n_judge_vetoed, 0)


class TestRegateUnfinalizes(unittest.TestCase):
    """An item auto-applied at a loose target must not stay finalized when a
    stricter re-gate routes it (pre-existing staleness bug)."""
    def test_stricter_target_clears_stale_finals(self):
        d = tempfile.mkdtemp()
        settings = Settings(db_path=os.path.join(d, "s.db"))
        storage = Storage(settings.db_path)
        taxonomy, _ = bootstrap_demo(storage, settings, target_precision=0.50)
        calibrate_and_gate(storage, "demo", taxonomy, 0.999, settings)
        for p in storage.get_predictions("demo"):
            if p.routed:
                self.assertIsNone(storage.get_item(p.item_id).final_label)
        storage.close()


class TestLLMJudgeParsing(unittest.TestCase):
    TAX = Taxonomy(id="t", name="t", labels=["a", "b"], definitions={}, guidelines="g")
    ITEM = Item(id="i", dataset_id="d", text="hello")

    def _judge(self, response):
        return LLMJudge("openai", "k", transport=lambda prompt: response)

    def test_fail_verdict_vetoes(self):
        ok, reason = self._judge(json.dumps({"verdict": "fail", "reason": "wrong"})) \
            .review(self.ITEM, self.TAX, "a")
        self.assertFalse(ok)
        self.assertEqual(reason, "wrong")

    def test_pass_verdict_keeps(self):
        ok, _ = self._judge(json.dumps({"verdict": "pass"})).review(self.ITEM, self.TAX, "a")
        self.assertTrue(ok)

    def test_error_fails_open(self):
        def boom(prompt):
            raise ValueError("down")
        judge = LLMJudge("openai", "k", transport=boom, max_retries=0)
        ok, reason = judge.review(self.ITEM, self.TAX, "a")
        self.assertTrue(ok)
        self.assertIn("judge unavailable", reason)

    def test_make_judge_off_by_default(self):
        self.assertIsNone(make_judge(Settings()))
        self.assertIsNone(make_judge(Settings(judge_provider="openai")))  # no key
        j = make_judge(Settings(judge_provider="openai", openai_api_key="k",
                                cache_path="none"))
        self.assertIsNotNone(j)
        self.assertTrue(j.model_id.startswith("judge:openai:"))


if __name__ == "__main__":
    unittest.main()
