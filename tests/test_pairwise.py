"""Pairwise/preference label type — end-to-end on the bundled sample (offline)."""
import json
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo, load_items, sample_dir
from tessera.labelers import LLMLabeler, make_stub_ensemble
from tessera.schemas import Item, Taxonomy

PAIR_TAX = Taxonomy(
    id="pt", name="pt", label_type="pairwise", labels=["A", "B"],
    guidelines="Prefer the response with specific steps that answer the question.")


def _pair_item(a, b, item_id="x1", prompt="How do I fix this?"):
    return Item(id=item_id, dataset_id="d", text=prompt,
                meta={"response_a": a, "response_b": b})


class TestSchemas(unittest.TestCase):
    def test_render_includes_prompt_and_both_responses(self):
        it = _pair_item("first reply", "second reply")
        r = it.render()
        for chunk in ("Prompt:", "How do I fix this?", "Response A:", "first reply",
                      "Response B:", "second reply"):
            self.assertIn(chunk, r)

    def test_render_classification_is_plain_text(self):
        it = Item(id="c", dataset_id="d", text="hello")
        self.assertEqual(it.render(), "hello")

    def test_pairwise_prompt_wording(self):
        p = PAIR_TAX.to_prompt()
        self.assertIn("compare the two candidate responses", p)
        self.assertIn('"label"', p)


class TestStubPairwise(unittest.TestCase):
    def test_clear_winner(self):
        it = _pair_item("Here are the specific steps that answer the question.",
                        "idk, figure it out.")
        for lab in make_stub_ensemble():
            out = lab.label(it, PAIR_TAX)
            self.assertEqual(out.top()[0], "A")

    def test_near_tie_is_flatter_than_clear_win(self):
        clear = _pair_item("The specific steps answer the question directly.", "nope.")
        tie = _pair_item("We give specific arrangements.", "The answer depends.", item_id="x2")
        lab = make_stub_ensemble()[0]
        self.assertGreater(lab.label(clear, PAIR_TAX).top()[1],
                           lab.label(tie, PAIR_TAX).top()[1])


class TestLLMPairwise(unittest.TestCase):
    def test_llm_labeler_reads_rendered_pair(self):
        seen = {}
        def transport(prompt):
            seen["prompt"] = prompt
            return json.dumps({"label": "B", "confidence": 0.9, "rationale": "b better"})
        out = LLMLabeler("anthropic", "k", n_samples=1, transport=transport) \
            .label(_pair_item("aaa", "bbb"), PAIR_TAX)
        self.assertEqual(out.top()[0], "B")
        self.assertIn("Response A:\naaa", seen["prompt"])
        self.assertIn("Response B:\nbbb", seen["prompt"])


class TestPairwiseEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "pw.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(
            self.storage, self.settings, target_precision=0.95, sample="pairwise")

    def tearDown(self):
        self.storage.close()

    def test_loader_maps_responses_into_meta(self):
        items = load_items(os.path.join(sample_dir(), "pairwise.jsonl"), "d")
        self.assertEqual(len(items), 20)
        self.assertTrue(all(it.is_pairwise() for it in items))
        self.assertTrue(all(it.text for it in items))          # prompt -> text

    def test_gate_partitions_and_meets_target(self):
        self.assertEqual(self.gate.n_auto + self.gate.n_queue, 20)
        self.assertGreaterEqual(self.gate.n_auto, 10)          # clear pairs auto-apply
        self.assertGreaterEqual(self.gate.n_queue, 2)          # ambiguous pairs routed
        self.assertTrue(self.gate.cross_validated)             # 20 gold >= CV minimum
        self.assertGreaterEqual(self.gate.achieved_precision, 0.95)

    def test_clear_items_correct_on_gold(self):
        gold = self.storage.get_gold("demo")
        preds = {p.item_id: p for p in self.storage.get_predictions("demo")}
        clear = [f"p{i:02d}" for i in range(1, 15)]
        for iid in clear:
            self.assertEqual(preds[iid].label, gold[iid])


if __name__ == "__main__":
    unittest.main()
