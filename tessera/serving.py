"""Zero-config local model serving (the out-of-the-box default).

When no model is configured at all, Tessera looks for a winc.cpp install
(models + bundled llama-server engine) and serves the right Qwen tier
itself, picked by available memory — the same rule Jobfaro ships:
**>= 7 GB -> 4B, else 2B** (Apple Silicon unified memory counts as VRAM;
a discrete NVIDIA GPU's VRAM is used when nvidia-smi answers).

The spawn uses the measured serving recipe (reasoning OFF via
--chat-template-kwargs enable_thinking:false — see the README serving
note; without it the logprob head reads empty answers). Explicit
configuration always wins: any TESSERA_PROVIDER / *_URL / API key disables
auto-serve, and TESSERA_AUTOSERVE=0 turns it off outright.
"""
from __future__ import annotations

import atexit
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request

TIER_GB = 7.0            # >= this much memory -> 4B, else 2B (Jobfaro rule)
_PATTERNS = {"4b": "*wen3.5-4B*.gguf", "2b": "*wen3.5-2B*.gguf"}
_proc = None             # the one auto-served engine per process
_url = None              # its /v1/chat/completions URL (for reuse)


def detect_memory_gb():
    """(gb, source): discrete-GPU VRAM when nvidia-smi answers, else
    physical RAM (Apple Silicon unified memory IS the VRAM)."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return int(out.stdout.split()[0]) / 1024.0, "GPU VRAM"
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"],
                                 capture_output=True, text=True, timeout=3)
            return int(out.stdout.strip()) / 1024 ** 3, "unified memory"
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong),
                            ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / 1024 ** 3, "RAM"
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 ** 2, "RAM"
    except Exception:
        pass
    return 0.0, "unknown"


def pick_tier(mem_gb):
    return "4b" if mem_gb >= TIER_GB else "2b"


def find_winc_assets(roots=None):
    """Locate a winc install's model files and bundled llama-server engine.
    Returns {"engine": path, "models": {tier: path}} or None."""
    home = os.path.expanduser("~")
    roots = roots or [os.environ.get("WINC_HOME") or "",
                      os.path.join(home, "winc.cpp"),
                      os.path.join(home, ".winc")]
    for root in [r for r in roots if r]:
        engine = os.path.join(root, "bin",
                              "llama-server.exe" if os.name == "nt" else "llama-server")
        mdir = os.path.join(root, "models")
        if not (os.path.isfile(engine) and os.path.isdir(mdir)):
            continue
        models = {}
        for tier, pat in _PATTERNS.items():
            hits = sorted(glob.glob(os.path.join(mdir, pat)))
            if hits:
                models[tier] = hits[0]
        if models:
            return {"engine": engine, "models": models}
    return None


def _explicitly_configured(settings):
    # A bare API key does NOT count: without TESSERA_PROVIDER set, no labeler
    # ever reads it (the run would silently be the stub) — auto-serve is
    # strictly better than that. Providers and URLs are explicit intent.
    return bool(os.environ.get("TESSERA_PROVIDER")
                or settings.openai_url or settings.anthropic_url)


def plan_auto(settings):
    """What zero-config serving WOULD do (no side effects), or None.
    {"tier", "model_path", "mem_gb", "mem_source", "engine"}"""
    if os.environ.get("TESSERA_AUTOSERVE", "1") in ("0", "false", "no"):
        return None
    if _explicitly_configured(settings):
        return None
    assets = find_winc_assets()
    if not assets:
        return None
    mem, source = detect_memory_gb()
    tier = pick_tier(mem)
    if tier not in assets["models"]:          # picked tier missing on disk:
        tier = next(iter(sorted(assets["models"])))   # use what exists, honestly
    return {"tier": tier, "model_path": assets["models"][tier],
            "mem_gb": round(mem, 1), "mem_source": source,
            "engine": assets["engine"]}


def _point_at(settings, url):
    settings.provider = "openai"
    settings.openai_url = url + "/v1/chat/completions"
    settings.model_id = "q"
    settings.logprobs = True
    settings.llm_samples = 1


def ensure_model(settings, wait_s=90, log=print):
    """Make settings point at a working model, spawning the winc-tier engine
    when nothing is configured. Returns a short status string. Idempotent:
    one engine per process — a live auto-serve is REUSED (any settings
    object gets pointed at it), never re-spawned."""
    global _proc, _url
    if _explicitly_configured(settings):
        return "explicit configuration"
    if _proc is not None and _proc.poll() is None and _url:
        _point_at(settings, _url)
        return "auto-serve already active"
    plan = plan_auto(settings)
    if plan is None:
        return "stub (offline)"
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    log(f"model: auto-starting winc qwen3.5-{plan['tier']} "
        f"({plan['mem_gb']} GB {plan['mem_source']} -> "
        f"{'4B' if plan['tier'] == '4b' else '2B'} tier) ...", flush=True)
    _proc = subprocess.Popen(
        [plan["engine"], "-m", plan["model_path"], "--port", str(port),
         "-np", "4", "-c", "16384", "--no-webui",
         "--chat-template-kwargs", '{"enable_thinking":false}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    atexit.register(stop)
    deadline = time.time() + wait_s
    url = f"http://127.0.0.1:{port}"
    while time.time() < deadline:
        if _proc.poll() is not None:
            _proc = None
            return "auto-serve failed: engine exited at startup"
        try:
            with urllib.request.urlopen(url + "/health", timeout=1) as r:
                if r.status == 200:
                    _url = url
                    _point_at(settings, url)
                    log(f"model: ready on :{port}", flush=True)
                    return f"auto: winc qwen3.5-{plan['tier']}"
        except Exception:
            time.sleep(0.5)
    stop()
    return "auto-serve failed: engine never became healthy"


def stop():
    global _proc, _url
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc = None
    _url = None
