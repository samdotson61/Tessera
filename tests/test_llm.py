"""LLM labeler tests — offline, via an injected fake transport (no network)."""
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from tessera.config import Settings
from tessera.labelers import make_labelers, LLMLabeler
from tessera.labelers.cache import ResponseCache, open_cache
from tessera.schemas import Item, Taxonomy

TAX = Taxonomy(id="t", name="t", labels=["a", "b", "c"],
               definitions={"a": "alpha", "b": "beta", "c": "gamma"})
ITEM = Item(id="i1", dataset_id="d", text="alpha alpha")


def _resp(label, conf=0.9, rationale="because"):
    return json.dumps({"label": label, "confidence": conf, "rationale": rationale})


class FakeTransport:
    """Returns queued responses in order; repeats the last one when exhausted."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def __call__(self, prompt):
        self.calls += 1
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


class TestSelfConsistency(unittest.TestCase):
    def test_majority_vote_wins_and_split_flattens(self):
        t = FakeTransport([_resp("a"), _resp("a"), _resp("a"), _resp("b", 0.6), _resp("a")])
        lab = LLMLabeler("anthropic", "k", n_samples=5, transport=t)
        out = lab.label(ITEM, TAX)
        self.assertEqual(out.top()[0], "a")
        self.assertEqual(t.calls, 5)
        self.assertIn("[4/5 samples agree]", out.rationale)
        # a unanimous run must be sharper than the split one
        t2 = FakeTransport([_resp("a")])
        out2 = LLMLabeler("anthropic", "k", n_samples=5, transport=t2).label(ITEM, TAX)
        self.assertGreater(out2.distribution["a"], out.distribution["a"])

    def test_distribution_normalized(self):
        t = FakeTransport([_resp("a", 0.5), _resp("b", 0.5), _resp("c", 0.5)])
        out = LLMLabeler("openai", "k", n_samples=3, transport=t).label(ITEM, TAX)
        self.assertAlmostEqual(sum(out.distribution.values()), 1.0, places=6)

    def test_off_taxonomy_label_coerced(self):
        t = FakeTransport([_resp("zzz")])
        out = LLMLabeler("anthropic", "k", n_samples=1, transport=t).label(ITEM, TAX)
        self.assertIn(out.top()[0], TAX.labels)

    def test_all_samples_fail_soft_uniform(self):
        t = FakeTransport([ValueError("boom")])
        out = LLMLabeler("anthropic", "k", n_samples=3, transport=t, max_retries=0).label(ITEM, TAX)
        self.assertIn("LLM error", out.rationale)
        for v in out.distribution.values():
            self.assertAlmostEqual(v, 1.0 / 3, places=6)


class TestRetries(unittest.TestCase):
    def test_retries_on_retriable_http_error_then_succeeds(self):
        err = urllib.error.HTTPError("u", 529, "overloaded", {}, None)
        t = FakeTransport([err, err, _resp("a")])
        lab = LLMLabeler("anthropic", "k", n_samples=1, transport=t, max_retries=3)
        with mock.patch("tessera.labelers.llm.time.sleep"):
            out = lab.label(ITEM, TAX)
        self.assertEqual(out.top()[0], "a")
        self.assertEqual(t.calls, 3)

    def test_non_retriable_http_error_fails_fast(self):
        err = urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
        t = FakeTransport([err, _resp("a")])
        lab = LLMLabeler("anthropic", "k", n_samples=1, transport=t, max_retries=3)
        with mock.patch("tessera.labelers.llm.time.sleep"):
            out = lab.label(ITEM, TAX)
        self.assertIn("LLM error", out.rationale)   # failed without retrying
        self.assertEqual(t.calls, 1)


class TestCache(unittest.TestCase):
    def test_second_pass_hits_cache(self):
        d = tempfile.mkdtemp()
        cache = ResponseCache(os.path.join(d, "cache.db"))
        t = FakeTransport([_resp("a")])
        lab = LLMLabeler("anthropic", "k", n_samples=3, cache=cache, transport=t)
        lab.label(ITEM, TAX)
        self.assertEqual(t.calls, 3)                 # one call per sample, first run
        lab.label(ITEM, TAX)
        self.assertEqual(t.calls, 3)                 # fully served from cache
        cache.close()

    def test_sample_index_in_key(self):
        k0 = ResponseCache.key("anthropic", "m", "v1", "p", 0)
        k1 = ResponseCache.key("anthropic", "m", "v1", "p", 1)
        self.assertNotEqual(k0, k1)

    def test_open_cache_none_disables(self):
        self.assertIsNone(open_cache("none"))
        self.assertIsNone(open_cache(""))


class TestFactory(unittest.TestCase):
    def test_two_family_ensemble(self):
        s = Settings(provider="anthropic,openai", anthropic_api_key="k1",
                     openai_api_key="k2", cache_path="none")
        labs = make_labelers(s)
        self.assertEqual([l.provider for l in labs], ["anthropic", "openai"])

    def test_missing_key_skipped_falls_back_to_stub(self):
        s = Settings(provider="anthropic", anthropic_api_key="", cache_path="none")
        labs = make_labelers(s)
        self.assertEqual(len(labs), 2)               # the stub ensemble
        self.assertTrue(all("stub" in l.model_id for l in labs))

    def test_default_model_is_current(self):
        self.assertEqual(LLMLabeler("anthropic", "k").model, "claude-haiku-4-5")


class TestConcurrentPass(unittest.TestCase):
    def test_pool_matches_serial(self):
        from tessera.storage import Storage
        from tessera.app import bootstrap_demo
        d = tempfile.mkdtemp()
        s1 = Settings(db_path=os.path.join(d, "serial.db"), workers=1)
        s2 = Settings(db_path=os.path.join(d, "pool.db"), workers=8)
        st1, st2 = Storage(s1.db_path), Storage(s2.db_path)
        _, g1 = bootstrap_demo(st1, s1, target_precision=0.95)
        _, g2 = bootstrap_demo(st2, s2, target_precision=0.95)
        p1 = {p.item_id: p.label for p in st1.get_predictions("demo")}
        p2 = {p.item_id: p.label for p in st2.get_predictions("demo")}
        self.assertEqual(p1, p2)
        self.assertEqual((g1.coverage, g1.threshold), (g2.coverage, g2.threshold))
        st1.close(); st2.close()


if __name__ == "__main__":
    unittest.main()
