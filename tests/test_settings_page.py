"""Settings page: persistence, env pins, serving-mode switching."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest import mock

from tessera.config import Settings, apply_saved, env_pins
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.server import Context, make_handler


class TestApplySaved(unittest.TestCase):
    def setUp(self):
        for k in ("TESSERA_WORKERS", "TESSERA_PROVIDER", "TESSERA_AUTOSERVE"):
            os.environ.pop(k, None)

    tearDown = setUp

    def test_saved_values_apply_and_bad_ones_are_ignored(self):
        s = Settings()
        apply_saved(s, {"workers": 3, "model_mode": "2b", "specialist": False,
                        "audit_rate": "0.2", "nonsense": 1, "propagate": "x"})
        self.assertEqual((s.workers, s.model_mode, s.specialist, s.audit_rate),
                         (3, "2b", False, 0.2))
        self.assertEqual(s.propagate, 0.0)      # bad value ignored

    def test_env_pins_beat_saved_values(self):
        os.environ["TESSERA_WORKERS"] = "12"
        s = Settings(workers=12)
        apply_saved(s, {"workers": 2})
        self.assertEqual(s.workers, 12)
        self.assertEqual(env_pins().get("workers"), "TESSERA_WORKERS")
        os.environ["TESSERA_PROVIDER"] = "openai"
        self.assertIn("model_mode", env_pins())


class TestSettingsApi(unittest.TestCase):
    def setUp(self):
        for k in ("TESSERA_WORKERS", "TESSERA_PROVIDER", "TESSERA_AUTOSERVE"):
            os.environ.pop(k, None)
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "s.db"),
                                 cache_path="none", audit_rate=0.0)
        self.storage = Storage(self.settings.db_path)
        self.tax, gate = bootstrap_demo(self.storage, self.settings)
        self.ctx = Context(self.storage, "demo", self.tax, self.settings)
        self.ctx.last_gate = gate
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctx))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.storage.close()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return json.loads(r.read())

    def _post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_get_shape(self):
        s = self._get("/api/settings")
        for k in ("values", "pins", "winc_tiers", "db_path"):
            self.assertIn(k, s)
        self.assertEqual(s["values"]["model_mode"], "auto")

    def test_save_applies_persists_and_reloads(self):
        r = self._post("/api/settings", {"model_mode": "stub", "workers": 3,
                                         "audit_rate": 0.25})
        self.assertIn("model_mode", r["applied"])
        self.assertEqual(self.ctx.settings.workers, 3)
        self.assertEqual(self.ctx.settings.model_mode, "stub")
        saved = json.loads(self.storage.get_kv("__app__", "ui_settings"))
        self.assertEqual(saved["audit_rate"], 0.25)
        # a fresh launch reads them back (the _load_saved path)
        from tessera.cli import _load_saved
        fresh = Settings(db_path=self.settings.db_path)
        _load_saved(self.storage, fresh)
        self.assertEqual((fresh.model_mode, fresh.workers), ("stub", 3))

    def test_env_pinned_fields_are_skipped_and_reported(self):
        os.environ["TESSERA_WORKERS"] = "9"
        try:
            r = self._post("/api/settings", {"workers": 1, "audit_rate": 0.1})
            self.assertEqual(r["skipped_env_pinned"], {"workers": "TESSERA_WORKERS"})
            self.assertNotEqual(self.ctx.settings.workers, 1)
            self.assertEqual(self.ctx.settings.audit_rate, 0.1)
        finally:
            del os.environ["TESSERA_WORKERS"]

    def test_unknown_mode_rejected_and_serving_status_follows_mode(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/settings", {"model_mode": "9b"})
        self.assertEqual(cm.exception.code, 400)
        r = self._post("/api/settings", {"model_mode": "stub"})
        self.assertIn("stub", r["serving"]["provider"])
        r2 = self._post("/api/settings", {"model_mode": "custom",
                                          "custom_url": "http://127.0.0.1:1/v1/x"})
        self.assertIn("custom", r2["serving"]["provider"])


if __name__ == "__main__":
    unittest.main()
