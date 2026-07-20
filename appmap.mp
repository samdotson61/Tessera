# Tessera App Map

Living tracker for turning the CLI+browser MVP into an installable,
multi-platform app. Update the boards below as construction proceeds —
every state change gets a date. (Markdown-formatted; `.mp` = "map".)

**North star:** one-liner installer (release binary, no Python required) →
`tessera app` → the review UI opens — on macOS, Windows, and Linux.
The native-shell tier ships only after a partner engagement proves demand.

---

## Tier board

| tier | what it is | status |
|---|---|---|
| **T1a — `tessera app`** | one command: pick/prepare a dataset, gate, start the server, open the browser (first run: offline sample demo) | **DONE 2026-07-20** |
| **T1b — window mode** | `tessera app --window`: native window via pywebview (optional extra `[app]`), browser fallback with honest note | **DONE 2026-07-20** — native window launched + served on macOS (pywebview loop up, API live, clean exit); interactive click-through rides along with the Windows smoke |
| **T1c — GitHub installer** | `curl \| sh` (macOS/Linux: release binary, pip-from-source fallback) + PowerShell one-liner (Windows) — no PyPI, no Python required on the binary path | **DONE 2026-07-20 — live-verified: binary path and pip-fallback path both E2E from the raw URL** |
| ~~T1c-alt — PyPI release~~ | `pip install tessera-label` — dist builds clean and the wheel is verified, but Sam has no PyPI account | **DEFERRED by decision 2026-07-20** (GitHub installer instead; revisit if discoverability ever matters) |
| **T1d — workflow-complete UI** | the app IS the product: Import → Rubric → Gold → Label → Review → Export in one page, state-derived guidance, tooltips everywhere, in-browser run with progress + honest serving status, one-click exports | **DONE 2026-07-20 (v0.17.0)** — live-verified end to end as a first-time user (import CSV → rubric edit → gold ×2 → run → correct → export) |
| **T2 — one-file binaries** | PyInstaller single-file `tessera` executables for macOS/Windows/Linux; CI matrix builds them on tag | **DONE 2026-07-20 — all 3 platforms built + smoke-passed in CI (run 29765123377); macOS also smoked locally end to end** |
| **T3 — native shell** | signed/notarized app managing the Python backend + llama-server + model downloads (Sporeholm-launcher class) | **GATED — after first partner** |

## Multi-platform status

| platform | CLI + server | `tessera app` | binary |
|---|---|---|---|
| macOS (arm64) | tested (dev machine, CI 3.10–3.13) | live-verified in browser | **built + demo-smoked in CI and locally** |
| Linux | CI tests green (ubuntu runners) | same code path (webbrowser/socket are stdlib-portable) | **built + demo-smoked in CI (2026-07-20)** |
| Windows | binary demo-smoked in CI (full offline loop through the .exe); interactive app session on real hardware still open | same code path | **built + demo-smoked in CI (2026-07-20)** |

**Windows smoke = open item (the last unverified cell).** On the Windows box:
run the installer one-liner (`irm .../install.ps1 | iex`), then `tessera app`
in a fresh terminal — confirm the browser opens, label a few sample items,
re-run the installer once (upgrade-in-place check). Record results here.

## Idempotency & path contract (v0.16.0, verified)

- **No hardcoded paths.** Db resolution: `--db` → `TESSERA_DB` →
  `./tessera.db` if present (project mode) → platform data home
  (`TESSERA_HOME` overrides): `%LOCALAPPDATA%\Tessera` /
  `~/Library/Application Support/Tessera` / `$XDG_DATA_HOME/tessera`.
  Cache sits beside the resolved db. `tessera app` prints `data: <path>`
  every start. Unit-tested per platform; cwd-independence drilled live
  (two directories, same data, no re-bootstrap).
- **Installers re-run clean and upgrade in place.** sh: temp + atomic
  rename (safe over a running binary — no ETXTBSY), pip fallback
  force-reinstalls (re-run = current main), `TESSERA_BIN`/`TESSERA_HOME`
  relocate everything. ps1: rename-running-exe dance, `$env:TESSERA_DIR`
  override, null-PATH guard; syntax-checked in the Windows CI job.
- **App re-runs are stable.** First-run demo happens once (dataset
  presence, not a marker file); busy ports get a new port, not a crash;
  re-gates always re-derive from stored state.

## Decisions log

- **2026-07-20 — Browser-first, window optional.** The core app experience is
  the default browser (zero added dependencies, works everywhere, keyboard
  UX identical). pywebview is an optional extra (`pip install
  tessera-label[app]`), never a requirement — the stdlib-pure core is a
  product principle.
- **2026-07-20 — First run shows something real.** `tessera app` on an empty
  db loads the bundled sample with the OFFLINE stub labeler (never the
  configured LLM — no surprise API/model calls) and says so honestly.
- **2026-07-20 — PyInstaller over Tauri/Electron for T2.** Pure-stdlib
  backend + static web UI = nothing to gain from a JS shell yet; one-file
  binaries cover the "no Python on the machine" case. Revisit at T3.
- **2026-07-20 — PyPI name is `tessera-label`** (`tessera` is squatted by an
  old Graphite dashboard).
- **2026-07-20 — Distribution is GitHub-native, not PyPI.** Sam has no PyPI
  account and doesn't want one; the install story is the release-binary
  `curl | sh` / PowerShell one-liner (his house pattern — same as Jobfaro),
  with pip-from-git as the documented alternative. PyPI deferred, not dead.
- **2026-07-20 — North star amended accordingly:** one-liner installer →
  `tessera app`, no Python required on the binary path.

## Handoffs (Sam)

1. **Windows smoke** (see table above) — the installer one-liner
   (`irm .../install.ps1 | iex`) plus an interactive `tessera app` session.
2. **T3 go/no-go** after the first partner engagement.

(PyPI upload was a handoff here until 2026-07-20; superseded by the GitHub
installer — see the decisions log. The dist tooling stays in `[release]` if
we ever want it.)

## Next milestones

- [x] Windows + Linux binary smoke via CI (2026-07-20, run 29765123377 — offline demo passes through all 3 binaries); interactive Windows session on real hardware still open
- [~] PyPI publish — DEFERRED by decision (see log); not blocking anything
- [ ] Windows interactive smoke (handoff #1) — the one open verification
- [ ] (T3, gated) native shell: process manager for llama-server, model
      download UI, auto-update, signing/notarization per OS
