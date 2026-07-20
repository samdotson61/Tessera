# Tessera

**Cursor for data labeling.** Point it at a dataset; it auto-labels the easy
majority at a *calibrated target precision*, routes only the uncertain remainder
to a human through a keyboard-first review loop, and logs every correction for the
data flywheel. *Tessera is a working codename — rename at will.*

> This repo contains a **runnable MVP** of the core loop plus the **full design
> suite** (`docs/`). The MVP is intentionally **pure Python standard library** —
> no installs, no API keys required — so it runs and its tests pass anywhere. The
> production stack (FastAPI, Postgres, a vector DB, Temporal, model distillation)
> is specified in [`docs/03`](docs/03-system-architecture.md) and
> [`docs/08`](docs/08-implementation-and-development-plan.md).

## Quickstart (zero dependencies)

```bash
# Run the whole loop on the bundled sample dataset (support-ticket intents):
python -m tessera --db demo.db demo --target 0.95

# Or the bundled A/B response-preference sample (pairwise labels):
python -m tessera --db demo-pw.db demo --pairwise

# Or the bundled entity-span (NER) sample — highlight/edit spans in the UI:
python -m tessera --db demo-span.db demo --span

# Then open the keyboard-first review UI for the items it routed to you:
python -m tessera --db demo.db demo --serve     # http://127.0.0.1:8080

# Label your own data (JSONL or CSV; the bundled sample doubles as a format example):
python -m tessera label --data tessera/sample_data/intents.jsonl \
    --taxonomy tessera/sample_data/taxonomy.json --gold tessera/sample_data/gold.jsonl --dataset ds1

# No gold yet? Author it first — a cluster-stratified sample, keyboard-first (cold start):
python -m tessera bootstrap --data your.csv --taxonomy tax.json --dataset ds1 --n 100

# Have labeled history? Check the rubric against it BEFORE labeling anything (session zero):
python -m tessera rubric-check --data your.csv --labels their_labels.csv --taxonomy tax.json
python -m tessera --db tessera.db report --dataset ds1
python -m tessera --db tessera.db export --dataset ds1 --out labels.jsonl --pairs pairs.jsonl
```

`report` and `serve` re-gate at the dataset's **last-gated target** by
default (override with `--target` or `TESSERA_TARGET_PRECISION`), so the
numbers you review are for the promise you actually made.

Run the tests:

```bash
python -m unittest discover -s tests -t . -v
```

## What the demo shows

```
=== coverage@precision ===
  target precision : 95.00%
  achieved         : 100.00%  (cross-validated)
  AUTO-LABELED     : 40 items (83.3% of dataset) at conf >= 1.000  [gold coverage 95% CI: 62.5%-100.0%]
  routed to human  : 8 items
  gold set         : 24 items
  calibration ECE  : 0.099 -> 0.042 (lower is better)

  >> Auto-labeled 83.3% of the data at >= 95% precision; a human only touches the remaining 8.
```

That sentence — **coverage at a guaranteed precision** — is the whole product. The
8 routed items are exactly the ambiguous ones (e.g. `"Refund please."`,
`"It's not working."`); the confident, clean majority is applied for you.

## How it works (the loop)

```
dataset ─▶ taxonomy/rubric ─▶ labeler(s) ─▶ ensemble confidence
                                                  │
                                       calibrate on a gold set
                                                  │
                                      confidence gate @ target precision
                                          ┌───────┴────────┐
                                  auto-applied        routed to human
                                  (+ audit)         (keyboard-first review)
                                          └───────┬────────┘
                                            every action logged
                                          (flywheel → train your own labeler)
```

The five ideas that make it more than "an LLM labels data":

1. **Calibrated confidence gate** — auto-apply only above the confidence threshold
   that holds the target precision on a held-out **gold set**. North-star metric:
   **coverage@precision**. (`tessera/engine/`)
2. **Honest measurement** — precision and calibration error are **cross-validated**,
   not reported in-sample; coverage carries a **bootstrap 95% CI**; the per-dataset
   **quality report** ships a reliability diagram and explicit caveats; and CI fails
   the build if the north-star number regresses on a fixed gold set.
3. **Layered verification** — deterministic rubric checks are the floor, and an
   optional **LLM-as-judge** from a *different model family* reviews every
   auto-apply candidate (a veto can only narrow the auto set, never widen it).
4. **Active-learning router** — the human queue is ordered most-uncertain-first,
   spread across labels, so each correction teaches the system the most. (A
   cluster-based uncertainty×informativeness×representativeness router exists
   too — the harness measured it below this baseline, so it ships opt-in.)
5. **Flywheel** — every accept/edit/reject is captured as a training pair and can
   **grow the gold set**, so calibration tightens run over run — the corpus for
   distilling your own per-taxonomy labeler (see [`docs/05`](docs/05-data-flywheel-and-model-strategy.md)).

## Using a real LLM

By default a deterministic keyword **stub** labeler drives the loop (so everything
runs offline). Set a provider + key to use a real model — no code change:

```bash
export TESSERA_PROVIDER=anthropic      # or: openai, or "anthropic,openai" for the two-family ensemble
export ANTHROPIC_API_KEY=sk-...
python -m tessera label --data my.jsonl --taxonomy my_taxonomy.json --gold my_gold.jsonl
```

The LLM path is production-shaped: **self-consistency** (N samples per item,
votes + verbalized confidence averaged into one distribution), a **response
cache** so re-runs and slider re-gates never re-pay the API, retry with
backoff, and a concurrent labeling pool.

| Env var | Default | What it does |
|---|---|---|
| `TESSERA_PROVIDER` | `stub` | `anthropic`, `openai`, or a comma list for a cross-family ensemble |
| `TESSERA_MODEL` | per provider | labeler model (default `claude-haiku-4-5` / `gpt-4o-mini`) |
| `TESSERA_SAMPLES` | `5` | self-consistency samples per item |
| `TESSERA_CACHE` | `tessera_cache.db` | LLM response cache; `none` disables |
| `TESSERA_WORKERS` | `8` | concurrent items in the labeling pass |
| `TESSERA_FEWSHOT` | `0` | k nearest gold examples shown in the prompt (RAG-lite, classification) |
| `TESSERA_FEWSHOT_STATIC` | `0` | one fixed example block for all items (measured: not recommended) |
| `TESSERA_LOGPROBS` | `0` | logprob-head classification: 1 call/item, token-probability confidence (openai-shaped local servers) |
| `TESSERA_ANSWER_KEY` | `auto` | logprob answer format: `auto` switches to lettered options (A/B/C…) when label words share prefixes; `letter`/`word` force it |
| `TESSERA_JUDGE` | off | LLM-as-judge provider — use a *different family* than the labeler |
| `TESSERA_JUDGE_MODEL` | per provider | judge model override |
| `TESSERA_GROW_GOLD` | `1` | record review accepts/edits as gold (source `human`) |
| `TESSERA_AUDIT_RATE` | `0.02` | share of auto-applied items also routed for human audit |
| `TESSERA_ROUTER` | `confidence` | queue order; `cluster` = experimental AL formula (lost the first errors-found A/B 17–21) |
| `TESSERA_SPECIALIST` | `1` | consensus gate (default ON): the Tier-0 specialist joins the ensemble (trained on half the trusted labels; calibration uses the other half); set `0` to disable |
| `TESSERA_SPECIALIST_MIN` | `10` | training examples required before the specialist joins |
| `TESSERA_PROPAGATE` | `0` | near-duplicate propagation cosine threshold (e.g. `0.95`); `0` = off |
| `TESSERA_AUTOPILOT` | `0` | closed-loop gate control from audit evidence (breach tightens, recovery relaxes) |
| `TESSERA_AUTOPILOT_MIN` | `20` | audits per autopilot decision window |

## Run it on a real dataset

`scripts/fetch_agnews.py` pulls 400 real news snippets (AG News test split,
keyless HuggingFace API), a stratified 120-item gold sample, and holds the full
ground truth back for post-hoc validation:

```bash
python scripts/fetch_agnews.py
export TESSERA_PROVIDER=anthropic ANTHROPIC_API_KEY=sk-...
python -m tessera --db agnews.db label --data data/agnews/items.jsonl \
    --taxonomy data/agnews/taxonomy.json --gold data/agnews/gold.jsonl \
    --dataset agnews --target 0.95
# Did the SLA hold on items the calibrator never saw? (the honest check)
python scripts/validate_run.py --db agnews.db --dataset agnews \
    --truth data/agnews/truth.jsonl --gold data/agnews/gold.jsonl
```

(~$1-2 of API usage at the defaults.) The demo numbers above come from the
offline stub on the bundled sample; run this to produce a coverage@precision
number on real data with a real model — the honest headline is *validated*
precision on the unseen split, not the gold-set estimate.

### …or for free, fully local

`TESSERA_ANTHROPIC_URL` points the labeler at any server that speaks the
Anthropic `/v1/messages` shape — e.g. [winc.cpp](https://github.com/samdotson61/winc.cpp)
or a llama.cpp server — no API key, no cost:

```bash
export TESSERA_PROVIDER=anthropic
export TESSERA_ANTHROPIC_URL=http://127.0.0.1:8080/v1/messages
export TESSERA_MODEL=qwen3.5-4b TESSERA_SAMPLES=1   # greedy server -> 1 sample
python -m tessera --db agnews.db label --data data/agnews/items.jsonl \
    --taxonomy data/agnews/taxonomy.json --gold data/agnews/gold.jsonl --dataset agnews
```

**Measured (2026-07-14, Qwen3.5-4B running locally via winc.cpp, $0):**
400 AG News items, 120-item stratified gold sample, 95% target — one run on a
greedy serve (single sample) and one on a sampling serve (5-vote
self-consistency):

| run | target | wall | coverage | true (all auto) | true (unseen) |
|---|---|---|---|---|---|
| Qwen3.5-4B greedy, 1 sample | 95% | ~5 min | 27.5% | 93.6% | 92.2% |
| Qwen3.5-4B sampled, 5 votes | 95% | ~15 min | 46.8% | 92.5% | 90.1% |
| Qwen3.5-4B rubric v2 + 4-shot, 5 votes | 95% | ~33 min | 56.8% | 93.0% | 91.2% |
| **Qwen3.5-4B logprob head** | 95% | **5.1 min** | **64.0%** | **94.1%** | **92.1%** |
| Qwen3.5-4B logprob + 4-shot | 95% | 6.9 min | 78.0% | 91.7% | 89.9% |
| Claude Haiku 4.5, 5 votes (~$1.30) | 95% | ~13 min | 0.0% | — | — |
| Claude Haiku 4.5, 5 votes | 90% | ~13 min | 89.0% | 88.8% | 87.2% |

**The logprob head** (`TESSERA_LOGPROBS=1`, llama-server/vLLM) asks for the
label as one word and reads the answer token's top-logprobs as the label
distribution — one call per item. It beat 5-vote self-consistency on every
axis at once: 3× faster, +17pts coverage, +2pts true precision. Verbalized
confidence is the model's *opinion about* its answer; the token distribution
*is* the answer's uncertainty. The local 4B with a logprob head also *beats
Haiku 4.5's verbalized confidence* on this task at both targets.

**The honest configuration (v0.9.0, 297 gold):** with gold at scale the gate
stops over-promising — 95% is correctly *refused* (this model's best band is
~94% on this data; the earlier 64%@95% was under-sampled-gold optimism), and
at a **90% target it delivers 64% coverage at 94.1% TRUE (97.2% on unseen
items)** — a conservative promise kept with margin. At 85%: 99.2% coverage.
Grow gold until the estimate stops moving; then the dial trades coverage for
precision truthfully across its whole range.

**The consensus gate (v0.10.0, default ON since v0.11.0) is the coverage
lever.** It trains the stdlib Tier-0 specialist on a hash-stable *half* of
the trusted labels and adds it to the ensemble (the gate calibrates only on
the other half — the leak guard). Agreement sharpens confidence;
disagreement flattens it and routes. It arms itself only when the trade is
sound: a classification task, enough training examples, **and enough gold
left on the calibration half to keep cross-validation alive** — otherwise
the run is unchanged (the bundled demo stays CV'd; `TESSERA_SPECIALIST=0`
disables outright). Same cached 4B calls, same 297 gold, held-back truth:

| run | target | coverage | true (all auto) | true (unseen) |
|---|---|---|---|---|
| 4B logprob alone | 90% | 64.0% | 94.1% | 97.2% |
| **4B + consensus** | 90% | **93.5%** | **92.8%** | **92.7%** |
| 4B + consensus | 85% | 100.0% | 88.8% | 89.3% |
| 4B + consensus | 95% | 0% — still honestly refused | — | — |
| 2B logprob alone | 90% | 33.5% | 88.1% | — |
| **2B + consensus** | 90% | **49.8%** | **98.0%** | **97.1%** |

A ~150-example logistic head that trains in under a second took the 4B from
64% to **93.5% coverage with the 90% promise kept out-of-sample**, and fixed
the 2B's confidence signal outright (+16 coverage, +10 precision — the
economy tier is now genuinely usable). The 95% refusal stands: consensus
sharpens the signal, it does not manufacture precision the dataset's label
noise can't support. (2B @ 85% holds in aggregate but its unseen split dips
to 82.8% — prefer 2B @ 90% or a 4B tier.)

The same lever on the SMS dogfood task (300 items, hidden reference, fixed
serving stack, near-duplicate propagation on — all 4 propagated members
match the reference):

| run | target | coverage | true (all auto) | true (unseen) |
|---|---|---|---|---|
| 2B logprob alone | 90% | 59.0% | 78.0% — promise broken | 74.0% |
| 2B logprob alone | 95% | 0% — honest refusal | — | — |
| **2B + consensus** | 90% | **100.0%** | **93.0%** | **92.5%** |
| **2B + consensus** | 95% | **87.0%** | **98.9%** | **99.0%** |
| 4B + consensus | 90% *and* 95% | 100.0% | 97.0% | 96.7% |

The 2B alone breaks its promise by 12 points (its 60-gold CV can't see the
miscalibration); with consensus it keeps both targets, and **the 95% target
is its sweet spot** — trading 13 points of coverage for ~6 points of
precision. That makes the smallest viable model an honest operating point
for X1-class laptops: 87% of the corpus auto-labeled at ~99% precision,
with the 4B as the full-coverage tier.

Measured and retired (the harness arbitrates): the cross-family local
ensemble (identical quality at 1.5× cost), static few-shot (coverage
regression, and its speed rationale is void — cross-item partial-prefix KV
reuse does not currently function on llama-server's chat endpoint), and the
9B for low-end (projected ~20s/item on X1-class CPUs). The cascade harness
(`scripts/cascade.py`) still ships for measuring specialist → LLM → human
tiering on organization-scale corpora.

**Near-duplicate propagation** (`TESSERA_PROPAGATE=0.95`) labels each tight
cosine group once — representatives (and anything holding gold) hit the LLM;
members mirror the rep's label and gate state with provenance, stay in the
audit universe, and resolve as a group in review (accepting a rep bulk-
accepts its members; an audit reject un-ships them all; a member a human
edits is emancipated). Honest scope note: both benchmark corpora arrive
pre-deduped (AG News: 1 near-dup at 0.9; SMS: 4–7), so the measured call
savings there are ~1–2% — the factor scales with your corpus's real
redundancy, which for raw org data (tickets, form responses, alert streams)
is typically far higher.

**The audit autopilot** (`TESSERA_AUTOPILOT=1`) closes the loop docs/04
drew: the audit verdict stream *is* the production SLA check, so the gate
now acts on it. Each decision window (default 20 fresh audits) is judged
with an exact binomial test against the target: a confident breach tightens
the gate one level (allowed error halves, capped at 3 levels), a clean
window at/above the promise relaxes one level, and inconclusive evidence
accumulates. The report states the level and the effective target the gate
actually ran at; corrections still flow to gold, and with the specialist on,
every run retrains it — drift pulls humans back in instead of silently
poisoning labels.

**The loop, demonstrated live (SMS 300, hidden reference — corrected in
v0.12.1):** on a healthy serving stack the local 4B logprob head labels all
300 SMS at **95.7% true, and with consensus 100% coverage at 97.0% true
(96.7% unseen), at both the 90% and 95% targets**. The original v0.10.0
session unknowingly ran against a mute model (empty reasoning-mode answers;
see the CHANGELOG erratum) — and the safety stack contained it exactly as
designed: consensus routed what the dead signal couldn't support, a 93-item
audit slice (86.0% confirmed) fed corrections to gold, and the re-gate
collapsed the broken config's coverage to 0.3% while the autopilot rightly
held its level (86% over 93 audits is not 95%-confident breach evidence).
Defense in depth, demonstrated against a real failure nobody had noticed —
and v0.12.1's no-signal guard now notices.

The frontier comparison (2026-07-17) added three more lessons:

- **Both models sit at the dataset's ceiling, not their own** — Haiku's true
  accuracy over all 400 items is 84% vs the local 4B's ~85%. AG News
  reference labels are noisy at the world/business/scitech boundaries; no
  labeler can beat a corpus's own label quality.
- **Haiku's zero at 95% is the gate being honest.** Its gold errors sit
  inside the top confidence bands, so cross-validation correctly concludes no
  threshold can promise 95% — refusing all coverage. (The 4B *claimed* 95%
  and truthed at 92.5%: the less honest outcome, invisibly.) At a defensible
  90% target Haiku auto-labels 89% of the dataset — ~2x the 4B's coverage,
  ~4 points less precise: diffuse verbalized confidence buys reach, not
  separation.
- **Gold provenance is load-bearing.** This gold was inherited corpus labels
  (noise included); in the SMS dogfood below, hand-curated gold produced a
  much better-behaved loop. Rubric and gold quality (docs/09 R3/R4) beat
  model choice.

Three honest lessons from the local runs, straight from the trust layer:

1. **Self-consistency buys resolution, and resolution buys coverage.** Greedy
   verbalized confidence collapsed to 5 distinct values — the gate physically
   had no intermediate threshold to choose, and growing the gold set moved
   nothing. Five sampled votes produced 48 distinct values and +70% coverage.
2. **A 4B's confidence signal saturates below a 95% SLA.** Both runs measured
   ~90–94% true precision on the auto-applied set — the 0–85% bootstrap
   coverage CI at 120 gold items said up front that the CV estimate couldn't
   be trusted to the point.
3. **Review-queue gold can't police the auto region — audit sampling does.**
   An oracle-reviewer simulation (`scripts/simulate_review.py`) worked the
   entire routed queue and coverage never moved: the errors that matter are
   *above* the threshold, where the reviewer never looks. With **audit
   sampling** built (a deterministic slice of auto-applied items is verified
   by a human — the label still ships, the verdict feeds gold), one audit
   round at 15% collapsed the estimate–truth gap from 5.7 to 1.3 points
   (CV 98.2%→95.9% vs true 92.5%→94.6%), re-priced coverage honestly
   (46.8%→36.8%), and lifted unseen true precision 90.1%→93.9%. The audit
   verdict stream is also the production SLA check (`audit_precision` in the
   quality report).

**End-to-end dogfood (2026-07-17, $0):** a real annotation job — 300 raw SMS
downloaded, a spam/ham rubric written, a 60-item gold sample hand-labeled,
machine-labeled by a locally-served Qwen3.5-4B, 39 items human-reviewed
(keyboard-first), training set exported. The gate auto-labeled 90.3% at
96.7% CV precision; audited against the dataset's own held-back reference
labels, the finished training set agreed 93.3% overall — the human decisions
100%, the auto set 92.6% (a fifth of the disagreements are arguably
reference noise). Working lesson that shipped as guidance: size the audit
sample by *count*, not rate — 10 audited items cannot detect a ~7% error
rate (~46% chance of seeing zero errors).

**Reasoning-model serving note (load-bearing):** the logprob head reads the
FIRST answer token, so the serving stack must not let the model deliberate
first. Qwen3.5 on a raw llama-server verbalizes reasoning on longer items —
empty `content`, first token literally "Thinking" — which turns the labeler
into a silent uniform voter (`--reasoning-budget 0` and `/no_think` do NOT
stop the prose variant; measured). Serve with
`--chat-template-kwargs '{"enable_thinking":false}'`. Tessera v0.12.1 counts
these dud responses per run (`n_no_signal`) and the report screams when the
share is meaningful — a mute labeler must never look like a quiet success.

Footprint note: labeling prompts are ~500-700 tokens end-to-end, so a tiny
context window is all a labeling endpoint needs. With winc.cpp ≥ v1.26.0, set
`context = "2048"` in winc.toml and the pin is honored exactly — measured
~2.9 GB RSS for the 4B (vs ~3.6 GB auto-fitted) with all parses clean. The
same floor works on a raw llama-server (`-c 2048 -np 2 --flash-attn on
--cache-type-k q8_0 --cache-type-v q8_0`) via `TESSERA_OPENAI_URL`. Below
~1K/slot the model's weights are the irreducible cost; prefer fewer, roomier
slots when squeezing memory (at 4 cramped slots we measured occasional parse
drops from contention).

## Layout

```
tessera/            core package (stdlib only)
  schemas.py        dataclasses (Item, Taxonomy, Prediction, Event, …)
  storage.py        SQLite persistence
  labelers/         stub (offline), Anthropic/OpenAI labelers (self-consistency
                    + cache + retries), and the LLM-as-judge
  engine/           confidence · calibration · metrics (CIs, reliability) ·
                    gating · router (+embed clusters, dedup groups) · goldset ·
                    verify · audit sampling · spans · specialist (Tier-0 +
                    consensus member)
  pipeline.py       the orchestrator (label → calibrate+gate+judge → human action/undo)
  flywheel.py       training-pair export + event stats
  quality.py        the dataset quality report
  server.py         stdlib review server (JSON API + static UI)
  web/              the keyboard-first review UI (accept/edit/reject/undo, A/B
                    panels for pairwise, report + reliability diagram)
  cli.py            `python -m tessera ...`
  sample_data/      bundled samples: support intents + A/B response preferences
scripts/            real-dataset fetch (AG News) + held-back-truth validation
tests/              unittest suite incl. the coverage@precision regression gate
docs/               the full design suite (start at docs/README.md)
```

## Status & scope

This is **Phase 0 plus Phase 1 (Trust)** of the roadmap in
[`docs/08`](docs/08-implementation-and-development-plan.md): the end-to-end
loop, the trust layer (calibration + CV + bootstrap CIs + LLM-as-judge +
audit sampling + the CI regression gate), gold-set growth, and **all three
beachhead label types** — classification, pairwise/preference, and span/NER
(each producing a calibrated coverage@precision number, the Phase 1 exit
criterion). The frontier comparison is done (Haiku 4.5 vs local Qwen, table
above); the remaining Phase 0 ask is the same loop on a real *partner*
dataset with curated gold — the engagement kit is ready (the
[partner one-pager](docs/partner-one-pager.md) and `rubric-check`, the
session-zero tool built from the rehearsal's main lesson) and the dress
rehearsal is done:
[the issue-triage case study](docs/case-study-issue-triage.md) ran the full
engagement protocol on 438 VS Code issues against maintainer labels as
held-back truth (findings: the annotator-vs-authority gap is the binding
risk; the rubric carries the customer's convention — 11%→90.9% coverage from
one rewritten paragraph; and the harness caught a silently mute model,
leaving the no-signal guard behind). Phase 2 (Loop) has begun: run-over-run
instrumentation ships, and the cluster router was built and A/B'd — the
harness kept confidence-first as default (21 vs 17 errors found at equal
budgets). The v0.10.0 automation pass added the consensus gate, near-
duplicate propagation, and the audit autopilot — the closed loop that lets
a human supervise the system instead of working it. Still ahead: the rest
of Phase 2 (event lake, real-usage dashboards), Phase 3 (per-customer
distillation), and Phase 4 (the cross-task "Composer" labeler), designed in
`docs/`. See
[`docs/09`](docs/09-risks-open-questions-and-glossary.md) for risks and open
questions.

## License

MIT © 2026 Sam Dotson. See [LICENSE](LICENSE).
