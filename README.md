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

# Label your own data (the bundled sample doubles as a format example):
python -m tessera label --data tessera/sample_data/intents.jsonl \
    --taxonomy tessera/sample_data/taxonomy.json --gold tessera/sample_data/gold.jsonl --dataset ds1
python -m tessera --db tessera.db report --dataset ds1
python -m tessera --db tessera.db export --dataset ds1 --out labels.jsonl --pairs pairs.jsonl
```

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
| `TESSERA_JUDGE` | off | LLM-as-judge provider — use a *different family* than the labeler |
| `TESSERA_JUDGE_MODEL` | per provider | judge model override |
| `TESSERA_GROW_GOLD` | `1` | record review accepts/edits as gold (source `human`) |
| `TESSERA_AUDIT_RATE` | `0.02` | share of auto-applied items also routed for human audit |
| `TESSERA_ROUTER` | `confidence` | queue order; `cluster` = experimental AL formula (lost the first errors-found A/B 17–21) |

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

| run | distinct conf values | coverage | CV estimate | true (all auto) | true (unseen) |
|---|---|---|---|---|---|
| greedy, 1 sample | 5 | 27.5% | 97.0% | 93.6% | 92.2% |
| sampled, 5 votes | 48 | **46.8%** | 98.2% | 92.5% | 90.1% |

Three honest lessons, straight from the trust layer:

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
                    gating · router (+embed clusters) · goldset · verify ·
                    audit sampling · spans
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
criterion). Still open: a frontier-model coverage@precision number on a real
partner dataset (the tooling is in `scripts/`; it needs an API key — local
models measured free, see above). Phase 2 (Loop) has begun: run-over-run
instrumentation ships, and the cluster router was built and A/B'd — the
harness kept confidence-first as default (21 vs 17 errors found at equal
budgets). Still ahead: the rest of Phase 2 (event lake, real-usage
dashboards), Phase 3 (per-customer distillation), and Phase 4 (the cross-task
"Composer" labeler), designed in `docs/`. See
[`docs/09`](docs/09-risks-open-questions-and-glossary.md) for risks and open
questions.

## License

MIT © 2026 Sam Dotson. See [LICENSE](LICENSE).
