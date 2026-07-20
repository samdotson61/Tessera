"""The workflow UI's server surface: import, rubric, run, export, switching."""
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo
from tessera.server import Context, make_handler

TAX_JSON = json.dumps({
    "id": "t2", "name": "tickets", "labels": ["billing", "technical"],
    "definitions": {"billing": "refund invoice charge",
                    "technical": "crash error login"},
    "guidelines": "primary intent"})
ITEMS_CSV = "text\nrefund my invoice please\nthe login crashes hard\nanother refund charge\n"
GOLD_CSV = "id,label\nrow00001,billing\nrow00002,technical\n"


class TestWorkflowApi(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "u.db"),
                                 cache_path="none", audit_rate=0.0)
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, gate = bootstrap_demo(self.storage, self.settings)
        self.ctx = Context(self.storage, "demo", self.taxonomy, self.settings)
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
            return r.status, r.headers, r.read()

    def _post(self, path, body):
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())

    def test_state_carries_the_workflow_fields(self):
        _, _, raw = self._get("/api/state")
        s = json.loads(raw)
        for key in ("datasets", "serving", "run", "db_path"):
            self.assertIn(key, s)
        self.assertIn("guidelines", s["taxonomy"])
        self.assertFalse(s["run"]["running"])

    def test_import_new_dataset_then_switch_back(self):
        _, r = self._post("/api/import", {
            "dataset": "tickets", "items_name": "items.csv", "items": ITEMS_CSV,
            "taxonomy": TAX_JSON, "gold_name": "gold.csv", "gold": GOLD_CSV})
        self.assertEqual((r["n_items"], r["n_gold"]), (3, 2))
        self.assertEqual(self.ctx.dataset_id, "tickets")
        self.assertEqual(self.ctx.taxonomy.labels, ["billing", "technical"])
        _, r2 = self._post("/api/dataset", {"id": "demo"})
        self.assertEqual(self.ctx.dataset_id, "demo")
        _, _, raw = self._get("/api/state")
        self.assertIn("tickets", json.loads(raw)["datasets"])

    def test_import_new_dataset_without_taxonomy_is_refused(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._post("/api/import", {"dataset": "naked", "items": ITEMS_CSV,
                                       "items_name": "items.csv"})
        self.assertEqual(cm.exception.code, 400)

    def test_rubric_edit_bumps_version_and_persists(self):
        v0 = self.ctx.taxonomy.version
        _, r = self._post("/api/taxonomy", {
            "guidelines": "new tie-break rule",
            "definitions": {l: f"def {l}" for l in self.ctx.taxonomy.labels}})
        self.assertEqual(r["version"], v0 + 1)
        stored = self.storage.get_taxonomy(self.ctx.taxonomy.id)
        self.assertEqual(stored.guidelines, "new tie-break rule")
        with self.assertRaises(urllib.error.HTTPError):
            self._post("/api/taxonomy", {"labels": ["only-one"]})

    def test_run_labels_and_reports_progress_then_refuses_double_start(self):
        self._post("/api/import", {
            "dataset": "tickets", "items_name": "items.csv", "items": ITEMS_CSV,
            "taxonomy": TAX_JSON, "gold_name": "gold.csv", "gold": GOLD_CSV})
        _, r = self._post("/api/run", {"target_precision": 0.5})
        self.assertEqual(r["total"], 3)
        for _ in range(100):
            if not self.ctx.run_state["running"]:
                break
            time.sleep(0.05)
        self.assertFalse(self.ctx.run_state["running"])
        self.assertIsNone(self.ctx.run_state["error"])
        self.assertEqual(self.ctx.run_state["done"], 3)
        self.assertIsNotNone(self.ctx.last_gate)
        self.assertEqual(len(self.storage.get_predictions("tickets")), 3)

    def test_exports_download_finalized_labels(self):
        st, headers, raw = self._get("/api/export/labels.jsonl")
        self.assertEqual(st, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        rows = [json.loads(l) for l in raw.decode().splitlines()]
        self.assertGreater(len(rows), 0)
        self.assertTrue(all({"id", "text", "label"} <= set(r) for r in rows))
        st, headers, raw = self._get("/api/export/labels.csv")
        self.assertTrue(raw.decode().startswith("id,text,label"))
        st, _h, _raw = self._get("/api/export/pairs.jsonl")
        self.assertEqual(st, 200)

    def test_bootstrap_start_stop_via_api(self):
        _, r = self._post("/api/bootstrap/start", {"n": 5})
        self.assertEqual(r["n"], 5)
        _, _, raw = self._get("/api/queue")
        q = json.loads(raw)["queue"]
        self.assertTrue(all(e["bootstrap"] for e in q))
        self._post("/api/bootstrap", {"item_id": q[0]["item_id"],
                                      "label": self.ctx.taxonomy.labels[0]})
        _, r2 = self._post("/api/bootstrap/stop", {})
        self.assertEqual(r2["authored"], 1)
        self.assertIsNone(self.ctx.bootstrap)


if __name__ == "__main__":
    unittest.main()
