"""Partner-prep features: CSV ingest, letter-keyed logprobs, gold bootstrap."""
import json
import math
import os
import tempfile
import unittest

from tessera.app import load_gold, load_items
from tessera.config import Settings
from tessera.engine.goldset import cluster_sample
from tessera.labelers import make_labelers
from tessera.labelers.llm import LLMLabeler, letters_needed
from tessera.schemas import Item, Taxonomy

AMBIG_TAX = Taxonomy(id="t", name="t",
                     labels=["billing_dispute", "billing_question", "tech_issue"],
                     definitions={"billing_dispute": "wrong charge",
                                  "billing_question": "how billing works",
                                  "tech_issue": "product is broken"})
ITEM = Item(id="i1", dataset_id="d", text="I was charged twice, that is wrong")


class TestCSVIngest(unittest.TestCase):
    def _write(self, name, content):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
        return p

    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_items_csv_with_meta_and_generated_ids(self):
        p = self._write("items.csv",
                        "text,customer,priority\n"
                        "refund my invoice,acme,high\n"
                        "login crashes,globex,low\n")
        items = load_items(p, "d")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, "row00001")   # no id column: generated
        self.assertEqual(items[0].text, "refund my invoice")
        self.assertEqual(items[0].meta, {"customer": "acme", "priority": "high"})

    def test_items_csv_explicit_id_and_bom(self):
        p = self._write("items.csv", "﻿id,text\nA7,hello there\n")
        items = load_items(p, "d")
        self.assertEqual(items[0].id, "A7")          # BOM did not mangle 'id'
        self.assertEqual(items[0].text, "hello there")

    def test_pairwise_csv_columns_land_in_meta(self):
        p = self._write("pairs.csv",
                        "id,prompt,response_a,response_b\n"
                        "p1,say hi,Hello!,hey\n")
        items = load_items(p, "d")
        self.assertTrue(items[0].is_pairwise())
        self.assertEqual(items[0].meta["response_a"], "Hello!")

    def test_gold_csv(self):
        p = self._write("gold.csv", "id,label\nA7,billing\nB2,technical\n")
        gold = load_gold(p, "d")
        self.assertEqual({g.item_id: g.label for g in gold},
                         {"A7": "billing", "B2": "technical"})

    def test_jsonl_unchanged(self):
        p = self._write("items.jsonl",
                        json.dumps({"id": "x", "text": "t", "meta": {"k": "v"}}) + "\n")
        items = load_items(p, "d")
        self.assertEqual((items[0].id, items[0].meta), ("x", {"k": "v"}))


class TestLetterKey(unittest.TestCase):
    def _lp(self, tops):
        return json.dumps({"content": "A", "top_logprobs": tops})

    def test_letters_needed_detects_shared_prefixes_only(self):
        self.assertTrue(letters_needed(["billing_dispute", "billing_question"]))
        self.assertFalse(letters_needed(["world", "sports", "business", "scitech"]))
        self.assertFalse(letters_needed(["spam", "ham"]))
        self.assertFalse(letters_needed([f"l{i}" for i in range(30)]))  # >26: words

    def test_letter_prompt_lists_options(self):
        p = AMBIG_TAX.to_prompt(style="letter")
        self.assertIn("A. billing_dispute", p)
        self.assertIn("C. tech_issue", p)
        self.assertIn("ONLY the single letter", p)

    def test_auto_mode_uses_letters_and_maps_back(self):
        seen = {}
        tops = [{"token": " A", "logprob": math.log(0.6)},
                {"token": "B", "logprob": math.log(0.3)},
                {"token": "Answer", "logprob": math.log(0.1)}]
        lab = LLMLabeler("openai", "k", logprobs=True,
                         transport=lambda p: (seen.__setitem__("p", p), self._lp(tops))[1])
        out = lab.label(ITEM, AMBIG_TAX)
        self.assertIn("A. billing_dispute", seen["p"])   # letter prompt was used
        self.assertEqual(out.top()[0], "billing_dispute")
        self.assertAlmostEqual(out.distribution["billing_dispute"], 0.6 / 0.9, places=3)
        self.assertAlmostEqual(out.distribution["billing_question"], 0.3 / 0.9, places=3)

    def test_word_mode_stays_for_distinct_labels(self):
        seen = {}
        tax = Taxonomy(id="t", name="t", labels=["spam", "ham"])
        tops = [{"token": " spam", "logprob": math.log(0.9)}]
        lab = LLMLabeler("openai", "k", logprobs=True,
                         transport=lambda p: (seen.__setitem__("p", p), self._lp(tops))[1])
        out = lab.label(ITEM, tax)
        self.assertIn("ONLY the label word", seen["p"])
        self.assertEqual(out.top()[0], "spam")

    def test_forced_letter_mode_via_settings(self):
        s = Settings(provider="openai", openai_url="http://x/v1", logprobs=True,
                     answer_key="letter", cache_path="none")
        labs = make_labelers(s)
        self.assertEqual(labs[0].answer_key, "letter")


class TestClusterSample(unittest.TestCase):
    RENDERED = {
        "a1": "refund my invoice please", "a2": "refund my invoice please",
        "a3": "refund my invoice please",
        "b1": "the login page crashes hard", "b2": "the login page crashes hard",
        "c1": "how do I export my data",
    }

    def test_spans_clusters_before_depth(self):
        out = cluster_sample(self.RENDERED, 3)
        self.assertEqual(len(out), 3)
        texts = {self.RENDERED[i] for i in out}
        self.assertEqual(len(texts), 3)   # one from each cluster first

    def test_deterministic_and_excludes(self):
        self.assertEqual(cluster_sample(self.RENDERED, 4),
                         cluster_sample(self.RENDERED, 4))
        out = cluster_sample(self.RENDERED, 10, exclude={"a1", "b1"})
        self.assertNotIn("a1", out)
        self.assertNotIn("b1", out)
        self.assertEqual(len(out), 4)


class TestBootstrapServer(unittest.TestCase):
    def setUp(self):
        import threading
        from http.server import ThreadingHTTPServer
        from tessera.server import Context, make_handler
        from tessera.storage import Storage
        from tessera.app import ingest
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "b.db"),
                                 cache_path="none")
        self.storage = Storage(self.settings.db_path)
        self.tax = Taxonomy(id="t", name="t", labels=["billing", "technical"],
                            definitions={"billing": "money", "technical": "broken"})
        items = [Item(id=f"i{k}", dataset_id="d", text=f"item {k} refund invoice")
                 for k in range(1, 4)]
        ingest(self.storage, "d", "d", items, self.tax, None)
        self.ctx = Context(self.storage, "d", self.tax, self.settings,
                           bootstrap_ids=["i1", "i2", "i3"])
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctx))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.storage.close()

    def _get(self, path):
        import urllib.request
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, json.loads(r.read())

    def _post(self, path, body):
        import urllib.request
        req = urllib.request.Request(
            self.base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())

    def test_state_and_queue_expose_bootstrap(self):
        _, s = self._get("/api/state")
        self.assertEqual(s["bootstrap"], {"remaining": 3, "done": 0,
                                          "target": 3, "gold": 0})
        _, q = self._get("/api/queue")
        self.assertEqual(len(q["queue"]), 3)
        self.assertTrue(all(e["bootstrap"] and e["predicted_label"] is None
                            for e in q["queue"]))

    def test_label_skip_and_undo(self):
        _, r = self._post("/api/bootstrap", {"item_id": "i1", "label": "billing"})
        self.assertEqual(r["remaining"], 2)
        self._post("/api/bootstrap", {"item_id": "i2", "label": None})   # skip
        gold = self.storage.get_gold("d")
        self.assertEqual(gold, {"i1": "billing"})
        self.assertEqual(self.storage.count_gold_by_source("d"),
                         {"bootstrap": 1})
        _, r = self._post("/api/undo", {})       # undo the skip: i2 returns
        self.assertEqual(r["item_id"], "i2")
        _, r = self._post("/api/undo", {})       # undo the label: gold removed
        self.assertEqual(r["item_id"], "i1")
        self.assertEqual(self.storage.get_gold("d"), {})
        _, q = self._get("/api/queue")
        self.assertEqual([e["item_id"] for e in q["queue"]], ["i1", "i2", "i3"])

    def test_unknown_label_rejected(self):
        import urllib.error
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/bootstrap", {"item_id": "i1", "label": "nope"})
        self.assertEqual(cm.exception.code, 400)

    def test_bootstrap_gold_feeds_the_loop(self):
        from tessera.app import run_full
        for iid, lab in [("i1", "billing"), ("i2", "billing"), ("i3", "technical")]:
            self._post("/api/bootstrap", {"item_id": iid, "label": lab})
        s = Settings(db_path=self.settings.db_path, min_gold_for_calibration=2,
                     specialist=False, cache_path="none")
        gate = run_full(self.storage, "d", self.tax, s, target_precision=0.5)
        self.assertEqual(gate.n_gold, 3)     # bootstrap gold calibrates the gate


if __name__ == "__main__":
    unittest.main()
