"""SQLite persistence (stdlib sqlite3).

Production swaps this for Postgres + object store + vector DB (see docs/03).
A single connection is shared with check_same_thread=False plus a lock, which is
sufficient for the single-user MVP server.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .schemas import Dataset, Item, Taxonomy, Prediction, GoldItem, Event


SCHEMA = """
CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY, name TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS items (
    id TEXT PRIMARY KEY, dataset_id TEXT, text TEXT, meta_json TEXT, final_label TEXT
);
CREATE TABLE IF NOT EXISTS taxonomies (
    id TEXT PRIMARY KEY, name TEXT, version INTEGER, label_type TEXT,
    labels_json TEXT, definitions_json TEXT, guidelines TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    item_id TEXT PRIMARY KEY, dataset_id TEXT, taxonomy_id TEXT, label TEXT,
    confidence_raw REAL, confidence_calibrated REAL, agreement REAL, rationale TEXT,
    votes_json TEXT, distribution_json TEXT, auto_applied INTEGER, routed INTEGER, source TEXT
);
CREATE TABLE IF NOT EXISTS gold (
    item_id TEXT PRIMARY KEY, dataset_id TEXT, label TEXT, source TEXT DEFAULT 'seed'
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT, dataset_id TEXT,
    model_id TEXT, model_label TEXT, model_rationale TEXT,
    confidence_raw REAL, confidence_calibrated REAL,
    ensemble_votes_json TEXT, weak_supervision_votes_json TEXT,
    routed_to_human INTEGER, route_reason TEXT,
    human_action TEXT, final_label TEXT, human_rationale TEXT,
    taxonomy_version INTEGER, rubric_snapshot TEXT, modality TEXT, input_ref TEXT,
    latency_ms REAL, cost_usd REAL, annotator_id TEXT, timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_ds ON items(dataset_id);
CREATE INDEX IF NOT EXISTS idx_pred_ds ON predictions(dataset_id);
CREATE INDEX IF NOT EXISTS idx_events_ds ON events(dataset_id);
"""


def _b(v) -> Optional[int]:
    """bool|None -> int|None for sqlite."""
    return None if v is None else int(v)


class Storage:
    def __init__(self, db_path: str = "tessera.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self.conn.executescript(SCHEMA)
            try:  # migrate pre-source gold tables in place
                self.conn.execute("ALTER TABLE gold ADD COLUMN source TEXT DEFAULT 'seed'")
            except sqlite3.OperationalError:
                pass
            self.conn.commit()

    def close(self):
        self.conn.close()

    # ---- datasets ----
    def add_dataset(self, ds: Dataset):
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO datasets VALUES (?,?,?)",
                              (ds.id, ds.name, ds.created_at))
            self.conn.commit()

    def get_dataset(self, dataset_id) -> Optional[Dataset]:
        r = self.conn.execute("SELECT * FROM datasets WHERE id=?", (dataset_id,)).fetchone()
        return Dataset(r["id"], r["name"], r["created_at"]) if r else None

    # ---- items ----
    def add_items(self, items):
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO items VALUES (?,?,?,?,?)",
                [(it.id, it.dataset_id, it.text, json.dumps(it.meta), it.final_label) for it in items])
            self.conn.commit()

    def get_items(self, dataset_id):
        rows = self.conn.execute(
            "SELECT * FROM items WHERE dataset_id=? ORDER BY id", (dataset_id,)).fetchall()
        return [self._row_item(r) for r in rows]

    def get_item(self, item_id) -> Optional[Item]:
        r = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        return self._row_item(r) if r else None

    def _row_item(self, r) -> Item:
        return Item(r["id"], r["dataset_id"], r["text"],
                    json.loads(r["meta_json"] or "{}"), r["final_label"])

    def set_final_label(self, item_id, label):
        with self._lock:
            self.conn.execute("UPDATE items SET final_label=? WHERE id=?", (label, item_id))
            self.conn.commit()

    # ---- taxonomies ----
    def add_taxonomy(self, t: Taxonomy):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO taxonomies VALUES (?,?,?,?,?,?,?)",
                (t.id, t.name, t.version, t.label_type, json.dumps(t.labels),
                 json.dumps(t.definitions), t.guidelines))
            self.conn.commit()

    def get_taxonomy(self, taxonomy_id) -> Optional[Taxonomy]:
        r = self.conn.execute("SELECT * FROM taxonomies WHERE id=?", (taxonomy_id,)).fetchone()
        if not r:
            return None
        return Taxonomy(r["id"], r["name"], r["version"], r["label_type"],
                        json.loads(r["labels_json"]), json.loads(r["definitions_json"]),
                        r["guidelines"])

    # ---- predictions ----
    def upsert_prediction(self, p: Prediction):
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (p.item_id, p.dataset_id, p.taxonomy_id, p.label, p.confidence_raw,
                 p.confidence_calibrated, p.agreement, p.rationale, json.dumps(p.votes),
                 json.dumps(p.distribution), _b(p.auto_applied), _b(p.routed), p.source))
            self.conn.commit()

    def get_predictions(self, dataset_id):
        rows = self.conn.execute(
            "SELECT * FROM predictions WHERE dataset_id=? ORDER BY item_id", (dataset_id,)).fetchall()
        return [self._row_pred(r) for r in rows]

    def get_prediction(self, item_id) -> Optional[Prediction]:
        r = self.conn.execute("SELECT * FROM predictions WHERE item_id=?", (item_id,)).fetchone()
        return self._row_pred(r) if r else None

    def _row_pred(self, r) -> Prediction:
        return Prediction(
            item_id=r["item_id"], dataset_id=r["dataset_id"], taxonomy_id=r["taxonomy_id"],
            label=r["label"], confidence_raw=r["confidence_raw"],
            confidence_calibrated=r["confidence_calibrated"], agreement=r["agreement"],
            rationale=r["rationale"], votes=json.loads(r["votes_json"] or "{}"),
            distribution=json.loads(r["distribution_json"] or "{}"),
            auto_applied=(None if r["auto_applied"] is None else bool(r["auto_applied"])),
            routed=(None if r["routed"] is None else bool(r["routed"])), source=r["source"])

    # ---- gold ----
    def add_gold(self, gold_items):
        with self._lock:
            self.conn.executemany(
                "INSERT OR REPLACE INTO gold VALUES (?,?,?,?)",
                [(g.item_id, g.dataset_id, g.label, g.source) for g in gold_items])
            self.conn.commit()

    def get_gold(self, dataset_id) -> dict:
        rows = self.conn.execute("SELECT * FROM gold WHERE dataset_id=?", (dataset_id,)).fetchall()
        return {r["item_id"]: r["label"] for r in rows}

    def remove_gold(self, item_id, source=None):
        """Delete one gold row (optionally only if it came from the given source)."""
        with self._lock:
            if source is None:
                self.conn.execute("DELETE FROM gold WHERE item_id=?", (item_id,))
            else:
                self.conn.execute("DELETE FROM gold WHERE item_id=? AND source=?",
                                  (item_id, source))
            self.conn.commit()

    def count_gold_by_source(self, dataset_id) -> dict:
        rows = self.conn.execute(
            "SELECT source, COUNT(*) AS n FROM gold WHERE dataset_id=? GROUP BY source",
            (dataset_id,)).fetchall()
        return {r["source"]: r["n"] for r in rows}

    # ---- events ----
    def append_event(self, e: Event) -> int:
        with self._lock:
            cur = self.conn.execute(
                """INSERT INTO events (item_id,dataset_id,model_id,model_label,model_rationale,
                   confidence_raw,confidence_calibrated,ensemble_votes_json,weak_supervision_votes_json,
                   routed_to_human,route_reason,human_action,final_label,human_rationale,
                   taxonomy_version,rubric_snapshot,modality,input_ref,latency_ms,cost_usd,
                   annotator_id,timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (e.item_id, e.dataset_id, e.model_id, e.model_label, e.model_rationale,
                 e.confidence_raw, e.confidence_calibrated, json.dumps(e.ensemble_votes),
                 json.dumps(e.weak_supervision_votes), int(e.routed_to_human), e.route_reason,
                 e.human_action, e.final_label, e.human_rationale, e.taxonomy_version,
                 e.rubric_snapshot, e.modality, e.input_ref, e.latency_ms, e.cost_usd,
                 e.annotator_id, e.timestamp))
            self.conn.commit()
            return cur.lastrowid

    def delete_auto_events(self, dataset_id):
        """Remove prior auto-apply (non-human) events for a dataset so re-gating is
        idempotent. Human-action events (routed_to_human=1) are preserved."""
        with self._lock:
            self.conn.execute(
                "DELETE FROM events WHERE dataset_id=? AND routed_to_human=0", (dataset_id,))
            self.conn.commit()

    def get_events(self, dataset_id=None):
        if dataset_id:
            rows = self.conn.execute(
                "SELECT * FROM events WHERE dataset_id=? ORDER BY id", (dataset_id,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM events ORDER BY id").fetchall()
        return [self._row_event(r) for r in rows]

    def _row_event(self, r) -> Event:
        return Event(
            item_id=r["item_id"], dataset_id=r["dataset_id"], model_id=r["model_id"],
            model_label=r["model_label"], model_rationale=r["model_rationale"],
            confidence_raw=r["confidence_raw"], confidence_calibrated=r["confidence_calibrated"],
            ensemble_votes=json.loads(r["ensemble_votes_json"] or "{}"),
            weak_supervision_votes=json.loads(r["weak_supervision_votes_json"] or "{}"),
            routed_to_human=bool(r["routed_to_human"]), route_reason=r["route_reason"],
            human_action=r["human_action"], final_label=r["final_label"],
            human_rationale=r["human_rationale"], taxonomy_version=r["taxonomy_version"],
            rubric_snapshot=r["rubric_snapshot"], modality=r["modality"], input_ref=r["input_ref"],
            latency_ms=r["latency_ms"], cost_usd=r["cost_usd"], annotator_id=r["annotator_id"],
            timestamp=r["timestamp"])

    def counts(self, dataset_id) -> dict:
        c = self.conn.execute
        q = lambda sql: c(sql, (dataset_id,)).fetchone()[0]
        return {
            "items": q("SELECT COUNT(*) FROM items WHERE dataset_id=?"),
            "predictions": q("SELECT COUNT(*) FROM predictions WHERE dataset_id=?"),
            "gold": q("SELECT COUNT(*) FROM gold WHERE dataset_id=?"),
            "auto_applied": q("SELECT COUNT(*) FROM predictions WHERE dataset_id=? AND auto_applied=1"),
            "queued": q("SELECT COUNT(*) FROM predictions WHERE dataset_id=? AND routed=1"),
            "events": q("SELECT COUNT(*) FROM events WHERE dataset_id=?"),
            "finalized": q("SELECT COUNT(*) FROM items WHERE dataset_id=? AND final_label IS NOT NULL"),
        }
