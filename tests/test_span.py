"""Span/NER label type — engine, labelers, and the end-to-end loop (offline)."""
import json
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo, load_gold, load_items, sample_dir
from tessera.engine import spans as spans_mod
from tessera.engine.verify import deterministic_checks
from tessera.labelers import LLMLabeler, make_stub_ensemble
from tessera.pipeline import record_human_action
from tessera.quality import build_quality_report
from tessera.schemas import Item, Taxonomy

TAX = Taxonomy(id="t", name="t", label_type="span",
               labels=["person", "org", "location"],
               definitions={"person": "chen okafor", "org": "acme council",
                            "location": "oslo rivertown"},
               guidelines="Mark entities, not titles.")


class TestSpanEngine(unittest.TestCase):
    def test_canonical_sorts_and_dedupes(self):
        a = spans_mod.canonical([{"start": 5, "end": 9, "type": "org"},
                                 {"start": 0, "end": 4, "type": "person"},
                                 {"start": 5, "end": 9, "type": "org"}])
        self.assertEqual(a, '[{"start":0,"end":4,"type":"person"},'
                            '{"start":5,"end":9,"type":"org"}]')
        self.assertEqual(spans_mod.parse(a)[0]["type"], "person")
        self.assertEqual(spans_mod.canonical([]), "[]")

    def test_validate_catches_bounds_overlap_type(self):
        text = "Chen met Acme."
        ok = [{"start": 0, "end": 4, "type": "person"}]
        self.assertEqual(spans_mod.validate(ok, text, {"person"}), [])
        bad = [{"start": 0, "end": 99, "type": "person"},
               {"start": 0, "end": 4, "type": "alien"},
               {"start": 2, "end": 6, "type": "person"}]
        v = spans_mod.validate(bad + ok, text, {"person"})
        self.assertTrue(any("out of bounds" in x for x in v))
        self.assertTrue(any("not in taxonomy" in x for x in v))
        self.assertTrue(any("overlaps" in x for x in v))

    def test_resolve_quoted_first_unclaimed_occurrence(self):
        text = "Oslo to Oslo"
        spans, un = spans_mod.resolve_quoted(
            [{"text": "Oslo", "type": "location"}, {"text": "Oslo", "type": "location"},
             {"text": "Ghost", "type": "org"}], text)
        self.assertEqual([(s["start"], s["end"]) for s in spans], [(0, 4), (8, 12)])
        self.assertEqual(un, ["Ghost"])

    def test_deterministic_checks_span_branch(self):
        it = Item(id="i", dataset_id="d", text="Chen met Acme.")
        good = spans_mod.canonical([{"start": 0, "end": 4, "type": "person"}])
        self.assertEqual(deterministic_checks(good, TAX, item=it), [])
        self.assertTrue(deterministic_checks("not json", TAX, item=it))
        self.assertEqual(deterministic_checks("[]", TAX, item=it), [])  # empty is legal


class TestSpanStub(unittest.TestCase):
    def test_clear_singleword_entities_agree(self):
        it = Item(id="x", dataset_id="d", text="Chen visited Oslo.")
        outs = [lab.label(it, TAX) for lab in make_stub_ensemble()]
        anns = {o.top()[0] for o in outs}
        self.assertEqual(len(anns), 1)
        got = spans_mod.parse(anns.pop())
        self.assertEqual({(s["start"], s["end"], s["type"]) for s in got},
                         {(0, 4, "person"), (13, 17, "location")})

    def test_title_boundary_disagrees_across_members(self):
        it = Item(id="y", dataset_id="d", text="President Okafor visited Oslo.")
        outs = [lab.label(it, TAX) for lab in make_stub_ensemble()]
        self.assertEqual(len({o.top()[0] for o in outs}), 2)   # tight vs run-extended


class TestSpanLLM(unittest.TestCase):
    def test_quoted_spans_resolved_and_voted(self):
        it = Item(id="z", dataset_id="d", text="Chen met the Rivertown Council.")
        resp = json.dumps({"spans": [{"text": "Chen", "type": "person"},
                                     {"text": "Rivertown Council", "type": "org"}],
                           "confidence": 0.9, "rationale": "two entities"})
        out = LLMLabeler("anthropic", "k", n_samples=3, transport=lambda p: resp) \
            .label(it, TAX)
        ann, conf = out.top()
        got = spans_mod.parse(ann)
        self.assertEqual({(s["start"], s["end"], s["type"]) for s in got},
                         {(0, 4, "person"), (13, 30, "org")})
        self.assertIn("[3/3 samples agree]", out.rationale)

    def test_unresolvable_quote_fails_soft(self):
        it = Item(id="z", dataset_id="d", text="Chen met Acme.")
        resp = json.dumps({"spans": [{"text": "Ghost Corp", "type": "org"}]})
        out = LLMLabeler("anthropic", "k", n_samples=2, transport=lambda p: resp) \
            .label(it, TAX)
        self.assertEqual(out.distribution, {})
        self.assertIn("LLM error", out.rationale)


class TestSpanEndToEnd(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "s.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings,
                                                  target_precision=0.95, sample="span")

    def tearDown(self):
        self.storage.close()

    def test_gold_loader_resolves_quotes(self):
        items = load_items(os.path.join(sample_dir(), "span.jsonl"), "d")
        gold = load_gold(os.path.join(sample_dir(), "span_gold.jsonl"), "d", items=items)
        self.assertEqual(len(gold), 20)
        s14 = next(g for g in gold if g.item_id == "s14")
        self.assertEqual(spans_mod.parse(s14.label)[0]["type"], "org")

    def test_gate_partitions_clear_auto_ambiguous_routed(self):
        self.assertEqual(self.gate.n_auto + self.gate.n_queue, 20)
        self.assertGreaterEqual(self.gate.n_auto, 10)     # single-word items agree
        self.assertGreaterEqual(self.gate.n_queue, 6)     # title/determiner boundary cases
        self.assertTrue(self.gate.cross_validated)        # 20 gold >= CV minimum
        self.assertGreaterEqual(self.gate.achieved_precision, 0.95)
        preds = {p.item_id: p for p in self.storage.get_predictions("demo")}
        self.assertTrue(preds["s01"].auto_applied)        # clear
        self.assertTrue(preds["s13"].routed)              # "President Okafor"

    def test_human_edit_supplies_span_annotation(self):
        routed = [p for p in self.storage.get_predictions("demo") if p.routed]
        target = next(p for p in routed if p.item_id == "s13")
        gold = self.storage.get_gold("demo")
        final = record_human_action(self.storage, self.taxonomy, target.item_id,
                                    "edit", label=gold["s13"], grow_gold=True)
        self.assertEqual(final, gold["s13"])
        self.assertEqual(self.storage.get_item("s13").final_label, gold["s13"])

    def test_quality_report_per_type_precision(self):
        rep = build_quality_report(self.storage, "demo", self.taxonomy, self.gate)
        self.assertEqual(set(rep.per_label_precision), {"person", "org", "location"})
        self.assertTrue(all(0.0 <= v <= 1.0 for v in rep.per_label_precision.values()))


if __name__ == "__main__":
    unittest.main()
