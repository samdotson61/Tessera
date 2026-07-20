"""rubric-check: diff a draft rubric against an authority's own labels."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tessera.labelers import KeywordStubLabeler
from tessera.labelers.llm import LLMLabeler
from tessera.rubricfit import check, print_report, stratified_by_label
from tessera.schemas import Item, Taxonomy
from tessera.cli import main

BILLING = "refund my invoice charge payment bill overcharge"
TECH = "crash error login bug website broken page slow"
TAX = Taxonomy(id="t", name="t", labels=["billing", "technical"],
               definitions={"billing": BILLING, "technical": TECH})


def _corpus(n=30):
    items, authority = [], {}
    for i in range(n):
        lab = "billing" if i % 2 == 0 else "technical"
        text = (f"case {i}: {BILLING}" if lab == "billing" else f"case {i}: {TECH}")
        items.append(Item(id=f"i{i:02d}", dataset_id="d", text=text))
        # the authority disagrees with keyword surface on every 5th item —
        # their convention, not a typo
        authority[f"i{i:02d}"] = ("technical" if i % 5 == 0 else lab)
    return items, authority


class TestStratified(unittest.TestCase):
    def test_round_robin_covers_all_labels(self):
        auth = {f"a{i}": "x" for i in range(10)}
        auth.update({f"b{i}": "y" for i in range(2)})
        picked = stratified_by_label(auth, 6)
        self.assertEqual(len(picked), 6)
        self.assertEqual(sum(1 for i in picked if auth[i] == "y"), 2)  # minority fully in

    def test_deterministic(self):
        auth = {f"i{i}": ("x" if i % 2 else "y") for i in range(20)}
        self.assertEqual(stratified_by_label(auth, 8), stratified_by_label(auth, 8))


class TestCheck(unittest.TestCase):
    def test_measures_convention_gap_and_patterns(self):
        items, authority = _corpus(30)
        report = check(items, authority, TAX, [KeywordStubLabeler()], n=30, workers=1)
        self.assertEqual(report["n"], 30)
        # every 5th item carries the authority's divergent convention; the
        # keyword reading misses exactly the billing-worded ones among them
        self.assertLess(report["agreement"], 1.0)
        self.assertGreater(report["agreement"], 0.5)
        pats = dict((k, len(v)) for k, v in report["patterns"])
        self.assertIn(("technical", "billing"), pats)   # their convention, our miss
        self.assertEqual(report["n_no_signal"], 0)

    def test_no_signal_is_surfaced(self):
        items, authority = _corpus(10)
        mute = LLMLabeler("openai", "k", logprobs=True,
                          transport=lambda p: '{"content": "", "top_logprobs": []}')
        report = check(items, authority, TAX, [mute], n=10, workers=1)
        self.assertEqual(report["n_no_signal"], 10)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            print_report(report, items)
        self.assertIn("NO SIGNAL", out.getvalue())

    def test_cli_end_to_end_and_unknown_class_guard(self):
        d = tempfile.mkdtemp()
        items, authority = _corpus(20)
        with open(os.path.join(d, "items.jsonl"), "w") as f:
            for it in items:
                f.write(json.dumps({"id": it.id, "text": it.text}) + "\n")
        with open(os.path.join(d, "labels.jsonl"), "w") as f:
            for iid, lab in authority.items():
                f.write(json.dumps({"id": iid, "label": lab}) + "\n")
        with open(os.path.join(d, "tax.json"), "w") as f:
            json.dump({"id": "t", "name": "t", "labels": TAX.labels,
                       "definitions": TAX.definitions}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = main(["rubric-check", "--data", os.path.join(d, "items.jsonl"),
                       "--labels", os.path.join(d, "labels.jsonl"),
                       "--taxonomy", os.path.join(d, "tax.json"), "--n", "20"])
        self.assertEqual(rc, 0)
        body = out.getvalue()
        self.assertIn("agreement", body)
        self.assertIn("kickoff agenda", body)
        # authority classes missing from the rubric are refused up front
        with open(os.path.join(d, "labels.jsonl"), "a") as f:
            f.write(json.dumps({"id": "i00", "label": "mystery"}) + "\n")
        with contextlib.redirect_stdout(io.StringIO()) as out2:
            rc2 = main(["rubric-check", "--data", os.path.join(d, "items.jsonl"),
                        "--labels", os.path.join(d, "labels.jsonl"),
                        "--taxonomy", os.path.join(d, "tax.json")])
        self.assertEqual(rc2, 1)


if __name__ == "__main__":
    unittest.main()
