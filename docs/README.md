# Tessera — Design Suite

*Tessera is a working codename; rename at will. It evokes assembling many small labeled tiles (tesserae) into one complete mosaic — a finished, trustworthy dataset.*

The design-doc suite for **"Cursor for data labeling"**: a tool you point at a raw dataset that auto-labels the easy majority at a *guaranteed precision*, routes only the hard cases to a human through a Cursor-grade keyboard-first review loop, and turns the human-correction stream into private, specialized labeling models that get better at your taxonomy every week.

Last updated: June 2026

---

## The thesis in one paragraph

Cursor started as a VS Code fork with AI tab-autocomplete + bring-your-own-model, built a tight semi-automated loop, then trained its own models (Composer) on the proprietary interaction data — and went from ~$100M ARR (Jan 2025) to ~$3B ARR (May 2026) at a ~$50B valuation. **Tessera ports that exact playbook to data labeling.** The raw capability — "an LLM labels a dataset" — is commoditizing and is *not* a business on its own (Refuel AI proved this: an autolabeling library that raised $5.2M and got absorbed by Together AI in 2025). The moat is the same three things Cursor nailed: **(1) Cursor-grade UX, (2) a trust layer teams actually believe, (3) a data flywheel that graduates from bring-your-own-LLM to your own specialized models.**

## The one mechanism that makes it work

**Confidence-gated auto-apply, calibrated to a target precision against a gold set.** You don't *assert* accuracy — you *gate* on calibrated confidence measured on a small held-out human-labeled "gold set," auto-apply only above the line that hits the target precision (e.g. ≥98%), and route the rest to a human. The north-star metric is **coverage@precision** — *"what % of your data we auto-label at ≥98% precision."* That single number is the entire pitch.

## Implementation status (June 2026)

A runnable **MVP of the core loop is built and shipped** — repo: **github.com/samdotson61/Tessera** (private). Quickstart and the code layout live in the repository's own top-level `README.md`; the documents in this folder are the design behind it. The MVP has **45 passing tests** and CI, runs with **no third-party dependencies**, and was hardened by an adversarial review pass. It corresponds to **Phase 0 + the core of Phase 1** below.

What exists today vs. what is still on the page:

| Capability | Doc | Status |
|---|---|---|
| Core loop: ingest → ensemble-label → calibrate → gate → review → export | 01, 04 | **Built** |
| coverage@precision; calibration (isotonic via weighted PAV); cross-validated precision/ECE | 04 | **Built** |
| Confidence ensemble; active-learning queue ordering; gold-set sampling; deterministic verification | 04 | **Built** |
| Entities, schemas, append-only flywheel event log | 06 | **Built** — SQLite + dataclasses subset |
| Keyboard-first review UI (pre-fill, one-key accept/correct, trust slider) | 07 | **Built** — custom stdlib web UI; the Label Studio fork in doc 03 is the design target |
| Flywheel data capture | 05 | **Built** — capture only; distillation (Phase 3) and the foundation model (Phase 4) are designed |
| Production infra — FastAPI, Postgres, vector DB, Temporal, Label Studio fork | 03 | **Designed.** The MVP ships a pure-stdlib subset: SQLite, a stdlib HTTP server + custom web UI, in-process orchestration, deterministic-stub or BYO-LLM labelers |
| Active learning at scale (Phase 2); distillation (Phase 3); cross-task "Composer" (Phase 4) | 08 | **Designed, not built** |

## How to read this suite

| # | Document | What's in it |
|---|---|---|
| — | [README](README.md) | This index |
| 01 | [Product Vision & PRD](01-product-vision-and-prd.md) | Problem, vision, the Cursor analogy, personas, the core loop, MVP scope, success metrics |
| 02 | [Business Case & Strategy](02-business-case-and-strategy.md) | Market sizing, competitive landscape (with numbers), positioning, pricing, the honest "is it game over?" verdict |
| 03 | [System Architecture](03-system-architecture.md) | Components, data flow, tech stack, deployment, scale, key decisions (ADRs) |
| 04 | [Accuracy & Trust Engine](04-accuracy-and-trust-engine.md) | The make-or-break core: confidence, calibration, verification, weak supervision, gold sets, the quality report |
| 05 | [Data Flywheel & Model Strategy](05-data-flywheel-and-model-strategy.md) | The moat: from bring-your-own-LLM → per-customer distilled labelers → a cross-task foundation model |
| 06 | [Data Model & API Contracts](06-data-model-and-api-contracts.md) | Entities, schemas, the flywheel event log, REST/SDK surface |
| 07 | [HITL UX Spec](07-hitl-ux-spec.md) | The interaction design that earns the Cursor comparison — screens, keyboard map, the trust slider |
| 08 | [Implementation & Development Plan](08-implementation-and-development-plan.md) | **The build plan in steps & phases** — the 2-week spike day-by-day, then Phases 1–4, milestones, critical path |
| 09 | [Risks, Open Questions & Glossary](09-risks-open-questions-and-glossary.md) | Candid risk register, unknowns to resolve, kill criteria, shared vocabulary |

### Suggested reading paths
- **Founder / quick orientation:** 01 → 02 → 08.
- **Engineer building it:** 01 → 03 → 04 → 06 → 08.
- **Investor / partner:** 02 → 01 → 05 → 09.
- **Designer:** 01 → 07 → 04.

## The phased roadmap (detailed in [doc 08](08-implementation-and-development-plan.md))

| Phase | Goal | Status | Proof point |
|---|---|---|---|
| **0 — Wedge** | The UX magic + the number | **Built** | The loop runs end-to-end; `tessera demo` auto-labels the bundled dataset at the target precision |
| **1 — Trust** | Believable accuracy | **Core built** | Calibration, gating, slider, and quality report shipped; design-partner validation still pending |
| **2 — Loop** | Effort drops | Designed | Active-learning routing; human effort per item falls run-over-run |
| **3 — Flywheel** | The moat | Designed | A distilled per-customer labeler beats the base frontier LLM at target precision, cheaper, private |
| **4 — Composer** | Network effects | Designed | A brand-new task hits useful coverage@precision far faster than a cold start |

(The MVP shipped Phase 0 on a pure-stdlib stack rather than the Label Studio fork in the original spike plan — see the status note above and [doc 08](08-implementation-and-development-plan.md).)

## Beachhead

Win one wedge first: **data for fine-tuning & evaluating LLMs** (SFT pairs, preference/RLHF data, output classification, eval-set curation) — the hottest 2026 buyer, technical (they get the flywheel), text-only (so current LLMs auto-label it most accurately → fastest trustworthy SLA), and dogfoodable. Then expand to enterprise text classification/NER, and only later to images/audio.

---

*Status: design suite + a shipped MVP, June 2026. Phase 0 and the core of Phase 1 are built and live at github.com/samdotson61/Tessera (a pure-stdlib implementation); Phases 2–4 and the production architecture in doc 03 are designed, not yet built. These remain living documents — revisit as design-partner reality contradicts the assumptions flagged throughout (especially the estimates in docs 03 and 08 and the open questions in doc 09).*
