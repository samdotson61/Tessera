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
   spread across labels, so each correction teaches the system the most.
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

## Layout

```
tessera/            core package (stdlib only)
  schemas.py        dataclasses (Item, Taxonomy, Prediction, Event, …)
  storage.py        SQLite persistence
  labelers/         stub (offline), Anthropic/OpenAI labelers (self-consistency
                    + cache + retries), and the LLM-as-judge
  engine/           confidence · calibration · metrics (CIs, reliability) ·
                    gating · router · goldset · verify
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

This is **Phase 0 plus most of Phase 1 (Trust)** of the roadmap in
[`docs/08`](docs/08-implementation-and-development-plan.md): the end-to-end
loop, the trust layer (calibration + CV + bootstrap CIs + LLM-as-judge + the
CI regression gate), gold-set growth, two of the three beachhead label types
(classification and pairwise/preference — span/NER not yet), and the flywheel
data capture. Still open in Phase 1: span/NER, and a real-model
coverage@precision number on a real partner dataset (the tooling is in
`scripts/`; it needs an API key). Phases 2–4 (active-learning at scale,
per-customer model distillation, the cross-task "Composer" labeler) are
designed in `docs/`, not yet built. See
[`docs/09`](docs/09-risks-open-questions-and-glossary.md) for risks and open
questions.

## License

MIT © 2026 Sam Dotson. See [LICENSE](LICENSE).
