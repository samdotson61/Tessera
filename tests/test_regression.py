"""The coverage@precision regression gate (docs/08, engineering principle #1).

Runs the full loop on the bundled sample datasets and fails if the north-star
number drops below the recorded baseline. This is what makes the CI a harness
gate rather than a unit-test suite: no prompt, calibration, router, or gating
change ships if it regresses coverage@precision on the fixed gold sets.
"""
import json
import os
import tempfile
import unittest

from tessera.config import Settings
from tessera.storage import Storage
from tessera.app import bootstrap_demo

BASELINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "regression_baseline.json")


def _run(sample, target):
    d = tempfile.mkdtemp()
    settings = Settings(db_path=os.path.join(d, "reg.db"))
    storage = Storage(settings.db_path)
    try:
        _, gate = bootstrap_demo(storage, settings, target_precision=target, sample=sample)
        return gate
    finally:
        storage.close()


class TestCoverageAtPrecisionRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(BASELINE, encoding="utf-8") as f:
            cls.baselines = {k: v for k, v in json.load(f).items() if not k.startswith("_")}

    def _check(self, sample):
        b = self.baselines[sample]
        gate = _run(sample, b["target_precision"])
        self.assertGreaterEqual(
            gate.coverage, b["min_coverage"] - 1e-6,
            f"{sample}: coverage@precision regressed "
            f"({gate.coverage:.4f} < baseline {b['min_coverage']}). If the drop is "
            "intentional, update tests/regression_baseline.json and justify it in "
            "the commit message.")
        self.assertGreaterEqual(
            gate.achieved_precision, b["min_achieved_precision"] - 1e-6,
            f"{sample}: achieved precision fell below the target "
            f"({gate.achieved_precision:.4f} < {b['min_achieved_precision']}).")

    def test_intents_baseline_holds(self):
        self._check("intents")

    def test_pairwise_baseline_holds(self):
        self._check("pairwise")

    def test_span_baseline_holds(self):
        self._check("span")


if __name__ == "__main__":
    unittest.main()
