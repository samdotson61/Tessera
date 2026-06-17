# Tessera — Risks, Open Questions & Glossary

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The candid risk register, the unknowns we must resolve with design partners and research, and a shared vocabulary so a non-ML reader can follow the rest of the suite.

Last updated: June 2026

---

## 1. Risk register

Likelihood and impact are early-stage estimates, scored Low / Medium / High. "Early-warning signal" is the metric or event we watch to know a risk is materializing *before* it hurts. Mitigations cross-link to the docs that own them — the [business case](02-business-case-and-strategy.md), the [accuracy & trust engine](04-accuracy-and-trust-engine.md), the [data flywheel](05-data-flywheel-and-model-strategy.md), and the [implementation plan](08-implementation-and-development-plan.md).

| # | Risk | Category | Likelihood (est.) | Impact (est.) | Mitigation | Early-warning signal |
|---|---|---|---|---|---|---|
| R1 | **Incumbent ships the loop.** Label Studio / HumanSignal (or Snorkel / Scale) bolts a credible pre-label → accept/correct loop onto its existing distribution and contests our wedge from incumbency. | Market | High | High | Fork Label Studio so we inherit the plumbing and race only on the loop, UX, and flywheel; reach **Phase B per-customer specialists before the wedge commoditizes** so switching cost becomes "abandon a model that improves weekly," not "export annotations" ([02](02-business-case-and-strategy.md) §3, [05](05-data-flywheel-and-model-strategy.md) §4). | A competitor ships AI pre-labeling or an "accept" affordance; their changelog/release notes mention confidence or auto-apply. |
| R2 | **Commoditization of the base layer.** Frontier LLMs get good and cheap enough at zero-shot labeling that the per-customer-model advantage shrinks toward zero (the recurring Refuel risk). | Market | High | Medium | Value is trust + UX + flywheel, not the raw labeler; better base models *help* the loop (better Phase A baseline, better teacher to distill) rather than replacing it ([05](05-data-flywheel-and-model-strategy.md) §8). | Specialist's coverage@precision lead over the raw frontier model narrows release-over-release on the same gold set. |
| R3 | **Accuracy ceiling on subjective tasks.** For ambiguous or opinion-laden taxonomies, no labeler — model or human — can hit a high precision SLA, because the "ground truth" itself is contested. | Technical | High | Medium | Cap the promise at the **human-agreement ceiling**: measure inter-annotator agreement first, only sell an SLA the rubric can support, push effort into rubric clarity; route the irreducibly subjective slice to humans ([04](04-accuracy-and-trust-engine.md)). | Inter-annotator agreement on a candidate task is low (e.g. < ~0.7); gold-set precision plateaus below the buyer's target regardless of model. |
| R4 | **Rubric / spec quality.** The customer's labeling guidelines are vague or self-contradictory, so labels are bad no matter how good the model is — garbage spec in, garbage labels out. | Technical | High | Medium | Treat the rubric as a first-class artifact: surface disagreement clusters back to the author, support versioned rubric snapshots, make spec-tightening part of onboarding ([04](04-accuracy-and-trust-engine.md), [07](07-hitl-ux-spec.md)). | High edit rate concentrated on specific label classes; annotators disagree with each other *and* with the model on the same items. |
| R5 | **Cold start.** A brand-new taxonomy has no gold set, so we cannot calibrate a confidence threshold or quote a precision SLA on day one — the chicken-and-egg of a fresh project. | Technical | High | Medium | RAG-few-shot from gold works from the *first* corrections; the **Phase C foundation labeler warm-starts day-zero projects**; seed gold sets are cheap to collect through the HITL loop ([05](05-data-flywheel-and-model-strategy.md) §3, §5). | Design partners stall at setup; time-to-first-trusted-auto-apply is long; new projects sit at near-zero coverage for many sessions. |
| R6 | **Correlated verifier errors + training-data poisoning.** A model judging its own output shares its blind spots, and auto-applied errors fed back into training entrench a feedback loop of confident mistakes. | Technical | Medium | High | Use **diverse-family judges** and **deterministic checks** that don't share the labeler's failure modes; **train only on human-corrected finals and edits, never on raw auto-applied labels**; keep a ~2% audit sample as ground-truth injection ([04](04-accuracy-and-trust-engine.md), [05](05-data-flywheel-and-model-strategy.md) §9). | Audit-sample precision drifts below the SLA while the gate still reports "in spec"; specialist accuracy rises on auto-labels but not on the held-out gold set. |
| R7 | **Privacy / data leakage in the flywheel.** A per-customer or foundation model memorizes and re-emits one customer's data to another, or contributed data leaks across the tenant boundary. | Legal-Privacy | Low | High | Per-customer tenant isolation; **opt-in only** for foundation-model training; de-identification + synthetic distillation; on-prem customers transmit nothing ([05](05-data-flywheel-and-model-strategy.md) §6). | A model surfaces verbatim content it was never trained on for that tenant; a security review flags the cross-tenant path. |
| R8 | **On-prem support burden.** Air-gapped and self-hosted deployments multiply environments, versions, and GPU configurations, dragging eng into bespoke support and stalling product velocity. | Execution | Medium | Medium | Ship a small, self-contained artifact (base + LoRA adapter) served via vLLM/llama.cpp; standardize on a narrow supported matrix; price on-prem to fund its own support; consider partnering for distribution (open question Q11). | On-prem onboarding takes weeks; support tickets per on-prem account climb; eng time on deploys crowds out roadmap. |
| R9 | **GPU / inference cost at scale.** Million-item runs and frequent retrains burn GPU budget faster than metered revenue covers, inverting margins. | Execution | Medium | Medium | Move volume from frontier APIs to **quantized small specialists served locally** (10–100x cheaper, estimate); batch inference; retrain on burst GPUs gated by the champion/challenger promotion ([05](05-data-flywheel-and-model-strategy.md) §4, §7). | Cost-per-labeled-item trends up; inference gross margin compresses; retrain spend outpaces coverage gains. |
| R10 | **Talent.** The build needs scarce, expensive people across calibration/eval ML, distillation, and Cursor-grade frontend — a hard hiring profile for a seed-stage team. | Execution | Medium | Medium | Sequence the roadmap so a small team ships the trust loop before the flywheel; dogfood to attract mission-aligned ML hires; keep the editor forked rather than built so frontend scope stays bounded ([08](08-implementation-and-development-plan.md)). | Key roles stay open for months; roadmap milestones slip on a single person; founder is on the critical path for ML work. |
| R11 | **Neutrality / acquisition ("getting Meta'd").** A strategic investor or acquirer takes a stake that makes us non-neutral, and customers pull work rather than feed a rival's part-owner — exactly what happened to Scale AI. | Market | Medium | High | Hold neutrality as an explicit, durable promise; stay un-acquired and multi-model by design; bank neutrality as a procurement unblocker while incumbents are compromised ([02](02-business-case-and-strategy.md) §3, §4). | Inbound strategic-investor interest with control or data strings; a design partner asks who owns us before signing. |
| R12 | **Beachhead timing.** The post-training data crunch eases, or the SFT/eval-data buyer consolidates into a few labs, before we land the wedge — the "why now" expires. | Market | Medium | Medium | Land design partners fast while the pain is acute; keep the taxonomy-agnostic core so we can expand from LLM data into enterprise text classification / NER if the first beachhead cools ([02](02-business-case-and-strategy.md) §6). | Inbound from the beachhead slows; buyers say "frontier zero-shot is good enough now"; SFT/eval work centralizes into a handful of accounts. |
| R13 | **GTM mismatch.** The product-led, bottoms-up motion fails to convert into the high-margin enterprise/on-prem expansion the business model depends on. | Execution | Medium | Medium | Instrument the land→expand funnel from day one; design the on-prem specialized labeler as the explicit expansion artifact and CFO services-to-software pitch ([02](02-business-case-and-strategy.md) §5, §6). | Self-serve usage grows but never produces team/org deals; expansion revenue stays a rounding error. |

## 2. The three most serious risks, expanded

### R1 — An incumbent (especially Label Studio) ships the loop

This is the single highest risk because it attacks from a position we cannot match on its own terms: distribution. Label Studio / HumanSignal already has the user base, the connectors, and the annotation UI that we intend to *fork*. The day they bolt a credible "AI proposes, you accept/correct" loop onto that base, they contest our wedge as the incumbent and we are the challenger. The naive defense — out-feature them on platform breadth — is a losing race.

**Strategic response.** First, fork rather than rebuild: by starting from Label Studio we inherit the same plumbing they have, so the contest collapses to the layers we intend to win on anyway — the keyboard-first loop, the calibrated trust layer, and the flywheel — where obsessive UX and execution speed are the weapons. Second, and decisively, **get to the flywheel before they do**. A pre-label-accept loop is copyable in a quarter; a per-customer specialized model trained on a year of one customer's corrections is not. Once a customer's switching cost is "abandon a model that gets better every week," feature parity on the loop no longer wins the account. Speed to Phase B is the whole game, which is why the [roadmap](08-implementation-and-development-plan.md) front-loads the trust loop and the flywheel rather than breadth.

### R2 — Commoditization of the base labeler (and why it's survivable)

The base capability — an LLM labeling a dataset — is commoditizing and will keep doing so as frontier models get better and cheaper. Refuel AI shipped exactly that, raised $5.2M, and was absorbed by Together AI. If Tessera *is* the base labeler, the same fate applies. The recurring worry is "won't the next frontier model just label everything and kill you?"

**Strategic response.** No — for the same structural reason "an AI can autocomplete code" did not kill Cursor. Our value lives in the three layers *around* the commodity: trust (a calibrated, audited precision SLA a team will ship on), UX (a loop engineers prefer), and the flywheel (corrections → private models). Crucially, a stronger base model is a **tailwind, not a threat**: it raises the Phase A baseline, improves the quality of the corrections we harvest, and gives us a better teacher to distill from. Every axis of the moat gets *better* as base models improve, because the flywheel is built on top of frontier capability, not in competition with it ([05](05-data-flywheel-and-model-strategy.md) §8). The failure mode is not "frontier models get good" — it is "we mistake the commodity for the company," as Refuel did. The discipline is to never sell "autolabeling."

### R3 — The accuracy ceiling on subjective tasks

The entire pitch rests on a guaranteed precision. But precision is only meaningful where a ground truth exists. For subjective or ambiguous tasks — tone, helpfulness, "is this a good SFT pair" — two careful humans given the same instructions routinely disagree, so the ceiling on *any* labeler is the human-agreement ceiling, not 100%. We cannot calibrate a 98% precision SLA on a task where humans only agree 70% of the time; the SLA would be a fiction.

**Strategic response.** Make the ceiling explicit and design around it. Measure **inter-annotator agreement before quoting an SLA**, and only promise precision the rubric can actually support. Much of the apparent "model error" on subjective tasks is really rubric ambiguity (R4), so the highest-leverage move is investing in rubric clarity — surfacing disagreement clusters to the spec author and tightening guidelines until agreement rises. Where a slice is irreducibly subjective, route it to humans rather than auto-applying, and report coverage honestly: "we auto-label the unambiguous majority; the genuinely contested cases always go to a person." Honesty about the ceiling is itself a trust asset — it is the opposite of the over-promising that breaks SLAs. The mechanics live in the [accuracy & trust engine](04-accuracy-and-trust-engine.md).

## 3. Open questions

Grouped Product / Technical / GTM. Each has *why it matters* and *how to resolve it* — usually a specific design-partner experiment or a piece of research.

### Product

| # | Open question | Why it matters | How to resolve |
|---|---|---|---|
| Q1 | **Which beachhead vertical within LLM-data first** — SFT, preference/RLHF, or eval-set curation? | Each has a different rubric shape, agreement ceiling, and buyer. Picking wrong wastes the first design-partner cohort. | Pilot one project of each type with design partners; pick the vertical with the highest coverage@precision *and* the clearest willingness to pay. |
| Q2 | **Fork Label Studio long-term, or build a bespoke editor?** | The fork accelerates us now but may cap how Cursor-grade the loop can feel; rebuilding costs years. | Push the fork until UX friction is measured against the keyboard-first target ([07](07-hitl-ux-spec.md)); revisit only if fork constraints demonstrably block the loop feel. |
| Q3 | **When to add multimodal** (images, audio)? | Vision is a huge adjacent market but dilutes the technical, dogfoodable text wedge if added too early. | Stay text-only until the text flywheel compounds; add a modality only when design partners pull for it and the trust engine generalizes. |

### Technical

| # | Open question | Why it matters | How to resolve |
|---|---|---|---|
| Q4 | **How much gold is enough** to calibrate a reliable threshold on a new taxonomy? | Too little gold → an unreliable SLA; too much → a heavy cold-start burden that stalls onboarding. | Empirical sweep: track how calibration error (ECE) and coverage@precision stabilize as gold-set size grows across partner projects; find the knee of the curve. |
| Q5 | **What achieves robust calibration in practice** — temperature scaling, isotonic, or Platt — and does it hold out-of-distribution? | Calibration *is* the product's trust guarantee; a miscalibrated confidence breaks the SLA silently. | Benchmark the three methods on partner gold sets; measure ECE on held-out and OOD slices; standardize the winner in [04](04-accuracy-and-trust-engine.md). |
| Q6 | **Which verifier-diversity recipe actually breaks correlated errors** (different model families, deterministic checks, self-consistency, ensemble disagreement)? | If verifiers share blind spots, the precision guarantee is hollow (R6). | A/B different judge ensembles against the audit sample; keep the configuration whose auto-applied error rate best matches the held-out gold set. |
| Q7 | **What is the real Phase B trigger** — how many corrected pairs before a specialist beats the BYO baseline? | Sizes the cold-start, the retrain cadence, and the time-to-moat. | Train challengers at increasing correction counts; record the point where the challenger reliably wins the champion/challenger gate ([05](05-data-flywheel-and-model-strategy.md) §4). |

### GTM

| # | Open question | Why it matters | How to resolve |
|---|---|---|---|
| Q8 | **What precision SLA do buyers actually require** to ship without re-review? | The SLA is the price lever; set it wrong and we either over-promise (R3) or under-sell. | Ask design partners the bar at which they would trust auto-labels unseen; validate that coverage at that precision is real on their data. |
| Q9 | **Pricing model — per-seat, per-item, or per-token?** | Determines margin shape and whether pricing scales with value or with our cost. | Test the three framings with design partners; watch which aligns price with the trust they're buying, not the tokens they consume ([02](02-business-case-and-strategy.md) §5). |
| Q10 | **Does product-led adoption convert to enterprise/on-prem expansion?** | The whole high-margin thesis depends on land→expand (R13). | Instrument the funnel from the first self-serve users; look for the first organic team-to-org expansion. |
| Q11 | **Build vs partner for on-prem distribution?** | On-prem is the highest-margin tier but the heaviest support burden (R8). | Scope the supported matrix with the first on-prem partner; decide build-vs-partner once the per-deployment support cost is measured. |

## 4. Kill criteria / pivot triggers

Concrete signals that the core thesis is disproven. If one fires durably (not as a transient), we pivot or stop rather than keep funding the same bet.

- **The flywheel doesn't beat the frontier.** After a fair Phase B effort on real partner data, the per-customer specialist **cannot beat a raw frontier LLM on coverage@precision** on the held-out gold set. If the moat asset is no better than the commodity, there is no moat — this is the Refuel outcome, confirmed.
- **Trust never lands.** Design partners **will not trust auto-labels at any SLA** we can credibly hit — they re-review everything regardless. If the trust gap doesn't close, the entire "ship without re-review" premise (and the pricing power on top of it) collapses.
- **The incumbent out-distributes us.** **Label Studio (or Scale/Snorkel) ships an equivalent loop** and, on the back of existing distribution, wins the beachhead before we reach a defensible Phase B. Parity plus distribution beats us if we are still pre-flywheel.
- **Calibration is unreliable.** Calibrated confidence **does not hold up out-of-distribution** — coverage@precision in production diverges from the gold-set promise often enough that the SLA can't be honored. A trust product that can't be trusted has no reason to exist.
- **The ceiling is too low to sell.** Across candidate beachhead tasks, **inter-annotator agreement is so low that no sellable precision SLA is achievable** — the subjective-task ceiling (R3) caps the product below what buyers will pay for.
- **Unit economics invert.** At realistic scale, **inference + retrain cost exceeds metered revenue** even after moving volume to local specialists, with no path to crossover. The "services-to-software" margin story fails.
- **The motion doesn't expand.** Self-serve adoption grows but **never converts to enterprise/on-prem expansion** over a meaningful window — the business caps at low-margin seats and the high-margin thesis is disproven (R13).

A single soft signal is data, not a verdict. The trigger is a *durable* miss that survives an honest attempt to fix it.

## 5. Glossary

Alphabetical. Each term is defined in one or two plain sentences for a reader who runs LLMs but isn't a deep ML/systems engineer. *(MTP — multi-token prediction — appears in some LLM-training contexts but is **not relevant** to Tessera and is intentionally omitted.)*

| Term | Plain-English definition |
|---|---|
| **Active learning** | A strategy where the system picks the *most useful* unlabeled items to send to a human next, instead of choosing at random, so each human decision teaches the model the most. |
| **Calibration** | Adjusting a model's confidence scores so they mean what they say — if it claims 90% confidence, it should be right about 90% of the time. Common methods are temperature scaling, isotonic regression, and Platt scaling. |
| **Champion / challenger** | The model currently in production is the *champion*; a newly trained candidate is the *challenger*. The challenger only replaces the champion if it beats it on a held-out test, so quality can only go up. |
| **Cleanlab** | A tool/library that implements confident learning (see below) to automatically flag likely mislabeled examples in a dataset. |
| **Confidence (raw vs calibrated)** | How sure the model is about a label. *Raw* confidence is the model's unadjusted score; *calibrated* confidence has been corrected so the number reliably reflects the true chance of being right. |
| **Confident learning** | A statistical method (popularized by Cleanlab) for finding label errors by comparing a model's predicted confidences against the given labels to spot likely mistakes. |
| **Coverage@precision** | Tessera's north-star metric: the share of a dataset the system can auto-label (*coverage*) while staying at or above a guaranteed correctness target (*precision*). "Auto-label 70% at 98% precision" is a coverage@precision claim. |
| **Deterministic checks** | Fixed, rule-based validations (regex, format, range, allowed-value checks) that catch errors with certainty and don't share an LLM's blind spots — e.g. "a date label must parse as a date." |
| **Distillation** | Training a smaller, cheaper model to copy the behavior of a larger one, so you get most of the quality at a fraction of the cost and size. |
| **Ensemble disagreement** | Running several different models (or prompts) on the same item and treating *how much they disagree* as a signal of uncertainty — high disagreement means the item is hard and should go to a human. |
| **Expected calibration error (ECE)** | A single number measuring how far a model's confidence is from reality across all its predictions. Lower ECE means better-calibrated confidence; it's how we grade calibration. |
| **Gold set** | A trusted set of correctly labeled examples used as the answer key — to calibrate confidence thresholds, set the precision SLA, and score whether a new model is actually better. |
| **HITL (human-in-the-loop)** | A workflow where a human reviews or corrects the system's output on the cases that need it, rather than the system acting fully autonomously. |
| **Inter-annotator agreement** | How often two human labelers, given the same items and instructions, choose the same label. It sets the realistic ceiling on accuracy — a task humans disagree on can't be labeled "perfectly" by anyone. |
| **Label function** | A small rule or heuristic that votes on a label (e.g. "if the text contains 'refund', tag it billing"). Individually noisy, but many combined become useful — the building block of weak supervision. |
| **Label model** | A model that combines many noisy label-function votes into a single best-guess label per item, weighting each function by how reliable it appears to be. |
| **LiteLLM** | An open-source library that gives one uniform API to call many different LLM providers (Claude, GPT, local models), so the product isn't locked to a single vendor. |
| **LLM-as-judge / verification pass** | Using a second LLM to check the first model's label — a "does this look right?" review step. To be trustworthy the judge should be a *different* model family so it doesn't share the first model's blind spots. |
| **LoRA / QLoRA** | *LoRA* (Low-Rank Adaptation) fine-tunes a model by training a small add-on "adapter" instead of all its weights — fast, cheap, and tiny to store. *QLoRA* does the same on a compressed (4-bit) base, so even a 7–8B model fine-tunes on a single GPU. |
| **Out-of-distribution (OOD)** | Data that looks meaningfully different from what the model (or its calibration) was tuned on. Calibration that holds on familiar data can quietly break OOD, which is why we test for it. |
| **Precision / recall / F1** | *Precision* = of the items we labeled X, how many were truly X (correctness of what we claim). *Recall* = of all the true X items, how many we caught (completeness). *F1* is their balanced average. Tessera optimizes precision first — being right about what we auto-apply. |
| **RAG (retrieval-augmented generation)** | Instead of a fixed prompt, the system fetches the most relevant prior examples at query time and pastes them into the model's context, so answers are grounded in on-topic, on-taxonomy examples. |
| **Rubric (see taxonomy/rubric)** | The written guidelines telling labelers exactly how to decide each label. Ambiguous rubrics produce bad labels no matter how good the model is. |
| **Self-consistency** | Asking the same model the same question several times (or several ways) and checking whether it gives the same answer — consistent answers signal confidence, varying ones signal uncertainty. |
| **SFT / RLHF / preference (pairwise) data** | The main flavors of LLM post-training data. *SFT* (supervised fine-tuning) = examples of correct inputs→outputs. *RLHF* (reinforcement learning from human feedback) tunes a model to human preferences. *Preference / pairwise* data = "response A is better than B" comparisons that train those preferences. |
| **Taxonomy / rubric** | The *taxonomy* is the set of allowed labels (the categories); the *rubric* is the guidelines for choosing among them. Together they define what a "correct" label even means for a project. |
| **Temporal** | An open-source workflow-orchestration engine for running reliable, long-running, multi-step jobs (like labeling runs and retrains) with automatic retries and state tracking. |
| **Uncertainty / informativeness / representativeness sampling** | Three ways active learning picks the next items for a human: *uncertainty* (the model is least sure), *informativeness* (the item would teach the model the most), *representativeness* (the item is typical of a large unlabeled cluster, so labeling it helps many). |
| **Vector DB** | A database that stores items as numeric vectors (embeddings) and finds the most *similar* ones quickly — the engine behind RAG's "fetch the nearest gold examples." |
| **Weak supervision** | Producing labels from many cheap, noisy sources (label functions) instead of expensive hand-labeling, then combining their votes with a label model into usable training labels. |

---

*See also: competitive and fundability framing — [business case & strategy](02-business-case-and-strategy.md); calibration, verification, and the precision SLA mechanics — [accuracy & trust engine](04-accuracy-and-trust-engine.md); the flywheel risks in depth — [data flywheel & model strategy](05-data-flywheel-and-model-strategy.md); build order and sequencing of these mitigations — [implementation & development plan](08-implementation-and-development-plan.md).*
