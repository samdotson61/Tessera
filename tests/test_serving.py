"""Zero-config model serving: tier pick, asset discovery, plan gating."""
import os
import tempfile
import unittest
from unittest import mock

from tessera.config import Settings
from tessera.serving import (detect_memory_gb, find_winc_assets, pick_tier,
                             plan_auto)


class TestTier(unittest.TestCase):
    def test_seven_gb_rule(self):
        self.assertEqual(pick_tier(6.9), "2b")
        self.assertEqual(pick_tier(7.0), "4b")
        self.assertEqual(pick_tier(24.0), "4b")
        self.assertEqual(pick_tier(0.0), "2b")

    def test_memory_detection_returns_something_real(self):
        gb, source = detect_memory_gb()
        self.assertGreater(gb, 1.0)
        self.assertIn(source, ("GPU VRAM", "unified memory", "RAM"))


class TestAssets(unittest.TestCase):
    def _tree(self, tiers=("4b", "2b"), engine=True):
        root = tempfile.mkdtemp()
        os.makedirs(os.path.join(root, "bin"))
        os.makedirs(os.path.join(root, "models"))
        if engine:
            p = os.path.join(root, "bin", "llama-server")
            open(p, "w").close()
        names = {"4b": "Qwen3.5-4B-Q4_K_M.gguf", "2b": "Qwen3.5-2B-Q4_K_M.gguf"}
        for t in tiers:
            open(os.path.join(root, "models", names[t]), "w").close()
        return root

    def test_finds_engine_and_both_tiers(self):
        root = self._tree()
        a = find_winc_assets(roots=[root])
        self.assertTrue(a["engine"].endswith("llama-server"))
        self.assertEqual(sorted(a["models"]), ["2b", "4b"])

    def test_missing_engine_or_models_means_none(self):
        self.assertIsNone(find_winc_assets(roots=[self._tree(engine=False)]))
        self.assertIsNone(find_winc_assets(roots=[tempfile.mkdtemp()]))


class TestPlan(unittest.TestCase):
    def setUp(self):
        self._env = {k: os.environ.pop(k, None) for k in
                     ("TESSERA_PROVIDER", "TESSERA_AUTOSERVE")}
        self.root = TestAssets()._tree()

    def tearDown(self):
        for k, v in self._env.items():
            if v is not None:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)

    def test_plan_picks_tier_from_memory(self):
        s = Settings()
        with mock.patch("tessera.serving.find_winc_assets",
                        return_value=find_winc_assets(roots=[self.root])), \
             mock.patch("tessera.serving.detect_memory_gb",
                        return_value=(16.0, "unified memory")):
            plan = plan_auto(s)
        self.assertEqual(plan["tier"], "4b")
        with mock.patch("tessera.serving.find_winc_assets",
                        return_value=find_winc_assets(roots=[self.root])), \
             mock.patch("tessera.serving.detect_memory_gb",
                        return_value=(6.0, "RAM")):
            self.assertEqual(plan_auto(s)["tier"], "2b")

    def test_missing_tier_falls_back_to_what_exists(self):
        only2b = TestAssets()._tree(tiers=("2b",))
        with mock.patch("tessera.serving.find_winc_assets",
                        return_value=find_winc_assets(roots=[only2b])), \
             mock.patch("tessera.serving.detect_memory_gb",
                        return_value=(32.0, "unified memory")):
            self.assertEqual(plan_auto(Settings())["tier"], "2b")

    def test_explicit_config_and_optout_disable_auto(self):
        self.assertIsNone(plan_auto(Settings(openai_url="http://x/v1")))
        # a bare key is NOT explicit config (no provider set -> nothing reads it)
        with mock.patch("tessera.serving.find_winc_assets",
                        return_value=find_winc_assets(roots=[self.root])), \
             mock.patch("tessera.serving.detect_memory_gb",
                        return_value=(16.0, "RAM")):
            self.assertIsNotNone(plan_auto(Settings(anthropic_api_key="k")))
        os.environ["TESSERA_PROVIDER"] = "stub"
        self.assertIsNone(plan_auto(Settings()))
        del os.environ["TESSERA_PROVIDER"]
        os.environ["TESSERA_AUTOSERVE"] = "0"
        self.assertIsNone(plan_auto(Settings()))


if __name__ == "__main__":
    unittest.main()
