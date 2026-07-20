# Tessera App Map

Living tracker for turning the CLI+browser MVP into an installable,
multi-platform app. Update the boards below as construction proceeds —
every state change gets a date. (Markdown-formatted; `.mp` = "map".)

**North star:** `pip install tessera-label` (or download one file) →
`tessera app` → the review UI opens — on macOS, Windows, and Linux.
The native-shell tier ships only after a partner engagement proves demand.

---

## Tier board

| tier | what it is | status |
|---|---|---|
| **T1a — `tessera app`** | one command: pick/prepare a dataset, gate, start the server, open the browser (first run: offline sample demo) | **DONE 2026-07-20** |
| **T1b — window mode** | `tessera app --window`: native window via pywebview (optional extra `[app]`), browser fallback with honest note | **BUILT 2026-07-20** — fallback path in place; native window untested (pywebview not installed on the dev box) |
| **T1c — PyPI release** | `pip install tessera-label` — dist built + verified locally; **upload = Sam** (needs PyPI account/token) | **READY — handoff** |
| **T2 — one-file binaries** | PyInstaller single-file `tessera` executables for macOS/Windows/Linux; CI matrix builds them on tag | **macOS built+smoked 2026-07-20; Win/Linux via CI** |
| **T3 — native shell** | signed/notarized app managing the Python backend + llama-server + model downloads (Sporeholm-launcher class) | **GATED — after first partner** |

## Multi-platform status

| platform | CLI + server | `tessera app` | binary |
|---|---|---|---|
| macOS (arm64) | tested (dev machine, CI 3.10–3.13) | live-verified in browser | built + smoked locally |
| Linux | CI tests green (ubuntu runners) | same code path (webbrowser/socket are stdlib-portable) | CI build job |
| Windows | untested end-to-end; code audit clean (no POSIX calls in package; static-path guard handles drive letters) | same code path | CI build job |

**Windows smoke = open item.** Sam has a Windows box (see
`~/Claude/winc-windows-speed-tests.md` handoff pattern): install Python 3.11+,
`pip install` the wheel from CI artifacts, run `tessera demo` + `tessera app`,
record results here.

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

## Handoffs (Sam)

1. **PyPI upload** (one time): create account/token at pypi.org, then
   `pip install build twine && python -m build && twine upload dist/*`
   from the repo root. Dist verified locally (wheel installs, `tessera demo`
   passes from the wheel).
2. **Windows smoke** (see table above).
3. **T3 go/no-go** after the first partner engagement.

## Next milestones

- [ ] Windows + Linux binary smoke from CI artifacts
- [ ] PyPI published → README/one-pager switch install lines to `pip install tessera-label`
- [ ] (T3, gated) native shell: process manager for llama-server, model
      download UI, auto-update, signing/notarization per OS
