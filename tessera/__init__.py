"""Tessera — "Cursor for data labeling" (MVP, pure standard library).

Point it at a dataset, it auto-labels the easy majority at a calibrated target
precision, routes the uncertain remainder to a human, and logs every interaction
for the data flywheel. See docs/ for the full design suite.

This MVP intentionally depends only on the Python standard library so it runs and
tests pass anywhere with no install step. The production stack (FastAPI, Postgres,
a vector DB, Temporal, model distillation) is described in docs/03 and docs/08.
"""

__version__ = "0.1.0"
