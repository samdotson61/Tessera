# Changelog

All notable changes to Tessera. Versions follow semver; the version lives in
`pyproject.toml` and `tessera/__init__.py`.

## 0.5.0 — 2026-07-17

### Added
- **Audit sampling** (docs/04 Layer 5, `TESSERA_AUDIT_RATE`, default 2%): a
  deterministic slice of AUTO-APPLIED items is also routed to the human. The
  label still ships (coverage unchanged); the verdict verifies the SLA in
  production (`audit_precision` on the quality report) and feeds auto-region
  errors into gold — the one channel queue review cannot provide. Accept
  confirms the shipped label, edit overturns it (correction enters gold),
  reject un-ships it and routes the item; undo restores the pending audit.
  Selection is hash-stable per item: re-gates keep the same audit set and
  reviewed items are never re-audited. UI: AUDIT badge in the queue, audit
  count in the header, audit line in the report caveats.
- `scripts/simulate_review.py --audit-rate` for measuring the effect.

### Measured (oracle sim on the local Qwen3.5-4B run, 15% audit)
- One audit round collapsed the estimate–truth gap from 5.7 to 1.3 points
  (CV 98.2%→95.9% vs true 92.5%→94.6%), re-priced coverage honestly
  (46.8%→36.8%), and lifted unseen true precision 90.1%→93.9%.

### Fixed
- A re-gate no longer overwrites a human's audit correction with the model
  label (human-resolved finals are preserved on auto-applied items).

## 0.4.0 — 2026-07-14

### Added
- **`TESSERA_OPENAI_URL`** — the openai-shaped twin of `TESSERA_ANTHROPIC_URL`:
  point the labeler at any `/v1/chat/completions` endpoint (ollama, vLLM,
  llama-server), key optional.
- **`scripts/simulate_review.py`** — oracle-reviewer simulation (held-back
  truth stands in for the human) measuring the gold-growth coverage climb
  round over round.
- LLM call timeouts raised to 180s (local servers decode slower than APIs and
  queue concurrent requests).

### Findings (measured, local Qwen3.5-4B, $0 — see README)
- Self-consistency raised confidence resolution 5 → 48 distinct values and
  coverage 27.5% → 46.8% at similar true precision.
- Review-queue gold growth alone cannot correct auto-region overconfidence —
  the docs/04 audit sample is required (next build item).

## 0.3.0 — 2026-07-14

### Added
- **Free, fully-local labeling**: `TESSERA_ANTHROPIC_URL` points the
  anthropic-shaped request at any `/v1/messages` endpoint — e.g. a local
  winc.cpp / llama.cpp server — with no API key required. `TESSERA_MODEL`
  now flows through to the LLM labelers (e.g. `qwen3.5-4b`).
- **Near-JSON salvage parser**: small local models occasionally drop a quote
  or brace; when the `label`/`confidence` fields are intact they are now
  recovered by regex instead of failing the sample (seen live: a 4B omitted
  the opening quote of the rationale string).

## 0.2.1 — 2026-07-14

### Fixed
- Keyless/stub runs no longer create an empty `tessera_cache.db` in the
  working directory — the LLM response cache opens lazily, only when a
  provider with an API key is configured.

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
