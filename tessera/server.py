"""Minimal review server (stdlib http.server).

Serves the keyboard-first review UI (tessera/web) and a small JSON API backed by
Storage. Production replaces this with FastAPI + the React/Label-Studio UI
(docs/03, docs/07); the contracts here mirror docs/06.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .schemas import GoldItem, to_dict
from .engine.router import order_queue
from .labelers.judge import make_judge
from .pipeline import record_human_action, calibrate_and_gate, undo_last_human_action
from .quality import build_quality_report
from .flywheel import event_stats, export_training_pairs

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_CT = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


class Context:
    def __init__(self, storage, dataset_id, taxonomy, settings, judge=None,
                 bootstrap_ids=None):
        self.storage = storage
        self.dataset_id = dataset_id
        self.taxonomy = taxonomy
        self.settings = settings
        self.judge = judge
        self.last_gate = None
        # Cold-start gold authoring (docs/04 §7): the ordered sample a human
        # labels before any model runs. done stack supports undo.
        self.bootstrap = list(bootstrap_ids) if bootstrap_ids else None
        self.bootstrap_target = len(self.bootstrap) if self.bootstrap else 0
        self.bootstrap_done = []
        # Labeling run driven from the UI: one at a time, progress polled
        # via /api/state.
        self.run_state = {"running": False, "done": 0, "total": 0, "error": None}
        self._run_lock = threading.Lock()


def _serving_status(settings):
    """What model endpoint is configured, and does it answer? Honest and
    cheap: never claims 'connected' without a live reply."""
    if settings.openai_url:
        base = settings.openai_url.split("/v1/")[0]
        url, provider = base + "/health", f"local ({settings.openai_url})"
    elif settings.anthropic_url:
        base = settings.anthropic_url.split("/v1/")[0]
        url, provider = base + "/health", f"local ({settings.anthropic_url})"
    elif settings.provider in ("anthropic", "openai") and (
            settings.anthropic_api_key or settings.openai_api_key):
        return {"provider": f"{settings.provider} API (key set)", "ok": True,
                "note": "remote API — reachability not probed"}
    else:
        return {"provider": "stub (offline, deterministic)", "ok": True,
                "note": "no model configured — set TESSERA_OPENAI_URL or an API key"}
    try:
        with urllib.request.urlopen(url, timeout=1.5) as r:
            ok = r.status == 200
    except Exception:
        ok = False
    return {"provider": provider, "ok": ok,
            "note": "" if ok else "endpoint not answering — start your model server"}


def _queue_payload(ctx):
    items = {it.id: it for it in ctx.storage.get_items(ctx.dataset_id)}
    if ctx.bootstrap is not None:
        # Bootstrap mode: no model has run; the queue is the sample to author.
        return [{
            "item_id": iid,
            "text": items[iid].text if iid in items else "",
            "meta": items[iid].meta if iid in items else {},
            "predicted_label": None, "confidence": 0.0, "agreement": 0.0,
            "rationale": "BOOTSTRAP — no model yet; you author this gold label.",
            "audit": False, "bootstrap": True, "distribution": {},
        } for iid in ctx.bootstrap]
    preds = ctx.storage.get_predictions(ctx.dataset_id)

    def entry(p, is_audit):
        it = items.get(p.item_id)
        return {
            "item_id": p.item_id,
            "text": it.text if it else "",
            "meta": it.meta if it else {},
            "predicted_label": p.label,
            "confidence": round(p.confidence(), 4),
            "agreement": round(p.agreement, 3),
            "rationale": p.rationale,
            "audit": is_audit,
            "distribution": {k: round(v, 4) for k, v in sorted(
                p.distribution.items(), key=lambda kv: kv[1], reverse=True)},
        }

    out = [entry(p, False) for p in order_queue(preds, items=items,
                                                mode=ctx.settings.router)]
    # Audit items follow the routed queue: their labels already shipped, so
    # verification is second in priority to items with no label at all.
    out.extend(entry(p, True) for p in sorted(
        (p for p in preds if p.audit), key=lambda p: p.item_id))
    return out


def make_handler(ctx: Context):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj))

        def do_GET(self):
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._serve_static("index.html")
            if path.startswith("/static/"):
                return self._serve_static(path[len("/static/"):])
            if path == "/api/state":
                c = ctx.storage.counts(ctx.dataset_id)
                rows = ctx.storage.conn.execute(
                    "SELECT id FROM datasets ORDER BY id").fetchall()
                return self._json({
                    "dataset_id": ctx.dataset_id,
                    "datasets": [r["id"] for r in rows],
                    "taxonomy": {"name": ctx.taxonomy.name, "labels": ctx.taxonomy.labels,
                                 "label_type": ctx.taxonomy.label_type,
                                 "definitions": ctx.taxonomy.definitions,
                                 "guidelines": ctx.taxonomy.guidelines,
                                 "version": ctx.taxonomy.version},
                    "counts": c,
                    "target_precision": ctx.settings.target_precision,
                    "gate": to_dict(ctx.last_gate) if ctx.last_gate else None,
                    "events": event_stats(ctx.storage, ctx.dataset_id),
                    "serving": _serving_status(ctx.settings),
                    "run": dict(ctx.run_state),
                    "db_path": os.path.abspath(ctx.settings.db_path),
                    "bootstrap": (None if ctx.bootstrap is None else {
                        "remaining": len(ctx.bootstrap),
                        "done": len(ctx.bootstrap_done),
                        "target": ctx.bootstrap_target,
                        "gold": c["gold"]}),
                })
            if path == "/api/queue":
                return self._json({"queue": _queue_payload(ctx)})
            if path == "/api/report":
                if not ctx.last_gate:
                    return self._json({"error": "run gating first"}, 400)
                report = build_quality_report(ctx.storage, ctx.dataset_id, ctx.taxonomy, ctx.last_gate)
                return self._json({**to_dict(report),
                                   "runs": ctx.storage.get_runs(ctx.dataset_id, limit=12)})
            if path.startswith("/api/export/"):
                return self._export(path[len("/api/export/"):])
            return self._json({"error": "not found"}, 404)

        def _export(self, kind):
            items = ctx.storage.get_items(ctx.dataset_id)
            finalized = [it for it in items if it.final_label is not None]
            stamp = f"{ctx.dataset_id}-{kind}"
            if kind == "labels.jsonl":
                body = "".join(json.dumps({"id": it.id, "text": it.text,
                                           "label": it.final_label}) + "\n"
                               for it in finalized)
                ctype = "application/x-ndjson"
            elif kind == "pairs.jsonl":
                body = "".join(json.dumps(p) + "\n"
                               for p in export_training_pairs(ctx.storage, ctx.dataset_id))
                ctype = "application/x-ndjson"
            elif kind == "labels.csv":
                import csv
                buf = io.StringIO()
                w = csv.writer(buf)
                w.writerow(["id", "text", "label"])
                for it in finalized:
                    w.writerow([it.id, it.text, it.final_label])
                body, ctype = buf.getvalue(), "text/csv"
            else:
                return self._json({"error": f"unknown export '{kind}'"}, 404)
            data = body.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{stamp}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            path = urlparse(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return self._json({"error": "bad json"}, 400)

            if path == "/api/action":
                try:
                    final = record_human_action(
                        ctx.storage, ctx.taxonomy, body["item_id"], body["action"],
                        label=body.get("label"), annotator=body.get("annotator", "human"),
                        grow_gold=ctx.settings.grow_gold)
                except (KeyError, ValueError) as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"ok": True, "final_label": final})

            if path == "/api/bootstrap":
                if ctx.bootstrap is None:
                    return self._json({"error": "not in bootstrap mode"}, 400)
                item_id = str(body.get("item_id", ""))
                if item_id not in ctx.bootstrap:
                    return self._json({"error": f"'{item_id}' not in the sample"}, 400)
                label = body.get("label")
                if label is not None:
                    if label not in ctx.taxonomy.labels:
                        return self._json({"error": f"unknown label '{label}'"}, 400)
                    ctx.storage.add_gold([GoldItem(
                        item_id=item_id, dataset_id=ctx.dataset_id,
                        label=label, source="bootstrap")])
                ctx.bootstrap.remove(item_id)
                ctx.bootstrap_done.append((item_id, label))
                return self._json({"ok": True, "item_id": item_id, "label": label,
                                   "remaining": len(ctx.bootstrap)})

            if path == "/api/undo":
                if ctx.bootstrap is not None:
                    if not ctx.bootstrap_done:
                        return self._json({"error": "nothing to undo"}, 400)
                    item_id, label = ctx.bootstrap_done.pop()
                    if label is not None:
                        ctx.storage.remove_gold(item_id, source="bootstrap")
                    ctx.bootstrap.insert(0, item_id)
                    return self._json({"ok": True, "item_id": item_id})
                item_id = undo_last_human_action(ctx.storage, ctx.dataset_id)
                if item_id is None:
                    return self._json({"error": "nothing to undo"}, 400)
                return self._json({"ok": True, "item_id": item_id})

            if path == "/api/gate":
                target = float(body.get("target_precision", ctx.settings.target_precision))
                ctx.settings.target_precision = target
                ctx.last_gate = calibrate_and_gate(
                    ctx.storage, ctx.dataset_id, ctx.taxonomy, target, ctx.settings,
                    judge=ctx.judge)
                return self._json({"ok": True, "gate": to_dict(ctx.last_gate)})

            if path == "/api/dataset":
                return self._switch_dataset(str(body.get("id", "")))

            if path == "/api/import":
                return self._import(body)

            if path == "/api/taxonomy":
                return self._edit_taxonomy(body)

            if path == "/api/run":
                return self._start_run(body)

            if path == "/api/bootstrap/start":
                from .engine.goldset import cluster_sample
                items = ctx.storage.get_items(ctx.dataset_id)
                if not items:
                    return self._json({"error": "no items — import data first"}, 400)
                if ctx.taxonomy.label_type == "span":
                    return self._json({"error": "span gold is authored by quotes, "
                                                "not in bootstrap mode"}, 400)
                n = int(body.get("n", 100))
                sample = cluster_sample({it.id: it.render() for it in items}, n,
                                        exclude=set(ctx.storage.get_gold(ctx.dataset_id)))
                if not sample:
                    return self._json({"error": "every item already holds gold"}, 400)
                ctx.bootstrap = sample
                ctx.bootstrap_target = len(sample)
                ctx.bootstrap_done = []
                return self._json({"ok": True, "n": len(sample)})

            if path == "/api/bootstrap/stop":
                authored = sum(1 for _i, lab in ctx.bootstrap_done or [] if lab)
                ctx.bootstrap = None
                ctx.bootstrap_done = []
                return self._json({"ok": True, "authored": authored})

            return self._json({"error": "not found"}, 404)

        def _switch_dataset(self, dataset_id):
            from .app import taxonomy_for_dataset
            if ctx.run_state["running"]:
                return self._json({"error": "a labeling run is in progress"}, 409)
            row = ctx.storage.get_dataset(dataset_id)
            if row is None:
                return self._json({"error": f"no dataset '{dataset_id}'"}, 400)
            tax = taxonomy_for_dataset(ctx.storage, dataset_id)
            if tax is None:
                return self._json({"error": f"dataset '{dataset_id}' has no taxonomy "
                                            "on record"}, 400)
            ctx.dataset_id = dataset_id
            ctx.taxonomy = tax
            ctx.bootstrap = None
            ctx.bootstrap_done = []
            ctx.last_gate = None
            preds = ctx.storage.get_predictions(dataset_id)
            if preds:
                runs = ctx.storage.get_runs(dataset_id, limit=1)
                if runs:
                    ctx.settings.target_precision = float(runs[-1]["target"])
                ctx.last_gate = calibrate_and_gate(
                    ctx.storage, dataset_id, tax, ctx.settings.target_precision,
                    ctx.settings, log_events=False, judge=ctx.judge)
            return self._json({"ok": True, "dataset_id": dataset_id})

        def _import(self, body):
            from .app import ingest, load_gold, load_items, load_taxonomy
            if ctx.run_state["running"]:
                return self._json({"error": "a labeling run is in progress"}, 409)
            dataset = str(body.get("dataset", "")).strip()
            items_text = body.get("items", "")
            items_name = str(body.get("items_name", "items.jsonl"))
            if not dataset or not items_text:
                return self._json({"error": "need a dataset name and an items file"}, 400)
            tmp = tempfile.mkdtemp()

            def _write(name, content):
                p = os.path.join(tmp, os.path.basename(name))
                with open(p, "w", encoding="utf-8") as f:
                    f.write(content)
                return p

            try:
                items = load_items(_write(items_name, items_text), dataset)
                if not items:
                    return self._json({"error": "no items parsed — check the file "
                                                "has a text column/field"}, 400)
                if body.get("taxonomy"):
                    tax = load_taxonomy(_write("taxonomy.json", body["taxonomy"]))
                else:
                    from .app import taxonomy_for_dataset
                    tax = (taxonomy_for_dataset(ctx.storage, dataset)
                           if ctx.storage.get_dataset(dataset) else None)
                    if tax is None:
                        return self._json({"error": "new dataset needs a taxonomy "
                                                    "(labels + definitions)"}, 400)
                gold = None
                if body.get("gold"):
                    gold = load_gold(_write(str(body.get("gold_name", "gold.jsonl")),
                                            body["gold"]), dataset, items=items)
            except Exception as e:
                return self._json({"error": f"import failed: {e}"}, 400)
            ingest(ctx.storage, dataset, dataset, items, tax, gold)
            ctx.storage.set_kv(dataset, "taxonomy_id", tax.id)
            ctx.dataset_id = dataset
            ctx.taxonomy = tax
            ctx.last_gate = None
            ctx.bootstrap = None
            ctx.bootstrap_done = []
            return self._json({"ok": True, "dataset_id": dataset,
                               "n_items": len(items), "n_gold": len(gold or [])})

        def _edit_taxonomy(self, body):
            if ctx.run_state["running"]:
                return self._json({"error": "a labeling run is in progress"}, 409)
            labels = body.get("labels")
            if labels is not None:
                labels = [str(l).strip() for l in labels if str(l).strip()]
                if len(labels) < 2:
                    return self._json({"error": "a rubric needs at least 2 labels"}, 400)
                ctx.taxonomy.labels = labels
            if body.get("definitions") is not None:
                ctx.taxonomy.definitions = {
                    str(k): str(v) for k, v in dict(body["definitions"]).items()}
            if body.get("guidelines") is not None:
                ctx.taxonomy.guidelines = str(body["guidelines"])
            ctx.taxonomy.version += 1
            ctx.storage.add_taxonomy(ctx.taxonomy)
            return self._json({"ok": True, "version": ctx.taxonomy.version})

        def _start_run(self, body):
            with ctx._run_lock:
                if ctx.run_state["running"]:
                    return self._json({"error": "already running"}, 409)
                n_items = len(ctx.storage.get_items(ctx.dataset_id))
                if not n_items:
                    return self._json({"error": "no items — import data first"}, 400)
                target = float(body.get("target_precision",
                                        ctx.settings.target_precision))
                ctx.settings.target_precision = target
                ctx.run_state = {"running": True, "done": 0,
                                 "total": n_items, "error": None}

            def _go():
                from .app import run_full
                try:
                    ctx.last_gate = run_full(
                        ctx.storage, ctx.dataset_id, ctx.taxonomy, ctx.settings,
                        target_precision=target,
                        on_progress=lambda d, t: ctx.run_state.update(done=d, total=t))
                except Exception as e:
                    ctx.run_state["error"] = str(e)
                finally:
                    ctx.run_state["running"] = False

            threading.Thread(target=_go, daemon=True).start()
            return self._json({"ok": True, "total": n_items})

        def _serve_static(self, rel):
            # Canonicalize and require the resolved path to stay inside WEB_DIR.
            # Rejects absolute paths, drive letters, "..", and symlink escapes —
            # string-munging guards are not sufficient (see review).
            web_root = os.path.realpath(WEB_DIR)
            full = os.path.realpath(os.path.join(web_root, rel.lstrip("/\\")))
            try:
                contained = os.path.commonpath([full, web_root]) == web_root
            except ValueError:  # different drives on Windows
                contained = False
            if not contained or not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            ext = os.path.splitext(full)[1]
            with open(full, "rb") as f:
                self._send(200, f.read(), _CT.get(ext, "application/octet-stream"))

    return Handler


def serve(storage, dataset_id, taxonomy, settings, gate_result=None,
          bootstrap_ids=None, on_ready=None):
    ctx = Context(storage, dataset_id, taxonomy, settings, judge=make_judge(settings),
                  bootstrap_ids=bootstrap_ids)
    ctx.last_gate = gate_result
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(ctx))
    mode = "gold bootstrap" if bootstrap_ids else "review UI"
    url = f"http://{settings.host}:{settings.port}"
    print(f"Tessera {mode}  ->  {url}")
    print("Ctrl+C to stop.", flush=True)
    if on_ready is not None:
        on_ready(url)   # socket is already bound; safe to open a browser at it
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
        if ctx.bootstrap is not None:
            n_gold = len(storage.get_gold(dataset_id))
            authored = sum(1 for _, lab in ctx.bootstrap_done if lab is not None)
            print(f"bootstrap session: {authored} gold authored this session; "
                  f"the dataset now holds {n_gold} gold label(s).")
            print(f"next: python -m tessera --db {settings.db_path} label "
                  f"--data <items> --taxonomy <tax> --dataset {dataset_id}  "
                  "(or run your usual loop — the gold is stored)")
