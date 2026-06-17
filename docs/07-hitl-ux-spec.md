# Tessera — Human-in-the-Loop UX Spec

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The interaction design that earns the "Cursor for data labeling" comparison: the screens, gestures, keyboard map, and latency budgets that make reviewing labels feel like tab-to-accept.

Last updated: June 2026

---

## 1. UX principles

These are non-negotiable. Every screen below is a consequence of them.

1. **Keyboard-first, mouse-optional.** A reviewer should be able to fly through an entire queue without touching the mouse. The hands stay on the home row; decisions are single keystrokes. The mouse is for exceptions (selecting a span, dragging a slider), never for the hot path.
2. **Speed/throughput is the headline metric.** The number we put on screen and optimize for is **items decided per minute**, not "accuracy of the tool." The whole UI is a stopwatch in disguise. If a design choice adds a keystroke to the common case, it loses.
3. **Trust is made visible.** Confidence, coverage, and precision are first-class UI — not buried in a settings panel. The user should always be able to answer "how much of this did the model do, and how sure are we?" without leaving the screen they're on. See [accuracy & trust engine](04-accuracy-and-trust-engine.md).
4. **Minimal mode-switching.** No "edit mode" vs "review mode." The pre-fill is already there; you either accept it or you don't. Correcting is the same gesture as labeling-from-scratch, not a separate flow.
5. **Never hide low confidence.** Uncertain, out-of-distribution (OOD), and rare-class items are *surfaced louder*, not quietly auto-applied. A reviewer's attention is the scarce resource; we spend it exactly where the model is weak. Rare classes are flagged so they're never silently accepted.
6. **The model assists, the human decides.** The pre-fill is a suggestion with an escape hatch on every item. We never make a decision the human can't see and override in one keystroke.

**The explicit Cursor parallel.** In Cursor, the editor shows you greyed-out code; you press `Tab` to accept or keep typing to override. The cognitive unit drops from "write this line" to "judge this line." Tessera ports that exactly: the model shows you a greyed-in label; you press `Enter`/`Tab` to accept or type to correct. The unit of human effort drops from **"produce a label"** to **"judge a label."** Everything else — the queue ordering, the confidence chrome, the dashboards — exists to protect that single gesture.

## 2. The signature interaction

The atomic loop, described precisely:

```
  ┌─────────────────────────────────────────────┐
  │  Item N of 1,248  ·  ~9m left  ·  142/min     │
  ├─────────────────────────────────────────────┤
  │  "The invoice total doesn't match the PO."    │
  │                                               │
  │  ▸ Prediction:  [ billing_dispute ]  92% ███▌ │   ← greyed pre-fill
  │    press Enter to accept · type to correct · ? why│
  └─────────────────────────────────────────────┘
```

- The **pre-filled label is already in the answer field**, rendered in a muted/greyed style (the Cursor "ghost text" affordance) so it reads as a *suggestion*, not a committed value.
- **Accept:** `Enter` (or `Tab`) commits the pre-fill and **auto-advances** to the next item. One keystroke = one decision. This is the 80% path and must feel frictionless.
- **Correct:** the reviewer just **starts typing**. The first keystroke clears the ghost text and opens a fuzzy class-picker filtered to the taxonomy; `Enter` commits the correction and advances. For span/preference tasks the "type to correct" maps to the task's native correction gesture (drag a span, press `A`/`B`) — see 3(c).
- **Reject / skip:** `X` rejects (sends to a deeper-review bucket) and advances; `S` skips without deciding.
- Every correction is captured as a `(prediction, human_label, was_corrected)` triple — this stream is the flywheel's fuel (see [data model & API contracts](06-data-model-and-api-contracts.md), `annotation_events`).

**Latency budget (estimate) — what makes it feel instant.** The felt-instant ceiling is the classic ~100 ms; we budget under it for the hot path:

| Stage | Target |
|---|---|
| Keystroke → label committed locally (optimistic) | < 16 ms (estimate) |
| Commit → next item painted from prefetch buffer | < 50 ms (estimate) |
| **Keystroke → next item interactive (perceived)** | **< 80 ms (estimate)** |
| Network write of the decision | async, off the hot path |

The trick is **prefetch + optimistic commit**: the client keeps the next 10–20 items (and their predictions) buffered locally, commits the decision to the UI immediately, and flushes writes asynchronously. The reviewer never waits on the network; a failed write reconciles silently and re-queues the item. Predictions are precomputed by the batch labeler, so the queue is never blocked on inference.

## 3. Core screens

### (a) Project / dataset setup & connect

```
  ┌──────────────────────────────────────────────────────────┐
  │  New project ▸ Connect data                                │
  │  ┌────────┐ ┌────────────┐ ┌──────┐ ┌──────────────────┐  │
  │  │ S3/GCS │ │ Hugging Face│ │ CSV  │ │ JSONL upload     │  │
  │  └────────┘ └────────────┘ └──────┘ └──────────────────┘  │
  │  s3://acme-data/tickets/*.jsonl            [ Connect ▸ ]   │
  │  ─────────────────────────────────────────────────────    │
  │  Detected 48,201 rows · text field: ⟨ body ⟩  ▾           │
  │  Preview:  "The invoice total doesn't match…"             │
  │  Task type:  ( ) single-class  ( ) multi-class            │
  │              ( ) span/NER     (•) pairwise A/B            │
  └──────────────────────────────────────────────────────────┘
```

**Interaction notes.** Connectors are S3/GCS, Hugging Face datasets, CSV, and JSONL upload. On connect we sample-parse, auto-detect the text column (user-overridable), and ask the one question that branches the whole UI: **task type** (the three beachhead types below + "other later"). Everything after this is task-aware. No schema modal walls — the user is reviewing sample rows within seconds.

### (b) Rubric / Taxonomy editor

```
  ┌──────────────────────────────────────────────────────────┐
  │  Rubric: Support intent                          [ Save ] │
  │  Classes                          Guidelines              │
  │  • billing_dispute   ┐  "Customer contests a charge,      │
  │  • refund_request    │   invoice, or amount owed.         │
  │  • how_to            │   NOT a refund (see refund_request)│
  │  • bug_report        ┘   Examples: x "…total is wrong"    │
  │  + add class                       - "…want my money back"│
  │  ──────────────────────────────────────────────────────  │
  │  ! 23 items are where labels disagree — clarify the rule. │
  │     ▸ "I was double-charged then want it reversed"        │
  │       model: billing_dispute (.51) · annotator: refund    │
  │       [ It's billing ] [ It's refund ] [ Add a rule ▸ ]   │
  └──────────────────────────────────────────────────────────┘
```

**Interaction notes — the ambiguity-surfacing flow.** The rubric editor is part of *trust*, not just setup. The taxonomy is a structured tree (classes, optional nesting, per-class guidelines with x/- examples). Its signature feature is the **disagreement banner**: the system continuously mines items where models disagree with each other, with annotators, or with gold, clusters them by the boundary they straddle, and presents them as *"these N items are where labels disagree — clarify the rule."* Resolving one is two clicks (pick the right side) plus an optional one-line rule that gets appended to the guideline and **fed back into the labeler's prompt/gold set**. This is how a fuzzy taxonomy sharpens over a project rather than rotting. See [accuracy engine](04-accuracy-and-trust-engine.md) for how disagreement is computed.

### (c) The review queue — the core screen

This is the screen the product lives or dies on. The list is **ordered by the active-learning router** (uncertainty × informativeness × representativeness — see [accuracy engine](04-accuracy-and-trust-engine.md)), so the most decision-worthy item is always on top.

```
  ┌───────────────┬──────────────────────────────────────────────┐
  │ QUEUE  1,248  │  Item #4471          conf 0.92 ███▌  Enter accept │
  │ ───────────── │  ──────────────────────────────────────────  │
  │ ▸#4471 .92 bd │  "The invoice total doesn't match the PO and  │
  │  #4472 .61 !  │   I've been charged twice this month."        │
  │  #4473 .55 !? │                                               │
  │  #4474 .98 bd │  Prediction ▸ [ billing_dispute ]   ▾         │
  │  #4475 .49 *  │  why? ▸ "mentions invoice mismatch + charge;  │
  │   …rare class │          no refund language" (toggle: e)      │
  │ ───────────── │  ──────────────────────────────────────────  │
  │ filter: all ▾ │  [Enter accept] [type correct] [x reject] [s skip]│
  │ *=rare !=low  │  j ↓  k ↑   ·   142/min · ~9m left            │
  └───────────────┴──────────────────────────────────────────────┘
```

**Interaction notes.**
- **Item + pre-filled label + confidence + rationale.** Each item shows the source text, the ghost-text prediction, a confidence chip (number + bar; see §5), and an **"explain" toggle (`E`)** that reveals the model's one-line rationale / top evidence tokens. Explain is *collapsed by default* — it costs attention, so it's opt-in per item or pinned-open as a preference.
- **Accept / correct / reject / skip** as in §2.
- **`J` / `K`** move next/previous without deciding (review-before-commit); the list pane mirrors position.
- **Bulk-accept a filtered view.** Filter the queue (e.g. `class:invoice conf:≥0.95`), then `Shift+Enter` → *"Accept all 247 'invoice' predictions ≥ 0.95?"* with a one-keystroke confirm. The throughput multiplier (see §6).
- **Diff view.** When an item was previously labeled (re-run, or model-vs-gold), a `D` toggle shows a side-by-side **old → new** diff with changed tokens/classes highlighted, so corrections on re-labeled data are auditable at a glance.
- **Rare-class & OOD markers** (`*`, `!?`) are rendered in the list rail so the reviewer sees *why* an item is queued before they reach it.

**How each beachhead label type renders in the answer pane:**

*Single / multi-class classification* — ghost-text chip(s); `Enter` accepts; typing opens fuzzy picker; for multi-class, number keys `1…9` toggle the top predicted classes.
```
  Prediction ▸ [ billing_dispute ]      Enter accept · type to change
  multi:  [x billing] [ refund ] [x urgent ]    (1/2/3 toggle)
```

*Span / NER highlighting* — predicted spans are pre-highlighted inline; `Enter` accepts all; `Tab` cycles spans; correcting = drag to reselect or `Backspace` to drop a span; `L` re-labels the focused span via the type picker.
```
  "Contact ⟦John Doe⟧ᴘᴇʀ at ⟦Acme Corp⟧ᴏʀɢ before ⟦Friday⟧ᴅᴀᴛᴇ."
  focused: ⟦Acme Corp⟧ ORG 0.88   Enter accept all · ⇥ next span · l relabel
```

*Pairwise / preference (A-vs-B)* — two responses side by side, the model's predicted winner highlighted with its margin; `A` / `B` pick a side, `=` ties, `Enter` accepts the prediction. For subjective tasks the **annotator-disagreement signal is shown** ("3 of 5 prefer A") so the reviewer judges with the spread in view rather than a false binary.
```
  ┌── A ──────────────┐  ┌── B ──────────────┐
  │ "Here's a step-by-│  │ "Just google it." │   predicted: A (margin .31)
  │  step fix…"        │  │                   │   disagreement: 3/5 → A
  └───────────────────┘  └───────────────────┘   a=A  b=B  = tie  Enter accept
```

### (d) The Trust / Coverage dashboard

```
  ┌──────────────────────────────────────────────────────────┐
  │  Trust                                                     │
  │  Target precision  ├────────●──────┤  0.95                 │
  │                   0.80          0.99   (drag = retune)      │
  │  ─────────────────────────────────────────────────────    │
  │  Coverage @ 0.95 precision:  71%  (34,223 auto-applied)   │
  │  Human queue:                29%  (13,978 items)          │
  │  Est. time to finish:        ~1h 38m at 142/min           │
  │                                                            │
  │  coverage│        ___________                              │
  │   @prec  │      _/           ‾‾‾‾●(you)                    │
  │          │   __/                    ‾‾‾‾\___               │
  │          └────────────────────────────────────  precision │
  │            0.80      0.90      0.95      0.99               │
  └──────────────────────────────────────────────────────────┘
```

**Interaction notes.** The **precision-target slider** is the product's main control surface — it directly sets the confidence gate (calibrated on the gold set; see [accuracy engine](04-accuracy-and-trust-engine.md)). Dragging it **live-updates** coverage, auto-applied count, queue size, and est. time-to-finish, and the marker moves along the **coverage@precision curve** (the north-star metric, plotted). The narrative the founder-designer should feel: *"slide it right for more trust → smaller auto-set, bigger human queue; slide left for more speed."* This screen is where COVERAGE@PRECISION stops being a metric in a doc and becomes a thing you can grab.

### (e) Gold-set / audit review

```
  ┌──────────────────────────────────────────────────────────┐
  │  Gold set (52 / 100 labeled)   ·  building calibration…   │
  │  These are labeled by YOU and define ground truth.        │
  │  [ same review gesture as the queue — Enter / type / x ]      │
  │  ──────────────────────────────────────────────────────  │
  │  Audit  ▸ spot-check auto-applied items                   │
  │  Sampling 200 of 34,223 auto-applied (random + boundary)  │
  │  Found 4 disagreements → measured precision 0.962 ≥ 0.95 (pass)  │
  │  [ view the 4 ] [ re-tune slider ] [ accept audit ]       │
  └──────────────────────────────────────────────────────────┘
```

**Interaction notes.** Two related jobs share one screen. **Gold labeling** uses the *identical* review gesture (no special mode) to label the bootstrap sample that calibrates confidence. **Audit** draws a sample of *auto-applied* items (random + near-the-boundary stratified), shows them with the same accept/correct gesture, and reports the **empirically measured precision** against the target — the trust receipt. If the audit comes in under target, one click re-tunes the slider conservatively.

### (f) Quality report + export

```
  ┌──────────────────────────────────────────────────────────┐
  │  Export ▸ Support intent · 48,201 items                   │
  │  Format: ( ) JSONL  (•) HF dataset  ( ) CSV  ( ) Parquet  │
  │  Include: [x] labels [x] confidence [x] provenance        │
  │  ──────────────────────────────────────────────────────  │
  │  QUALITY REPORT                                            │
  │   • Coverage: 71% auto · 29% human-reviewed               │
  │   • Precision by class:  billing .97 · refund .94 !       │
  │   • Audit: measured 0.962 over 200-item sample            │
  │   • Known failure modes: refund↔billing boundary (23)     │
  │   [ Download report.md ]   [ Export dataset ▸ ]           │
  └──────────────────────────────────────────────────────────┘
```

**Interaction notes.** Export emits the **labeled dataset** (JSONL / HF / CSV / Parquet, each row carrying label + confidence + provenance) plus a standalone **quality report**: precision by class, coverage, audit results, and *known failure modes* (the unresolved disagreement clusters from 3(b)). The report is the artifact a buyer hands to their boss to justify shipping without re-review — it's a core trust deliverable, not an afterthought.

## 4. Full keyboard map

| Key | Action |
|---|---|
| `Enter` / `Tab` | Accept the pre-filled label, advance |
| *type a letter* | Begin correcting → open fuzzy class picker |
| `1`–`9` | Toggle / select the Nth predicted class (multi-class) |
| `A` / `B` / `=` | Pick A, pick B, or tie (pairwise/preference) |
| `X` | Reject → send to deep-review bucket, advance |
| `S` | Skip without deciding |
| `J` / `K` | Next / previous item (no decision) |
| `E` | Toggle the "explain" / rationale reveal |
| `D` | Toggle diff view (old → new label) |
| `L` | Re-label the focused span (span/NER) |
| `⇥` / `Shift+⇥` | Next / previous span (span/NER) |
| `Backspace` | Drop the focused span (span/NER) |
| `U` | Undo last decision |
| `Shift+Enter` | Bulk-accept the current filtered view |
| `/` | Focus the filter / command bar |
| `G` | Go to gold-set / audit view |
| `?` | Show keyboard cheatsheet overlay |

The map is **user-remappable** and the cheatsheet (`?`) is always one key away. Defaults are chosen so the 80% hot path (`Enter`, type, `J`/`K`) sits under the right hand without reaching.

## 5. Confidence & rationale display patterns

The hard problem is showing confidence **without overload** — a number on every row becomes noise. Guidance:

- **Encode confidence redundantly but quietly.** A short bar + the numeric score, co-located with the label chip. Color is a *secondary* channel, never the only one (accessibility, §8). Suggested affordances: **high** (≥ target) = calm/neutral, no alarm; **borderline** (just under target) = amber chip + `!`; **low / OOD** = stronger marker (`!?`) and the item floated up the queue.
- **Rare classes get a dedicated glyph** (`*`), never a color alone, and are never inside the auto-applied set — they always reach a human.
- **The "explain" reveal is progressive disclosure.** Collapsed by default (a `why?` affordance + `E`); expanded it shows a one-line rationale and, where available, highlighted evidence tokens in the source text. The reviewer pulls explanation when a prediction surprises them — it's not shoved at them on every easy item.
- **Confidence is *calibrated*, and we say so.** The chip reflects the calibrated probability the gate uses, not a raw softmax — the dashboard's audited precision is the proof the number means what it says (see [accuracy engine](04-accuracy-and-trust-engine.md)).

## 6. Bulk operations & filtered views

Single-item flow is the floor; **bulk is the throughput multiplier.** The command/filter bar (`/`) composes predicates over the queue: `class:`, `conf:`, `status:`, `disagree:`, full-text. Any filtered view supports:

- **Bulk-accept** (`Shift+Enter`): *"accept all 'invoice' predictions ≥ 0.95"* — one confirm commits hundreds of decisions. The canonical move for a high-confidence cluster the reviewer has sampled and trusts.
- **Bulk-reassign / relabel**: set a class across a filtered selection (e.g. fixing a systematic boundary error after a rubric clarification).
- **Saved views**: "borderline refunds," "OOD," "rare classes" persist as one-key filters.

Bulk actions are **always reversible** (`U` / an undo toast) and **logged to the audit trail**, so speed never costs accountability. The mental model: *sample a cluster in single-item mode to build trust, then bulk-accept the rest.*

## 7. Empty / cold-start states

The first five minutes decide adoption, so cold-start is designed, not defaulted.

- **No gold yet → bootstrap flow.** On a fresh project there's no calibration, so the dashboard slider is disabled with a clear call to action: *"Label 50–100 items to calibrate trust."* The system seeds this set with a diverse/representative sample (not the first N rows) and the user labels them with the normal gesture. Once enough gold exists, the slider unlocks and the coverage@precision curve appears — a deliberate **"aha" moment** where the auto-apply count snaps into being. See [accuracy engine](04-accuracy-and-trust-engine.md) for the bootstrap math.
- **No model / no key → BYO key.** Day-one capability is **bring-your-own-labeler**: a friendly prompt to paste an API key (Claude / GPT / a local endpoint) before the first batch runs. No key, no labels — so this empty state is a single, unmissable card, not a buried setting. As the flywheel kicks in, this transparently graduates to the customer's specialized model (see [data flywheel](05-data-flywheel-and-model-strategy.md)) with no UX change.
- **Empty queue (done!)** → a celebratory terminal state that routes straight to the **export / quality report** (3f), closing the loop.

## 8. Speed & accessibility budgets

**Speed budgets (estimate).**

| Budget | Target |
|---|---|
| Keystroke → next item interactive | < 80 ms (estimate) |
| Prediction prefetch buffer depth | 10–20 items (estimate) |
| Bulk-accept confirm → committed (1k items) | < 1 s, optimistic (estimate) |
| Slider drag → dashboard recompute | < 150 ms, from cached curve (estimate) |
| Sustained reviewer throughput (text classification) | 100–180 items/min (estimate) |

**Accessibility budgets.**

- **Full keyboard navigability is a hard requirement**, not a nice-to-have — it's literally the product thesis. Every action in §4 is reachable without a mouse; focus order is logical; the cheatsheet (`?`) documents it.
- **Color is never load-bearing.** Confidence/rare/OOD states always pair color with a glyph or text (§5); contrast targets WCAG AA (estimate).
- **Screen-reader announcements** for state changes (label committed, item N of M, audit result) so the keyboard-first flow is also usable non-visually (estimate).
- **Respect `prefers-reduced-motion`**: auto-advance and ghost-text transitions degrade to instant cuts.

These budgets are the contract behind the Cursor comparison: if the loop isn't *felt* as instant and fully keyboard-driven, the product is just another annotation tool. The implementation plan in [implementation & development plan](08-implementation-and-development-plan.md) sequences the build so the review-queue hot path (3c) and the trust slider (3d) ship first — they are the wedge.
