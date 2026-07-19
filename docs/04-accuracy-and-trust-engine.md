# Tessera — Accuracy & Trust Engine

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The technical core that makes "accurate labels you don't have to re-check" real, measurable, and defensible.

Last updated: June 2026

> **Status (June 2026):** the mechanisms here are implemented in the shipped MVP — confidence ensemble, isotonic calibration (weighted PAV), cross-validated coverage@precision, the confidence gate, the active-learning queue order, gold-set sampling, and the quality report all exist in code (github.com/samdotson61/Tessera). See the [suite status](README.md).

---

## 1. Why this is make-or-break

Tessera's one-liner promises labels you can ship **without re-reviewing them.** That promise lives or dies here. Every other pillar — the keyboard-first UI, the data flywheel — is worthless if the user doesn't believe the auto-applied labels. So this is the pillar that has to be unimpeachable.

Look closely at the pitch and one word is doing all the work: *accurate.* It is also the easiest word to lie with. Any autolabeler can run an LLM (large language model — a model that predicts text) over a dataset and emit a label for every row. What none of them can honestly do is tell you **which of those labels are right** — and that is the only thing the buyer actually cares about, because the unknown-wrong labels are what force a full manual re-review, which destroys the entire time savings (see [PRD §1](01-product-vision-and-prd.md)).

The trap is **asserting** accuracy: shipping a model, quoting a benchmark number from someone else's dataset, and asking the customer to trust it on *their* data. Benchmarks don't transfer; the customer's taxonomy and distribution are their own. So Tessera's founding principle is:

> **Gate, don't assert.** We never claim a label is accurate. We *measure* the model's confidence, *calibrate* that confidence against ground truth on the customer's own data, and *auto-apply only above the confidence line that provably hits the target precision.* Everything below the line goes to a human. The accuracy claim is therefore not a marketing statement — it is a measured, audited property of each specific dataset.

"Precision" here means: of the labels we auto-applied, what fraction were correct. If we auto-apply 1,000 labels at 98% precision, ~20 are wrong. The whole engine exists to make that 98% a number we earned and can prove, not one we hoped for.

## 2. Coverage@precision, defined precisely

The north-star product metric (see [PRD §10](01-product-vision-and-prd.md)) is **coverage@precision**:

> **Coverage@precision = the fraction of the dataset that can be auto-applied while holding measured precision at or above a target** (e.g. ≥98%).

It is a single point on a tradeoff curve. Demand higher precision → fewer items clear the bar → lower coverage. Relax precision → more items clear → higher coverage but more auto-applied errors. The number is meaningless without its target attached; always write it as a pair, e.g. **"70% coverage @ 98% precision."**

### Worked example

A dataset of **100,000** items. We want **98% precision** on whatever we auto-apply. Each item carries a *calibrated confidence* (§3, §4) — a number where "0.98" genuinely means "~98% likely correct."

```
Step 1 — score every item, sort by calibrated confidence (descending):

  confidence band   items in band   measured precision in band (from gold set)
  0.99 – 1.00          41,000              99.4%
  0.98 – 0.99          23,000              98.1%
  0.95 – 0.98          16,000              96.0%   ← drops below target here
  0.90 – 0.95          11,000              92.7%
  < 0.90                9,000              (low / uncertain)

Step 2 — walk down the sorted list, accumulating, until cumulative
         precision would fall below 98%. That cutoff is the GATE.

  cutoff at confidence ≈ 0.98:
     auto-applied  = 41,000 + 23,000          = 64,000   (cumulative precision 98.9%)
     adding the 0.95–0.98 band would pull the cumulative below 98% → STOP.

Step 3 — result:
     AUTO-APPLIED : 64,000 items   →  COVERAGE@98% = 64%
     HUMAN QUEUE  : 36,000 items   →  routed to the keyboard-first loop
     expected auto errors ≈ 64,000 × (1 − 0.989) ≈ 700 items, caught by audit (§7)
```

The user touches 36k items instead of 100k — a 64% reduction in human effort *this run* — and the 64k auto-applied labels carry a measured, defensible precision. Next run, after the model learns from the 36k corrections (see [flywheel](05-data-flywheel-and-model-strategy.md)), the curve shifts right and coverage climbs toward the pitch's 70%+.

The gate is computed *empirically* from the calibration curve on the gold set, not from a formula. That is the entire trick: we never assume the model is right, we **measure where it is reliably right and draw the line there.**

## 3. The five layers

The engine stacks five layers. Layers 1–4 each *produce a confidence signal or a candidate label*; layer 5 is the ground-truth spine that calibrates and audits everything above it. Think of layers 1–4 as voters and layer 5 as the scorekeeper that learns how much to trust each voter.

```
  ┌─────────────────────────────────────────────────────────────┐
  │  L5  GOLD SET + EVAL HARNESS   (ground truth; calibrates all) │
  │   ┌─────────────────────────────────────────────────────┐    │
  │   │ L4  WEAK SUPERVISION  (cheap noisy voters → label model)  │
  │   │ L3  VERIFICATION PASS (LLM-judge + deterministic checks)  │
  │   │ L2  CALIBRATION       (raw confidence → P(correct))       │
  │   │ L1  CONFIDENCE ESTIMATION (logprobs, verbalized,          │
  │   │                            self-consistency, ensemble)    │
  │   └─────────────────────────────────────────────────────┘    │
  └─────────────────────────────────────────────────────────────┘
```

### Layer 1 — Confidence estimation

**What it is.** Producing a raw confidence number for each proposed label, by blending several signals that each fail differently. No single signal is trustworthy; the blend is.

**How it works.** Four signals, in increasing cost and reliability:

- **Token logprobs** — the model's own probability for the tokens it emitted (the log-probability, i.e. the model's internal "how sure am I about this word"). *Cheap* (free with the generation) but *weakly calibrated* — modern instruction-tuned LLMs are overconfident and their raw probabilities don't match real accuracy.
- **Verbalized confidence** — literally ask the model "how confident are you, 0–100?". Surprisingly informative, but *biased* (models anchor high, and the number is itself a generation that can be wrong).
- **Self-consistency** — sample the label **N** times at non-zero *temperature* (the randomness knob on generation; higher = more varied samples). The final label is the **majority vote**; the **confidence is the agreement fraction** (e.g. 9 of 10 samples agree → 0.9). Cost is N× inference but it is one of the strongest single signals: stable answers are reliable, answers that wobble across samples are not.
- **Ensemble disagreement** — run **2–3 different model families** (e.g. Claude + GPT + a local open-weights model). **Agreement across families = high confidence; disagreement = route to a human.** Diverse families is the key word: different architectures and training data fail on *different* examples, so their agreement is far more meaningful than one model agreeing with itself. This is the strongest signal we have and the one that most resists the correlated-error problem (§5).

**For structured outputs, score per region.** For tasks like NER (named-entity recognition — tagging spans of text such as person/org/location), a single item contains many sub-decisions. Score **per span/region, not just per item**: a sentence might have one rock-solid `PERSON` span and one shaky `ORG` span. We auto-apply the confident spans and route only the uncertain ones, which dramatically raises coverage on structured tasks.

**Inputs:** item + rubric + labeler model(s). **Outputs:** candidate label(s) + a raw confidence in [0,1] (per item, and per span where structured).

**Failure modes / pitfalls.** Raw logprobs are systematically overconfident; verbalized confidence is gameable and clusters at round numbers; self-consistency multiplies cost and can be *confidently wrong* when the model has a consistent misconception (all N samples agree on the wrong answer — this is why we still need layer 5). None of these numbers means "P(correct)" until layer 2 maps it.

### Layer 2 — Calibration

**What it is.** **Calibration** = the property that a stated confidence matches the real frequency of being correct. A *calibrated* "0.9" means ~90% of such labels are right. Raw LLM confidence is **not** calibrated, so layer 2 fits a function that maps raw → calibrated using the gold set.

**How it works.** Fit a one-input correction map on held-out gold labels (§7). Three standard methods (each learns the map from (raw confidence, was-it-correct) pairs):

- **Platt scaling** — fit a logistic (S-curve) from raw score to probability. Simple, few parameters, good when you have little gold.
- **Temperature scaling** — divide the logits by a single learned scalar T before the softmax; the cheapest fix for the classic "overconfident" failure and the default for neural nets.
- **Isotonic regression** — fit any monotonic (non-decreasing) step function. Most flexible, corrects weird non-linear miscalibration, but needs more gold or it overfits.

**Inputs:** raw confidences + gold labels. **Outputs:** a calibration map, plus the **calibration curve** (a reliability diagram: predicted confidence on x, observed accuracy on y; perfect calibration is the 45° line). The gate in §2 reads directly off this curve. Deep-dive in §4.

**Failure modes / pitfalls.** Calibration **drifts**: a new taxonomy version, a swapped labeler model, or a shifted data distribution silently invalidates the map. A stale map is worse than none, because it lends false authority to a wrong number. Sparse classes are calibrated on too few points and are unreliable there (§9). Mitigation: version every calibration model and trigger re-fit on any change (§4).

### Layer 3 — Verification pass

**What it is.** A second model, **LLM-as-judge** — an LLM given (the input, the proposed label, the rubric) and asked to **critique** it: *agree* or *flag*, plus a reason. It's a cheap second opinion that catches a class of errors the labeler's confidence misses.

**How it works.** For borderline items (and a sample of confident ones), the judge returns `{verdict: agree|flag, reason}`. A flag pushes the item below the gate regardless of the labeler's confidence. The reason string is also surfaced to the human in the queue, so a routed item arrives pre-explained.

**Inputs:** item + proposed label + rubric. **Outputs:** verdict + reason, folded in as another confidence signal.

**The correlated-error caveat (be honest).** A judge that shares the labeler's blind spots will *confidently agree with the same mistakes* — the errors are **correlated**, so the judge adds little. This is the single most important honesty caveat in the whole engine. Three mitigations, used together:

1. **Different model family as judge** than the labeler — decorrelates the blind spots (same logic as ensemble disagreement in §1).
2. **Ask it to argue the opposite** — prompt the judge to make the strongest case that the label is *wrong*, then decide. This breaks sycophantic agreement.
3. **Deterministic rubric checks** — non-LLM code that catches dumb errors *for free* and with zero correlation: allowed-value set membership (is the label even in the taxonomy?), span contiguity (does the NER span cover a valid contiguous range?), schema/type validity (does the output parse?). These never share an LLM's blind spots because they aren't an LLM. They are cheap, they run on 100% of items, and they catch a surprising share of real failures. We run them *first*, before spending a judge call.

### Layer 4 — Weak supervision

**What it is.** **Weak supervision** (the Snorkel approach) lets users add many *cheap, noisy* voters and combines them statistically into a probabilistic label — **with no gold labels required to start.** Each voter is a **labeling function**: a small rule that votes a label or abstains.

**How it works.** Users (or we, by default) add labeling functions: a regex, a keyword list, an existing classifier the customer already owns, or the LLM itself as one voter. A **label model** — a statistical model that estimates *each source's accuracy purely from how the sources agree and disagree with each other* (no ground truth needed; it exploits the fact that an accurate source agrees with other accurate sources more often than chance) — then aggregates the votes, weighting accurate sources up and noisy ones down, into a single probabilistic label per item.

**Inputs:** N labeling functions (regex/keyword/classifier/LLM). **Outputs:** a probabilistic label per item + an estimated accuracy per source — and, usefully, **an extra confidence signal** that is independent of the LLM, so it also helps decorrelate §3.

**Why it earns its place.** It is the **cold-start** answer (§6): it produces a first labeled pass and a first confidence signal *before any gold exists,* and it can boost coverage even after gold arrives.

**Failure modes / pitfalls.** If all the cheap sources share a bias (e.g. every regex keys off the same surface word), the label model is fooled — it can't detect a bias common to *all* its inputs. It needs a few *independent* sources to work. And it estimates *relative* accuracy, not absolute truth, so it never replaces the gold set — it complements it.

### Layer 5 — Gold set + eval harness

**What it is.** The **gold set** — a small set of items labeled by trusted humans, treated as ground truth — and the harness that uses it to score everything above. This is the **trust spine**; without it, layers 1–4 are just opinions. Full lifecycle in §7.

**How it works.** Bootstrap with a small **stratified** human-labeled sample (§7), then continuously:

- compute **precision / recall / F1 per taxonomy version** (recall = of all true positives, how many we found; F1 = the harmonic mean of precision and recall — a single balanced score),
- fit and plot the confidence→accuracy **calibration curve** (feeds layer 2),
- compute **coverage@precision** (the north star, §2),
- **audit-sample ~2%** of *auto-applied* labels — re-check them with a human even though they cleared the gate — to catch silent drift before it spreads,
- measure **inter-annotator agreement (IAA)** — how often two humans labeling the same item agree (humans are noisy; e.g. via Cohen's κ, a chance-corrected agreement score from 0 to 1). IAA is the *ceiling*: we cannot be more "accurate" than humans agree with each other, so it bounds the achievable precision on subjective tasks (§9),
- run **label-error detection** — **confident learning** (the Cleanlab approach): a statistical method that flags *likely-mislabeled items* by finding cases where the model is confidently sure of a label that disagrees with the assigned one. Crucially we run it on **both model labels and human/gold labels** — humans mislabel too, and a wrong gold label silently corrupts calibration.

**Inputs:** human gold labels + all model outputs and confidences. **Outputs:** the calibration curve, the metrics, the gate, the audit results, the Quality Report (§10).

**Failure modes / pitfalls.** A too-small or non-stratified gold set gives a confident-looking but unrepresentative curve (§7). A contaminated gold set (human errors) is the worst case — hence running confident learning on the gold itself.

### The closed loop (shipped v0.10.0)

Two mechanisms turn the layers above from a measurement stack into a system that acts on its own evidence:

- **Consensus gate** (default ON since v0.11.0; `TESSERA_SPECIALIST=0` disables) — the L1 ensemble-disagreement signal, made free. The Tier-0 specialist (a stdlib logistic head over hashed-BoW features, docs/05 Phase B's smallest form) joins the ensemble as an ordinary member. It trains on a hash-stable **half** of the trusted labels, and L2 calibrates **only on the other half** — the leak guard, since training memorization otherwise masquerades as agreement exactly where the calibrator looks. The active guard is detected from stored votes, so report-time re-gates stay leak-safe with no configuration. Default-on is made safe by a join rule: the specialist arms only on a classification task with ≥ `TESSERA_SPECIALIST_MIN` training examples of ≥ 2 labels **and** ≥ `TESSERA_MIN_GOLD` gold items remaining on the calibration half — the co-signal is never allowed to cost cross-validated calibration; below those floors the run is unchanged. Measured (AG News, 297 gold, held-back truth): the 4B went from 64% to **93.5% coverage at the 90% target with the promise kept out-of-sample (92.7% unseen)**; the 2B from 33.5%@88.1% to 49.8%@98.0%. Disagreement between a microsecond head and the LLM is nearly as decorrelating as a second model family, at none of the cost.
- **Audit autopilot** (`TESSERA_AUTOPILOT=1`) — L5's audit stream, acting on the gate. Each decision window (default 20 fresh audits since the last adjustment) is judged with an exact one-sided binomial test against the target: a confident breach (p < target at 95% confidence) tightens the gate one level — allowed error halves per level, capped at 3 — a clean window at/above the promise relaxes one level, and inconclusive evidence accumulates until it isn't. The report states the level and the effective target the gate actually ran at; report-only re-gates read but never consume evidence. Combined with audit corrections entering gold and the specialist retraining every run, drift now *tightens* the system and pulls humans back in, rather than silently spending the SLA.

## 4. Calibration deep-dive

**Why raw LLM confidence is untrustworthy.** Instruction-tuned LLMs are trained to sound fluent and helpful, not to report well-calibrated probabilities. The result is systematic **overconfidence**: a model's raw "0.97" might empirically be right only 80% of the time. Verbalized confidence is worse — it anchors high and clusters at round numbers (90, 95, 99). Using any raw number as if it were P(correct) would set the gate in the wrong place and break the precision guarantee. **Calibration is the layer that converts vibes into a probability we can gate on.**

**The methods, and when to use which.**

| Method | What it fits | Params | Use when |
|---|---|---|---|
| Temperature scaling | one scalar T on the logits | 1 | overconfidence is the main problem; very little gold |
| Platt scaling | a logistic curve | 2 | small gold set; smooth monotonic miscalibration |
| Isotonic regression | any monotonic step function | many | enough gold (≳ a few hundred); non-linear miscalibration |

Default: temperature or Platt early (gold is scarce), graduate to isotonic as the gold set grows.

**When/how to re-calibrate.** The map is only valid for the conditions it was fit under. Re-fit whenever any of these change: the **taxonomy** (new/edited classes), the **labeler model** (version bump or family swap), or the **data distribution** (a new batch that looks different — detected via embedding-distance drift, see [architecture](03-system-architecture.md)). The harness watches for these and flags a stale calibration before it's used.

**Per-taxonomy-version calibration models.** Calibration is keyed to a *(taxonomy version, model)* pair and **versioned alongside the rubric** ([data model](06-data-model-and-api-contracts.md) carries the schema). When the rubric changes, we don't silently reuse the old map — we mark it stale and require fresh gold for the new version. This is what stops the most insidious failure mode: a rubric edit that quietly makes yesterday's "98% precision" a fiction.

## 5. Verification + correlated-error caveat + deterministic checks

The verification pass (layer 3) is powerful but only if its errors are *uncorrelated* with the labeler's — restating §3 because it is the part most likely to be over-trusted. A judge from the same model family as the labeler is close to a rubber stamp: it agrees with the same mistakes for the same reasons. So the design rule is firm:

- **The judge must be a different family than the labeler.** If the labeler is Claude, the judge is GPT or a local model, and vice versa.
- **Make the judge adversarial** — prompt it to argue the label is wrong before ruling, which strips out sycophancy.
- **Lead with deterministic checks.** The unglamorous workhorses — allowed-value membership, contiguous-span validity, schema/type parsing — catch a real fraction of failures with *zero* LLM cost and *zero* correlation with any model's blind spots. They run on 100% of items, before any judge call, and they are the cheapest precision we will ever buy. A judge should never be spent on an item a regex can already prove is malformed.

## 6. Weak supervision for cold-start

The chicken-and-egg problem: calibration needs gold, gold needs human labels, and a brand-new customer has neither on day one. Weak supervision (layer 4) breaks the cycle.

```
  Day 0, zero gold labels:
    user adds 3–5 cheap labeling functions
      e.g.  regex /refund|charge.?back/  → BILLING
            keyword list {crash, error, 500} → BUG
            an existing in-house classifier   → its prediction
            the LLM itself                     → its label
                         │
                         ▼
              LABEL MODEL learns each source's accuracy
              from their agreement patterns (no gold needed)
                         │
                         ▼
        first probabilistic labels + first confidence signal
                         │
                         ▼
        → seed the human queue with the *least* certain items,
          whose human labels become the FIRST gold (→ §7)
```

So the very first human effort is spent labeling the items weak supervision is *least* sure about — which is exactly the most informative gold to collect. Cold-start and gold-bootstrap become the same motion. The router that orders this queue is specified in [architecture](03-system-architecture.md).

## 7. Gold-set lifecycle

The gold set is the asset the entire trust claim rests on. It has a lifecycle, not a one-time setup.

**1. Stratified bootstrap.** Don't sample uniformly at random — that over-samples the common classes and the dense center of the data, leaving you blind exactly where errors hide. Draw a **stratified** sample (50–200 items to start *(estimate)*), stratified along **two axes**:

- by **predicted class** — so every label, including rare ones, gets some gold, and
- by **embedding cluster** — group items by semantic similarity (embeddings = numeric vectors capturing meaning) and sample across clusters, so the gold *covers the space* rather than clumping in the easy region.

**2. Growth via active learning.** The gold set grows from the human queue. The router orders that queue by **uncertainty × informativeness × representativeness** (see [architecture](03-system-architecture.md)), so each human label is chosen to *maximally improve both the labeler and the gold set* — not just to clear work. Corrections flow into both the gold set and the [flywheel](05-data-flywheel-and-model-strategy.md).

**3. Audit sampling.** Continuously re-check **~2%** of *auto-applied* labels with a human, even above the gate. This is the smoke detector for **silent drift**: if audit precision starts slipping below target, the gate auto-tightens and we re-calibrate before the customer is ever exposed to a wave of bad labels.

**4. Drift detection.** Watch incoming batches for distribution shift (embedding distance from the calibration data) and watch audit precision over time. Either tripping triggers re-calibration (§4) and, if needed, fresh stratified gold.

**How much gold is enough? (heuristics, estimates.)**

- **Calibration map:** ~50–100 labeled items for temperature/Platt; ~300–500 for stable isotonic. *(estimate)*
- **Per-class precision:** you need enough gold *per class* to bound the error — rough rule, ≥~30 gold items per class you intend to auto-apply, more for rare classes or you simply cannot certify them (§9). *(estimate)*
- **Stop growing** when the calibration curve and per-class precision stabilize across two consecutive audits — additional gold stops moving the numbers. *(estimate)*
- **Always** run confident learning on the gold itself; a corrupt gold set is worse than a small one.

## 8. The confidence gate + the trust slider

The gate (§2) is the runtime decision: auto-apply above the calibrated-confidence line, route below. The **trust slider** is how the user *operates* it — the single most important control in the product and the visible payoff of this whole engine.

The user drags a **target-precision slider**; the UI reads the measured calibration curve and shows the live tradeoff:

```
  TARGET PRECISION  ──────●────────────  98%
                   95%        99%   99.5%

  → at 98% precision:   COVERAGE 64%   QUEUE 36,000 items
  → at 99% precision:   COVERAGE 41%   QUEUE 59,000 items
  → at 95% precision:   COVERAGE 83%   QUEUE 17,000 items
```

The numbers are *measured*, not promised — every position is a real point on the gold-set curve, so the user is choosing a point on an evidence-backed frontier, not trusting a slogan. They trade human effort against auto-applied error rate with full visibility. Full interaction design in [HITL UX](07-hitl-ux-spec.md).

**Champion / challenger.** Run the current production labeler (the **champion**) alongside a candidate (the **challenger** — a new model version or a freshly distilled model from the [flywheel](05-data-flywheel-and-model-strategy.md)) on the *same* gold set. Promote the challenger only when it beats the champion on coverage@precision *and* calibration. This makes every model change a measured, reversible upgrade rather than a leap of faith — and ties directly into the flywheel's promotion gate.

## 9. Hard cases & honest limits

A trust engine that pretends it has no limits isn't trustworthy. The honest boundaries:

- **Subjective / ambiguous tasks.** When the right label is genuinely contestable (sentiment, toxicity, intent), precision is **capped by the human-agreement ceiling** — IAA from §5. If two humans agree only 80% of the time, no model can be certified above ~80%; the disagreement is in the *task*, not the model. Tessera's response is not to fake a number but to **surface the disagreement clusters** — the regions where humans and models split — and **force a rubric edit.** This is why **the Rubric editor is part of the accuracy engine, not just UX**: tightening the rubric is often the only lever that actually raises the achievable ceiling. The rubric, gold set, and calibration are one versioned unit ([data model](06-data-model-and-api-contracts.md)).
- **Rare / long-tail classes.** Where data is sparse, calibration is unreliable — too few points to fit a trustworthy curve. We **never auto-apply a rare class without an audit**, and we flag it in the Quality Report as low-confidence-by-construction rather than burying it.
- **Out-of-distribution (OOD) items.** Items unlike anything in the gold set (detected by embedding distance) get **no calibrated confidence we trust**, so they are **auto-flagged and routed**, never auto-applied. New-and-weird always goes to a human.
- **The human floor.** There is an irreducible slice that must be human-labeled — the genuinely hard, novel, and contested items. The product's job is to make that slice **as small and as cheap to clear as possible,** not to pretend it is zero. Honesty about the floor is itself a trust signal.

## 10. The Quality Report

The deliverable that lets a team *ship* the data — and a sales artifact. One per dataset, per taxonomy version, regenerated on every run.

**Exact contents:**

- **Headline:** coverage@precision (e.g. "70% @ 98%"), total items, auto-applied vs. human-cleared counts.
- **Precision / recall / F1 by class** — including the rare classes flagged as low-confidence.
- **Calibration curve** (reliability diagram) + **ECE** (expected calibration error — the average gap between stated confidence and observed accuracy; lower is better, §11).
- **Audit results** — the ~2% audit-sample pass rate, with confidence interval, and any drift trend.
- **Inter-annotator agreement** (the human ceiling) and any unresolved disagreement clusters.
- **Known failure modes** — rare classes, OOD rate, subjective regions hitting the IAA ceiling — stated plainly.
- **Provenance** — labeler model(s), judge model, calibration method + version, rubric/taxonomy version, gold-set size and composition.

**Who reads it, and why it does double duty:**

- The **ML engineer** reads it to decide *whether to ship the dataset* — it answers "can I trust this for my fine-tune/eval, and where can't I?" in one page.
- The **data-team lead** reads it to *report quality to stakeholders* — a defensible audit trail instead of gut feel.
- A **prospect** reads it as proof: it is the artifact that converts "trust us" into "here is the measured evidence on *your* data." It **unlocks shipping and it sells** — the same document does both.

## 11. Evaluation methodology — how we measure the measurer

The engine itself must be held to a standard. We measure *our own measuring*:

- **Precision / recall / F1 by class**, recomputed every run against held-out gold (never train calibration and report metrics on the same gold split — keep a clean holdout, or the numbers self-flatter).
- **Calibration error — ECE** (expected calibration error): bin items by stated confidence, take the average absolute gap between each bin's stated confidence and its observed accuracy. ECE → 0 means "0.9 really is 90%." We track ECE per taxonomy version and alert on regressions; rising ECE is the earliest sign calibration is going stale.
- **Coverage@precision over time** — the north star, tracked run-over-run on the same taxonomy. The flywheel's whole job is to push this line up ([flywheel](05-data-flywheel-and-model-strategy.md)); a flat line is a flywheel that isn't turning.
- **Audit pass rate** — the rolling fraction of audit-sampled auto-applied labels that survive human re-check. This is the realest number in the system: the others are estimates *of* precision, the audit rate is a *direct measurement* of it. If audit pass rate and predicted precision diverge, calibration is wrong and the gate tightens automatically.
- **Challenger win rate** — how often a challenger model beats the champion on the held-out gold (§8) — a leading indicator that the flywheel is producing real improvements rather than noise.

The discipline across all of it: **a clean holdout, versioned everything, and audit as the ground-truth check on our own claims.** That discipline is the product. The labeling is commoditized; the *provable, audited trust* is the moat ([business case](02-business-case-and-strategy.md)).
