# Changelog

All notable changes to Tessera. Versions follow semver; the version lives in
`pyproject.toml` and `tessera/__init__.py`.

## 0.2.0 — 2026-07-01

Phase 1 (Trust) build-out: the LLM path made production-shaped, a second
beachhead label type, and the trust layer finished per docs/04 + docs/08.

### Added
- **Self-consistency LLM labeling** — N samples per item (default 5,
  `TESSERA_SAMPLES`); per-sample verbalized-confidence distributions are
  averaged so vote agreement and stated confidence both shape the result.
- **Response cache** (`tessera_cache.db`, `TESSERA_CACHE`) keyed on
  (provider, model, params, prompt, sample) — re-runs and slider re-gates
  never re-pay the API. Retries with exponential backoff on 429/5xx/network.
- **Two-family ensemble**: `TESSERA_PROVIDER=anthropic,openai` runs both
  labelers per item — cross-family disagreement is the strongest routing signal.
- **Concurrent labeling pass** (`TESSERA_WORKERS`, default 8).
- **LLM-as-judge verification** (`TESSERA_JUDGE`, `TESSERA_JUDGE_MODEL`):
  a different-family model reviews every auto-apply candidate and can veto it
  to the human queue; vetoes narrow the auto set, never widen it. Off by default.
- **Pairwise/preference label type** end-to-end: items carry
  `response_a`/`response_b`, the review UI renders side-by-side panels, and a
  bundled 20-pair sample ships with `python -m tessera demo --pairwise`.
- **Gold growth**: review-server accepts/edits become gold rows
  (source `human`; seed gold never overwritten; `TESSERA_GROW_GOLD=0` disables),
  so calibration tightens run over run.
- **Bootstrap 95% CI on coverage@precision**, computed on the out-of-fold CV
  values; shown in the CLI summary, the quality report, and the UI.
- **Undo** (`U` / `POST /api/undo`): reverts the last human action — event
  removed, grown gold dropped, item back in the queue.
- **Quality report panel in the UI** (`Q`): coverage + CI, achieved precision,
  ECE, an SVG reliability diagram, and the caveats list.
- **coverage@precision regression gate in CI** (`tests/regression_baseline.json`
  + `make regression`): the build fails if the north-star number drops on the
  fixed gold sets.
- **Real-dataset tooling**: `scripts/fetch_agnews.py` (keyless, stdlib fetch of
  400 AG News items + stratified 120-item gold) and `scripts/validate_run.py`
  (true precision of the auto-applied set against held-back ground truth).

### Fixed
- An item auto-applied at a loose target no longer keeps its stale
  `final_label` when a stricter re-gate routes it to a human.
- Current default labeler model (`claude-haiku-4-5`); no sampling params sent
  (newer models reject them; provider defaults give sample diversity).

### Changed
- Test suite grown from 45 to 93 tests, still zero third-party dependencies.

## 0.1.0 — 2026-06-17

Initial MVP: the end-to-end loop (ingest → ensemble labelers → isotonic
calibration → coverage@precision gate → keyboard-first review UI → flywheel
event log → export), cross-validated precision/ECE, the per-dataset quality
report, the bundled support-intents sample, and the full design suite in
`docs/` — pure standard library, 45 tests.
