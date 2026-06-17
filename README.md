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
python -m tessera demo --target 0.95

# Then open the keyboard-first review UI for the items it routed to you:
python -m tessera --db demo.db demo --serve     # http://127.0.0.1:8080

# Label your own data:
python -m tessera label --data my.jsonl --taxonomy my_taxonomy.json --gold my_gold.jsonl
python -m tessera report  --dataset ds1
python -m tessera export  --dataset ds1 --out labels.jsonl --pairs pairs.jsonl
```

Run the tests:

```bash
python -m unittest discover -s tests -t . -v
```

## What the demo shows

```
=== coverage@precision ===
  target precision : 95.00%
  achieved (CV)    : 100.00%  (cross-validated on gold)
  AUTO-LABELED     : 40 items (83.3% of dataset) at conf >= 1.000
  routed to human  : 8 items
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

The four ideas that make it more than "an LLM labels data":

1. **Calibrated confidence gate** — auto-apply only above the confidence threshold
   that holds the target precision on a held-out **gold set**. North-star metric:
   **coverage@precision**. (`tessera/engine/`)
2. **Honest measurement** — precision and calibration error are **cross-validated**,
   not reported in-sample, plus a per-dataset **quality report** with caveats.
3. **Active-learning router** — the human queue is ordered most-uncertain-first,
   spread across labels, so each correction teaches the system the most.
4. **Flywheel** — every accept/edit/reject is captured as a training pair, the
   corpus for distilling your own per-taxonomy labeler (see [`docs/05`](docs/05-data-flywheel-and-model-strategy.md)).

## Using a real LLM

By default a deterministic keyword **stub** labeler drives the loop (so everything
runs offline). Set a provider + key to use a real model — no code change:

```bash
export TESSERA_PROVIDER=anthropic      # or: openai
export ANTHROPIC_API_KEY=sk-...
python -m tessera label --data my.jsonl --taxonomy my_taxonomy.json --gold my_gold.jsonl
```

## Layout

```
tessera/            core package (stdlib only)
  schemas.py        dataclasses (Item, Taxonomy, Prediction, Event, …)
  storage.py        SQLite persistence
  labelers/         stub (offline) + optional Anthropic/OpenAI labelers
  engine/           confidence · calibration · metrics · gating · router · goldset · verify
  pipeline.py       the orchestrator (label → calibrate+gate → human action)
  flywheel.py       training-pair export + event stats
  quality.py        the dataset quality report
  server.py         stdlib review server (JSON API + static UI)
  web/              the keyboard-first review UI
  cli.py            `python -m tessera ...`
tests/              unittest suite (no third-party deps)
sample_data/        bundled support-intents dataset + taxonomy + gold
docs/               the full design suite (start at docs/README.md)
```

## Status & scope

This is **Phase 0–1** of the roadmap in [`docs/08`](docs/08-implementation-and-development-plan.md):
the end-to-end loop, the trust layer, and the flywheel data capture. Phases 2–4
(active-learning at scale, per-customer model distillation, the cross-task
"Composer" labeler) are designed in `docs/` and scaffolded behind clean
interfaces, not yet built. See [`docs/09`](docs/09-risks-open-questions-and-glossary.md)
for risks and open questions.

## License

MIT © 2026 Sam Dotson. See [LICENSE](LICENSE).
