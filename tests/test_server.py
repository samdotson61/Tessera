"""Smoke tests for the review server: start it on an ephemeral port and hit the API.

Each test gets its own freshly-bootstrapped server (per-method setUp) so there is no
order-dependence on shared mutable state.
"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.server import Context, make_handler


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return r.status, json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


class TestServer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "demo.db"))
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.gate = bootstrap_demo(self.storage, self.settings)
        self.ctx = Context(self.storage, "demo", self.taxonomy, self.settings)
        self.ctx.last_gate = self.gate
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(self.ctx))
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.storage.close()

    def test_state(self):
        _, s = _get(self.base, "/api/state")
        self.assertEqual(s["dataset_id"], "demo")
        self.assertEqual(len(s["taxonomy"]["labels"]), 6)
        self.assertEqual(s["counts"]["items"], 48)
        self.assertIsNotNone(s["gate"])

    def test_index_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            html = r.read().decode()
        self.assertIn("Tessera", html)

    def test_queue_then_action(self):
        _, q = _get(self.base, "/api/queue")
        self.assertGreater(len(q["queue"]), 0)
        first = q["queue"][0]
        for key in ("item_id", "text", "predicted_label", "confidence", "rationale"):
            self.assertIn(key, first)
        _, resp = _post(self.base, "/api/action", {"item_id": first["item_id"], "action": "accept"})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["final_label"], first["predicted_label"])
        _, q2 = _get(self.base, "/api/queue")
        self.assertEqual(len(q2["queue"]), len(q["queue"]) - 1)

    def test_gate_endpoint_overrides_target(self):
        _, resp = _post(self.base, "/api/gate", {"target_precision": 0.80})
        self.assertTrue(resp["ok"])
        self.assertAlmostEqual(resp["gate"]["target_precision"], 0.80)

    def test_report_endpoint(self):
        _, rep = _get(self.base, "/api/report")
        for key in ("coverage", "achieved_precision", "caveats", "per_label_precision"):
            self.assertIn(key, rep)

    def test_report_requires_gate(self):
        self.ctx.last_gate = None       # same ctx the handler closes over
        try:
            _get(self.base, "/api/report")
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()

    def test_path_traversal_blocked(self):
        # The critical bug fixed in review: absolute / .. escapes must 404, not leak files.
        for evil in ("/static/..%2f..%2fREADME.md", "/static/../../README.md"):
            try:
                status, _ = _get(self.base, evil)
                self.fail(f"expected 404 for {evil}, got {status}")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 404)
                e.close()

    def test_bad_action_400(self):
        try:
            _post(self.base, "/api/action", {"item_id": "nope", "action": "frobnicate"})
            self.fail("expected HTTP 400")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()


if __name__ == "__main__":
    unittest.main()
