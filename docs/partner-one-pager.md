# Tessera — a labeling pilot for your data

*Auto-label the confident majority of your data at a precision target you
set. Review the rest with one keystroke each. Every number below was
measured, and the measurement method ships with the tool.*

## What it does

Tessera labels text data (categories, A/B preferences, entity spans) with a
language model, then **only auto-applies labels it can statistically promise
are right** — calibrated against a gold sample of your own labels, with the
threshold cross-validated, a random slice of shipped labels audited by a
human, and the gate automatically tightening if audits ever slip. Everything
below the bar is queued for a keyboard-first review UI. Your corrections
retrain the system as you work.

**Your data never leaves your machines.** The whole loop runs on a local
open-weights model on ordinary hardware (a 3-year-old laptop is enough) at
$0 per label — or on your own API key if you prefer a frontier model.

## Measured results (public, reproducible)

| task | outcome (validated against held-back truth) |
|---|---|
| SMS spam (300 items, local 4B model) | **100% auto-labeled at 97.0% precision** |
| SMS spam (smaller 2B model, laptop-class) | 87% auto-labeled at **98.9%** precision |
| AG News topics (noisy benchmark, 297 gold) | 93.5% auto-labeled at 92.8% — and at a 95% target the gate **honestly refuses** rather than over-promise |
| GitHub issue triage (438 real VS Code issues) | full engagement rehearsal — [case study](case-study-issue-triage.md) |

The system's defining behavior is honesty: when a precision target isn't
achievable on your data, it ships *nothing* at that target and says why —
it does not quietly deliver worse labels than it promised.

## What the pilot asks of you

1. **A 1–2 hour kickoff.** We draft the labeling rubric together and run
   `rubric-check` against a sample of your *already-labeled* history (if you
   have any) — measuring, before anything else, whether the rubric captures
   how your team actually labels. This step exists because we measured what
   happens without it.
2. **~100 gold labels**, authored by your expert in the keyboard UI
   (typically under an hour).
3. **A labeled backlog, if one exists** (old tickets, tagged emails, past
   annotations). It becomes held-back truth: we validate the entire pipeline
   against it and show you the real number before you trust anything.

## What you get

- Your dataset labeled: the confident majority auto-applied at the agreed
  precision target, the remainder queued with the model's reasoning attached.
- **The quality report**: coverage, cross-validated precision, calibration
  curve, audit results, and every caveat that applies — the artifact your
  team reviews before shipping the labels.
- The exported label set and training pairs (they're yours), plus the gold
  set and rubric your team can keep running without us.

*Tessera is open source: github.com/samdotson61/Tessera — the design docs,
test suite (200+ tests), and every measurement above are in the repo.
Install is one line (`pip install
git+https://github.com/samdotson61/Tessera.git`), and `tessera app` opens
the whole thing; single-file binaries are built for macOS, Windows, and
Linux.*
