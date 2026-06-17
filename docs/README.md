# Tessera — Design Suite

*Tessera is a working codename; rename at will. It evokes assembling many small labeled tiles (tesserae) into one complete mosaic — a finished, trustworthy dataset.*

The design-doc suite for **"Cursor for data labeling"**: a tool you point at a raw dataset that auto-labels the easy majority at a *guaranteed precision*, routes only the hard cases to a human through a Cursor-grade keyboard-first review loop, and turns the human-correction stream into private, specialized labeling models that get better at your taxonomy every week.

Last updated: June 2026

---

## The thesis in one paragraph

Cursor started as a VS Code fork with AI tab-autocomplete + bring-your-own-model, built a tight semi-automated loop, then trained its own models (Composer) on the proprietary interaction data — and went from ~$100M ARR (Jan 2025) to ~$3B ARR (May 2026) at a ~$50B valuation. **Tessera ports that exact playbook to data labeling.** The raw capability — "an LLM labels a dataset" — is commoditizing and is *not* a business on its own (Refuel AI proved this: an autolabeling library that raised $5.2M and got absorbed by Together AI in 2025). The moat is the same three things Cursor nailed: **(1) Cursor-grade UX, (2) a trust layer teams actually believe, (3) a data flywheel that graduates from bring-your-own-LLM to your own specialized models.**

## The one mechanism that makes it work

**Confidence-gated auto-apply, calibrated to a target precision against a gold set.** You don't *assert* accuracy — you *gate* on calibrated confidence measured on a small held-out human-labeled "gold set," auto-apply only above the line that hits the target precision (e.g. ≥98%), and route the rest to a human. The north-star metric is **coverage@precision** — *"what % of your data we auto-label at ≥98% precision."* That single number is the entire pitch.

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

| Phase | Goal | Proof point |
|---|---|---|
| **0 — Wedge** | The UX magic + the number | 2-week spike: fork Label Studio + LLM pre-label + tab-accept + gold harness → *"X% auto-labeled at ≥98% precision"* |
| **1 — Trust** | Believable accuracy | A design partner *ships* auto-labels they trust; the precision SLA holds |
| **2 — Loop** | Effort drops | Active-learning routing; human effort per item falls run-over-run |
| **3 — Flywheel** | The moat | A distilled per-customer labeler beats the base frontier LLM at target precision, cheaper, private |
| **4 — Composer** | Network effects | A brand-new task hits useful coverage@precision far faster than a cold start |

## Beachhead

Win one wedge first: **data for fine-tuning & evaluating LLMs** (SFT pairs, preference/RLHF data, output classification, eval-set curation) — the hottest 2026 buyer, technical (they get the flywheel), text-only (so current LLMs auto-label it most accurately → fastest trustworthy SLA), and dogfoodable. Then expand to enterprise text classification/NER, and only later to images/audio.

---

*Status: initial design suite, June 2026. These are living documents — revisit as design-partner reality contradicts the assumptions flagged throughout (especially the estimates in docs 03 and 08 and the open questions in doc 09).*
