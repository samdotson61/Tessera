"""Minimal review server (stdlib http.server).

Serves the keyboard-first review UI (tessera/web) and a small JSON API backed by
Storage. Production replaces this with FastAPI + the React/Label-Studio UI
(docs/03, docs/07); the contracts here mirror docs/06.
"""
from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from .schemas import to_dict
from .engine.router import order_queue
from .pipeline import record_human_action, calibrate_and_gate
from .quality import build_quality_report
from .flywheel import event_stats

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
_CT = {".html": "text/html", ".js": "application/javascript", ".css": "text/css"}


class Context:
    def __init__(self, storage, dataset_id, taxonomy, settings):
        self.storage = storage
        self.dataset_id = dataset_id
        self.taxonomy = taxonomy
        self.settings = settings
        self.last_gate = None


def _queue_payload(ctx):
    preds = ctx.storage.get_predictions(ctx.dataset_id)
    items = {it.id: it for it in ctx.storage.get_items(ctx.dataset_id)}
    out = []
    for p in order_queue(preds):
        it = items.get(p.item_id)
        out.append({
            "item_id": p.item_id,
            "text": it.text if it else "",
            "predicted_label": p.label,
            "confidence": round(p.confidence(), 4),
            "agreement": round(p.agreement, 3),
            "rationale": p.rationale,
            "distribution": {k: round(v, 4) for k, v in sorted(
                p.distribution.items(), key=lambda kv: kv[1], reverse=True)},
        })
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
                return self._json({
                    "dataset_id": ctx.dataset_id,
                    "taxonomy": {"name": ctx.taxonomy.name, "labels": ctx.taxonomy.labels,
                                 "definitions": ctx.taxonomy.definitions},
                    "counts": c,
                    "target_precision": ctx.settings.target_precision,
                    "gate": to_dict(ctx.last_gate) if ctx.last_gate else None,
                    "events": event_stats(ctx.storage, ctx.dataset_id),
                })
            if path == "/api/queue":
                return self._json({"queue": _queue_payload(ctx)})
            if path == "/api/report":
                if not ctx.last_gate:
                    return self._json({"error": "run gating first"}, 400)
                report = build_quality_report(ctx.storage, ctx.dataset_id, ctx.taxonomy, ctx.last_gate)
                return self._json(to_dict(report))
            return self._json({"error": "not found"}, 404)

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
                        label=body.get("label"), annotator=body.get("annotator", "human"))
                except (KeyError, ValueError) as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"ok": True, "final_label": final})

            if path == "/api/gate":
                target = float(body.get("target_precision", ctx.settings.target_precision))
                ctx.settings.target_precision = target
                ctx.last_gate = calibrate_and_gate(
                    ctx.storage, ctx.dataset_id, ctx.taxonomy, target, ctx.settings)
                return self._json({"ok": True, "gate": to_dict(ctx.last_gate)})

            return self._json({"error": "not found"}, 404)

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


def serve(storage, dataset_id, taxonomy, settings, gate_result=None):
    ctx = Context(storage, dataset_id, taxonomy, settings)
    ctx.last_gate = gate_result
    httpd = ThreadingHTTPServer((settings.host, settings.port), make_handler(ctx))
    print(f"Tessera review UI  ->  http://{settings.host}:{settings.port}")
    print("Ctrl+C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        httpd.server_close()
