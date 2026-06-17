# Tessera — System Architecture

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

How the system is built: the services, how data flows between them, the stack that runs them, and the load-bearing decisions behind the shape.

Last updated: June 2026

---

## 1. Architecture goals & principles

The architecture exists to make one thing cheap and repeatable: **auto-label the easy majority at a guaranteed precision, route only the hard cases to a human, and recycle every correction into a better model.** Six principles fall out of that.

| Principle | What it means concretely |
|---|---|
| **Modular services** | Each capability (ingest, rubric, orchestration, confidence, routing, training) is an independent service with a clean contract, deployable and scalable on its own. No monolith. |
| **Swappable UI, IP in the backend** | The human-in-the-loop (HITL) editor is a *fork* of an OSS tool; all proprietary logic lives in backend services it merely calls. The moat must not be a copyable frontend plugin. |
| **Cost-aware model calls** | Every model call is metered, batched, cached, and gated by a budget ceiling. Frontier-LLM spend is the dominant variable cost; the system is built to drive it down (toward distilled models) over time. |
| **Privacy / on-prem capable** | The same images deploy as multi-tenant SaaS *or* single-tenant in a customer VPC. No design choice (e.g. a shared queue) may block the air-gapped deployment. |
| **Eval-driven** | Nothing ships — not a prompt, a calibration, or a distilled model — without passing the gold-set harness. The eval harness is a first-class service, not a script. |
| **Durable by default** | A 1M-item run takes hours and costs real money. State lives in durable storage and durable workflows so a crash resumes, never restarts. |

These principles recur as the trade-offs in §11.

---

## 2. Component diagram

Solid arrows = item/data flow. `[NET-NEW IP]` marks the four proprietary components (per the brief); everything else is glue, OSS, or a fork.

```
                          ┌──────────────────────────────────────────────────────────────┐
                          │                      CONTROL PLANE                             │
   sources                │  Rubric Service [NET-NEW IP]   Eval / Gold Harness             │
 S3│HF│DB│CSV             │   taxonomy → prompt + validator    coverage@precision, audit   │
     │                    └─────────┬───────────────────────────────┬────────────────────┘
     ▼                              │ rubric vN                      │ reads gold + events
┌──────────┐   chunks+embeds   ┌────▼─────────────────────────────┐ │
│  INGEST  │──────────────────▶│      LABELING ORCHESTRATOR        │ │
│ chunk    │                   │      (Temporal workflows)         │ │
│ embed    │   dataset store   │  build prompt → call model(s) →   │ │
│ dedup    │◀─────────┐        │  self-consistency → verify →      │ │
└────┬─────┘          │        │  calibrate → GATE                 │ │
     │ vectors        │        └──┬──────────────┬─────────────────┘ │
     ▼                │           │ prompt        ▲ pred+conf         │
┌──────────┐          │           ▼               │                  │
│ VECTOR   │          │     ┌───────────────┐  ┌──┴───────────────┐  │
│  DB      │          │     │  MODEL LAYER  │  │ CONFIDENCE /     │  │
│ (Qdrant/ │          │     │  (LiteLLM)    │  │ VERIFICATION     │  │
│ LanceDB) │          │     │ Claude│GPT│   │  │ [NET-NEW IP]     │  │
└────┬─────┘          │     │ vLLM│distilled│  │ blend+calib+judge│  │
     │ embeddings     │     └───────┬───────┘  └──────────────────┘  │
     ▼                │             │ routes to champion             │
┌──────────────────┐  │     ┌───────▼────────┐                       │
│ ACTIVE-LEARNING  │  │     │ MODEL REGISTRY │◀──────────┐           │
│ ROUTER [NET-NEW] │  │     │ champion/chall.│           │ promote   │
│ uncertainty ×    │  │     └────────────────┘           │           │
│ info × represent.│  │                          ┌───────┴────────┐  │
└───┬──────────┬───┘  │                          │ DISTILLATION   │  │
    │ auto     │ route│                          │ [NET-NEW IP]   │  │
    ▼          ▼      │                          │ LoRA fine-tune │  │
┌────────┐ ┌─────────────┐                       │ on burst GPU   │  │
│ AUTO-  │ │  HITL UI    │                        └───────▲────────┘  │
│ APPLY  │ │ (LS fork)   │                                │ corp pairs │
│ label+ │ │ accept/edit │                                │            │
│ conf   │ │ /reject     │                                │            │
└───┬────┘ └──────┬──────┘                                │            │
    │ events      │ events (edit = gold)                  │            │
    └──────┬──────┘                                       │            │
           ▼                                               │            │
   ┌───────────────────┐    append-only event log         │            │
   │  FLYWHEEL DATA    │──────────────────────────────────┘            │
   │  LAKE             │────────────────────────────▶ Eval/Gold ───────┘
   │  every accept/    │    training corpus + analytics
   │  edit/reject      │
   └─────────┬─────────┘
             │ finished labels
             ▼
   ┌───────────────────┐
   │  EXPORT / API     │  HF datasets · JSONL · webhook · SDK
   └───────────────────┘
```

The loop closes: corrections from Auto-apply + HITL → flywheel lake → distillation → registry → orchestrator picks the new champion → next batch is labeled more cheaply. The accuracy mechanics inside the orchestrator/confidence box are doc [04](04-accuracy-and-trust-engine.md); the lake→model loop is doc [05](05-data-flywheel-and-model-strategy.md).

---

## 3. Component responsibilities

One subsection per service. Schemas and API contracts live in doc [06](06-data-model-and-api-contracts.md); this is the *what and why*.

### 3.1 Ingest
- **Responsibility:** turn a raw source into a versioned, embedded, deduplicated dataset ready to label.
- **In:** S3 prefix, HF dataset id, DB connection, or CSV/JSONL upload. **Out:** rows in the dataset store (blobs in object store, metadata in Postgres), one embedding per item in the vector DB, a pinned dataset version.
- **Key tech:** FastAPI workers; an embedding model via LiteLLM; chunking for long text; near-duplicate detection (cosine threshold) and clustering (for the router); **lakeFS or DVC** to make the dataset version immutable and reproducible.

### 3.2 Rubric service `[NET-NEW IP]`
- **Responsibility:** own the structured *definition of the labeling task* — label set, definitions, positive/negative examples, hard constraints, edge cases — and compile it into two artifacts.
- **In:** the rubric authored in the UI (or imported). **Out:** **(a)** a model prompt (instructions + few-shot scaffold) and **(b)** a deterministic validator (e.g. "label ∈ enum", "span within bounds", "JSON matches schema"). Every rubric is versioned; a label is always stamped with the rubric version that produced it.
- **Key tech:** Postgres-backed versioning; a prompt-template compiler; a rule/JSON-Schema-based validator generator. Why net-new: the rubric→{prompt, validator} compiler is the contract that makes auto-labeling auditable.

### 3.3 Labeling orchestrator
- **Responsibility:** the conductor. For each item: assemble the prompt (rubric + RAG few-shot from gold), call the model(s), run self-consistency and ensemble, run the verifier and deterministic checks, attach calibrated confidence, and **gate** (auto-apply vs route). Owns batching, rate limits, cost caps, retries, idempotency.
- **In:** dataset version + rubric version + active model policy. **Out:** per-item `prediction + confidence + verifier verdict + cost`, emitted as flywheel events.
- **Key tech:** **Temporal** durable workflows (§7); calls the Model Layer and the Confidence module as libraries/services. Deliberately *not* net-new IP itself — it is orchestration glue wiring the IP together.

### 3.4 Confidence / Verification module `[NET-NEW IP]`
- **Responsibility:** turn raw model outputs into a *trustworthy calibrated probability* and a verification verdict. This is the trust engine.
- **In:** model logprobs, verbalized confidence, self-consistency samples, ensemble votes, the deterministic validator result. **Out:** a single calibrated confidence in [0,1] plus a pass/fail verdict and reason.
- **Key tech:** layers 1–4 of doc [04](04-accuracy-and-trust-engine.md) — confidence blend, calibration (temperature scaling / isotonic regression) fit per-taxonomy on the gold set, LLM-as-judge from a *different model family*, weak-supervision label model for cold-start. Why net-new: calibrated coverage@precision is the product promise.

### 3.5 Active-learning router `[NET-NEW IP]`
- **Responsibility:** decide, per item, **auto-apply or send to a human** — and order the human queue so the most valuable items come first.
- **In:** calibrated confidence + embedding (for cluster representativeness) + current gold coverage. **Out:** a routing decision and a queue priority = `uncertainty × informativeness × representativeness`.
- **Key tech:** the calibration gate (confidence ≥ threshold-for-target-precision → auto-apply); embedding-cluster scoring against the vector DB; diversity sampling so the human isn't shown 500 near-identical items. Why net-new: this is what makes one human cover a 1M-item dataset.

### 3.6 HITL UI (Label Studio fork)
- **Responsibility:** the Cursor-grade human loop. Show the pre-filled label + rationale + confidence; let the human accept, correct, or reject in one keystroke.
- **In:** routed items + predictions from the orchestrator (via backend API). **Out:** human decisions (accept/edit/reject) as flywheel events; **edits become gold.**
- **Key tech:** forked **Label Studio** (React). Keyboard-first: Tab/Enter = accept, type = correct, J/K = navigate, bulk-accept on a filtered above-threshold view, diff view, "explain" toggle. Full interaction spec in doc [07](07-hitl-ux-spec.md). Critically, it holds **no proprietary logic** — it renders what the backend sends and posts decisions back.

### 3.7 Eval / Gold harness
- **Responsibility:** the source of truth for quality. Manage the gold set, compute coverage@precision and calibration curves, run audit sampling and label-error detection, and emit the Quality Report.
- **In:** the gold set + the flywheel event log. **Out:** coverage@precision per threshold, reliability diagrams, a ~2% audit sample, inter-annotator agreement, flagged label errors, the per-run Quality Report.
- **Key tech:** stratified bootstrap for confidence intervals; **Cleanlab** for label-error detection (buy/OSS, not built — see §11f); reads the lake, writes reports to Postgres/object store.

### 3.8 Training / Distillation pipeline `[NET-NEW IP]`
- **Responsibility:** convert accumulated corrections into a private, cheap, fast per-customer labeler.
- **In:** correction pairs from the lake (item → human-confirmed label). **Out:** a fine-tuned 4–8B adapter, an eval-vs-gold scorecard, and — if it beats the champion — a promoted registry entry.
- **Key tech:** HF Transformers + **PEFT** (LoRA/QLoRA); **burst GPU on Modal/RunPod** (or local/on-prem); champion/challenger evaluation against gold; writes to the model registry. Strategy detail in doc [05](05-data-flywheel-and-model-strategy.md).

### 3.9 Flywheel data lake
- **Responsibility:** the append-only system of record for every labeling event — the substrate for both training and analytics.
- **In:** events from auto-apply, HITL, orchestrator, eval. **Out:** the training corpus (for distillation) and the analytics feed (for the harness and dashboards).
- **Key tech:** append-only event log (object store, partitioned by tenant/dataset/time; Postgres index over it). Append-only because labels evolve and audits require the full history.

### 3.10 Export / integrations
- **Responsibility:** get finished labels out, cleanly.
- **In:** a finished dataset version. **Out:** HF datasets push, JSONL/CSV download, webhook on completion, REST API, and a Python SDK.
- **Key tech:** FastAPI + the SDK; export reads the dataset store at a pinned version so exports are reproducible.

---

## 4. End-to-end data flow

### A single item

```
1. Ingest writes item + embedding (dataset vN).
2. Orchestrator dequeues it → Rubric service compiles {prompt, validator} for rubric vM.
3. RAG: nearest gold examples pulled from the vector DB → injected as few-shot.
4. Model Layer (LiteLLM) calls the champion model; self-consistency = N samples;
   ensemble = a second family if policy says so.
5. Confidence module: blend logprobs + verbalized + self-consistency agreement +
   ensemble disagreement → calibrate (per-taxonomy curve) → calibrated p.
6. Verification: LLM-judge (different family) + deterministic validator → verdict.
7. Router: p ≥ threshold(target precision) AND verdict=pass?
        ├─ YES → AUTO-APPLY: label written, event logged.
        └─ NO  → ROUTE: enqueued for human at priority = unc × info × repr.
8. (if routed) HITL UI shows prefilled label + conf + rationale.
        Human Tab/Enter (accept) | types (correct→gold) | rejects.
9. Decision → flywheel lake as an event. Done.
```

### A 100k-item dataset (the job/batch flow)

1. **Kickoff.** User points Tessera at the source and selects a rubric. Ingest fans out across workers; 100k items embedded and versioned (minutes, parallel).
2. **Calibration-first.** The harness ensures a gold set exists (or asks the human to label a stratified ~200–500-item seed). Calibration curves are fit so thresholds are *meaningful before the batch runs*.
3. **Parent workflow.** The orchestrator starts one Temporal parent workflow that fans out child activities in batches (e.g. 500/batch), respecting per-provider rate limits and the **cost ceiling** — the run pauses, not crashes, when it nears the cap.
4. **Streaming gate.** As items resolve, the router auto-applies the confident majority and streams the uncertain remainder into the human queue. The human starts working the queue *while the batch is still running* — no wait for 100% completion.
5. **Bulk human pass.** In the UI the human sorts by cluster, bulk-accepts above-threshold groups, and hand-corrects the genuinely hard tail.
6. **Quality Report.** The harness produces coverage@precision, calibration plots, and a ~2% audit. Export emits HF/JSONL when the user accepts the report.
7. **Flywheel.** All events are in the lake; if correction volume warrants, distillation kicks off and the next dataset runs cheaper.

A 1M-item dataset is the same flow with more child batches and longer wall-clock — which is precisely why durability (§7) is non-negotiable.

---

## 5. The model layer

A single seam between Tessera and *every* model, so the orchestrator never hard-codes a vendor.

- **Unified API — LiteLLM.** One call signature for Claude, GPT, open models (via vLLM/llama.cpp), and distilled labelers. Swapping or adding a provider is config, not code (see ADR §11d).
- **Three model classes:**
  - **BYO frontier** (Claude / GPT) — highest quality, highest cost; the default in Phase A and the *teacher* for distillation.
  - **Local open** (vLLM for throughput on a GPU, llama.cpp for CPU/edge and on-prem) — cheaper, private, used in ensembles and on air-gapped deploys.
  - **Distilled per-customer** (4–8B LoRA) — 10–100× cheaper/faster than frontier; becomes the champion once it beats it on gold.
- **Model registry + champion/challenger.** Every model version is registered with its gold scorecard. The current **champion** serves production traffic; a **challenger** (e.g. a freshly distilled adapter) is shadow-evaluated against gold and promoted only on a win. The orchestrator reads "who is champion for this taxonomy" from the registry — promotion is a registry write, not a redeploy.
- **Cost caps & batching.** Calls are batched per provider; responses cached on `(prompt-hash, model, params)` so re-runs and self-consistency don't re-pay. Each run carries a hard cost ceiling; the orchestrator tracks spend and pauses at the limit. Self-consistency sample count and whether to fire the ensemble are policy knobs the router can dial *down* for easy clusters to save money.

---

## 6. Storage

Four stores, each chosen for one job. (Schemas in doc [06](06-data-model-and-api-contracts.md).)

| Store | Tech | What lives there | Why |
|---|---|---|---|
| **Metadata DB** | Postgres | datasets, items (refs), rubrics + versions, labels, jobs, users/tenants, model registry, quality reports | Relational, transactional, queryable; the system of record for *structure*. |
| **Blob store** | S3 / MinIO | raw item payloads (text, files), large artifacts, exports, event-log files | Cheap, infinite, durable; MinIO gives the *same API* on-prem. |
| **Vector DB** | Qdrant / LanceDB | one embedding per item; gold-example index | Fast ANN for RAG few-shot, dedup, and the router's cluster/representativeness scoring. LanceDB embeds for small/on-prem; Qdrant scales out. |
| **Dataset versioning** | lakeFS / DVC | immutable dataset snapshots (`vN`) | A label must trace to the *exact* data + rubric it was made from; reproducible exports and re-runs. |
| **Flywheel event log** | append-only (object store + Postgres index) | every accept / edit / reject / auto-apply event | Append-only history powers distillation, audit, and analytics; never mutated. |

Split rationale (one DB can't do all three well) is ADR §11e.

---

## 7. Orchestration & jobs

Labeling runs are **long, expensive, and partially human** — a 1M-item run can span hours and hundreds of dollars, with humans clearing the queue in parallel. A naive job queue handles the happy path and falls apart on the messy one (a worker dies at item 700k; a provider rate-limits for 10 minutes; the cost cap is hit; an item is retried and double-charged).

**Temporal durable workflows** solve this:

- **Durable state / resume-not-restart.** Workflow progress is persisted continuously. A crashed worker resumes from the last completed activity — you never re-pay for 700k labeled items.
- **Idempotency.** Each item's labeling activity is keyed; a retried activity returns the prior result instead of re-calling the model. (See ADR §11c.)
- **Retries with backoff.** Transient model/provider failures retry automatically with exponential backoff; permanent failures (validation can never pass) are dead-lettered for human review.
- **Rate limits & cost ceilings.** The workflow throttles to each provider's limits and tracks cumulative spend, pausing (a durable signal, resumable later) when the ceiling is reached.
- **Long-running + human-in-the-loop.** A workflow can legitimately *wait days* for the human queue to drain — Temporal models that natively, where a Redis queue would time out or leak state.

**Start-simple escape hatch:** Phase 0 can ship on **Redis / RQ** for a single-node MVP; the orchestrator's activity interface is written so Temporal slots in without touching the confidence/router code. Migrate when run sizes or reliability demands cross the threshold.

---

## 8. Deployment topology

```
┌─────────────────────────────┐        ┌──────────────────────────────┐
│   CLOUD SaaS (multi-tenant)  │       │  SINGLE-TENANT VPC / ON-PREM   │
│  shared FastAPI services     │        │  same Docker/K8s images        │
│  Postgres (row-level tenant) │        │  customer's Postgres + MinIO   │
│  MinIO/S3, Qdrant            │        │  local vLLM/llama.cpp models   │
│  Temporal cluster            │        │  NO frontier egress if air-gap │
│  pooled BYO API keys         │        │  customer's own keys/models    │
└──────────────┬───────────────┘       └───────────────┬──────────────┘
               │ burst                                   │ burst (optional)
               ▼                                         ▼
       ┌──────────────────┐                     ┌──────────────────┐
       │ BURST GPU        │                      │ LOCAL / RENTED GPU│
       │ Modal / RunPod   │                      │ for distillation  │
       │ (distillation,   │                      │ stays in tenant   │
       │  local inference)│                      │ boundary          │
       └──────────────────┘                     └──────────────────┘
```

- **Cloud SaaS (default):** one set of stateless services, multi-tenant via row-level isolation; fastest to operate and the home of the cross-customer learnings (opt-in only — §10).
- **Single-tenant VPC / on-prem (sensitive data):** the *same container images* deployed inside the customer's boundary. Postgres/MinIO/Qdrant run locally; models run on local vLLM/llama.cpp; for air-gapped customers there is **zero frontier-LLM egress** and they rely on local + distilled models. This is why no architectural choice may assume a shared backplane.
- **Burst GPU:** distillation (and any heavy local inference) runs on ephemeral GPUs — **Modal/RunPod** for cloud, customer-owned/rented for on-prem. GPUs are rented per-job, not kept hot, because distillation is bursty (ADR §11 rationale carries into doc [05](05-data-flywheel-and-model-strategy.md)).

---

## 9. Scale & reliability

**Rough load for a 1M-item text dataset** *(all figures estimate)*:

| Quantity | Estimate | Note |
|---|---|---|
| Items | 1,000,000 | text-first beachhead |
| Auto-applied (coverage) | ~70–90% | depends on task difficulty + target precision |
| Routed to human | ~100k–300k | the hard/uncertain tail |
| Model calls | ~1M–4M | × self-consistency N and any ensemble |
| Frontier cost (Phase A, frontier-only) | ~$1k–10k (estimate) | the number distillation is built to collapse |
| Cost after distillation (Phase B) | ~10–100× lower per item | (estimate) |
| Wall-clock (well-parallelized) | hours, not days | bounded by provider rate limits + human queue |

**Scaling strategy:**
- **Stateless horizontal scale.** Ingest, orchestrator activity workers, confidence, and API are stateless — add replicas behind a queue/load balancer. All durable state is in Postgres/object store/Temporal, so workers are cattle.
- **Stateful tiers scale independently.** Postgres → read replicas + partitioning by tenant; Qdrant → sharding/replication; object store is effectively infinite.
- **Failure / retry.** Temporal handles transient failures (§7); poison items dead-letter to human review; provider outages degrade gracefully via LiteLLM fallback to an alternate model.
- **Monitoring / alerting.** Per-run dashboards (throughput, cost burn vs ceiling, coverage@precision drift, queue depth, model latency/error rate); alerts on cost-cap approach, calibration drift, and worker backlog.
- **What I'd revisit as we grow (estimate):** move the event log from object-store files to a real streaming bus (Kafka) once event volume is high; introduce a dedicated feature/embedding store; shard Temporal; add a caching read-tier in front of Postgres for hot rubric/registry reads. None of these change the component boundaries — they're swaps behind existing seams.

---

## 10. Security & privacy architecture

Privacy is a *sales requirement* for the beachhead (people's proprietary fine-tuning data) and the precondition for the flywheel being trusted at all.

- **Tenant isolation.** Row-level isolation in Postgres (every row tenant-scoped); object-store and event-log paths partitioned per tenant; per-tenant encryption keys. On-prem is the strongest form of isolation — a separate deployment entirely.
- **Per-customer model isolation.** A distilled model is trained **only** on that customer's corrections and is served **only** to that customer. One tenant's data never trains another's labeler. The model registry is tenant-scoped.
- **Opt-in foundation-model sharing.** The Phase C cross-task foundation labeler (doc [05](05-data-flywheel-and-model-strategy.md)) trains across customers **only with explicit opt-in**, and only on data the customer marks shareable. Default is fully isolated; sharing is a deliberate, revocable choice — never a silent default.
- **Secrets & key handling.** BYO provider API keys stored in a secrets manager (Vault / cloud KMS), never in Postgres or logs; encrypted at rest, injected at call time. In multi-tenant, pooled keys are rate-limited and attributed per tenant for cost; in single-tenant, the customer supplies their own.
- **Data in transit & at rest** encrypted throughout; audit log (the same append-only event lake) records who saw/changed what for compliance.

---

## 11. Key architecture decisions & trade-offs

ADR-style: Context / Decision / Why / Alternatives / Consequences.

### (a) Fork Label Studio vs build a labeling editor
- **Context:** we need a polished, extensible labeling editor on day one.
- **Decision:** fork **Label Studio** (OSS, React, multi-modal).
- **Why:** building a labeling editor from scratch burns months on table-stakes UI that is *not* the moat. The moat is the backend loop (§1).
- **Alternatives:** build from scratch (slow); embed a closed SaaS editor (no control / can't fork).
- **Consequences:** inherit Label Studio's architecture and upgrade churn; must keep our keyboard-first/diff/explain customizations maintainable against upstream. Accepted — the UI is explicitly **swappable**.

### (b) Keep IP in backend services vs embed in the fork
- **Context:** orchestrator, confidence, router, distillation are the proprietary value.
- **Decision:** keep all four as **separate backend services**; the fork only renders and posts decisions.
- **Why:** if the IP lived inside an OSS fork it would be a copyable plugin, and the UI couldn't be swapped without losing the engine.
- **Alternatives:** plugins inside Label Studio (couples moat to a forkable frontend).
- **Consequences:** an extra network boundary (UI ↔ backend API) and contract discipline; in exchange the moat is protected and the frontend is replaceable. The defining design rule of the project.

### (c) Temporal vs a simple queue
- **Context:** multi-hour, costly, human-interleaved runs that must survive crashes.
- **Decision:** **Temporal** for durable workflows (Redis/RQ as a Phase-0 stand-in behind the same interface).
- **Why:** resume-not-restart, idempotency, retries, cost-ceiling pause, and day-long human waits are first-class in Temporal and bolt-on hacks on a bare queue (§7).
- **Alternatives:** Redis/RQ or Celery (simpler, but you rebuild durability/idempotency yourself); cloud step-functions (more lock-in).
- **Consequences:** Temporal is operational weight (a cluster to run). Mitigated by the swap-in interface so we adopt it when scale demands, not on day one.

### (d) LiteLLM model-abstraction layer
- **Context:** we call many model providers and will add/swap them constantly.
- **Decision:** route **all** model calls through **LiteLLM**.
- **Why:** one signature for Claude/GPT/local/distilled; provider changes, fallbacks, and cost tracking become config. Decouples the orchestrator from any single vendor — central to the cost-down strategy.
- **Alternatives:** per-provider SDKs (N integrations, N code paths); a homegrown shim (reinventing LiteLLM).
- **Consequences:** a dependency in the hot path and occasional lag behind a provider's newest features; worth it for the abstraction. (Anthropic/OpenAI calls go through LiteLLM, not raw SDKs.)

### (e) Postgres + object store + vector DB split
- **Context:** we store relational metadata, large blobs, and high-dim vectors.
- **Decision:** **three** purpose-built stores (Postgres, S3/MinIO, Qdrant/LanceDB) rather than one.
- **Why:** each workload (transactions, cheap bulk blobs, ANN search) has a different ideal engine; forcing one tool to do all three degrades all three.
- **Alternatives:** Postgres-only with `pgvector` + bytea blobs (simpler ops, but weak at large blobs and large-scale ANN); a single "do-everything" platform.
- **Consequences:** three systems to operate and keep consistent (the metadata DB holds the canonical refs). Accepted for performance and the clean on-prem story (MinIO + LanceDB shrink neatly for single-tenant).

### (f) Build confidence/calibration vs buy error-detection
- **Context:** trust comes from calibrated confidence *and* from catching label errors.
- **Decision:** **build** the confidence/calibration engine (§3.4); **buy/OSS Cleanlab** for label-error detection.
- **Why:** calibrated coverage@precision *is* the product — it must be ours, tuned per taxonomy. Confident-learning error detection is a solved, well-packaged problem; rebuilding it is waste.
- **Alternatives:** buy a confidence/eval SaaS (cedes the moat); build our own error detector (reinvents Cleanlab).
- **Consequences:** we own and maintain the calibration code (the right kind of cost) and take a Cleanlab dependency in the eval harness (the right kind of leverage). Clean build-vs-buy line: **build the moat, buy the commodity.**

---

*Next: the trust mechanics inside the engine — doc [04](04-accuracy-and-trust-engine.md); the learning loop — doc [05](05-data-flywheel-and-model-strategy.md); contracts — doc [06](06-data-model-and-api-contracts.md); the build sequence — doc [08](08-implementation-and-development-plan.md).*
