"""Path portability: per-user data home, db resolution, no cwd dependence."""
import os
import tempfile
import unittest
from unittest import mock

from tessera.config import data_home, resolve_db


class TestDataHome(unittest.TestCase):
    def test_tessera_home_overrides_everything(self):
        with mock.patch.dict(os.environ, {"TESSERA_HOME": "/x/y"}):
            self.assertEqual(data_home(), "/x/y")

    def test_windows_uses_localappdata(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\s\AppData\Local"},
                             clear=False), \
             mock.patch("tessera.config.os.name", "nt"):
            os.environ.pop("TESSERA_HOME", None)
            self.assertEqual(data_home(),
                             os.path.join(r"C:\Users\s\AppData\Local", "Tessera"))

    def test_macos_uses_application_support(self):
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch("tessera.config.os.name", "posix"), \
             mock.patch("tessera.config.sys.platform", "darwin"):
            os.environ.pop("TESSERA_HOME", None)
            h = data_home()
        self.assertTrue(h.endswith(os.path.join("Library", "Application Support",
                                                "Tessera")))
        self.assertTrue(os.path.isabs(h))

    def test_linux_honors_xdg(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/data"}, clear=False), \
             mock.patch("tessera.config.os.name", "posix"), \
             mock.patch("tessera.config.sys.platform", "linux"):
            os.environ.pop("TESSERA_HOME", None)
            self.assertEqual(data_home(), os.path.join("/data", "tessera"))


class TestResolveDb(unittest.TestCase):
    def setUp(self):
        self.cwd = os.getcwd()
        self.dir = tempfile.mkdtemp()
        os.chdir(self.dir)
        self.home = tempfile.mkdtemp()
        os.environ["TESSERA_HOME"] = self.home
        os.environ.pop("TESSERA_DB", None)

    def tearDown(self):
        os.chdir(self.cwd)
        os.environ.pop("TESSERA_HOME", None)
        os.environ.pop("TESSERA_DB", None)

    def test_explicit_wins(self):
        self.assertEqual(resolve_db("/tmp/x.db"), "/tmp/x.db")

    def test_env_wins_over_cwd_and_home(self):
        open("tessera.db", "w").close()
        os.environ["TESSERA_DB"] = "/tmp/env.db"
        self.assertEqual(resolve_db(), "/tmp/env.db")

    def test_existing_cwd_db_is_project_mode(self):
        open("tessera.db", "w").close()
        self.assertEqual(resolve_db(), "tessera.db")

    def test_default_is_the_data_home_regardless_of_cwd(self):
        want = os.path.join(self.home, "tessera.db")
        self.assertEqual(resolve_db(), want)
        other = tempfile.mkdtemp()
        os.chdir(other)                    # launch from anywhere:
        self.assertEqual(resolve_db(), want)   # ...same database

    def test_data_home_is_created_on_demand(self):
        os.environ["TESSERA_HOME"] = os.path.join(self.home, "nested", "deeper")
        p = resolve_db()
        self.assertTrue(os.path.isdir(os.path.dirname(p)))


class TestCacheColocation(unittest.TestCase):
    def test_default_cache_sits_beside_the_resolved_db(self):
        import types
        from tessera.cli import _apply_paths
        from tessera.config import Settings
        home = tempfile.mkdtemp()
        cwd = os.getcwd()
        os.chdir(tempfile.mkdtemp())       # no ./tessera.db here
        os.environ["TESSERA_HOME"] = home
        os.environ.pop("TESSERA_DB", None)
        os.environ.pop("TESSERA_CACHE", None)
        try:
            s = Settings()
            _apply_paths(s, types.SimpleNamespace(db=None))
            self.assertEqual(os.path.dirname(s.db_path), home)
            self.assertEqual(s.cache_path, os.path.join(home, "tessera_cache.db"))
            s2 = Settings(cache_path="/explicit/cache.db")
            _apply_paths(s2, types.SimpleNamespace(db=None))
            self.assertEqual(s2.cache_path, "/explicit/cache.db")  # explicit wins
        finally:
            os.chdir(cwd)
            os.environ.pop("TESSERA_HOME", None)


if __name__ == "__main__":
    unittest.main()
