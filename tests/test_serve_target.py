"""report/serve default to the dataset's last-gated target, not the env default."""
import contextlib
import io
import json
import os
import tempfile
import unittest

from tessera.cli import _resolve_target, main
from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo


class TestResolveTarget(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(db_path=os.path.join(self.dir, "t.db"),
                                 cache_path="none")
        self.storage = Storage(self.settings.db_path)
        bootstrap_demo(self.storage, self.settings, target_precision=0.80)
        self._env = os.environ.pop("TESSERA_TARGET_PRECISION", None)

    def tearDown(self):
        if self._env is not None:
            os.environ["TESSERA_TARGET_PRECISION"] = self._env
        else:
            os.environ.pop("TESSERA_TARGET_PRECISION", None)
        self.storage.close()

    def test_last_gated_target_wins_over_builtin_default(self):
        target, source = _resolve_target(self.storage, "demo", self.settings)
        self.assertEqual((target, source), (0.80, "last gating run"))

    def test_explicit_flag_wins(self):
        target, source = _resolve_target(self.storage, "demo", self.settings,
                                         explicit=0.99)
        self.assertEqual((target, source), (0.99, "--target"))

    def test_env_var_wins_over_stored(self):
        os.environ["TESSERA_TARGET_PRECISION"] = "0.85"
        s = Settings.from_env()
        s.db_path = self.settings.db_path
        target, source = _resolve_target(self.storage, "demo", s)
        self.assertEqual((target, source), (0.85, "TESSERA_TARGET_PRECISION"))

    def test_no_runs_falls_back_to_default(self):
        st = Storage(os.path.join(self.dir, "empty.db"))
        target, source = _resolve_target(st, "nothing", self.settings)
        self.assertEqual((target, source), (self.settings.target_precision, "default"))
        st.close()

    def test_report_command_uses_stored_target(self):
        os.environ.pop("TESSERA_TARGET_PRECISION", None)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = main(["--db", self.settings.db_path, "report", "--dataset", "demo"])
        self.assertEqual(rc, 0)
        body = out.getvalue()
        report = json.loads(body[body.index("{"):body.index("\n}") + 2])
        self.assertEqual(report["target_precision"], 0.80)
        self.assertIn("last gating run", err.getvalue())

    def test_report_flag_overrides_stored(self):
        os.environ.pop("TESSERA_TARGET_PRECISION", None)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            main(["--db", self.settings.db_path, "report", "--dataset", "demo",
                  "--target", "0.5"])
        body = out.getvalue()
        report = json.loads(body[body.index("{"):body.index("\n}") + 2])
        self.assertEqual(report["target_precision"], 0.5)


if __name__ == "__main__":
    unittest.main()
