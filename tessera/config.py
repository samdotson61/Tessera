"""Runtime settings, configurable via environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class Settings:
    db_path: str = "tessera.db"
    provider: str = "stub"            # "stub" | "anthropic" | "openai"
    model_id: str = "stub-kw-v1"
    n_samples: int = 5                # self-consistency samples (real LLM path)
    target_precision: float = 0.95
    calibration: str = "isotonic"     # "isotonic" | "histogram" | "identity"
    min_gold_for_calibration: int = 10
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    host: str = "127.0.0.1"
    port: int = 8080

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            db_path=os.environ.get("TESSERA_DB", "tessera.db"),
            provider=os.environ.get("TESSERA_PROVIDER", "stub"),
            model_id=os.environ.get("TESSERA_MODEL", "stub-kw-v1"),
            n_samples=int(os.environ.get("TESSERA_N_SAMPLES", "5")),
            target_precision=float(os.environ.get("TESSERA_TARGET_PRECISION", "0.95")),
            calibration=os.environ.get("TESSERA_CALIBRATION", "isotonic"),
            min_gold_for_calibration=int(os.environ.get("TESSERA_MIN_GOLD", "10")),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            host=os.environ.get("TESSERA_HOST", "127.0.0.1"),
            port=int(os.environ.get("TESSERA_PORT", "8080")),
        )
