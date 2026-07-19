# Changelog

All notable changes to Tessera. Versions follow semver; the version lives in
`pyproject.toml` and `tessera/__init__.py`.

## 0.11.0 — 2026-07-17

### Changed
- **The consensus gate is now DEFAULT ON** (`TESSERA_SPECIALIST=0` to
  disable). Safe by construction: the specialist joins only on a
  classification task whose train half holds ≥ `TESSERA_SPECIALIST_MIN`
  examples of ≥ 2 labels, **and** — the new rule that makes default-on
  honest — only when the calibration half keeps at least
  `TESSERA_MIN_GOLD` gold items, so enabling the co-signal can never cost
  cross-validated calibration. Otherwise the run is byte-identical to
  v0.9 behavior (the bundled 24-gold demo: specialist stays out, CV
  survives; AG News 297-gold: joins and delivers 93.5% @ 92.8% TRUE with
  no flags set).

## 0.10.0 — 2026-07-17

The automation pass: consensus gate, near-duplicate propagation, and the
audit autopilot — the three pieces that turn the loop into a system a human
supervises rather than works.

### Added
- **Consensus gate** (`TESSERA_SPECIALIST=1`): the Tier-0 specialist joins
  the labeling ensemble as an ordinary member, trained on a hash-stable HALF
  of the trusted labels; the gate then calibrates only on the other half
  (leak guard, detected from stored votes so report-time re-gates stay
  consistent). Specialist–LLM disagreement flattens ensemble confidence and
  routes. `TESSERA_SPECIALIST_MIN` (default 10) sets the minimum training
  half before it joins; classification only.
- **Near-duplicate propagation** (`TESSERA_PROPAGATE=<cosine>`, e.g. 0.95):
  greedy leader grouping at a high cosine threshold; only representatives —
  plus everything holding gold — are labeled by the LLM, and members mirror
  their representative's label, confidence, and gate state (provenance in
  the `clusters` table and the rationale). Members stay in the audit
  universe. Resolving a representative resolves its group (bulk accept);
  an audit reject un-ships the group; undo restores it; a member a human
  has touched is emancipated and never mirrored again.
- **Audit autopilot** (`TESSERA_AUTOPILOT=1`): closed-loop gate control from
  audit evidence. Each adjustment judges only the audits since the last one
  (min window `TESSERA_AUTOPILOT_MIN`, default 20): a breach — exact
  one-sided binomial test against the target at 95% confidence — raises the
  tightening level (allowed error halves per level, capped at 3); a clean
  window at/above the target lowers it. The gate then runs at the effective
  target; report and CLI state the level and the tightened target.
  Report-only re-gates read the level but never consume evidence.
- `GateResult`/`QualityReport` carry `n_propagated`, `autopilot_level`,
  `effective_target`; `counts()` reports propagated rows; CLI summary and
  report caveats surface all three features.

### Measured (AG News 400, 297 gold, held-back truth; cached champion calls)
- **Consensus 4B @ 90% target: 93.5% coverage at 92.8% TRUE (92.7% on items
  the calibrator/specialist never saw)** — versus 64.0% coverage without
  the specialist. The 90%-coverage goal is met on the noisy benchmark.
- Consensus 2B @ 90%: 49.8% coverage at 98.0% TRUE (97.1% unseen) — versus
  33.5% @ 88.1% alone; the co-signal fixes the small model's confidence.
- Consensus 4B @ 85%: 100% coverage at 88.8% TRUE (89.3% unseen). @ 95%:
  still honestly refused (0 coverage) — label noise, not signal, binds.
- 2B @ 85% keeps the promise in aggregate (89.3%) but its unseen split dips
  to 82.8% — prefer 2B @ 90% or the 4B tiers.
- Near-duplicate density is corpus-dependent and both benchmarks arrive
  pre-deduped (AG News: 1 member at 0.9; SMS: 4–7 at 0.95–0.85), so
  propagation's call savings are ~1–2% there; the semantics are covered by
  tests and the factor scales with real corpus redundancy.

### Measured (SMS 300, live 4B logprob run, hidden reference)
- The logprob head alone at a 90% target over-promises on this task: 100%
  coverage at 85.3% TRUE. **Consensus catches it: 85.7% coverage at 89.9%
  TRUE (88.6% unseen)** — the co-signal routed the 43 items that broke the
  promise. At 95% both configs honestly refuse under CV. All 4 propagated
  members (cosine 1.00) match the hidden reference.
- **Closed loop, live:** on the over-promising config, an oracle worked the
  93-item audit slice (86.0% confirmed vs the 90% promise). The autopilot
  correctly held level 0 (p≈0.13 — not confident breach evidence), but the
  13 audit corrections entered gold and the re-gate collapsed coverage
  100%→0.3%: the system un-shipped the broken promise by itself, through
  the first-responder channel (gold growth), with the autopilot as backstop.

### Tests
165 (23 new: consensus split/leak guard, propagation mirror/bulk-accept/
un-ship/undo/emancipation/re-gate lockstep, autopilot breach/recovery/
report-isolation/accumulation).

## 0.9.0 — 2026-07-17

The limits pass: low-end verdicts, honest gold at scale, the Tier-0
specialist, and three retirements the harness ordered.

### Added
- **Tier-0 specialist + cascade** (`tessera/engine/specialist.py`,
  `scripts/cascade.py`): a pure-stdlib logistic head over the hashed-BoW
  features, trained on human-trusted labels only, wrapped as an ordinary
  labeler behind the same calibration/gate; the cascade script measures
  specialist → LLM → human end to end with a train/calibrate gold split
  (leak measured first: same-gold calibration passed 100% of items).
- **Static few-shot** (`TESSERA_FEWSHOT_STATIC=1`): one fixed example block
  (class round-robin) shared by every prompt; self-items fall back to
  nearest-mode. Built as a prefix-cache speed lever.
- **Multi-endpoint local ensemble**: `TESSERA_OPENAI_URL` + `TESSERA_MODEL`
  accept comma lists — one labeler per (url, model) pair.
- `cache_prompt: true` on local logprob calls (llama-server KV opt-in).

### Measured and decided (the harness arbitrates)
- **Gold at scale is the honest configuration.** With 297 gold: the 95%
  target is correctly refused (the 4B's best band is ~94% — the old 64%@95%
  claim was under-sampled-gold optimism); at 90% the gate delivers 64%
  coverage at 94.1% TRUE (97.2% unseen) — a conservative promise, kept with
  margin. At 85%: 99.2% coverage at 86.4%.
- **2B (low-end): an economy option only** — 129s/400 on M4 (projected
  ~20 min/400 on X1-class CPUs) but 33.5% coverage @ 88.1% true at a 90%
  target; 75.8% @ 85.8% at 85%. 9B fails the low-end bar (~20s/item
  projected on X1) — bench-projected, not X1-measured.
- **Retired by measurement:** the cross-family Gemma ensemble (63.0% @ 92.1%
  unseen = identical to plain 4B at 1.5x cost); static few-shot for accuracy
  (39% coverage vs plain's 64%); and its speed rationale — cross-item
  partial-prefix KV reuse does not function on the current llama-server chat
  endpoint regardless of cache-reuse/cache_prompt flags (identical-prompt
  reuse works; 351 prefill tokens paid per item either way). Engine-level;
  worth an upstream report.
- **Specialist at 149 training examples earns zero honest coverage at 90%**
  on noisy 4-class news — the mechanism works (0.5s training, ~instant
  inference, honest gate); it awaits flywheel-scale corrections. Tier 1
  carried the cascade at champion numbers (64.2% @ 94.2% true).

## 0.8.0 — 2026-07-17

Speed AND accuracy: the logprob head + gold few-shot, measured head-to-head.

### Added
- **Logprob-head classification** (`TESSERA_LOGPROBS=1`, openai-shaped local
  servers — llama-server/vLLM): the prompt asks for the label as ONE word and
  the first answer token's top-logprobs become the label distribution. One
  call per item instead of N samples; continuous, honestly-calibratable
  confidence instead of verbalized buckets. Unmatched token mass is discarded
  and the rest renormalized; zero label mass routes the item.
- **Gold few-shot retrieval** (`TESSERA_FEWSHOT=k`, docs/05 Phase A RAG-lite):
  the k nearest gold examples (hashed-BoW cosine) are shown in the prompt,
  self-leak guarded. Classification only.
- AG News sample rubric v2 (explicit boundary rules for the measured
  world/business/scitech confusions).

### Measured (Qwen3.5-4B local, 400 AG News items, 95% target, truth-validated)
| config | wall | coverage | true unseen |
|---|---|---|---|
| 5-vote baseline | ~15.5 min | 46.8% | 90.1% |
| rubric v2 + fewshot4, 5-vote | ~33 min | 56.8% | 91.2% |
| logprob head, plain | 5.1 min | 64.0% | 92.1% |
| logprob + fewshot4 | 6.9 min | 78.0% | 89.9% |

The logprob head dominates: 3x faster than the baseline with +17pts coverage
and +2pts true precision. Few-shot on top buys +14pts more coverage at ~-2pts
precision — a defensible choice at a 90% target, not at 95%. Verbalized
confidence is the model's opinion about its answer; the token distribution IS
the answer's uncertainty.

## 0.7.1 — 2026-07-17

### Fixed
- **A re-gate no longer resurrects human-reviewed items into the queue.**
  Found dogfooding a real 300-message spam-filter annotation run: after the
  reviewer finished every item, re-gating reported the full queue again
  (finals were preserved, but the count claimed work that was done). Human-
  resolved items now stay out of the queue.

### Dogfood run (2026-07-17, real user journey, $0)
- 300 raw SMS downloaded, rubric written, 60-item gold hand-labeled, labeled
  by a locally-served Qwen3.5-4B (released winc v1.26.0 binary, context pin
  verified through the real install path), 39 human reviews (5 in the UI,
  keyboard-first), training set exported. Hidden-reference audit: 93.3%
  agreement overall — human decisions 29/29 (100%), auto 92.6% (~4-6 of the
  20 disagreements are reference noise). Lesson recorded: a 2% audit at
  n=10 cannot detect a ~7% auto error rate — size audits by count, not rate.

## 0.7.0 — 2026-07-17

Phase 2 (Loop) instrumentation + the router experiment, decided by the harness.

### Added
- **Run-over-run instrumentation**: every gating run appends a row (coverage,
  threshold, gold size, queue size, cumulative human touches) — visible in the
  CLI report, the UI report panel, and `/api/report`. This is docs/08 Phase
  2's "coverage up, effort down" made visible.
- **Cluster-aware router (experimental, `TESSERA_ROUTER=cluster`)**: hashed
  bag-of-words embeddings + leader clustering (stdlib), queue priority =
  uncertainty × informativeness × representativeness with cluster
  interleaving for diversity.
- `scripts/simulate_review.py --router` for A/B-ing queue orderings.

### Decided (the harness arbitrates — docs/08 Phase 2 risk rule)
- **Confidence-first stays the default router.** Equal 86-review oracle
  budgets on the local AG News run: confidence-only found 21 model errors,
  the cluster formula 17 — representativeness weighting traded away raw
  uncertainty. Cluster mode ships opt-in with its diversity rationale
  documented; re-run the A/B on your own data before switching.

## 0.6.0 — 2026-07-17

Span/NER — the third beachhead label type. Phase 1's "three label types live"
exit criterion is met.

### Added
- **Span annotations as first-class labels**: a span set serializes to one
  canonical JSON string that flows through predictions, gold, finals, events,
  audit, and exports unchanged. Confidence is whole-annotation voting — any
  boundary/type disagreement between ensemble members or self-consistency
  samples flattens the distribution and routes the item. Deterministic
  validators (bounds, order, overlap, entity types) are the floor.
- **LLM span labeling by exact quote** (models are unreliable with character
  offsets): quotes are resolved to offsets locally; an unresolvable quote
  fails the sample soft instead of shipping a guess.
- **Quote-based gold authoring**: gold rows may give {text, type} instead of
  offsets; the loader resolves them against the item text.
- **Span review UI**: highlighted entities with type tags, click a span to
  remove it, select text + number key (or type button) to add one, Enter
  submits as accept or edit automatically. Verified live end-to-end.
- **Stub lexicon NER** (offline demo): deterministic member boundary styles
  (tight vs run-extended) make title/determiner cases disagree and route.
  `demo --span`: 65% auto-labeled at 100% CV precision, 7/20 routed.
- Per-entity-type span precision in the quality report; span regression
  baseline in CI.

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
