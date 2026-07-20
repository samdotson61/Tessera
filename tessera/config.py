"""Runtime settings, configurable via environment variables."""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass


def data_home() -> str:
    """Per-user Tessera data directory, by platform convention.

    TESSERA_HOME overrides; else %LOCALAPPDATA%\\Tessera (Windows),
    ~/Library/Application Support/Tessera (macOS), $XDG_DATA_HOME/tessera or
    ~/.local/share/tessera (Linux). Never hardcoded — derived per user."""
    override = os.environ.get("TESSERA_HOME")
    if override:
        return override
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Tessera")
    if sys.platform == "darwin":
        return os.path.expanduser(os.path.join("~", "Library",
                                               "Application Support", "Tessera"))
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser(
        os.path.join("~", ".local", "share"))
    return os.path.join(base, "tessera")


def resolve_db(explicit=None) -> str:
    """Where the database lives, in order of authority: an explicit --db,
    TESSERA_DB, an existing ./tessera.db (project mode — a checkout keeps its
    data local), else the per-user data home (app mode — the same data no
    matter which directory you launch from)."""
    if explicit:
        return explicit
    env = os.environ.get("TESSERA_DB")
    if env:
        return env
    if os.path.exists("tessera.db"):
        return "tessera.db"
    home = data_home()
    os.makedirs(home, exist_ok=True)
    return os.path.join(home, "tessera.db")


def load_dotenv(path: str = ".env") -> None:
    """Minimal stdlib .env loader: populate os.environ from KEY=VALUE lines if the
    file exists. Does not overwrite variables already set in the environment."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            os.environ.setdefault(key, val)


@dataclass
class Settings:
    db_path: str = "tessera.db"
    provider: str = "stub"            # "stub" | "anthropic" | "openai"
    model_id: str = "stub-kw-v1"
    target_precision: float = 0.95
    calibration: str = "isotonic"     # "isotonic" | "histogram" | "identity"
    min_gold_for_calibration: int = 10
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    anthropic_url: str = ""           # alternate /v1/messages endpoint (e.g. a local
                                      # winc.cpp/llama.cpp server); key optional then
    openai_url: str = ""              # alternate /v1/chat/completions endpoint (e.g.
                                      # ollama/vLLM/llama-server); key optional then
    llm_samples: int = 5              # self-consistency samples per item (LLM labelers)
    fewshot: int = 0                  # k nearest gold examples in the prompt (0 = off);
                                      # docs/05 Phase A RAG-lite, classification only
    logprobs: bool = False            # classification via the label token's logprobs:
                                      # 1 call/item, continuous confidence (openai-shaped
                                      # local servers only — llama-server/vLLM)
    answer_key: str = "auto"          # logprob answer format: "auto" uses letters (A/B/C)
                                      # when label words share prefixes and can't be told
                                      # apart by their first token, else the label word;
                                      # "letter" | "word" force it
    fewshot_static: bool = False      # one fixed example block for every item (shared
                                      # prompt prefix -> the server caches the prefill)
    cache_path: str = "tessera_cache.db"   # LLM response cache; "none" disables
    workers: int = 8                  # concurrent items in the labeling pass
    judge_provider: str = ""          # LLM-as-judge: "anthropic" | "openai" | "" (off).
    judge_model: str = ""             # judge model override; pick a different family than the labeler
    grow_gold: bool = True            # record human accept/edit decisions as gold (source "human")
    audit_rate: float = 0.02          # share of AUTO-APPLIED items also routed for human audit
    router: str = "confidence"        # review-queue order: "confidence" (default — won the
                                      # errors-found-per-review A/B) | "cluster" (experimental)
    specialist: bool = True           # consensus gate (DEFAULT ON since v0.11.0): train the
                                      # Tier-0 specialist on half the trusted labels and add it
                                      # to the ensemble; disagreement with the LLM flattens
                                      # confidence and routes (measured: 4B 64%->93.5% coverage
                                      # at a kept 90% promise). Joins only when the train half
                                      # has >= specialist_min_train examples of >= 2 labels on a
                                      # classification task; otherwise the run is unchanged.
                                      # TESSERA_SPECIALIST=0 disables.
    specialist_min_train: int = 10    # min training examples (train half) before the specialist joins
    propagate: float = 0.0            # near-duplicate propagation: cosine threshold (0 = off).
                                      # Only cluster representatives hit the LLM; members mirror
                                      # their rep's label/state and stay in the audit universe
    autopilot: bool = False           # closed loop: audit-precision breaches tighten the gate
                                      # automatically (and recoveries relax it) — see docs/04
    autopilot_min_audits: int = 20    # audits required before the autopilot may adjust
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()  # pick up a local .env if present
        return cls(
            db_path=os.environ.get("TESSERA_DB", "tessera.db"),
            provider=os.environ.get("TESSERA_PROVIDER", "stub"),
            model_id=os.environ.get("TESSERA_MODEL", "stub-kw-v1"),
            target_precision=float(os.environ.get("TESSERA_TARGET_PRECISION", "0.95")),
            calibration=os.environ.get("TESSERA_CALIBRATION", "isotonic"),
            min_gold_for_calibration=int(os.environ.get("TESSERA_MIN_GOLD", "10")),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            anthropic_url=os.environ.get("TESSERA_ANTHROPIC_URL", ""),
            openai_url=os.environ.get("TESSERA_OPENAI_URL", ""),
            llm_samples=int(os.environ.get("TESSERA_SAMPLES", "5")),
            fewshot=int(os.environ.get("TESSERA_FEWSHOT", "0")),
            logprobs=os.environ.get("TESSERA_LOGPROBS", "0") in ("1", "true", "yes"),
            answer_key=os.environ.get("TESSERA_ANSWER_KEY", "auto"),
            fewshot_static=os.environ.get("TESSERA_FEWSHOT_STATIC", "0") in ("1", "true", "yes"),
            cache_path=os.environ.get("TESSERA_CACHE", "tessera_cache.db"),
            workers=int(os.environ.get("TESSERA_WORKERS", "8")),
            judge_provider=os.environ.get("TESSERA_JUDGE", ""),
            judge_model=os.environ.get("TESSERA_JUDGE_MODEL", ""),
            grow_gold=os.environ.get("TESSERA_GROW_GOLD", "1") not in ("0", "false", "no"),
            audit_rate=float(os.environ.get("TESSERA_AUDIT_RATE", "0.02")),
            router=os.environ.get("TESSERA_ROUTER", "confidence"),
            specialist=os.environ.get("TESSERA_SPECIALIST", "1") not in ("0", "false", "no"),
            specialist_min_train=int(os.environ.get("TESSERA_SPECIALIST_MIN", "10")),
            propagate=float(os.environ.get("TESSERA_PROPAGATE", "0")),
            autopilot=os.environ.get("TESSERA_AUTOPILOT", "0") in ("1", "true", "yes"),
            autopilot_min_audits=int(os.environ.get("TESSERA_AUTOPILOT_MIN", "20")),
            host=os.environ.get("TESSERA_HOST", "127.0.0.1"),
            port=int(os.environ.get("TESSERA_PORT", "8080")),
        )
