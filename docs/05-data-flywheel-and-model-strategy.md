# Tessera — Data Flywheel & Model Strategy

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

How the human-correction stream becomes the moat: the path from bring-your-own frontier LLM to private specialized labelers and, finally, a cross-task labeling foundation model.

Last updated: June 2026

> **Status (June 2026):** Phase A (bring-your-own-LLM) and the flywheel **event capture** are built in the MVP; per-customer distillation (Phase B) and the cross-task foundation labeler (Phase C) are designed, not yet built. See the [suite status](README.md).

---

## 1. The flywheel — and the Cursor/Composer parallel

Every other doc in this suite serves the mechanism described here. The [accuracy & trust engine](04-accuracy-and-trust-engine.md) makes the auto-applied slice *trustworthy*; the [HITL UX](07-hitl-ux-spec.md) makes the human slice *cheap to clear*. This doc is about what happens to the **byproduct** of those two things — the stream of human decisions on the hard cases — and why that byproduct is the only durable asset in the business.

The strategic claim from the [product vision](01-product-vision-and-prd.md) and [business case](02-business-case-and-strategy.md): "an LLM labels a dataset" is commoditizing. Refuel AI shipped exactly that capability (an autolabel library claiming 25–100x speedups), raised only a $5.2M seed, and was acquired by Together AI in May 2025 ([VentureBeat](https://venturebeat.com/ai/refuel-ai-nabs-5m-to-create-training-ready-datasets-with-llms)). The capability is table stakes. The **flywheel** — which Refuel never built — is the moat.

The parallel is Cursor's, exactly. Cursor did not win because an AI can autocomplete code; that commoditized. It won by wrapping a frontier model in a tight loop with great UX, then training **Composer** — its own models — on the proprietary stream of accept/reject/edit interactions that the loop generated. The interaction data was the asset; the model trained on it was the moat. Tessera ports the same machine to labeling:

```
        ┌──────────────────────────────────────────────────────────┐
        │                                                            │
        ▼                                                            │
  ┌───────────┐                                                      │
  │  USAGE    │   customers run labeling projects                    │
  │ (labeling │                                                      │
  │   runs)   │                                                      │
  └─────┬─────┘                                                      │
        │ hard cases routed to humans                                │
        ▼                                                            │
  ┌───────────┐                                                      │
  │CORRECTIONS│   accept / EDIT / reject on each routed item         │
  │ (the      │   ── EDITS are the gold signal ──                    │
  │  event    │                                                      │
  │   log)    │                                                      │
  └─────┬─────┘                                                      │
        │ supervised pairs: (input + rubric → corrected label)       │
        ▼                                                            │
  ┌───────────┐                                                      │
  │  BETTER   │   distill / fine-tune a per-customer specialist;     │
  │  MODELS   │   warm-start from the foundation labeler             │
  └─────┬─────┘                                                      │
        │ promoted only if it beats incumbent on the gold set        │
        ▼                                                            │
  ┌──────────────────────┐                                          │
  │ MORE COVERAGE@PRECISION│  the gate auto-applies a larger share    │
  │  → LESS HUMAN EFFORT   │  at the same target precision            │
  └─────┬──────────────────┘                                         │
        │ cheaper, faster, more accurate → customer runs more ───────┘
        ▼
   (loop tightens every cycle)
```

The loop is *visible*: the metric that turns it — **coverage@precision**, the share of the dataset auto-applied at or above the target precision (defined in [doc 04](04-accuracy-and-trust-engine.md)) — is the same number we show customers ("you touch less every run") and investors ("the moat is compounding").

## 2. What's captured, and why edits are gold

For every item the system touches we append one immutable event to a log: the model's label, its rationale (free-text justification), its raw and calibrated confidence, ensemble votes, weak-supervision votes, whether the item was routed to a human and the route reason, the human's action (**accept / edit / reject**), the final label, the taxonomy version and rubric snapshot in force, plus latency, cost, timestamp, and a hashed annotator id. The full schema lives in the [data model](06-data-model-and-api-contracts.md) — this doc only cares about what the events *mean* for training.

Every accept, edit, and reject is a supervised training pair (an `(input → correct output)` example a model can learn from). But **edits are the gold signal**, and they are the precise analog of Cursor's edit-acceptance data. An edit encodes the highest-value statement in the entire system: *"the frontier model proposed X here, and the right answer on OUR taxonomy is Y."* It is a labeled error, located exactly at a decision boundary the generalist model gets wrong on this customer's distribution. Accepts confirm the model is right; rejects say it is wrong without saying what's right; edits say *both where it's wrong and what correct looks like* — which is what a specialist most needs to learn. We weight them accordingly when we build training sets (§4).

## 3. Phase A — bring-your-own frontier LLM + RAG-few-shot from gold

**Phase A is renting capability while harvesting data.** The labeler is a frontier LLM the customer brings (Claude, GPT, or a local model via one API — see [architecture](03-system-architecture.md)), prompted zero- or few-shot with the rubric in context. There is **no fine-tuning and no moat yet** — anyone can call the same API. The point of Phase A is to (a) deliver value on day one with zero training, and (b) start filling the event log.

The one piece of intelligence we add in Phase A — before any training — is **RAG-retrieved few-shot examples from the gold set**. RAG (retrieval-augmented generation) means: instead of a fixed prompt, we retrieve the most relevant prior examples at inference time and paste them into the model's context. Concretely, for each new item we embed it (turn it into a vector), find the *k* nearest already-labeled gold examples (typically the trickiest ones, near past decision boundaries), and inject them into the prompt as worked examples on this exact taxonomy.

The effect is a cheap, training-free flywheel that runs *immediately*: as the gold set grows from human corrections, the retrieved exemplars get better and more on-distribution, so the frontier model's labels improve run-over-run **with no fine-tuning at all**. This buys accuracy from the first hundred corrections — long before we have enough data to justify training a model — and it bridges directly into Phase B, because the same accumulated gold set is the training corpus.

**Near-duplicate propagation (shipped v0.10.0)** is the other zero-training lever, and it prices redundancy instead of difficulty: real org corpora (tickets, form responses, alert streams) repeat themselves, so items are grouped at a high cosine threshold (`TESSERA_PROPAGATE=0.95`), only group representatives — plus anything holding gold — are labeled by the LLM, and members mirror their representative's label and gate state with full provenance. Members stay in the audit universe, and in review a group resolves as one: accepting a representative bulk-accepts its members, an audit reject un-ships the whole group, and a member a human edits is emancipated from the mirror. The LLM-call savings equal the corpus's duplication factor (benchmark sets arrive pre-deduped, ~1–2%; raw operational data is typically far higher), and consistency *within* a group is enforced by construction rather than hoped for.

## 4. Phase B — per-customer specialized labelers (the first real moat)

This is **Phase 3 (Flywheel)** of the [roadmap](08-implementation-and-development-plan.md): the first defensible asset. We distill the frontier model's behavior — corrected by humans on the customer's own taxonomy — into a small private model that the customer owns.

*Its smallest form already ships:* the v0.10.0 **consensus gate** trains the stdlib Tier-0 specialist on half the trusted labels each run and seats it in the ensemble, where its agreement with the LLM is the coverage lever (measured: 64%→93.5% coverage at a kept 90% promise; details in [docs/04](04-accuracy-and-trust-engine.md) §3). Phase B proper replaces that logistic head with the LoRA-tuned model below — same seat, same leak-safe calibration split, more capacity.

### Trigger conditions

We train a per-customer specialist once a project has accumulated **enough corrected pairs to beat the BYO baseline on the gold set** — often a few thousand items, weighted heavily toward edits (estimate; the real trigger is empirical, see below, not a fixed count). Concretely, retraining is triggered when *either*:

- a **volume** threshold of new corrections since the last train is crossed (≈2–5k new pairs, estimate), **or**
- a **drift** signal fires — gold-set accuracy of the incumbent model slips, or the taxonomy version changes (§9).

A challenger that can't beat the incumbent is simply not promoted (see the gate below), so an *early* trigger is cheap to attempt and safe to fail.

### Model size, and LoRA/QLoRA

The specialist is a **small open model, 4–8B parameters** (Llama/Qwen/Mistral-class) — small enough to serve cheaply and privately, large enough to absorb a rubric. We fine-tune it with **LoRA** (Low-Rank Adaptation: instead of updating all the model's weights, we train a small set of low-rank "adapter" matrices bolted onto the frozen base — orders of magnitude fewer trainable parameters, minutes-to-hours of GPU, and a tiny artifact to store). **QLoRA** is LoRA on top of a 4-bit *quantized* (compressed) base, which lets a 7-8B fine-tune run on a single consumer/burst GPU. This is what makes per-customer models economically sane: each customer can have *their own adapter* without us hosting a full model per tenant.

### Constructing training pairs from the event log

We build the supervised set straight from the log (schema in [doc 06](06-data-model-and-api-contracts.md)). The fine-tuning target deliberately includes the **rationale**, not just the label, because teaching the small model to *reason on the rubric* generalizes better than teaching it to pattern-match labels:

```
INPUT  (prompt):   <rubric snapshot> + <RAG few-shot exemplars> + <item>
OUTPUT (target):   <final corrected label> + <rationale>

source of OUTPUT, by event type:
  edit   → human-corrected label  + (model rationale, repaired) ── highest weight
  reject → excluded as a positive; used as a hard negative / contrast pair
  accept → model label (confirmed correct) ──────────────────── lower weight
```

Crucially, we train on **human-corrected finals and edits — never raw auto-applied labels** (the auto-applied slice is the model's own output; training on it would teach the model to imitate itself and bake in its errors; see §9). We pin every pair to its taxonomy version + rubric snapshot so a taxonomy change doesn't silently poison the set (§9).

### Champion / challenger promotion gate

A freshly trained model is a **challenger**; the model currently in production is the **champion**. The challenger never auto-replaces the champion. It must **beat the incumbent on the held-out gold set at the customer's target precision** — i.e. deliver *higher coverage@precision* (or equal coverage at higher precision) — to be promoted. Both are scored by the same eval harness from [doc 04](04-accuracy-and-trust-engine.md) against the same gold set; the winner is written to the model registry and the orchestrator routes new items to it. This makes every retrain **strictly safe**: the metric can only go up or stay flat, never down, because a regression simply isn't shipped.

### Retrain cadence

On a **cadence** (e.g. weekly) *or* event-driven when enough new edits accumulate (the volume/drift triggers above). Each successful promotion pushes coverage@precision up, which means the human queue shrinks — the loop visibly tightens. That run-over-run human-effort reduction is the leading indicator we report (it is success metric #4 in [doc 01](01-product-vision-and-prd.md)).

### Why the specialist wins — four axes

| Axis | BYO frontier API (Phase A) | Per-customer specialist (Phase B) |
|---|---|---|
| **Cost** | Per-token API spend on every item | **10–100x cheaper** at million-item scale (estimate) — a 7B model served locally vs. a frontier API call per row |
| **Latency** | Network round-trip + queue per call | Local batch inference; far higher throughput |
| **Accuracy** | Generalist, off-distribution on *this* taxonomy | **Higher on-distribution** — a specialist beats a generalist on the task it was trained on |
| **Privacy** | Data leaves the building to a third party | Runs **private / on-prem**; data never leaves (§6, §7) |

The accuracy point is the non-obvious one and the heart of the moat: a 7B model that has *only ever seen this customer's taxonomy and its hardest corrected cases* will out-label a frontier generalist on that customer's distribution, because it has specialized exactly where the generalist is weakest — the edits.

### On-prem deployment

The specialist is small and self-contained (base + adapter), so it ships **inside the customer's environment** and is served locally via vLLM or llama.cpp (see [architecture](03-system-architecture.md), and §7 below). For the enterprise persona this is the killer feature: a guaranteed-precision labeler running entirely on-prem, with no data egress.

## 5. Phase C — the cross-task labeling foundation model (the Composer analog)

**Phase 4 (Composer)** of the roadmap, and the *durable* moat. Phase B gives every customer a model good at *their* task. Phase C trains one model good at the **meta-skill** of labeling itself.

The meta-skill, stated precisely: *given a rubric + a few examples + an item → produce a calibrated label + rationale.* Phase B fine-tunes on one `(item → label)` distribution at a time. Phase C trains across **thousands of de-identified tasks at once**, so the model learns the *general competence of labeling-from-a-specification* rather than any single taxonomy. The input always carries the rubric and exemplars, so the model learns to *condition on the spec* — which is exactly what makes it transfer to a task it has never seen.

**Training data:** the de-identified, opt-in correction streams across many customers and tasks (privacy in §6), framed uniformly as `(rubric + few-shot → label + rationale)`. The taxonomies differ wildly — sentiment, intent, NER, moderation, SFT-pair quality — and that diversity is the point: it forces generalization over the *skill*, not memorization of any one label set.

**Cold-start benefit:** a new customer's *first* project warm-starts from the foundation labeler instead of a generic frontier model. Because the foundation model is already expert at reading-a-rubric-and-labeling, it is **better than generic GPT/Claude at labeling-from-a-spec on day zero**, with near-zero cold start — high coverage@precision before a single correction exists. That directly attacks the worst weakness of the whole approach (the cold-start gold-set burden, §9).

**Network effect:** each opt-in loop, on any task, improves the foundation labeler → better cold-start for the next customer → more customers → more loops. This is the cross-task axis of the moat (axis 2 in §8) and the reason the business compounds across the customer base, not just within one account.

## 6. Privacy architecture — and why it's also a sales weapon

Privacy is non-negotiable *and* a competitive weapon, especially against a Scale AI that became non-neutral the moment Meta took a 49% stake. The contract with the customer is one sentence: **"your data trains only your model unless you opt in."** The architecture that backs it:

- **Per-customer isolation.** Each customer's event log, gold set, and specialist (base + LoRA adapter) live in their own tenant boundary. A per-customer model is trained **only** on that customer's data and is never served to anyone else. Models do not leak across tenants — this is enforced structurally, not by policy alone.
- **Opt-in only for the foundation model.** Phase C trains **exclusively** on data customers explicitly opt in to contribute. The default is *no contribution*. On-prem customers contribute **nothing** unless they opt in (and being on-prem, often can't even if they wanted to — their data never reaches us).
- **De-identification + synthetic distillation.** Contributed data is de-identified (PII stripped/hashed) before it touches any shared training. Where raw text is too sensitive even de-identified, we prefer **synthetic distillation**: we learn the *pattern* of corrections (the kinds of mistakes the generalist makes on a class of rubrics) and train on synthetic examples that reproduce the pattern without carrying the customer's literal content. The foundation model learns the *skill*, not the *strings*.
- **Contractual + technical guarantees, together.** The promise is both a contract term and an architectural property (tenant isolation, opt-in gating, de-identification pipeline). Neither alone is sufficient; together they let us make the pitch credibly to a security review.

The sales advantage is direct: an enterprise that *cannot* send its data to a third-party labeler (compliance, regulated data, IP) can run the entire loop — including model training — privately, and still gets a model that improves weekly. No incumbent that depends on shipping data out can match that.

## 7. Local / on-prem inference advantage

This is where the founder's local-LLM strength becomes a structural moat, not just a preference. The whole Phase B/C design assumes **small models served locally** (vLLM or llama.cpp; see [architecture](03-system-architecture.md)) rather than a permanent dependence on a frontier API:

- **Sensitive-data labeling on-prem.** The single largest blocker for the enterprise persona (regulated, IP-sensitive, or air-gapped data) is data egress. A 4–8B specialist that runs *inside their environment* removes the blocker entirely — labeling and the training loop both happen behind their firewall.
- **Unit economics at scale.** Million-item labeling runs on a per-token frontier API are expensive; the same runs on a quantized 7B model on burst or owned GPUs are 10–100x cheaper (estimate, §4). Local serving is what makes "auto-label the easy majority" *profitable*, not just possible.
- **No single-vendor dependency.** The product stays neutral and un-acquired (principle #5 in [doc 01](01-product-vision-and-prd.md)): we are not locked to one model family, and our margins don't evaporate if a frontier provider raises prices. Better frontier models *help* us (§8) without our economics depending on any one of them.

## 8. Moat math — three compounding axes

Value compounds on **three axes simultaneously**, and a raw-frontier-LLM entrant has *none* of them:

1. **More usage → more corrections → better per-customer model.** Within an account, every run feeds the specialist; coverage@precision climbs; human effort per item falls; the customer runs more. (Within-customer loop, §4.)
2. **More tasks → better foundation labeler → faster cold-start.** Across accounts, every opt-in loop improves the meta-skill model, shrinking the cold-start for the *next* customer. (Cross-customer network effect, §5.)
3. **Longer track record → trusted precision SLA → easier to auto-ship.** Over time, an audited history of hitting the precision target on real data earns the right to auto-apply more aggressively — trust itself compounds, and a trusted SLA is hard for a newcomer to assert. (Reputation, see [doc 04](04-accuracy-and-trust-engine.md).)

**Why better base models help, not hurt.** A naive worry: "won't GPT-6 just label everything and kill you?" No — for the same reason "GPT can autocomplete" didn't kill Cursor. A stronger frontier model makes our **Phase A baseline better**, raises the quality of the corrections we harvest, and gives us a better teacher to distill from. Every axis above *gets better* as base models improve. The flywheel is built *on top of* frontier capability, not *in competition with* it.

**The explicit contrast — the Refuel lesson.** An entrant who shows up with a raw frontier LLM and an autolabel library has the day-one *capability* and **zero** of the three axes: no correction stream, no per-customer specialists, no foundation labeler, no track record. That is precisely the position Refuel was in — real capability, $5.2M seed, acqui-hired. The capability is not the asset; the compounding loop is. (See [business case](02-business-case-and-strategy.md) for the full competitive read.)

## 9. Flywheel-specific risks & mitigations

These are tracked in full in [risks, open questions & glossary](09-risks-open-questions-and-glossary.md); the flywheel-critical ones:

| Risk | Why it bites | Mitigation |
|---|---|---|
| **Cold start** | A brand-new customer has no corrections, no specialist, no gold — the loop has nothing to spin. | RAG-few-shot from gold works from the *first* corrections (§3); the **Phase C foundation labeler warm-starts day-zero projects** (§5); seed gold sets are cheap to collect via the HITL loop. |
| **Correlated / model errors poisoning training** | If we trained on the auto-applied slice, the model would learn from its *own* (possibly systematically wrong) labels and entrench correlated errors — a feedback loop of confident mistakes. | **Train only on human-corrected finals + edits, never on raw auto-applied labels** (§4). Humans are the only ground-truth injection; the audit sample (≈2%, [doc 04](04-accuracy-and-trust-engine.md)) keeps catching errors the gate let through. |
| **Drift** | The data distribution or the labeling task shifts over time; a specialist trained on old data silently degrades. | Drift triggers retraining (§4); the champion/challenger gate re-scores against a *current* gold set every cycle, so a stale champion is replaced as soon as a fresher challenger beats it. |
| **Privacy leakage** | A per-customer model or the foundation model memorizes and re-emits another customer's data. | Tenant isolation + opt-in-only foundation training + de-identification + synthetic distillation (§6); on-prem customers never transmit data at all (§7). |
| **Over-fitting to a stale taxonomy** | When a customer revises their rubric, a model trained on the old taxonomy keeps "correctly" reproducing the *old* answers. | Every training pair is pinned to a **taxonomy version + rubric snapshot** ([doc 06](06-data-model-and-api-contracts.md)); a taxonomy change fires a drift retrain and re-weights toward post-change corrections, so the new rubric wins. |

---

*Mechanism cross-references: confidence gate and coverage@precision — [accuracy & trust engine](04-accuracy-and-trust-engine.md); event-log schema — [data model & API contracts](06-data-model-and-api-contracts.md); training/distillation pipeline, model registry, local serving — [system architecture](03-system-architecture.md); phased build order (Phase 3 Flywheel, Phase 4 Composer) — [implementation & development plan](08-implementation-and-development-plan.md).*
