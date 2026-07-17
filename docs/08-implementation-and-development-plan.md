# Tessera — Implementation & Development Plan

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The step-by-step, phase-by-phase build plan: what a small team builds, in what order, with the exit criteria that gate each step.

Last updated: June 2026

---

## 1. How to read this plan

This document turns the roadmap from the [product vision](01-product-vision-and-prd.md) into an executable sequence. The phases use the suite's canonical names — **Phase 0 — Wedge, Phase 1 — Trust, Phase 2 — Loop, Phase 3 — Flywheel, Phase 4 — Composer** — and each phase is described with the same skeleton:

| Field | What it tells you |
|---|---|
| **Goal** | The one outcome that defines the phase. |
| **Workstreams** | The parallel tracks of work (so a 2-3 person team can split). |
| **Steps** | The concrete, ordered build tasks. |
| **Deliverables** | The artifacts that exist when the phase ends. |
| **Exit criteria** | The objective bar — almost always a number from the gold harness — that says "done." |
| **Dependencies** | What must exist first. |
| **Risks** | What most likely derails this phase, and the mitigation. |

**Every timeline in this document is an estimate.** A 1-3 person team's velocity is unpredictable, and the phases are gated by *quality bars*, not by calendar dates — a phase ends when its number is hit, not when the clock runs out. Durations are given to convey rough relative scope and sequencing, not commitments. Treat the week/month figures as planning aids, not deadlines.

The single load-bearing idea: **the coverage@precision harness gates everything.** No prompt, calibration, router heuristic, or distilled model ships until the harness confirms it improved (or at least held) the north-star number on a held-out gold set. This is "eval-driven development," and it is the spine of the plan (§3).

## 1a. Build status (July 2026)

**Phase 0 (Wedge) and most of Phase 1 (Trust) are built and shipped** — github.com/samdotson61/Tessera (public), 94 passing tests + CI (v0.2.1). What actually shipped, and where it diverged from the plan below:

- **Built (Phase 0, June 2026):** the full loop (ingest → ensemble labelers → calibration → coverage@precision gate → keyboard-first review UI → flywheel event log → export), the gold-set harness, the adjustable precision slider, and the per-dataset quality report.
- **Built (Phase 1, July 2026):** production-shaped LLM labeling (self-consistency sampling, response cache, retries, concurrency, optional two-family ensemble); the **LLM-as-judge verification pass** (different-family, veto-only, fail-open); the **pairwise/preference label type** end-to-end (second of the three beachhead types); **gold-set growth from human corrections** (source-tracked, seed gold immutable); **bootstrap 95% CIs on coverage@precision** computed on the out-of-fold CV values; **undo** in the review loop; the **quality-report panel with a reliability diagram** in the UI; and the **coverage@precision regression gate wired into CI** (engineering principle #1 made enforceable).
- **Divergence — the wedge shipped on a pure-stdlib stack, not the production stack.** To make the MVP run anywhere with zero install, Phase 0 used SQLite (not Postgres), a stdlib HTTP server plus a custom keyboard-first web UI (not a Label Studio fork), and in-process orchestration (not Temporal). The production substitutions in [architecture](03-system-architecture.md) remain the target; the orchestration and model-layer interfaces are written so they slot in.
- **Added during build:** cross-validated precision/ECE (the in-sample numbers were optimistic), plus an adversarial review pass that fixed a path-traversal bug, a gate/precision coherence bug, and non-idempotent event logging; later, a stale-final-label fix when a stricter re-gate routes a previously auto-applied item.
- **First real-model numbers produced (2026-07-14, local, $0):** 400 AG News items labeled by a locally-served Qwen3.5-4B via `TESSERA_ANTHROPIC_URL` (winc.cpp). Greedy single-sample: 27.5% coverage, 93.6% true precision (vs 97.0% CV estimate). 5-vote self-consistency: 46.8% coverage, 92.5% true (90.1% unseen) — resolution went from 5 to 48 distinct confidence values. Both miss the 95% SLA out-of-sample, as the wide bootstrap CI warned. **Structural finding:** an oracle-reviewer simulation (`scripts/simulate_review.py`) showed gold grown from the routed queue never corrects auto-region overconfidence — the docs/04 **audit sample is load-bearing**. **Audit sampling built (v0.5.0, 2026-07-17):** a deterministic `TESSERA_AUDIT_RATE` slice of auto-applied items is human-verified (label ships, verdict feeds gold + reports `audit_precision` — the production SLA check; accept confirms, edit overturns, reject un-ships and routes). Measured at 15% audit on the local run: estimate–truth gap 5.7→1.3 points, coverage honestly re-priced 46.8%→36.8%, unseen true precision 90.1%→93.9%. The §4.3 headline number on a frontier model and a real partner dataset remains open.
- **Span/NER built (v0.6.0, 2026-07-17)** — the third beachhead label type, completing the Phase 1 "three label types live" exit criterion: annotations are canonical span-sets flowing through the whole engine as one label; confidence = whole-annotation voting (boundary/type disagreement routes, per docs/08's start-simple guidance); deterministic validators (bounds/overlap/types) are the floor; the LLM path asks for exact quotes and resolves offsets locally (models are unreliable with offsets); gold can be authored by quote; the review UI highlights spans with click-to-remove and select-text+number-key add (verified live: an edited annotation landed at exact gold offsets). Sample demo: 65% auto-labeled at 100% CV precision, the 7 title/determiner boundary cases routed.
- **Full user-journey dogfood (v0.7.1, 2026-07-17, $0):** a real annotation job run end to end (download 300 raw SMS → write rubric → hand-label 60 gold → local-model labeling on the released winc v1.26.0 → keyboard review of all routed+audit items, partly in the UI → export). Result: 90.3% auto at 96.7% CV; hidden-reference agreement of the finished training set 93.3% (human decisions 100%, auto 92.6%). Two products of the run: the re-gate queue-resurrection fix, and the audit-sizing lesson (size by count, not rate — n=10 cannot see a ~7% error rate).
- **Phase 2 begun (v0.7.0, 2026-07-17):** run-over-run instrumentation (per-run coverage/gold/queue/human-touch rows in the report, UI, and API) and the router experiment — the §6 formula (uncertainty × informativeness × representativeness over stdlib hashed-BoW leader clusters) was built and A/B'd against confidence-only with the oracle harness at equal budgets: **confidence-only found 21 errors to the formula's 17**, so per this plan's own risk rule ("the harness arbitrates") confidence-first remains the default and cluster mode ships opt-in (`TESSERA_ROUTER=cluster`).
- **Not yet built:** the rest of Phase 2 (production event lake, run-over-run dashboards on real usage, embedding at scale), Phase 3 (per-customer distillation), Phase 4 (cross-task Composer). The Phase 0 / Phase 1 sections below now read as a record of what was done.

---

## 2. Team & skills assumptions

The plan assumes a **founding team of 1-3 people** at the start, growing only as revenue and load justify it. Three roles cover Phase 0-2; specialist hires arrive at Phase 3.

| Role | Owns | Core skills | When essential |
|---|---|---|---|
| **Founder (designer-founder)** | Product, the felt loop, the beachhead/partner relationships, the rubric UX, the quality-report narrative | Product taste, UX, runs local LLMs, writes the partner pitch | Day 1 |
| **Full-stack / ML generalist** | Backend services, orchestrator, confidence/calibration, model layer, eval harness | Python/FastAPI, LLM APIs, basic stats (calibration), Postgres, Docker | Day 1 (this is the critical hire if the founder isn't this person) |
| **Frontend / UX engineer** | The Label Studio fork, the keyboard-first loop, the report UI | React, the Label Studio codebase, keyboard-interaction design | Phase 0 Days 8-10 (can be the founder if they code; otherwise hire by Phase 1) |

Phase-by-phase staffing (estimates):

| Phase | Headcount (est.) | What it needs that's new | Add-a-hire trigger |
|---|---|---|---|
| **0 — Wedge** | 2 (founder + generalist; frontend part-time) | Nothing net-new in skills — it's an integration sprint | — |
| **1 — Trust** | 2-3 | Dedicated frontend for the loop; calibration rigor | Hire frontend engineer if founder can't carry the fork |
| **2 — Loop** | 3 | Active-learning / sampling intuition; data-pipeline discipline | First **ML/data engineer** if generalist is saturated |
| **3 — Flywheel** | 3-4 | LoRA/QLoRA fine-tuning, GPU ops, model registry/MLOps | Dedicated **ML training engineer**; consider a part-time **DevOps/infra** hand for on-prem packaging |
| **4 — Composer** | 4-5 | Multi-task training, larger eval surface | **Research-leaning ML hire** for the cross-task model |

The discipline: stay at 2-3 until the flywheel (Phase 3) demands real training/MLOps skills. The first three phases are an *integration and trust* problem, not a research problem; over-hiring before Phase 3 spends runway on the wrong skills.

---

## 3. Engineering principles

Five principles, applied from the first commit. They are the reason the build sequence looks the way it does.

1. **Eval-driven development.** The gold-set harness and the coverage@precision number come *first* (Phase 0, Days 6-7) and gate every change after. Before merging a new prompt, calibration method, router rule, or model, you run it through the harness and confirm the number didn't regress. The harness is a first-class service, not a notebook — see the [accuracy & trust engine](04-accuracy-and-trust-engine.md).

2. **Capture flywheel data from day one — even before you use it.** Every accept / edit / reject / auto-apply is logged to the event schema from the very first project of Phase 0, *months before* any model trains on it. The flywheel is worthless without history, and history cannot be backfilled. Schema lives in the [data model](06-data-model-and-api-contracts.md); strategy in the [flywheel doc](05-data-flywheel-and-model-strategy.md).

3. **Fork Label Studio, keep the IP in the backend.** The forked UI renders what the backend sends and posts decisions back — it holds *no* proprietary logic. The four net-new-IP components (Rubric, Confidence/Verification, Active-learning router, Distillation) live in separate backend services behind a clean API. This is the defining architectural rule (see [architecture](03-system-architecture.md) §11b); violating it makes the moat a copyable frontend plugin.

4. **Ship the felt loop, not a library.** The Refuel cautionary tale (a $5.2M acqui-hire for raw autolabeling) is the whole reason for the product. We do not ship "an LLM that labels data." We ship a *trusted, keyboard-first product*. Every phase must improve something the user *feels* — speed, trust, or effort saved — not just a backend metric.

5. **Data versioning + CI from early.** Datasets are pinned to immutable versions (lakeFS/DVC) and rubrics are versioned so every label traces to the exact data + rubric that produced it. A lightweight CI runs the harness on a fixed regression gold set on every backend change. Reproducibility is a trust requirement, not a nicety — a customer staking a release on our labels can ask "show me how this label was made," and the answer must be deterministic.

---

## 4. Phase 0 — Wedge (the 2-week spike)

> **This is the most important and most detailed section. Everything downstream depends on producing one real number on one real dataset.**

**Goal:** in ~2 weeks, fork Label Studio, pre-label ONE real partner dataset with an LLM, attach calibrated confidence, ship tab-to-accept, build the gold harness, and **produce the headline coverage@precision number** — e.g. *">X% auto-labeled at ≥98% precision, human time cut Y%."* No distillation, no multimodal, no integrations, no billing.

**Estimate:** ~2 calendar weeks (10 working days, plus buffer), 2 people. This is a deliberate spike — favor wiring things together over polishing them.

### 4.1 Day-by-day plan

All days are estimates; the sequence and dependencies matter more than the exact dates.

| Day(s) | Goal | Concrete steps | Output |
|---|---|---|---|
| **1-2** | Pick the dataset + define the rubric + stand up the skeleton | Choose ONE real partner dataset (e.g. **support-ticket intent** classification OR **LLM-output quality** rating). Write a v1 rubric: label set, definitions, 2-3 positive/negative examples per label, edge cases. Stand up **Label Studio** (Docker), **Postgres**, and **LiteLLM** with one frontier key (Claude/GPT). Load the raw dataset into the dataset store. | A real dataset loaded; a written rubric; LS + Postgres + LiteLLM running locally; one item visible in the UI. |
| **3-5** | Orchestrator v0 — get labels + confidence + rationale | Build the labeling loop: for each item, compile the rubric into a prompt + few-shot scaffold, call the model via LiteLLM, run **self-consistency (N=5 samples)**, take the majority vote, and store `label + raw confidence + rationale`. Confidence v0 = self-consistency agreement (fraction of samples agreeing) blended with verbalized confidence. Run it over the full dataset; cache responses on `(prompt-hash, model, params)` so you don't re-pay. | Every item has a predicted label, a confidence in [0,1], and a rationale, persisted in Postgres. |
| **6-7** | Gold harness v0 — produce the NUMBER | Human-label a **stratified ~100-item gold sample** (stratify by predicted label + confidence band so every region is represented). Fit a v0 **calibration** map (start with simple binning / isotonic regression on the gold set) so confidence ≈ true precision. Compute **coverage@precision**: for a target precision (e.g. 98%), find the confidence threshold that holds it on gold, and report what % of the dataset clears it. | The first **coverage@precision** number. A reliability sketch (predicted vs actual precision). The threshold for the target precision. |
| **8-10** | HITL UI — the felt loop | In the LS fork: pre-fill each item with the model's label + rationale + confidence; bind **one key = accept**, **type/select = correct**, **one key = reject**; sort the queue by **ascending confidence** (most uncertain first); add a **bulk-accept** action for a filtered above-threshold view. Wire the UI to the backend API (no IP in the fork). | A reviewer can clear the queue keyboard-only; bulk-accept the confident slice; correct the hard tail. The loop is *felt*. |
| **11-12** | Confidence-gated auto-apply + slider + export | Add the **gate**: items with calibrated confidence ≥ threshold auto-apply; the rest route to the queue. Add a **precision slider** that moves the threshold and live-updates the projected coverage and the queue size. Add **JSONL export** of the finished labels. | Auto-apply works; the slider lets the user trade coverage vs precision; labels export as JSONL. |
| **13-14** | Flywheel logging + quality report + demo | Log **every** accept / edit / reject / auto-apply to the flywheel event schema (do **not** train yet). Generate a **quality report**: coverage@precision, the calibration sketch, human-time-saved estimate, a ~2% audit sample. Rehearse and run the **partner demo**. | The full event log populated; a one-page quality report; a working demo and the headline number for the partner. |

### 4.2 Tech setup checklist

A literal "stand it up" list for Days 1-2. Keep each piece behind the seams the [architecture](03-system-architecture.md) defines so Phase 1+ can swap them.

- [ ] **Repo + monorepo layout** — `backend/` (FastAPI services), `ui/` (Label Studio fork), `harness/` (eval/gold), `infra/` (Docker Compose).
- [ ] **Docker Compose** for local dev: Postgres, Label Studio, MinIO (S3-compatible), one FastAPI app.
- [ ] **Label Studio fork** — fork the repo, get it building locally, confirm you can render a custom labeling interface.
- [ ] **Postgres** — schema for `dataset`, `item`, `rubric`, `label`, `event` (use the [data model](06-data-model-and-api-contracts.md) as the source of truth even in v0).
- [ ] **LiteLLM** — configured with one frontier provider key; confirm a round-trip call and that the **response cache** is on.
- [ ] **Object store (MinIO)** — for raw payloads, exports, and the event-log files.
- [ ] **Vector DB (defer or minimal)** — Phase 0 can skip RAG few-shot and use static rubric examples; if time allows, stand up LanceDB for nearest-gold few-shot. Not required for the number.
- [ ] **Orchestration = Redis/RQ or a plain async loop** — the simplest thing that runs the batch. Temporal is explicitly **not** a Phase-0 dependency (architecture §7 escape hatch).
- [ ] **Flywheel event sink** — append-only JSONL to MinIO + a Postgres index, wired from the first interaction.
- [ ] **Eval harness module** — a `harness/` package that takes (predictions, gold) → coverage@precision + calibration curve. This is the artifact that outlives the spike.
- [ ] **One precision target chosen with the partner** (e.g. 98%) so the number is meaningful to them.

### 4.3 Success metric

**The deliverable of Phase 0 is a number, on a real dataset, that a real partner cares about:**

> *">X% of the dataset auto-labeled at ≥98% precision (calibrated on a held-out gold set), cutting human labeling time by ~Y%."*

If that sentence can be said truthfully and demoed, Phase 0 succeeded — regardless of how rough the code is. If it can't, the wedge isn't real yet and no later phase should start. Everything in Phases 1-4 is making that sentence bigger, more trustworthy, and self-improving.

**Explicitly NOT in the Phase 0 spike:** multimodal, distilled models, the foundation model, integrations, multi-tenant billing, on-prem packaging. (Mirrors the MVP scope in [PRD](01-product-vision-and-prd.md) §9.)

---

## 5. Phase 1 — Trust

**Goal:** turn the rough Phase-0 number into a *defensible, repeatable SLA*. A design partner ships auto-labels they trust, and the precision SLA holds on a held-out set they didn't see.

**Estimate:** ~4-8 weeks after Phase 0 (estimate), 2-3 people.

### Workstreams
- **Gold-harness hardening** — stratified sampling, bootstrap confidence intervals on coverage@precision, a fixed regression gold set wired into CI, audit-sample reporting.
- **Calibration** — move from v0 binning to robust calibration (temperature scaling / isotonic regression) fit **per taxonomy**; validate calibration holds on a held-out gold split, not just the fit split.
- **Gating + slider** — the production confidence gate with a target-precision SLA; the precision slider with live coverage/queue projection; clear "below threshold → human" routing.
- **Quality report** — the polished, shareable per-dataset report (coverage@precision, reliability diagram, audit sample, time saved) that a buyer can stake a release on.
- **Multi-label-type support for the beachhead** — extend beyond single-label classification to the three beachhead types: **text classification, span/NER, pairwise/preference.** Each needs its own confidence + verifier semantics.

### Steps
1. Replace v0 calibration with per-taxonomy temperature scaling/isotonic; add a held-out calibration validation split.
2. Add deterministic **validators** (label ∈ enum, span within bounds, JSON-schema match) feeding the verdict alongside confidence.
3. Add an **LLM-as-judge** verification pass using a *different model family* than the labeler (correlated-error mitigation; see [accuracy engine](04-accuracy-and-trust-engine.md)).
4. Implement span/NER and pairwise/preference label flows end-to-end (orchestrator, confidence, UI, export).
5. Build the production gold-set management flow (seed gold, grow gold from human edits, refresh on drift).
6. Wire the regression gold set into CI; fail the build on coverage@precision regression beyond a tolerance.
7. Ship the polished quality report and a clean JSONL/HF export.

### Deliverables
- Per-taxonomy calibration with a validated SLA.
- Three working label types (classification, span/NER, preference).
- The shareable quality report; CI gate on the north-star number.

### Exit criteria

| Criterion | Bar |
|---|---|
| **SLA holds on held-out set** | Target precision (e.g. ≥98%) is met on a gold split the calibration never saw, within the stated confidence interval. |
| **Design partner ships** | At least one design partner exports and *uses* auto-labels in their real workflow without full re-review. |
| **Three label types live** | Classification, span/NER, and pairwise/preference each produce a calibrated coverage@precision number. |
| **CI gate live** | Backend changes are blocked on a coverage@precision regression on the fixed gold set. |

### Dependencies
Phase 0's gold harness and event logging. (Calibration depends on a gold set existing; the SLA depends on calibration.)

### Risks
- **Calibration doesn't transfer across taxonomies** (the open question in [risks doc](09-risks-open-questions-and-glossary.md)) → mitigate by fitting per-taxonomy and validating on held-out splits; surface uncertainty in the report rather than over-promising.
- **Correlated errors fool LLM-as-judge** → use a different model family for the judge; lean on deterministic validators and the audit sample as independent checks.
- **Span/NER confidence is harder than classification** → start with token-level agreement + boundary validators; treat preference tasks (pairwise) as the easiest second type to add.

---

## 6. Phase 2 — Loop

**Goal:** close the active-learning loop so human effort *measurably drops run-over-run* on the same taxonomy. The system shows coverage climbing and the human queue shrinking, run over run.

**Estimate:** ~6-10 weeks after Phase 1 (estimate), 3 people.

### Workstreams
- **Active-learning router** — the net-new-IP routing service: queue priority = **uncertainty × informativeness × representativeness**, with diversity sampling so the human isn't shown 500 near-identical items.
- **Full event logging / data lake** — graduate the Phase-0 event sink into the proper append-only flywheel lake (partitioned by tenant/dataset/time, Postgres-indexed), with the complete event schema from the [data model](06-data-model-and-api-contracts.md).
- **Coverage-climb instrumentation** — per-run dashboards that plot coverage@precision and human-effort-per-item across successive runs on the same taxonomy. This is the flywheel made visible (and the leading indicator of the moat).
- **Queue prioritization** — surface the router's ordering in the UI; cluster-aware bulk-accept; "work the most valuable items first."

### Steps
1. Build embedding/clustering over the vector DB (dedup + cluster + representativeness scoring).
2. Implement the router scoring function and the auto-apply-vs-route decision as a distinct service.
3. Add diversity/coverage sampling so routed items span clusters, not duplicates.
4. Harden the event lake (partitioning, append-only guarantees, the full schema).
5. Build the run-over-run instrumentation dashboard (coverage up, effort down).
6. Expose queue priority + cluster grouping in the UI; cluster-level bulk-accept.

### Deliverables
- The active-learning router service.
- The production event lake with full logging.
- The run-over-run coverage/effort dashboard.

### Exit criteria

| Criterion | Bar |
|---|---|
| **Measured effort reduction** | Human-reviewed share per item drops run-over-run on the same taxonomy (the instrumentation proves it). |
| **Router beats confidence-only** | Prioritized queue clears the dataset to target precision with fewer human touches than ascending-confidence-only ordering. |
| **Full event lake live** | Every interaction lands in the append-only lake in the canonical schema, ready for distillation. |

### Dependencies
Phase 1's calibrated confidence (the router needs trustworthy confidence as an input) and the Phase-0 event logging (now hardened into the lake).

### Risks
- **Router complexity yields little over confidence-only** → ship uncertainty-only first, add informativeness/representativeness only if the dashboard shows they help; the harness arbitrates.
- **Diversity sampling hides easy wins or starves clusters** → tune the mix against effort-per-item, not intuition.

---

## 7. Phase 3 — Flywheel

**Goal:** a **distilled per-customer labeler BEATS the base frontier LLM** at the target precision on a real taxonomy, at lower cost. The loop from [architecture](03-system-architecture.md) §2 closes: corrections → distillation → registry → orchestrator picks the new champion.

**Estimate:** ~8-12 weeks after Phase 2 (estimate), 3-4 people (now needs ML training + GPU ops).

### Workstreams
- **Distillation pipeline** — the net-new-IP training service: correction pairs from the lake → **LoRA/QLoRA** fine-tune of a 4-8B model on burst GPU (Modal/RunPod or local), per the [flywheel doc](05-data-flywheel-and-model-strategy.md) Phase B.
- **Model registry** — every model version registered with its gold scorecard; the orchestrator reads "who is champion for this taxonomy."
- **Champion/challenger** — a freshly distilled adapter is **shadow-evaluated against gold** and promoted *only on a win*. Promotion is a registry write, not a redeploy.
- **On-prem / private packaging** — the same Docker/K8s images deployable in a customer VPC; air-gapped path with zero frontier egress (local + distilled models only).
- **Retrain cadence** — automatic distillation triggered when correction volume crosses a threshold; scheduled re-evaluation against the latest gold.

### Steps
1. Build the corrections-to-training-set extractor from the lake (item → human-confirmed label pairs, tenant-scoped).
2. Stand up the LoRA/QLoRA fine-tune job on burst GPU; produce a 4-8B adapter.
3. Build the model registry (versions + gold scorecards + champion pointer per taxonomy).
4. Implement champion/challenger shadow evaluation against gold; promote only on a win.
5. Route orchestrator traffic to the champion via LiteLLM (distilled model is just another provider behind the seam).
6. Package on-prem images; validate the air-gapped, zero-egress deployment.
7. Add the retrain-cadence trigger (volume- and schedule-based).

### Deliverables
- A distilled per-customer labeler that beats frontier on gold.
- The model registry + champion/challenger promotion.
- On-prem/air-gapped packaging.

### Exit criteria

| Criterion | Bar |
|---|---|
| **Distilled beats frontier** | On a real taxonomy, the distilled labeler meets/exceeds target precision at **equal-or-higher coverage** than the base frontier LLM. |
| **Cheaper per item** | Distilled inference is materially cheaper/faster per item than frontier (the cost the flywheel was built to collapse). |
| **Promotion is automated + safe** | Champion/challenger promotion happens only on a gold win, tenant-isolated, with no cross-tenant data leakage. |
| **On-prem works** | A single-tenant/air-gapped deployment runs end-to-end with zero frontier egress. |

### Dependencies
Phase 2's full event lake (no corrections corpus → nothing to distill) and Phase 1's gold harness (the only thing that can certify "beats frontier"). **This is the hard ordering constraint: event logging before distillation.**

### Risks
- **Not enough corrections to distill a winner** → set a minimum-volume gate before claiming a per-customer model; until then, stay on frontier + RAG few-shot (flywheel Phase A).
- **GPU ops burn runway** → rent per-job (Modal/RunPod), never keep GPUs hot; distillation is bursty.
- **Distilled model regresses on rare classes** → champion/challenger against a *stratified* gold set; never promote on aggregate alone.

---

## 8. Phase 4 — Composer

**Goal:** a **cross-task labeling foundation model** that gives near-zero cold-start on a brand-new task — a new taxonomy reaches useful coverage@precision *materially faster* than a Phase-A (frontier + RAG) cold start.

**Estimate:** ~3-6+ months after Phase 3 (estimate, lowest confidence in the plan), 4-5 people including a research-leaning ML hire.

### Workstreams
- **Cross-task foundation labeler** — train across many tasks/customers (**opt-in only**, on shareable data) into a base labeler that generalizes across taxonomies; flywheel doc Phase C.
- **Cold-start evaluation** — a harness that measures time/effort to reach a target coverage@precision on a *held-out, never-seen* task, comparing the foundation labeler vs the Phase-A baseline.
- **Privacy guardrails** — explicit, revocable opt-in; default fully isolated; no silent cross-customer training (architecture §10).

### Steps
1. Build the opt-in, shareable-data pipeline (tenant consent, data marking, isolation by default).
2. Assemble the cross-task training corpus from opted-in data.
3. Train the foundation labeler; evaluate generalization on held-out tasks.
4. Build the cold-start eval (time-to-useful-coverage on a new task).
5. Wire the foundation labeler as the new cold-start default (behind the model layer).

### Deliverables
- The cross-task foundation labeler.
- The cold-start eval and its headline comparison number.

### Exit criteria

| Criterion | Bar |
|---|---|
| **Faster cold-start** | A brand-new task reaches useful coverage@precision **materially faster** than the Phase-A (frontier + RAG) cold start. |
| **Privacy honored** | Foundation training uses only explicit, opt-in, shareable data; isolation-by-default verified. |

### Dependencies
Phase 3's distillation pipeline and registry, and a *fleet* of per-customer corrections. **Phase B (per-customer distillation) before Phase C (foundation).** This is the longest-horizon, highest-uncertainty phase — treat it as a research bet, not a committed deliverable.

### Risks
- **Cross-task transfer underwhelms** → the foundation model may not beat per-customer distillation; keep Phase 3 as the proven default and gate Phase C on a clear cold-start win.
- **Privacy/opt-in friction** → if few customers opt in, the corpus is thin; design the opt-in as a clear value exchange, never a dark pattern.

---

## 9. Milestone & timeline table

**All durations are estimates** and represent rough relative scope, not commitments. Phases are gated by their exit number, not the calendar — a phase ends when the bar is hit.

| Phase | Rough duration (est.) | Headline deliverable | The number that proves it |
|---|---|---|---|
| **0 — Wedge** | ~2 weeks | A real coverage@precision number on a partner dataset, with the felt tab-accept loop | ">X% auto-labeled at ≥98% precision; human time cut ~Y%" |
| **1 — Trust** | ~4-8 weeks | Defensible precision SLA + quality report; 3 beachhead label types | SLA holds on a held-out gold set a partner ships against |
| **2 — Loop** | ~6-10 weeks | Active-learning router + run-over-run instrumentation | Human effort/item drops run-over-run on the same taxonomy |
| **3 — Flywheel** | ~8-12 weeks | Distilled per-customer labeler + registry + on-prem | Distilled labeler beats frontier at target precision, cheaper |
| **4 — Composer** | ~3-6+ months | Cross-task foundation labeler | New task hits useful coverage@precision faster than cold-start |

Cumulatively, Phases 0-3 (the MVP-through-moat arc) are roughly a **~5-9 month** effort for a small team (estimate); Phase 4 is a longer-horizon research bet that follows only once the flywheel is proven.

---

## 10. Critical path & dependencies

The hard ordering constraints (everything else can flex):

1. **Gold harness before gating.** You cannot set a trustworthy confidence threshold without a held-out gold set to calibrate against. The harness is Phase 0, Days 6-7 — earlier than the UI polish.
2. **Calibration before the SLA.** The precision SLA is only meaningful once confidence is calibrated per taxonomy and validated on held-out data (Phase 1).
3. **Event logging before distillation.** Corrections must be captured *from day one* (Phase 0) or there is nothing to distill in Phase 3. This is why logging is principle #2, not a Phase-3 task.
4. **Per-customer distillation (Phase B) before the foundation model (Phase C).** You need a fleet of per-customer labelers and their corrections before a cross-task model has anything to generalize from.

```
                 ┌─────────────────────────────────────────────────────────────┐
   Flywheel event logging (Phase 0, day 1) ─────────────────────────┐           │
                 │                                                   │           │
   Gold harness ─┼─► Calibration ─► Precision SLA ─► Router ─────────┤           │
   (P0 d6-7)     │   (P1)           (P1)             (P2)            ▼           │
                 │                                            Distillation       │
   Felt loop ────┘                                            (P3, Phase B)      │
   (P0 d8-10)                                                       │            │
                                                                    ▼            │
                                                       Foundation labeler        │
                                                       (P4, Phase C) ────────────┘
```

Read it as: **logging + gold harness are the roots**; calibration → SLA → router is the trust/loop trunk; distillation needs the lake; the foundation labeler needs the distillation fleet. Nothing on the right can start before its left-hand dependency is real.

---

## 11. Build-vs-fork-vs-buy summary

This table is the operational form of the [architecture](03-system-architecture.md) §11 decisions. The rule: **build the moat, buy/OSS the commodity, fork the table-stakes UI.**

| Component | Decision | Why | Reference |
|---|---|---|---|
| **HITL annotation UI** | **Fork** Label Studio | Editor UI is table stakes, not the moat; skip years of plumbing (the Cursor/VS Code move) | arch §11a |
| **Rubric service** | **Build** (net-new IP) | The rubric→{prompt, validator} compiler is the auditable contract | arch §3.2 |
| **Confidence / calibration / verification** | **Build** (net-new IP) | Calibrated coverage@precision *is* the product promise | arch §3.4, §11f |
| **Active-learning router** | **Build** (net-new IP) | What makes one human cover a 1M-item dataset | arch §3.5 |
| **Distillation pipeline** | **Build** (net-new IP) | Per-customer models out-labeling frontier are the moat | arch §3.8 |
| **Label-error detection** | **Buy / OSS Cleanlab** | Confident-learning is a solved, packaged problem | arch §11f |
| **Orchestration** | **Buy / OSS Temporal** (Redis/RQ to start) | Durable, idempotent, resumable long runs; start simplest | arch §7, §11c |
| **Model abstraction** | **Buy / OSS LiteLLM** | One signature for all providers; provider swaps become config | arch §11d |
| **Vector DB** | **Buy / OSS Qdrant / LanceDB** | ANN search is a commodity; LanceDB for small/on-prem, Qdrant to scale | arch §6, §11e |
| **Data versioning** | **Buy / OSS lakeFS / DVC** | Reproducible, immutable dataset versions | arch §6 |
| **Distillation (PEFT)** | **Buy / OSS HF Transformers + PEFT** | LoRA/QLoRA tooling is mature; we own the *pipeline*, not the trainer | arch §3.8 |

The four **Build** rows are the only net-new IP. Everything else is glue, OSS, or a fork — by design.

---

## 12. Definition of Done per phase

The single metric that proves each phase, restated as a crisp DoD:

| Phase | Definition of Done | The one metric that proves it |
|---|---|---|
| **0 — Wedge** | The headline sentence is true and demoable on a real partner dataset | A real **coverage@precision** number (e.g. ">X% at ≥98%") |
| **1 — Trust** | A design partner ships auto-labels without full re-review; the SLA holds on a held-out set | **Held-out precision SLA holds** at the target |
| **2 — Loop** | Run-over-run human effort drops, instrumented and visible | **Human-reviewed share per item ↓ run-over-run** |
| **3 — Flywheel** | A distilled per-customer labeler beats frontier at target precision, cheaper | **Distilled coverage@precision ≥ frontier**, at lower cost |
| **4 — Composer** | A new task reaches useful coverage@precision faster than cold-start | **Time-to-useful-coverage on a new task ↓** vs Phase-A baseline |

If a phase's metric isn't met, the phase isn't done — no matter how much code shipped. The metric, not the feature list, is the gate.

---

## 13. Immediate next 30 days

Concrete actions, in order, to get from this document to the Phase-0 number.

| Week | Action | Owner | Output |
|---|---|---|---|
| **Week 1** | **Lock the beachhead and pick the first label task.** Confirm LLM post-training data (SFT/preference/eval) per [PRD](01-product-vision-and-prd.md); choose the single first task (support-ticket intent OR LLM-output quality). | Founder | A one-paragraph beachhead + task definition. |
| **Week 1-2** | **Sign 1-2 design partners** and get a real dataset + a precision target from at least one. | Founder | Signed partner(s); one real dataset in hand; an agreed target precision (e.g. 98%). |
| **Week 2-4** | **Run the Phase 0 spike** (the §4 day-by-day plan) on the partner dataset. | Generalist + founder | Forked LS + LLM pre-label + gold harness + tab-accept + export. |
| **Week 4** | **Report the number.** Produce the quality report and demo the headline coverage@precision to the partner. | Founder + generalist | The sentence: ">X% auto-labeled at ≥98% precision, human time cut ~Y%." |

The 30-day goal is singular: **a real number on a real partner dataset.** Sign the partner, run the spike, report the number. Everything in Phases 1-4 is making that number bigger, more trustworthy, and self-improving — but it does not start until the wedge is proven.

---

*Cross-references: scope & success metrics — [product vision](01-product-vision-and-prd.md); fundability & pricing — [business case](02-business-case-and-strategy.md); the services and stack — [architecture](03-system-architecture.md); the trust mechanics each phase is gated by — [accuracy & trust engine](04-accuracy-and-trust-engine.md); the Phase A/B/C model strategy — [data flywheel](05-data-flywheel-and-model-strategy.md); the event schema logged from day one — [data model & API contracts](06-data-model-and-api-contracts.md); the felt loop built in Phase 0 — [HITL UX spec](07-hitl-ux-spec.md); the open questions behind the risks — [risks, open questions & glossary](09-risks-open-questions-and-glossary.md).*
