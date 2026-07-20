# Case study: OSS issue triage — the partner-engagement dress rehearsal

*2026-07-20 · Tessera v0.12.x · fully local (Qwen3.5-4B via llama-server, $0) ·
single annotator · corpus: 438 microsoft/vscode issues (2026), maintainer
labels held back as truth*

This is the rehearsal docs/08 §4.3 asks for before a real partner dataset: run
the entire engagement protocol — rubric authoring, cold-start gold, the loop,
review, audit — on a taxonomy we don't control, then score every layer against
the label authority (the repo's maintainers). It produced the two findings a
real engagement most needs, plus one the harness earned the hard way.

## 1. Setup

- **Corpus:** 438 issues with exactly one maintainer kind-label (170 `bug`,
  170 `feature-request`, 98 `*question`), created ≥ 2026-01-01, fetched with
  [`scripts/fetch_github_issues.py`](../scripts/fetch_github_issues.py); text =
  title + body (1,400 chars). Maintainer labels went into a truth file that
  stayed unopened until every human decision was committed.
- **Rubric v1:** written from the repo's public conventions before seeing
  items (bug = broken; feature-request = new capability; question = help).
- **Protocol:** `tessera bootstrap` picked a cluster-stratified 105-item
  sample; the annotator authored gold in the UI flow (67 bug / 29
  feature-request / 9 question). One `label` run (logprob head + consensus +
  10% audit, 90% target, 7.5 min for 438 items), then the human phase: the
  full 28-item audit stream and 60 queue items in router order, corrections
  growing gold 105 → 175, then a re-gate.

## 2. Finding 1 — the annotator-vs-authority gap (survives everything)

The truth file's verdict on the *humans*: **the annotator's own 105 gold
labels agree with the maintainers 69.5% of the time.** The dominant pattern
(18 of 32 disagreements): issues *phrased* as bug reports whose cause is the
author's own code, environment, or coursework — which VS Code triagers close
as `*question`, and which a phrasing-based rubric calls `bug`. The system
faithfully executed the rubric it was given (on auto items the annotator also
judged, the gate agreed with the annotator 97.8%) — while every internal
metric stayed blind to the convention gap, because the audit stream can only
check the promise against its own gold authority. On the working model
(§4), the same ceiling reappears: ~72–74% vs maintainers under rubric v1.

## 3. Finding 2 — the harness caught a mute model (and grew a guard)

A counterfactual arm produced numbers too strange to accept, and inspection
of the raw responses explained everything: on these long items, Qwen3.5 on a
raw llama-server *verbalizes deliberation* — empty `content`, first token
literally `"Thinking"` — so the logprob head got **no usable signal on
434 of 438 items**, silently voting uniform. The entire first pass had run on
the consensus specialist alone, end to end, with plausible coverage, honest-
looking CV, a working audit stream — and nothing flagged that one ensemble
member was dead. (`--reasoning-budget 0` and `/no_think` do *not* stop the
prose variant; `--chat-template-kwargs '{"enable_thinking":false}'` does.
The AG News and intents benchmark caches were audited: 0 empty responses —
short items never triggered it. The v0.10.0 SMS numbers were affected; see
the CHANGELOG erratum and §5.)

Two permanent products: the **serving recipe** (README), and the
**no-signal guard** (v0.12.1) — every run now counts labeler responses that
carried no signal (`n_no_signal`), and the report refuses to be quiet when
the share is meaningful. A mute labeler must never look like a quiet success.

## 4. Finding 3 — the rubric carries the convention (measured, fixed server)

All arms re-run against the corrected serving stack, target 90%, validated
against maintainer truth:

| arm (gold / rubric) | coverage | true vs maintainers (unseen) |
|---|---|---|
| annotator gold / rubric v1 | 100% | 72.2% (73.9%) |
| maintainer gold / rubric v1 | 11.0% | 100% (100%, n=9) |
| **maintainer gold / rubric v2** | **90.9%** | **82.7% (79.7%)** |
| maintainer gold / rubric v2, LLM only | 0% — honest refusal | — |
| maintainer gold / rubric v1, LLM only | 5.3% | 95.7% (95.0%) |

- **Under a rubric that contradicts its gold authority, the gate collapses
  honestly** (11% at 100% true): the model's confidence can't certify the
  maintainers' convention, so almost nothing clears.
- **Rewriting one paragraph** — "a help-seeking report whose cause is the
  author's own code/env/coursework is a `question` even when phrased as a
  bug; work-item specs are `feature-request`" — **took coverage 11% → 90.9%**
  at 82.7% true (79.7% unseen). The remaining gap to the 90% promise is
  organizational knowledge no text-reader has (maintainers know what is
  *supposed* to work) plus a 48-item calibration base — the growth path is
  the ordinary one: partner gold accumulates, audits feed it, the gate
  re-prices.
- **The consensus gate is the coverage lever on real partner data too**:
  the LLM alone refuses (0%); with the specialist co-signal it ships 90.9%.

## 5. The corrected SMS numbers (erratum companion)

Re-measured on the fixed stack, hidden-reference truth, both model tiers
(near-duplicate propagation on; all 4 propagated members match the
reference):

| run | target | coverage | true (unseen) |
|---|---|---|---|
| 4B logprob alone | 90% / 95% | 100% | 95.7% (95.8%) |
| **4B + consensus** | 90% / 95% | **100%** | **97.0% (96.7%)** |
| 2B logprob alone | 90% | 59.0% | 78.0% (74.0%) — promise broken |
| 2B logprob alone | 95% | 0% — honest refusal | — |
| **2B + consensus** | 90% | **100%** | **93.0% (92.5%)** |
| **2B + consensus** | 95% | **87.0%** | **98.9% (99.0%)** |

The v0.10.0 "plain logprob over-promises / consensus catches it" narrative
was measured against the mute model (its "labels" were the ham base rate) —
but the corrected 2B arms show the same shape for real: the small model's
own confidence breaks the 90% promise by 12 points, invisible to its
60-gold CV, and the consensus co-signal is what makes it honest. On the 2B
the 95% target is the sweet spot (13 points of coverage buys ~6 of
precision — 87% of the corpus at ~99%). What the original session *actually*
demonstrated is that the safety stack — consensus routing, audit sampling,
gold-growth collapse — correctly contained a broken labeler nobody knew was
broken.

## 6. Protocol for the real engagement

1. **Session zero is rubric calibration, not labeling.** Co-label 30–50 of
   the partner's own historically labeled items; reconcile every
   disagreement into rubric text. The 69.5% annotator-vs-authority gap was
   invisible to every internal metric.
2. **The partner's conventions live in the rubric; gold sharpens what the
   rubric states.** Measured: convention-in-rubric moved coverage 11% →
   90.9%; gold alone couldn't (and under the broken model, gold under a
   divergent rubric produced the study's only over-claim).
3. **Verify the serving stack before trusting any number** — one canary item
   per run; the `n_no_signal` guard now does this continuously.
4. **Use their history as truth-on-tap.** Any partner with a labeled backlog
   gives you a held-back truth file for free; `fetch_github_issues.py` is
   that harness for public repos.

## 7. Reproduce it

```bash
python scripts/fetch_github_issues.py --repo microsoft/vscode \
    --labels 'bug,feature-request,*question' --per-label 170 --since 2026-01-01 --out triage
# serve the model with reasoning OFF (load-bearing — see README):
llama-server -m Qwen3.5-4B-Q4_K_M.gguf --port 8091 -np 4 -c 16384 \
    --chat-template-kwargs '{"enable_thinking":false}'
python -m tessera --db triage.db bootstrap --data triage/items.jsonl \
    --taxonomy your_rubric.json --dataset triage --n 105       # author gold in the UI
TESSERA_PROVIDER=openai TESSERA_OPENAI_URL=http://127.0.0.1:8091/v1/chat/completions \
TESSERA_MODEL=q TESSERA_LOGPROBS=1 TESSERA_AUDIT_RATE=0.1 \
python -m tessera --db triage.db label --data triage/items.jsonl \
    --taxonomy your_rubric.json --dataset triage --target 0.90
python -m tessera --db triage.db serve --dataset triage        # work audits + queue
python scripts/validate_run.py --db triage.db --dataset triage \
    --truth triage/truth.jsonl                                 # only after review
```

*Honest limitations: one annotator (no true IAA), one model family, one
public repo whose `*question` label encodes "not our bug" as much as "asked
a question"; maintainer labels are themselves noisy; the rubric-v2 arm was
authored after seeing the error decomposition, as a kickoff session would
be. The first-pass human phase (audit 89.3% confirmed, gate-vs-annotator
97.8%) measured a specialist-only system — reported here as what it was.*
