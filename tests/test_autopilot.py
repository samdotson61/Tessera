"""Audit autopilot: audit evidence tightens the gate; recovery relaxes it."""
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.pipeline import _binom_cdf, calibrate_and_gate
from tessera.schemas import Event
from tessera.storage import Storage
from tessera.app import bootstrap_demo


def _audit_event(i, action):
    return Event(item_id=f"aud{i}", dataset_id="demo", routed_to_human=True,
                 route_reason="audit", human_action=action,
                 final_label="x" if action != "reject" else None)


class TestBinom(unittest.TestCase):
    def test_cdf_bounds_and_direction(self):
        self.assertAlmostEqual(_binom_cdf(10, 10, 0.95), 1.0, places=9)
        self.assertLess(_binom_cdf(5, 10, 0.95), 0.001)   # 5/10 at target .95: breach
        self.assertGreater(_binom_cdf(10, 10, 0.5), 0.99)


class TestAutopilot(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.settings = Settings(
            db_path=os.path.join(self.dir, "ap.db"), autopilot=True,
            autopilot_min_audits=10, audit_rate=0.0, cache_path="none")
        self.storage = Storage(self.settings.db_path)
        self.taxonomy, self.base_gate = bootstrap_demo(
            self.storage, self.settings, target_precision=0.95)

    def tearDown(self):
        self.storage.close()

    def _gate(self, log_events=True):
        return calibrate_and_gate(self.storage, "demo", self.taxonomy, 0.95,
                                  self.settings, log_events=log_events)

    def test_no_evidence_no_change(self):
        self.assertEqual(self.base_gate.autopilot_level, 0)
        self.assertIsNone(self.base_gate.effective_target)

    def test_breach_tightens_and_does_not_double_count(self):
        for i in range(10):   # 5/10 confirmed at a 95% promise: clear breach
            self.storage.append_event(_audit_event(i, "accept" if i < 5 else "edit"))
        g = self._gate()
        self.assertEqual(g.autopilot_level, 1)
        self.assertAlmostEqual(g.effective_target, 0.975)
        self.assertEqual(self.storage.get_kv("demo", "autopilot_level"), "1")
        # same evidence must not tighten again on the next gate
        g2 = self._gate()
        self.assertEqual(g2.autopilot_level, 1)

    def test_recovery_relaxes_a_level(self):
        for i in range(10):
            self.storage.append_event(_audit_event(i, "accept" if i < 5 else "edit"))
        self._gate()
        for i in range(10, 20):   # a clean window at/above the promise
            self.storage.append_event(_audit_event(i, "accept"))
        g = self._gate()
        self.assertEqual(g.autopilot_level, 0)
        self.assertIsNone(g.effective_target)

    def test_report_regate_reads_but_never_consumes_evidence(self):
        for i in range(10):
            self.storage.append_event(_audit_event(i, "accept" if i < 5 else "edit"))
        g = self._gate(log_events=False)   # report path: no adjustment
        self.assertEqual(g.autopilot_level, 0)
        self.assertIsNone(self.storage.get_kv("demo", "autopilot_n"))
        g2 = self._gate()                  # a real run consumes it
        self.assertEqual(g2.autopilot_level, 1)
        g3 = self._gate(log_events=False)  # report now reflects the stored level
        self.assertEqual(g3.autopilot_level, 1)

    def test_inconclusive_window_accumulates(self):
        # 9/10 at a 95% promise: not a confident breach, not a recovery — hold.
        for i in range(10):
            self.storage.append_event(_audit_event(i, "accept" if i < 9 else "edit"))
        g = self._gate()
        self.assertEqual(g.autopilot_level, 0)
        self.assertIsNone(self.storage.get_kv("demo", "autopilot_n"))

    def test_off_by_default(self):
        plain = Settings(db_path=os.path.join(self.dir, "off.db"),
                         audit_rate=0.0, cache_path="none")
        st = Storage(plain.db_path)
        tax, _ = bootstrap_demo(st, plain, target_precision=0.95)
        for i in range(10):
            st.append_event(_audit_event(i, "edit"))
        g = calibrate_and_gate(st, "demo", tax, 0.95, plain)
        self.assertEqual(g.autopilot_level, 0)
        st.close()


if __name__ == "__main__":
    unittest.main()
