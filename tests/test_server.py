"""Smoke tests for the review server: start it on an ephemeral port and hit the API."""
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.server import Context, make_handler


def _get(base, path):
    with urllib.request.urlopen(base + path, timeout=5) as r:
        return json.loads(r.read())


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.settings = Settings(db_path=os.path.join(cls.dir, "demo.db"))
        cls.storage = Storage(cls.settings.db_path)
        cls.taxonomy, cls.gate = bootstrap_demo(cls.storage, cls.settings)
        ctx = Context(cls.storage, "demo", cls.taxonomy, cls.settings)
        ctx.last_gate = cls.gate
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(ctx))
        cls.port = cls.httpd.server_address[1]
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.storage.close()

    def test_state(self):
        s = _get(self.base, "/api/state")
        self.assertEqual(s["dataset_id"], "demo")
        self.assertEqual(len(s["taxonomy"]["labels"]), 6)
        self.assertEqual(s["counts"]["items"], 48)
        self.assertIsNotNone(s["gate"])

    def test_queue_then_action(self):
        q = _get(self.base, "/api/queue")
        self.assertGreater(len(q["queue"]), 0)
        first = q["queue"][0]
        for key in ("item_id", "text", "predicted_label", "confidence", "rationale"):
            self.assertIn(key, first)
        resp = _post(self.base, "/api/action",
                     {"item_id": first["item_id"], "action": "accept"})
        self.assertTrue(resp["ok"])
        self.assertEqual(resp["final_label"], first["predicted_label"])
        # the accepted item leaves the queue
        q2 = _get(self.base, "/api/queue")
        self.assertEqual(len(q2["queue"]), len(q["queue"]) - 1)

    def test_index_served(self):
        with urllib.request.urlopen(self.base + "/", timeout=5) as r:
            html = r.read().decode()
        self.assertIn("Tessera", html)

    def test_bad_action_400(self):
        try:
            _post(self.base, "/api/action", {"item_id": "nope", "action": "frobnicate"})
            self.fail("expected HTTP error")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)
            e.close()


if __name__ == "__main__":
    unittest.main()
