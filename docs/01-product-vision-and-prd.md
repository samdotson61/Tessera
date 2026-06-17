# Tessera — Product Vision & PRD

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The canonical "what we're building and why" document: the problem, the vision, the core loop, the users, the MVP, and how we'll know it's working.

Last updated: June 2026

---

## 1. Problem statement

Data labeling — attaching ground-truth annotations to raw data so a model can learn from or be measured against it — is still slow, expensive, and inconsistent. Teams either pay a BPO (business process outsourcing) vendor by the label, stand up a manual annotation queue, or write brittle heuristics. All three are linear in cost: doubling the dataset doubles the spend and the calendar. And inconsistency is endemic — two human annotators given the same ambiguous example and the same instructions routinely disagree, which means the "ground truth" is itself noisy.

The LLM (large language model) post-training boom changed the buyer. A new wave of technical teams — anyone building a fine-tune, an evaluation set, or a content classifier on top of a frontier model — now urgently needs high-quality labeled data: supervised fine-tuning (SFT) pairs, preference data for RLHF (reinforcement learning from human feedback), output classifications, and curated eval sets. These buyers are sophisticated, in a hurry, and acutely aware that **the quality of their model is bounded by the quality of their labels.** Garbage labels produce a garbage fine-tune that fails silently on the eval that matters.

The obvious answer — "just have an LLM label the dataset" — already works for the easy majority of examples and is **commoditizing fast.** It is not a business by itself. The clearest precedent is Refuel AI, whose Autolabel library claimed a 25–100x speedup yet raised only a $5.2M seed and was acquired by Together AI in May 2025 ([VentureBeat](https://venturebeat.com/ai/refuel-ai-nabs-5m-to-create-training-ready-datasets-with-llms)). The raw capability is table stakes.

The real problem is **trust.** When a team runs an LLM over a dataset, they have no principled way to know which of its labels are right. So they do the only safe thing: they re-review the auto-labels by hand. That re-review destroys the entire time savings — you've paid for the LLM *and* paid for the humans, and you're back to a linear-cost manual process with extra steps. The trust gap is the bottleneck, not the labeling. **Tessera exists to close that gap.**

## 2. Vision & the one-liner

> A labeling tool you point at a raw dataset; it auto-labels the easy majority at a *guaranteed precision*, routes only the hard/uncertain cases to a human via a keyboard-first review loop, and turns the human-correction stream into private, specialized labeling models that get better at the customer's taxonomy every week.

The tagline: **"Cursor for data labeling."** The product makes a promise no autolabeler makes today: *we will tell you, with calibrated and audited evidence, which labels you can trust without looking — and we'll prove it on your data.* The point is not that an LLM did the labeling; it's that you can ship the result without re-reviewing it.

## 3. The Cursor → labeling mapping (this analogy is the strategy)

Cursor did not win because "an AI can autocomplete code" — that capability commoditized too. It won by forking an existing editor (VS Code) rather than building one, wrapping a frontier model in a tight semi-automated loop with a great keyboard-first UX, and then using the proprietary stream of accept/reject interactions to train its own models (Composer). It went from ~$100M ARR (Jan 2025) to ~$3B ARR (May 2026) at a ~$50B valuation in talks ([TechCrunch](https://techcrunch.com/2025/06/05/cursors-anysphere-nabs-9-9b-valuation-soars-past-500m-arr/), [TNW](https://thenextweb.com/news/cursor-anysphere-2-billion-funding-50-billion-valuation-ai-coding)). Tessera ports that exact playbook to labeling. The mapping below is a build plan, not a metaphor.

| Cursor (code) | Tessera (labeling) | Why it matters |
|---|---|---|
| Forked VS Code (didn't build an editor) | Fork **Label Studio** (don't build the annotation UI) | Skip years of editor work; spend our effort on net-new IP, not table-stakes UI |
| Tab autocomplete: AI proposes, you accept | **Pre-filled label** + one-keystroke accept / correct | The unit of human effort drops from "type a label" to "press a key" |
| Bring-your-own-model | **Bring-your-own-labeler** (Claude / GPT / local via one API) | Day-one capability with zero training; no vendor lock-in for the customer |
| Semi-automated agent loop | **Active-learning loop**: model labels → routes uncertain cases → retrains | Human effort shrinks run-over-run as the model learns the taxonomy |
| Composer (own models on interaction data) | **Specialized labeling models** trained on the correction stream | The moat: per-customer models that out-label frontier LLMs on *their* taxonomy |

The analogy is the strategy because the moat is identical: (1) Cursor-grade UX, (2) a trust/accuracy layer teams actually believe, and (3) a data flywheel that graduates from bring-your-own-LLM to your own specialized models. See [business case](02-business-case-and-strategy.md) for the competitive read on why none of the incumbents (Scale, Snorkel, Labelbox, Refuel) occupies this exact position.

## 4. Why now

1. **The buyer just appeared.** The LLM post-training boom created a large, technical, well-funded buyer who needs SFT/eval/preference data *this quarter* and understands exactly why label quality gates model quality. This buyer didn't exist at scale two years ago.
2. **The capability is good enough — and commoditizing.** Frontier LLMs are now accurate enough to auto-label the easy majority of text tasks, and the cost is falling. The raw labeling capability is becoming free; the value migrates to trust and to the flywheel. Refuel's $5.2M outcome is the proof that the capability alone is not defensible.
3. **The "VS Code" is sitting there, open-source.** Label Studio (HumanSignal, ~$50M raised; [Crunchbase](https://www.crunchbase.com/organization/humansignal)) is a mature, widely-adopted OSS annotation platform. As with Cursor and VS Code, a small team can fork a proven editor and put 100% of its energy into the differentiated layer instead of reinventing annotation UI.

## 5. Target users & personas

**Primary — ML/AI engineer building fine-tunes & evals.**
- *Job to be done:* "Turn my pile of raw text into a high-quality, trustworthy labeled dataset for SFT / preference / eval **without** burning a week hand-labeling or babysitting an outsourcing vendor."
- *Current painful workaround:* a Jupyter notebook that calls an LLM over the dataset, followed by manually spot-checking a few hundred rows, getting nervous, and re-reviewing far more than they should. Or a spreadsheet shared with a couple of contractors. No calibrated notion of which labels are safe.

**Secondary — data-team lead.**
- *Job to be done:* "Run a labeling project across my team, hit a defensible quality bar, and report cost/throughput/quality to stakeholders — with an audit trail."
- *Current painful workaround:* stitching Label Studio + spreadsheets + a BPO contract + ad-hoc inter-annotator agreement checks, with quality assurance done by gut feel and the occasional manual audit.

**Later — enterprise data-ops (text classification / NER, moderation, compliance).**
- *Job to be done:* "Continuously label a high-volume operational stream (tickets, intent, moderation, compliance) at a guaranteed quality SLA, on-prem, without sending data to a third party."
- *Current painful workaround:* a large outsourced human-labeling operation plus internal review, with privacy and consistency both perennial headaches.

We build for the primary persona first; the others come along the [expansion order](#7-beachhead--expansion-order).

## 6. Product shape & the core loop

Tessera is a project-oriented web app. You create a project, point it at a raw dataset, define a **rubric** (the labeling task + taxonomy + decision rules — see [architecture](03-system-architecture.md)), and run the loop. The product's job is to make the auto-applied slice large *and* trustworthy while making the human slice as cheap as possible to clear.

The heart of the system is a **confidence gate**: a calibrated threshold, set against a held-out human-labeled **gold set**, above which the model's labels are auto-applied because they hit your target precision (e.g. ≥98%). Everything below the line goes to a human. The full mechanism is specified in the [accuracy & trust engine](04-accuracy-and-trust-engine.md); the flywheel in the [model strategy](05-data-flywheel-and-model-strategy.md).

```
                            ┌──────────────────────────────────────────────────┐
                            │                                                    │
   raw dataset ──► rubric ──► labeler model ──► confidence gate (calibrated      │
                  (task,      (BYO LLM /         vs. gold set, target precision)  │
                   taxonomy,  distilled)                │                        │
                   rules)                               │                        │
                                          ┌─────────────┴──────────────┐         │
                                  above the line                 below the line  │
                                          │                              │       │
                                          ▼                              ▼       │
                                  AUTO-APPLY                       HUMAN QUEUE    │
                                  + ~2% audit sample        (Cursor-grade UI:     │
                                          │                  pre-filled label,    │
                                          │                  one-key ACCEPT /     │
                                          │                  CORRECT / REJECT)    │
                                          │                              │       │
                                          └──────────────┬───────────────┘       │
                                                         ▼                        │
                                            every interaction logged              │
                                          (accept / edit / reject = gold)         │
                                                         │                        │
                                                         ▼                        │
                                          periodic distillation / retrain ────────┘
                                          (model learns the taxonomy;
                                           coverage@precision rises each run)
```

The loop's promise in one sentence: **"we auto-label 70% of your data at 98% precision; you only touch the hard 30%"** — and next run the model has learned from your corrections, so the auto-applied share climbs and the human share shrinks.

## 7. Beachhead + expansion order

**Beachhead: data for fine-tuning & evaluating LLMs** — SFT pairs, preference/RLHF data, output classification, and eval-set curation. It is the hottest 2026 buyer; the users are technical and immediately *get* the flywheel; the data is text-only (where LLM accuracy is highest, so we can reach a trustworthy SLA fastest); and we can dogfood it on our own model training.

**Expansion order:** LLM data → enterprise **text** classification / NER (tickets, intent, moderation, compliance) → images / audio later. We do **not** lead with vision — text is where the accuracy story is strongest and the time-to-trust shortest.

## 8. Core product principles

1. **Keyboard-first UX.** The human's job is reduced to a stream of single keystrokes (accept / correct / reject) with zero mouse hunting. Throughput per reviewer is a first-class design target, not an afterthought. (Full spec: [HITL UX](07-hitl-ux-spec.md).)
2. **Trust is measured, not asserted.** We never claim accuracy; we *gate* on calibrated confidence and *prove* coverage@precision against a gold set. Every dataset ships with a defensible **quality report.**
3. **Capture every interaction from day one.** Every accept, edit, and reject is logged from the very first project — edits especially are gold-standard corrections. The flywheel is worthless if we don't instrument it before we have anything to train on.
4. **Privacy by default.** A customer's data and corrections train **only their own model**, never a shared one, unless they explicitly opt in. This is non-negotiable and a sales weapon, especially for the enterprise persona.
5. **Stay neutral and un-acquired.** Scale AI became non-neutral the moment Meta took a 49% stake ([TechCrunch](https://techcrunch.com/2025/06/13/scale-ai-confirms-significant-investment-from-meta-says-ceo-alexandr-wang-is-leaving/)). Independence from any single model vendor — bring-your-own-labeler across families — is a structural advantage we protect.

## 9. MVP definition

The MVP is **Phase 0 (Wedge) + Phase 1 (Trust)** from the [implementation plan](08-implementation-and-development-plan.md), which carries the full detail.

**Status (June 2026): built and shipped** — github.com/samdotson61/Tessera (a pure-stdlib implementation; 45 tests + CI). The in-scope list below is delivered, with one divergence noted. In brief:

**In scope:**
- LLM pre-labeling via one bring-your-own-labeler API (Claude / GPT / local), on a custom keyboard-first UI. *(Shipped as a stdlib web UI rather than a Label Studio fork; the fork remains the design target — see [architecture](03-system-architecture.md).)*
- Confidence estimation + calibration against a human-labeled gold set.
- Confidence-gated auto-apply with an adjustable precision slider.
- Keyboard-first review queue: pre-filled label, one-key accept / correct / reject.
- Gold-set eval harness that produces the **coverage@precision** number and a per-dataset quality report.
- Full interaction logging from day one.
- One real text dataset (SFT pairs or text classification) as the proving ground.

**Explicitly out of scope for MVP:**
- Per-customer distilled models (Phase 3) and the cross-task foundation labeler (Phase 4).
- Active-learning routing that retrains mid-project (Phase 2) — MVP logs corrections but defers the closed retrain loop.
- Images / audio / any non-text modality.
- Multi-tenant enterprise admin, SSO, on-prem packaging (follows the enterprise persona later).

## 10. Success metrics

- **North-star: coverage@precision** — the % of the dataset auto-applied at or above the target precision (e.g. ≥98%). Everything else is in service of moving this number up.
- **Activation** — % of new projects that reach a calibrated gate and a first quality report.
- **Time-to-first-trusted-label** — wall-clock from "point at dataset" to "first auto-applied label the user trusts enough to ship." The core promise of speed.
- **Run-over-run human-effort reduction** — the share of examples routed to a human shrinks across successive runs on the same taxonomy. This is the flywheel made visible and is the leading indicator of the moat compounding.
- **Retention** — projects and teams that come back for the next labeling run (the flywheel only pays off with repeat use).

## 11. Non-goals

- We are **not** building a new annotation editor — we fork Label Studio and keep our IP out of the forked UI (see [architecture](03-system-architecture.md)).
- We are **not** a human-labeling services / BPO business; we automate that spend, we don't resell it.
- We are **not** "an LLM that labels data" sold as a thin library — that capability is commoditized (Refuel). Our product is trust + the flywheel.
- We are **not** leading with vision or multimodal labeling.
- We are **not** asserting accuracy by marketing claim; if it isn't gated and audited against a gold set, we don't promise it.

## 12. Open questions

Calibration robustness across taxonomies, correlated-error blind spots in LLM-as-judge verification, the cold-start gold-set burden on new customers, and the economics/timing of the per-customer distilled models are tracked in [risks, open questions & glossary](09-risks-open-questions-and-glossary.md).
