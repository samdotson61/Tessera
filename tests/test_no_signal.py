"""No-signal guard: a mute labeler must be loudly visible, not silently uniform."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.labelers import KeywordStubLabeler
from tessera.labelers.llm import LLMLabeler
from tessera.pipeline import calibrate_and_gate, run_labeling_pass
from tessera.quality import build_quality_report
from tessera.schemas import GoldItem, Item, Taxonomy
from tessera.storage import Storage
from tessera.app import ingest

TAX = Taxonomy(id="t", name="t", labels=["billing", "technical"],
               definitions={"billing": "refund invoice charge",
                            "technical": "crash error login"})


class TestNoSignalGuard(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "n.db"),
                                 min_gold_for_calibration=4, specialist=False,
                                 audit_rate=0.0, cache_path="none")
        self.storage = Storage(self.settings.db_path)
        items = [Item(id=f"i{k}", dataset_id="d",
                      text=f"case {k} refund invoice charge") for k in range(8)]
        gold = [GoldItem(item_id=f"i{k}", dataset_id="d", label="billing")
                for k in range(6)]
        ingest(self.storage, "d", "d", items, TAX, gold)
        # A mute LLM (reasoning-mode shape: empty content, no label tokens)
        # alongside a live stub — the ensemble still produces labels.
        mute = LLMLabeler("openai", "k", logprobs=True,
                          transport=lambda p: '{"content": "", "top_logprobs": []}')
        run_labeling_pass(self.storage, "d", TAX, [mute, KeywordStubLabeler()])

    def tearDown(self):
        self.storage.close()

    def test_predictions_carry_the_no_signal_marker(self):
        preds = self.storage.get_predictions("d")
        self.assertTrue(all("[1/2 labelers no-signal]" in p.rationale for p in preds))

    def test_gate_counts_and_report_screams(self):
        gate = calibrate_and_gate(self.storage, "d", TAX, 0.8, self.settings)
        self.assertEqual(gate.n_no_signal, 8)
        report = build_quality_report(self.storage, "d", TAX, gate)
        self.assertEqual(report.n_no_signal, 8)
        self.assertTrue(any("CHECK THE SERVING STACK" in c for c in report.caveats))

    def test_healthy_run_reports_zero(self):
        st2 = Storage(os.path.join(self.dir, "ok.db"))
        items = [Item(id=f"j{k}", dataset_id="d2",
                      text=f"case {k} refund invoice charge") for k in range(6)]
        gold = [GoldItem(item_id=f"j{k}", dataset_id="d2", label="billing")
                for k in range(5)]
        ingest(st2, "d2", "d2", items, TAX, gold)
        run_labeling_pass(st2, "d2", TAX, [KeywordStubLabeler()])
        gate = calibrate_and_gate(st2, "d2", TAX, 0.8, self.settings)
        self.assertEqual(gate.n_no_signal, 0)
        st2.close()


if __name__ == "__main__":
    unittest.main()
