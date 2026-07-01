"""Response cache for LLM calls (stdlib sqlite3).

Keyed on a hash of (provider, model, params, prompt, sample index) so a re-run of
the same dataset never re-pays the API — the caching the docs/08 Day 3-5 plan
calls for. Self-consistency samples are distinct entries (the sample index is in
the key), so N samples stay N independent draws on the first run and N hits after.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from typing import Optional


class ResponseCache:
    def __init__(self, db_path: str = "tessera_cache.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "key TEXT PRIMARY KEY, response TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            self.conn.commit()

    @staticmethod
    def key(provider: str, model: str, params: str, prompt: str, sample: int = 0) -> str:
        raw = f"{provider}|{model}|{params}|{sample}|{prompt}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            r = self.conn.execute("SELECT response FROM responses WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def put(self, key: str, response: str) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO responses (key, response) VALUES (?,?)",
                              (key, response))
            self.conn.commit()

    def close(self):
        self.conn.close()


def open_cache(path: str) -> Optional[ResponseCache]:
    """Cache from a settings path; 'none'/'' disables caching."""
    if not path or path.lower() == "none":
        return None
    return ResponseCache(path)
