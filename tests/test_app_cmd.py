"""`tessera app`: dataset resolution, first-run offline demo, port picking."""
import contextlib
import io
import os
import tempfile
import types
import unittest

from tessera.cli import _free_port, _prepare_app
from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo


def _args(dataset=None):
    return types.SimpleNamespace(dataset=dataset)


class TestFreePort(unittest.TestCase):
    def test_prefers_the_asked_port_else_os_assigned(self):
        p = _free_port("127.0.0.1", 0)      # 0 -> OS picks
        self.assertGreater(p, 0)
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        try:
            p2 = _free_port("127.0.0.1", busy)
            self.assertNotEqual(p2, busy)   # busy port is skipped, not fatal
        finally:
            s.close()


class TestPrepareApp(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_first_run_loads_offline_sample(self):
        # Even with an LLM configured, first run must use the stub — no
        # surprise model calls from double-clicking the app.
        settings = Settings(db_path=os.path.join(self.dir, "a.db"),
                            provider="openai", openai_url="http://127.0.0.1:1/x",
                            logprobs=True, cache_path="none")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            storage, ds, tax, gate, first = _prepare_app(_args(), settings)
        self.assertTrue(first)
        self.assertEqual(ds, "demo")
        self.assertGreater(gate.n_auto + gate.n_queue, 0)
        self.assertIn("OFFLINE", out.getvalue())
        preds = storage.get_predictions("demo")
        self.assertTrue(all("stub" in p.source for p in preds))  # never the LLM
        storage.close()

    def test_opens_most_recently_gated_dataset(self):
        settings = Settings(db_path=os.path.join(self.dir, "b.db"), cache_path="none")
        st = Storage(settings.db_path)
        bootstrap_demo(st, settings, dataset_id="alpha")
        bootstrap_demo(st, settings, dataset_id="beta")   # beta gated last
        st.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            storage, ds, _tax, gate, first = _prepare_app(_args(), settings)
        self.assertEqual(ds, "beta")
        self.assertFalse(first)
        self.assertIn("--dataset", out.getvalue())     # override is advertised
        storage.close()

    def test_explicit_dataset_and_unknown_dataset(self):
        settings = Settings(db_path=os.path.join(self.dir, "c.db"), cache_path="none")
        st = Storage(settings.db_path)
        bootstrap_demo(st, settings, dataset_id="alpha")
        st.close()
        with contextlib.redirect_stdout(io.StringIO()):
            storage, ds, _t, _g, _f = _prepare_app(_args("alpha"), settings)
        self.assertEqual(ds, "alpha")
        storage.close()
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stdout(io.StringIO()):
                _prepare_app(_args("nope"), settings)

    def test_uses_last_gated_target(self):
        settings = Settings(db_path=os.path.join(self.dir, "d.db"), cache_path="none")
        st = Storage(settings.db_path)
        bootstrap_demo(st, settings, dataset_id="demo", target_precision=0.80)
        st.close()
        os.environ.pop("TESSERA_TARGET_PRECISION", None)
        with contextlib.redirect_stdout(io.StringIO()):
            storage, _ds, _t, gate, _f = _prepare_app(_args(), settings)
        self.assertEqual(gate.target_precision, 0.80)
        storage.close()


if __name__ == "__main__":
    unittest.main()
