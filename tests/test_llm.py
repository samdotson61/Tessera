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

    def test_near_json_reply_is_salvaged(self):
        # A local 4B dropped the opening quote on rationale (seen live) — the
        # label and confidence fields are intact and must be recovered.
        broken = ('{"label": "c", "confidence": 0.95, "rationale": The text focuses '
                  'on research."}')
        t = FakeTransport([broken])
        out = LLMLabeler("anthropic", "k", n_samples=1, transport=t).label(ITEM, TAX)
        self.assertEqual(out.top()[0], "c")
        self.assertAlmostEqual(out.distribution["c"], 0.95, places=2)

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

    def test_keyless_run_creates_no_cache_file(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "cache.db")
        make_labelers(Settings(provider="stub", cache_path=path))
        make_labelers(Settings(provider="anthropic", anthropic_api_key="",
                               cache_path=path))
        self.assertFalse(os.path.exists(path))       # cache opens lazily, key-gated

    def test_default_model_is_current(self):
        self.assertEqual(LLMLabeler("anthropic", "k").model, "claude-haiku-4-5")

    def test_local_url_needs_no_key(self):
        s = Settings(provider="anthropic", anthropic_api_key="", cache_path="none",
                     anthropic_url="http://127.0.0.1:8080/v1/messages",
                     model_id="qwen3.5-4b")
        labs = make_labelers(s)
        self.assertEqual(len(labs), 1)
        self.assertEqual(labs[0].base_url, "http://127.0.0.1:8080/v1/messages")
        self.assertEqual(labs[0].model, "qwen3.5-4b")

    def test_default_base_url_is_anthropic_api(self):
        self.assertEqual(LLMLabeler("anthropic", "k").base_url,
                         "https://api.anthropic.com/v1/messages")

    def test_anthropic_url_does_not_enable_openai(self):
        s = Settings(provider="openai", openai_api_key="", cache_path="none",
                     anthropic_url="http://127.0.0.1:8080/v1/messages")
        self.assertTrue(all("stub" in l.model_id for l in make_labelers(s)))

    def test_openai_local_url_needs_no_key(self):
        s = Settings(provider="openai", openai_api_key="", cache_path="none",
                     openai_url="http://127.0.0.1:11434/v1/chat/completions",
                     model_id="tessera-qwen")
        labs = make_labelers(s)
        self.assertEqual(len(labs), 1)
        self.assertEqual(labs[0].base_url, "http://127.0.0.1:11434/v1/chat/completions")
        self.assertEqual(labs[0].model, "tessera-qwen")

    def test_openai_default_base_url(self):
        self.assertEqual(LLMLabeler("openai", "k").base_url,
                         "https://api.openai.com/v1/chat/completions")


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


class TestFewshot(unittest.TestCase):
    def test_nearest_gold_examples_in_prompt_and_self_excluded(self):
        seen = {}
        def transport(prompt):
            seen["prompt"] = prompt
            return _resp("a")
        examples = [("alpha apple text", "a"), ("beta banana text", "b"),
                    ("gamma grape text", "c"), ("alpha alpha", "a")]
        lab = LLMLabeler("anthropic", "k", n_samples=1, transport=transport,
                         examples=examples, fewshot=2)
        lab.label(ITEM, TAX)   # ITEM text: "alpha alpha"
        self.assertIn("Examples of correct labels:", seen["prompt"])
        self.assertIn("alpha apple text", seen["prompt"])       # nearest neighbor
        self.assertNotIn("gamma grape text", seen["prompt"])    # not in top-k
        self.assertEqual(seen["prompt"].count('Answer: {"label"'), 2)
        # the item's own gold row is never leaked
        self.assertNotIn('Text: alpha alpha\nAnswer', seen["prompt"])

    def test_fewshot_off_by_default(self):
        seen = {}
        def transport(prompt):
            seen["prompt"] = prompt
            return _resp("a")
        LLMLabeler("anthropic", "k", n_samples=1, transport=transport,
                   examples=[("x", "a")]).label(ITEM, TAX)
        self.assertNotIn("Examples of correct labels:", seen["prompt"])


class TestLogprobHead(unittest.TestCase):
    def _lp_reply(self, tops, content=" a"):
        return json.dumps({"content": content, "top_logprobs": tops})

    def test_distribution_from_token_logprobs(self):
        import math
        tops = [{"token": " a", "logprob": math.log(0.7)},
                {"token": " b", "logprob": math.log(0.2)},
                {"token": "\n", "logprob": math.log(0.1)}]
        lab = LLMLabeler("openai", "k", transport=lambda p: self._lp_reply(tops),
                         logprobs=True)
        out = lab.label(ITEM, TAX)
        self.assertEqual(out.top()[0], "a")
        self.assertAlmostEqual(out.distribution["a"], 0.7 / 0.9, places=3)
        self.assertAlmostEqual(out.distribution["b"], 0.2 / 0.9, places=3)
        self.assertIn("logprob head", out.rationale)

    def test_prompt_is_word_style_single_call(self):
        seen = {"n": 0}
        def transport(prompt):
            seen["n"] += 1
            seen["prompt"] = prompt
            return self._lp_reply([{"token": "a", "logprob": -0.1}])
        LLMLabeler("openai", "k", n_samples=5, transport=transport,
                   logprobs=True).label(ITEM, TAX)
        self.assertEqual(seen["n"], 1)                      # ONE call despite n_samples=5
        self.assertIn("ONLY the label word", seen["prompt"])
        self.assertTrue(seen["prompt"].rstrip().endswith("Answer:"))

    def test_no_label_mass_routes_uniform(self):
        out = LLMLabeler("openai", "k", logprobs=True,
                         transport=lambda p: self._lp_reply(
                             [{"token": "zzz", "logprob": -0.1}])).label(ITEM, TAX)
        self.assertAlmostEqual(max(out.distribution.values()), 1.0 / 3, places=6)
        self.assertIn("no label mass", out.rationale)

    def test_anthropic_provider_ignores_logprobs_flag(self):
        t = FakeTransport([_resp("a")])
        lab = LLMLabeler("anthropic", "k", n_samples=1, transport=t, logprobs=True)
        self.assertFalse(lab.logprobs)
        self.assertEqual(lab.label(ITEM, TAX).top()[0], "a")


class TestStaticFewshotAndEnsemble(unittest.TestCase):
    def test_static_block_identical_across_items_and_class_spread(self):
        seen = []
        def transport(prompt):
            seen.append(prompt)
            return _resp("a")
        examples = [("t1", "a"), ("t2", "a"), ("t3", "b"), ("t4", "c")]
        lab = LLMLabeler("openai", "k", n_samples=1, transport=transport,
                         examples=examples, fewshot=3, fewshot_static=True)
        lab.label(Item(id="x", dataset_id="d", text="one text"), TAX)
        lab.label(Item(id="y", dataset_id="d", text="other text"), TAX)
        b1 = seen[0].split("Examples of correct labels:")[1].split("Text:\n")[0]
        b2 = seen[1].split("Examples of correct labels:")[1].split("Text:\n")[0]
        self.assertEqual(b1, b2)                       # shared prefix
        for t in ("t1", "t3", "t4"):                   # one per class first
            self.assertIn(t, b1)
        self.assertNotIn("t2", b1)

    def test_static_self_item_falls_back_to_nearest(self):
        seen = []
        def transport(prompt):
            seen.append(prompt)
            return _resp("a")
        examples = [("alpha alpha", "a"), ("beta beta", "b"), ("gamma gamma", "c")]
        lab = LLMLabeler("openai", "k", n_samples=1, transport=transport,
                         examples=examples, fewshot=3, fewshot_static=True)
        lab.label(ITEM, TAX)                           # ITEM text == "alpha alpha"
        self.assertNotIn("Text: alpha alpha\nAnswer", seen[0])   # no self-leak

    def test_multi_endpoint_openai_ensemble(self):
        s = Settings(provider="openai", openai_api_key="", cache_path="none",
                     openai_url="http://h1:1/v1/chat/completions, http://h2:2/v1/chat/completions",
                     model_id="qwen,gemma")
        labs = make_labelers(s)
        self.assertEqual([l.base_url.split("//")[1][:2] for l in labs], ["h1", "h2"])
        self.assertEqual([l.model for l in labs], ["qwen", "gemma"])
