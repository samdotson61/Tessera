# Tessera — Data Model & API Contracts

*Part of the Tessera design suite — see [README](README.md). Tessera is a working codename; rename at will.*

The entities, schemas, and the REST/SDK surface the whole system is built on — the contract every other doc compiles against.

Last updated: June 2026

> **Status (June 2026):** implemented in the MVP as a pure-stdlib subset — Python dataclasses + SQLite, with the flywheel event schema as built. The Postgres / object-store / vector-DB split and the FastAPI REST surface below are the production target. See the [suite status](README.md).

---

## 1. Entity overview

Everything in Tessera hangs off a small set of nouns. An **Organization** (tenant) owns **Projects**; a Project binds one **Dataset** (a versioned collection of **Items**, the atomic units to label) to one **Taxonomy/Rubric** (a versioned definition of the labeling task). A **LabelJob** runs a labeler **Model** over a slice of the dataset and produces **Labels**, each bound to a specific `taxonomy_version`. Every model decision and every human action emits one **Flywheel Event** to an append-only log — the raw material for both analytics and the distillation flywheel (see [flywheel](05-data-flywheel-and-model-strategy.md)). **GoldItems** are held-out human ground truth; a **CalibrationModel** (one per taxonomy version) maps raw model confidence to a calibrated probability against that gold; and a **QualityReport** is the per-run, gold-audited summary that carries the north-star number, **coverage@precision**.

```
                                  ┌────────────────┐
                                  │  Organization  │ (tenant)
                                  └───────┬────────┘
                       ┌──────────────────┼──────────────────┐
                       │                  │                  │
                   ┌───▼───┐         ┌────▼────┐         ┌────▼────────┐
                   │ User  │         │ Project │◄────────┤ Model       │
                   └───────┘         └────┬────┘  uses   │ registry    │
                                          │             │(champ/chall)│
                   binds 1 Dataset @ ver  │             └────┬────────┘
                   + 1 Taxonomy @ ver     │                  │ scores
                       ┌──────────────────┼───────────┐      │
                       │                  │           │      │
                  ┌────▼─────┐      ┌──────▼──────┐  ┌─▼──────▼──┐
                  │ Dataset  │      │  Taxonomy   │  │ LabelJob  │
                  │ (versioned)     │ /Rubric     │  │ /Run      │
                  └────┬─────┘      │ (versioned) │  └─────┬─────┘
                       │ 1..N       └──────┬──────┘        │ produces
                  ┌────▼─────┐             │ governs       │
                  │  Item    │◄────────────┼───────────────┤
                  └────┬─────┘   label binds to a          │
                       │         taxonomy_version          │
              ┌────────┼─────────────┐                     │
         ┌────▼────┐ ┌─▼────────┐  ┌─▼──────────┐    ┌──────▼───────┐
         │ Label   │ │ GoldItem │  │ Flywheel   │    │ Calibration  │
         │(per tax │ │ (held-out│  │ Event      │    │ Model        │
         │ version)│ │  truth)  │  │(append-only│    │ (per tax ver)│
         └─────────┘ └────┬─────┘  │  log)      │    └──────┬───────┘
                          │        └─────┬──────┘           │
                          └──────────────┴──────────────────┤
                                  feed                       ▼
                                                     ┌───────────────┐
                                                     │ QualityReport │
                                                     │ coverage@prec │
                                                     └───────────────┘
```

Read the arrows as: Org → owns → Project → binds → (Dataset@ver, Taxonomy@ver) → LabelJob runs a Model → emits Events + writes Labels → GoldItems + CalibrationModel + Events roll up into a QualityReport.

## 2. Core entity schemas

Schemas below are a typed pseudo-schema (JSON-ish; `?` = optional, `[]` = array, `enum(...)` = closed set). IDs are prefixed ULIDs (e.g. `proj_01H…`) so they sort by creation time. All entities carry `created_at`/`updated_at` (omitted below for brevity) and a `org_id` for tenant isolation (also omitted on children of Project).

### Organization & User

```jsonc
Organization {
  id: string,                    // org_…
  name: string,
  plan: enum(free, team, enterprise),
  default_target_precision: float,   // 0..1, inherited by new projects, e.g. 0.98
  privacy_mode: enum(isolated, shared_optin),  // gates flywheel data reuse; default isolated
  region: string                 // data-residency / on-prem hint
}

User {
  id: string,                    // user_…
  org_id: string,
  email: string,
  role: enum(owner, admin, labeler, viewer),
  annotator_id_hash: string      // stable hash used in event log; never the raw user id
}
```

`annotator_id_hash` exists so the flywheel can attribute and de-bias by annotator without putting personally identifying data in the analytics lake.

### Project, Dataset, Item

```jsonc
Project {
  id: string,                    // proj_…
  org_id: string,
  name: string,
  dataset_id: string,            // the bound dataset
  active_dataset_version: string,
  active_taxonomy_version: string,
  target_precision: float,       // the SLA knob; see §6 set-target-precision
  champion_model_id: string,     // current production labeler for this project
  challenger_model_id?: string   // shadow-scored against champion (see §2 Model)
}

Dataset {
  id: string,                    // ds_…
  project_id: string,
  modality: enum(text, image, audio, multimodal),  // text-first; field is extensible
  source: enum(upload, s3, hf_hub, connector),
  source_uri?: string,
  versions: DatasetVersion[]
}

DatasetVersion {
  version: string,               // monotonic, e.g. "v3" or a lakeFS/DVC commit hash
  parent_version?: string,       // lineage
  item_count: int,
  content_hash: string,          // hash of the manifest; identical content => identical version
  created_by: string,
  note?: string
}

Item {
  id: string,                    // item_…
  dataset_id: string,
  dataset_version: string,       // the version this row first appeared in
  modality: enum(text, image, audio, multimodal),
  input_ref: string,             // S3/MinIO URI for blobs; inline text may be stored directly
  input_inline?: string,         // small text payloads kept in Postgres for query speed
  meta: object,                  // arbitrary source columns (source, lang, length, etc.)
  embedding_ref?: string,        // pointer into the vector DB (Qdrant/LanceDB)
  content_hash: string           // for dedup; see vector DB usage in storage map
}
```

`input_ref` vs `input_inline`: short text is duplicated inline for cheap SQL filtering/preview; anything large (long documents, image/audio bytes) lives in object storage and is referenced. See the storage map in §5.

### Taxonomy / Rubric

The Taxonomy is the most load-bearing entity: it is simultaneously human documentation, the source that **compiles to a model prompt**, and the source that **compiles to a deterministic validator** (a pure function that rejects structurally invalid labels). Authoring this once and compiling both artifacts from it is what keeps the labeler and the checker in lockstep.

```jsonc
Taxonomy {
  id: string,                    // tax_…
  project_id: string,
  label_type: enum(classification_single, classification_multi,
                   span_ner, pairwise, freeform),
  versions: TaxonomyVersion[]
}

TaxonomyVersion {
  version: string,               // monotonic; labels bind to this exact string
  parent_version?: string,
  task_instructions: string,     // the natural-language framing of the job
  labels: LabelDef[],            // the closed label set (omit for freeform)
  constraints: Constraint[],     // HARD rules the validator enforces
  edge_cases: EdgeCaseRule[],    // disambiguation guidance + canonical decisions
  examples: FewShotExample[],    // gold-derived; also used for RAG few-shot retrieval
  compiled_prompt: string,       // generated artifact: the labeler system/user prompt
  validator_spec: object,        // generated artifact: machine-checkable rule set
  status: enum(draft, active, archived)
}

LabelDef {
  key: string,                   // stable machine key, e.g. "toxic"
  display: string,
  definition: string,            // precise meaning — goes verbatim into the prompt
  positive_examples: string[],
  negative_examples: string[]
}

Constraint {
  kind: enum(mutually_exclusive, requires, max_labels,
             span_no_overlap, allowed_values, regex),
  args: object                   // e.g. {"labels": ["a","b"]} for mutually_exclusive
}

EdgeCaseRule { situation: string, decision: string }   // "if sarcasm → label non_toxic"
FewShotExample { item_ref: string, label: Label, why: string }
```

The **validator** is deterministic and runs on every label (model or human) before it is accepted: it checks the value against `label_type` and every `Constraint`. A label that fails the validator can never be auto-applied — it is force-routed to a human regardless of confidence. The **compiled prompt** stitches `task_instructions` + each `LabelDef.definition` + `edge_cases` + retrieved few-shot examples; recompilation happens automatically on any taxonomy edit and bumps the version (§4).

### Label / Annotation

One Label entity covers all first-class types via a discriminated `value`. Classification, span/NER, and pairwise/preference are first-class for the beachhead (LLM fine-tune/eval data); `freeform` covers open-ended generation-with-rationale and keeps the model extensible.

```jsonc
Label {
  id: string,                    // lbl_…
  item_id: string,
  taxonomy_version: string,      // CANONICAL binding — a label is only meaningful w.r.t. a version
  source: enum(model, human, ensemble, weak_supervision),
  value: LabelValue,             // typed per label_type, below
  rationale?: string,            // model_rationale or human_rationale
  confidence_calibrated?: float, // present for model/ensemble sources
  validator_passed: bool,
  status: enum(auto_applied, pending_review, human_final, rejected)
}

// LabelValue is one of:
ClassificationSingle { type: "single",  label_key: string }
ClassificationMulti  { type: "multi",   label_keys: string[] }
SpanNER              { type: "spans",   spans: Span[] }
Pairwise             { type: "pairwise", winner: enum(a, b, tie),
                       a_ref: string, b_ref: string }
Freeform             { type: "freeform", text: string }

Span { start: int, end: int, label_key: string, text: string }  // char offsets into input
```

A `pairwise` Item references two candidate outputs (`a_ref`, `b_ref`) — the natural shape for preference/RLHF data where the unit being labeled is *a comparison*, not a single text.

### LabelJob / Run

```jsonc
LabelJob {
  id: string,                    // job_…
  project_id: string,
  dataset_version: string,
  taxonomy_version: string,
  model_id: string,              // labeler used (resolved via LiteLLM)
  slice: object,                 // filter/sample spec, e.g. {"where": {...}, "limit": 5000}
  mode: enum(label, recalibrate, eval_only),
  status: enum(queued, running, paused, completed, failed),
  progress: { total: int, done: int, auto_applied: int, routed_to_human: int },
  quality_report_id?: string,    // set on completion
  started_at?: timestamp, finished_at?: timestamp
}
```

### GoldItem, Model, CalibrationModel, QualityReport

```jsonc
GoldItem {
  id: string,                    // gold_…
  item_id: string,
  taxonomy_version: string,      // gold is version-specific
  gold_label: LabelValue,
  established_by: enum(consensus, expert, adjudication),
  split: enum(calibration, holdout),  // calibration tunes the gate; holdout audits it
  locked: bool                   // locked gold is excluded from any training input
}

Model {
  id: string,                    // mdl_…
  org_id: string,
  kind: enum(byo_foundation, distilled, foundation_labeler),
  provider_ref: string,          // LiteLLM route, e.g. "anthropic/claude-…" or a private endpoint
  taxonomy_version?: string,     // distilled models are tied to the version they trained on
  status: enum(champion, challenger, candidate, retired),
  metrics?: { coverage_at_precision: float, accuracy_on_gold: float }
}

CalibrationModel {
  id: string,                    // cal_…
  taxonomy_version: string,      // exactly one active per version
  model_id: string,              // the labeler whose confidence it calibrates
  method: enum(isotonic, platt, histogram_binning),
  threshold: float,              // confidence cutoff that hits target_precision on calibration gold
  fitted_at: timestamp,
  gold_calibration_n: int
}

QualityReport {
  id: string,                    // qr_…
  job_id: string,
  taxonomy_version: string,
  target_precision: float,
  coverage_at_precision: float,  // NORTH STAR: share auto-applied at/above target precision
  measured_precision_on_holdout: float,
  auto_applied: int, routed_to_human: int,
  human_action_breakdown: { accept: int, edit: int, reject: int },
  cost_usd: float, est_human_hours_saved: float,
  drift_flag: bool               // calibration looks stale vs recent holdout
}
```

`Model.status` encodes the champion/challenger pattern: the **champion** serves production labels; one or more **challengers** are scored in shadow against the same gold and promoted only when they beat the champion on `coverage_at_precision` (see [flywheel](05-data-flywheel-and-model-strategy.md) and the promote endpoint in §6). The `CalibrationModel.threshold` is the literal confidence gate from the [accuracy engine](04-accuracy-and-trust-engine.md) — it is the number that turns a raw model score into an auto-apply decision.

## 3. The Flywheel Event schema

Every model decision and human action appends exactly one immutable event. This append-only log is the single source of truth for distillation, calibration drift detection, and analytics; entities in §2 are projections that can be rebuilt from it.

```jsonc
FlywheelEvent {
  item_id: string,
  dataset_id: string,
  modality: enum(text, image, audio, multimodal),
  input_ref: string,                  // pointer to the item payload at decision time
  taxonomy_version: string,
  rubric_snapshot: object,            // frozen copy of the taxonomy version actually used
  model_id: string,
  model_label: LabelValue,
  model_rationale: string,
  confidence_raw: float,              // model's native score, pre-calibration
  confidence_calibrated: float,       // after the CalibrationModel
  ensemble_votes: LabelValue[],       // per-member votes when an ensemble labeled
  weak_supervision_votes: object[],   // labeling-function / heuristic votes + names
  routed_to_human: bool,
  route_reason: enum(below_threshold, validator_failed, disagreement,
                     audit_sample, active_learning),
  human_action?: enum(accept, edit, reject),
  final_label: LabelValue,            // what the dataset ends up with
  human_rationale?: string,
  latency_ms: int,
  cost_usd: float,
  timestamp: timestamp,
  annotator_id: string                // HASHED; matches User.annotator_id_hash
}
```

Why the non-obvious fields exist:

- **`rubric_snapshot`** — the taxonomy can be edited after a label is made; the snapshot freezes the *exact* rules used so a historical decision is always interpretable and reproducible.
- **`confidence_raw` AND `confidence_calibrated`** — keeping both lets us re-fit calibration retroactively and audit how well calibration is tracking, rather than throwing away the model's native signal.
- **`ensemble_votes` / `weak_supervision_votes`** — disagreement among voters is itself a routing signal (`route_reason: disagreement`) and a rich training feature; we log the raw votes, not just the aggregate.
- **`route_reason`** — distinguishes *why* a human saw an item (low confidence vs a hard validator failure vs a random audit sample vs active-learning selection); essential for measuring the gate and for not double-counting audit samples as "uncertain."
- **`human_action` = edit** — an **edit is gold**: a human correcting a model is the highest-value training pair in the whole system (see [flywheel](05-data-flywheel-and-model-strategy.md)).
- **`annotator_id` (hashed)** — enables per-annotator agreement and bias analysis without storing identity in the lake.

## 4. Dataset & taxonomy versioning semantics

A **version is an immutable, content-addressed snapshot.** For datasets, a version is a manifest of item references with a `content_hash`; identical content yields an identical version (backed by lakeFS/DVC — see [architecture](03-system-architecture.md)). For taxonomies, a version freezes the label set, definitions, constraints, edge cases, examples, and the two compiled artifacts (`compiled_prompt`, `validator_spec`).

**Labels bind to `taxonomy_version`, not to "the taxonomy."** A Label is only meaningful relative to the exact ruleset that produced it. This is non-negotiable because precision is *defined* against a rubric: a label that was correct under `v2` may be wrong under `v3` if a definition tightened.

**What happens on re-versioning a taxonomy:**

1. Editing any rubric field forks a new `TaxonomyVersion` (draft → active); the prompt and validator are recompiled automatically.
2. Existing labels keep their old `taxonomy_version` — they are **not** silently reinterpreted.
3. The GoldItem set is **re-projected**: gold whose `gold_label` is still structurally valid under the new version carries forward; the rest is flagged for re-adjudication.
4. The CalibrationModel **must be re-fit** against the new version's calibration gold — a stale threshold is invalid, so the project's gate is marked uncalibrated until a `recalibrate` job runs.
5. A fresh `eval_only` (or full `label`) job re-measures **coverage@precision** on holdout gold, producing a new QualityReport. The dashboard surfaces the delta so a rubric change's effect on the SLA is visible.

Re-versioning a **dataset** (new items, fixes) does not invalidate calibration but does require labeling the new slice; the QualityReport is scoped to a `(dataset_version, taxonomy_version)` pair.

## 5. Storage mapping

Each kind of data lives in the store whose access pattern it fits; pointers tie them together (see [architecture](03-system-architecture.md)).

| Data | Store | Why |
|---|---|---|
| Org, User, Project, Dataset/version metadata, Taxonomy versions, Label rows, LabelJob, GoldItem, Model/Calibration registry, QualityReport | **Postgres** | Relational, transactional, heavily queried/filtered; the system of record for entities |
| Raw item blobs (long docs, images, audio), compiled prompts, model artifacts, export bundles | **S3 / MinIO** | Cheap, large, immutable objects; referenced by `input_ref` / artifact URIs |
| Item embeddings | **Qdrant / LanceDB** | Vector search for dedup, clustering, active-learning sampling, and RAG few-shot retrieval |
| Flywheel Event log | **Data lake** (partitioned object storage / columnar table) | Append-only, high-volume, scan-oriented; feeds distillation + analytics, not point lookups |
| Dataset version manifests / lineage | **lakeFS / DVC** | Content-addressed, branch/commit semantics for reproducible dataset versions |

Rule of thumb: Postgres holds *what something is*, S3 holds *the bytes*, the vector DB holds *what it's like*, and the lake holds *what happened*.

## 6. REST API contracts

FastAPI, REST/JSON, OpenAI-compatible where it overlaps (see [architecture](03-system-architecture.md)). Base path `/v1`; auth via `Authorization: Bearer <key>`; tenant resolved from the key. Errors are `{ "error": { "code", "message" } }`. Only representative request/response bodies are sketched.

**Connect / create a dataset** — `POST /v1/datasets`
```jsonc
// req
{ "project_id": "proj_1", "modality": "text",
  "source": "hf_hub", "source_uri": "imdb",
  "text_field": "review", "meta_fields": ["label","len"] }
// res 201
{ "id": "ds_1", "version": "v1", "item_count": 50000, "content_hash": "…" }
```

**Define / version a taxonomy** — `POST /v1/taxonomies` (create) · `POST /v1/taxonomies/{id}/versions` (re-version)
```jsonc
// req (new version)
{ "label_type": "classification_single",
  "task_instructions": "Label each movie review's sentiment.",
  "labels": [ {"key":"pos","definition":"…","positive_examples":["…"]},
              {"key":"neg","definition":"…","positive_examples":["…"]} ],
  "constraints": [ {"kind":"mutually_exclusive","args":{"labels":["pos","neg"]}} ] }
// res 201 — server compiles the prompt + validator
{ "id":"tax_1","version":"v2","compiled_prompt":"…","validator_spec":{…},
  "status":"active","recalibration_required": true }
```

**Start a label job** — `POST /v1/jobs`
```jsonc
// req
{ "project_id":"proj_1", "dataset_version":"v1", "taxonomy_version":"v2",
  "model_id":"mdl_champion", "mode":"label",
  "slice": { "where": {"len": {"$gt": 20}}, "limit": 5000 } }
// res 202
{ "id":"job_1", "status":"queued" }
```

**Fetch the review queue** (sorted by the active-learning router — most informative first; see [accuracy engine](04-accuracy-and-trust-engine.md)) — `GET /v1/jobs/{id}/queue?limit=50&cursor=…`
```jsonc
// res 200
{ "items": [
    { "item_id":"item_9", "input":"…",
      "model_label":{"type":"single","label_key":"neg"},
      "model_rationale":"…", "confidence_calibrated":0.71,
      "route_reason":"below_threshold",
      "suggestions":[{"label_key":"neg","p":0.71},{"label_key":"pos","p":0.29}] } ],
  "next_cursor":"…" }
```

**Submit accept / correct / reject** — `POST /v1/items/{item_id}/review`
```jsonc
// req — "correct" carries the human value; "accept"/"reject" omit it
{ "job_id":"job_1", "taxonomy_version":"v2",
  "human_action":"edit",
  "final_label":{"type":"single","label_key":"pos"},
  "human_rationale":"sarcastic praise, actually positive" }
// res 200 — emits a FlywheelEvent
{ "label_id":"lbl_9", "status":"human_final", "event_logged": true }
```

**Manage the gold set** — `POST /v1/projects/{id}/gold` · `GET /v1/projects/{id}/gold`
```jsonc
// req
{ "taxonomy_version":"v2",
  "items":[ {"item_id":"item_3","gold_label":{"type":"single","label_key":"pos"},
             "split":"holdout","established_by":"expert"} ] }
// res 201
{ "added": 1, "calibration_n": 220, "holdout_n": 180 }
```

**Set target precision / read coverage@precision** — `PUT /v1/projects/{id}/target-precision` · `GET /v1/projects/{id}/coverage`
```jsonc
// PUT req
{ "target_precision": 0.98 }
// PUT res 200 — recompute the gate against calibration gold
{ "target_precision":0.98, "threshold":0.93, "recalibrated": true }

// GET coverage res 200
{ "taxonomy_version":"v2", "target_precision":0.98,
  "coverage_at_precision":0.71, "measured_precision_on_holdout":0.983 }
```

**List / promote models (champion ↔ challenger)** — `GET /v1/projects/{id}/models` · `POST /v1/projects/{id}/models/{model_id}/promote`
```jsonc
// promote res 200 — challenger must beat champion on coverage@precision
{ "model_id":"mdl_distilled_v3", "new_status":"champion",
  "previous_champion":"mdl_byo", "decided_on":"coverage_at_precision",
  "challenger":0.78, "champion":0.71 }
```

**Generate & fetch the quality report** — `POST /v1/jobs/{id}/quality-report` · `GET /v1/quality-reports/{id}`
```jsonc
// res 200
{ "id":"qr_1", "taxonomy_version":"v2", "target_precision":0.98,
  "coverage_at_precision":0.71, "measured_precision_on_holdout":0.983,
  "auto_applied":3550, "routed_to_human":1450,
  "human_action_breakdown":{"accept":900,"edit":420,"reject":130},
  "cost_usd":12.40, "est_human_hours_saved":59.2, "drift_flag":false }
```

**Export** (Hugging Face `datasets` / JSONL) — `POST /v1/datasets/{id}/export`
```jsonc
// req
{ "format":"jsonl", "taxonomy_version":"v2",
  "include":["final_label","human_rationale","confidence_calibrated"],
  "filter":{"status":["auto_applied","human_final"]} }
// res 202 -> async; result is an S3 artifact URI
{ "export_id":"exp_1", "status":"running" }
```

## 7. Webhooks & events

Projects register webhook subscriptions; Tessera POSTs a signed JSON envelope (`X-Tessera-Signature` HMAC) on each event. Used to drive CI, notify reviewers, or trigger a downstream training run.

```jsonc
WebhookEvent {
  type: enum(job.completed, threshold.reached, drift.detected,
             model.promoted, export.ready),
  project_id: string,
  occurred_at: timestamp,
  data: object   // type-specific payload
}
```

- **`job.completed`** — a LabelJob finished; payload carries `job_id` + `quality_report_id`.
- **`threshold.reached`** — coverage@precision crossed a configured target (e.g. ≥70% auto-applied); the cue to ship.
- **`drift.detected`** — recent holdout precision diverged from calibration; payload flags the `taxonomy_version` and suggests a `recalibrate` job (see [accuracy engine](04-accuracy-and-trust-engine.md)).
- **`model.promoted`** — a challenger beat the champion and was promoted.

## 8. Python SDK sketch

The SDK is a thin typed wrapper over §6 — connect a dataset, set the taxonomy, run a job, poll, export.

```python
from tessera import Tessera

t = Tessera(api_key="sk-…")
proj = t.projects.create(name="imdb-sentiment", target_precision=0.98)

# 1. connect a dataset
ds = proj.datasets.connect(source="hf_hub", uri="imdb",
                           text_field="review", modality="text")

# 2. set the taxonomy (compiles prompt + validator server-side)
tax = proj.taxonomy.set(
    label_type="classification_single",
    task="Label each review's sentiment.",
    labels={"pos": "Clearly positive sentiment.",
            "neg": "Clearly negative sentiment."},
    constraints=[("mutually_exclusive", ["pos", "neg"])],
)

# 3. seed gold + calibrate the confidence gate to the target precision
proj.gold.add_from_csv("gold.csv", taxonomy_version=tax.version)
proj.set_target_precision(0.98)        # fits the CalibrationModel threshold

# 4. run a labeling job with the champion labeler
job = proj.jobs.run(dataset_version=ds.version,
                    taxonomy_version=tax.version,
                    model="champion", mode="label")

# 5. poll to completion
report = job.wait()                    # blocks until QualityReport is ready
print(report.coverage_at_precision, report.measured_precision_on_holdout)

# (review the routed-to-human queue interactively in the app; see HITL UX spec)

# 6. export the trusted slice for fine-tuning
art = ds.export(format="jsonl", taxonomy_version=tax.version,
                status=["auto_applied", "human_final"])
art.download("imdb_labeled.jsonl")
```

See the [HITL UX spec](07-hitl-ux-spec.md) for the keyboard-first review loop that clears the routed queue, the [accuracy & trust engine](04-accuracy-and-trust-engine.md) for how the gate and coverage@precision are computed, and the [data flywheel & model strategy](05-data-flywheel-and-model-strategy.md) for how the event log distills into champion/challenger models.
