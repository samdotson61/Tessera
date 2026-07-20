"use strict";
// Keyboard-first review loop (docs/07). Pre-fills the model's suggested label;
// the human confirms with one keystroke or corrects by picking another label.

let queue = [];
let idx = 0;
let labels = [];
let labelType = "classification";
let currentSpans = [];   // span-mode working copy of the annotation being reviewed
let bootstrapMode = false;   // cold-start gold authoring: no model has run yet
let S = null;            // last /api/state payload — the UI's single source of truth
let runPoll = null;      // interval handle while a labeling run is in flight

// ---- workflow shell: panels, strip, guidance ----

const PANELS = ["import", "rubric", "gold", "run", "review", "export"];

function showPanel(name) {
  PANELS.forEach((p) => {
    document.getElementById("panel-" + p).hidden = p !== name;
    document.querySelector(`.flow button[data-panel="${p}"]`)
      .classList.toggle("active", p === name);
  });
  if (name === "rubric") renderRubricEditor();
}

function currentPanel() {
  for (const p of PANELS) if (!document.getElementById("panel-" + p).hidden) return p;
  return "review";
}

function flowState() {
  // Derive each stage's state from the dataset's REAL state — the strip is
  // honest guidance, not decoration.
  if (!S) return {};
  const c = S.counts;
  const st = {};
  st.import = c.items > 0 ? "done" : "attn";
  st.rubric = c.items > 0 ? "done" : "";
  st.gold = c.gold >= 10 ? "done" : (c.items > 0 && c.predictions === 0 ? "attn" : "");
  st.run = c.predictions > 0 ? "done"
         : (c.items > 0 && c.gold >= 10 ? "attn" : "");
  const open = c.queued + c.audit_pending;
  st.review = c.predictions === 0 ? "" : (open > 0 ? "attn" : "done");
  st.export = c.finalized > 0 ? "done" : "";
  return st;
}

function guideText(st) {
  if (!S) return "";
  const c = S.counts;
  if (bootstrapMode) return "authoring gold — label the sample in Review, then Stop authoring (step 3)";
  if (st.import === "attn") return "next: Import your items (step 1) — or explore the bundled sample";
  if (st.gold === "attn") return `next: author gold labels (step 3) — ${c.gold} so far; the gate calibrates on these`;
  if (st.run === "attn") return "next: run labeling (step 4) — the model labels everything, the gate keeps its promise";
  if (S.run && S.run.running) return `labeling… ${S.run.done}/${S.run.total}`;
  if (st.review === "attn") return `next: review — ${c.queued} queued + ${c.audit_pending} audit item(s) need you`;
  if (st.export === "done" && st.review === "done") return "all reviewed — export your labels (step 6), or import more data";
  return "";
}

function renderFlow() {
  const st = flowState();
  PANELS.forEach((p) => {
    const b = document.querySelector(`.flow button[data-panel="${p}"]`);
    b.classList.toggle("done", st[p] === "done");
    b.classList.toggle("attn", st[p] === "attn");
  });
  document.getElementById("guide").textContent = guideText(st);
}

async function getJSON(url) { const r = await fetch(url); return r.json(); }
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function fmtPct(x) { return (x * 100).toFixed(1) + "%"; }

async function refreshState() {
  const s = await getJSON("/api/state");
  S = s;
  labels = s.taxonomy.labels;
  labelType = s.taxonomy.label_type || "classification";
  bootstrapMode = !!s.bootstrap;

  // dataset picker
  const pick = document.getElementById("dsPick");
  pick.innerHTML = "";
  (s.datasets || [s.dataset_id]).forEach((d) => {
    const o = document.createElement("option");
    o.value = o.textContent = d;
    o.selected = d === s.dataset_id;
    pick.appendChild(o);
  });

  // serving status + run progress (Label panel)
  const sv = document.getElementById("serving");
  sv.textContent = `model: ${s.serving.provider}` +
    (s.serving.ok ? " — answering" : " — NOT answering") +
    (s.serving.note ? ` · ${s.serving.note}` : "");
  sv.className = "serving " + (s.serving.ok ? "ok" : "bad");
  const bar = document.getElementById("runBar");
  if (s.run && (s.run.running || s.run.error)) {
    bar.hidden = false;
    const pctRun = s.run.total ? Math.round(100 * s.run.done / s.run.total) : 0;
    document.getElementById("runFill").style.width = pctRun + "%";
    document.getElementById("runText").textContent = s.run.error
      ? "run failed — see below" : `labeling ${s.run.done} / ${s.run.total}`;
    if (s.run.error) {
      const m = document.getElementById("runMsg");
      m.textContent = s.run.error; m.className = "notice err";
    }
  } else { bar.hidden = true; }
  document.getElementById("bootStop").hidden = !bootstrapMode;
  renderFlow();

  if (bootstrapMode) {
    document.querySelector(".hint").innerHTML =
      "<b>1–9</b> pick the correct label · <b>R</b> skip · <b>U</b> undo · " +
      "<b>J/K</b> next/prev — you are authoring seed gold; no model has run yet";
    const b = s.bootstrap;
    document.getElementById("stats").textContent =
      `${s.counts.items} items · authoring gold: ${b.done} done · ${b.remaining} to go`;
    document.getElementById("coverage").innerHTML =
      `Gold so far: <b>${b.gold}</b> — labels you author here calibrate the gate ` +
      `(aim for ~50–150; hit <b>Stop authoring</b> in step 3 when done)`;
    return;
  }
  if (labelType === "span") {
    document.querySelector(".hint").innerHTML =
      "<b>select text + 1–9</b> add span · <b>click a span</b> remove · " +
      "<b>Enter</b>/<b>A</b> submit · <b>R</b> reject · <b>U</b> undo · " +
      "<b>J/K</b> next/prev · <b>E</b> explain · <b>Q</b> report";
  }
  const c = s.counts;
  document.getElementById("stats").textContent =
    `${c.items} items · ${c.auto_applied} auto · ${c.queued} queued · ${c.finalized} finalized` +
    (c.audit_pending ? ` · ${c.audit_pending} audit` : "");
  if (s.gate) {
    document.getElementById("coverage").innerHTML =
      `Auto-labeled <b>${s.gate.n_auto}</b> (<b>${fmtPct(s.gate.coverage)}</b> of dataset) ` +
      `at ≥ ${fmtPct(s.gate.target_precision)} precision · ${s.gate.n_queue} routed to you`;
    document.getElementById("target").value = s.gate.target_precision;
    document.getElementById("targetVal").textContent = fmtPct(s.gate.target_precision);
  } else {
    document.getElementById("coverage").textContent = c.predictions
      ? "re-gating…" : "not labeled yet — Import (1), author Gold (3), then Run (4)";
  }
}

// ---- import / rubric / gold / run / dataset handlers ----

function readFile(input) {
  const f = input.files && input.files[0];
  if (!f) return Promise.resolve(null);
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res({ name: f.name, content: r.result });
    r.onerror = rej;
    r.readAsText(f);
  });
}

function note(id, msg, ok) {
  const el = document.getElementById(id);
  el.textContent = msg;
  el.className = "notice " + (ok ? "ok" : "err");
}

async function doImport() {
  const name = document.getElementById("impName").value.trim();
  const items = await readFile(document.getElementById("impItems"));
  if (!name || !items) return note("impMsg", "need a dataset name and an items file", false);
  const tax = await readFile(document.getElementById("impTax"));
  const gold = await readFile(document.getElementById("impGold"));
  const r = await postJSON("/api/import", {
    dataset: name, items_name: items.name, items: items.content,
    taxonomy: tax ? tax.content : null,
    gold_name: gold ? gold.name : null, gold: gold ? gold.content : null,
  });
  if (r.error) return note("impMsg", r.error, false);
  note("impMsg", `imported ${r.n_items} item(s)` +
       (r.n_gold ? ` + ${r.n_gold} gold` : "") +
       ` into '${r.dataset_id}' — next: rubric (2) and gold (3)`, true);
  await refreshState(); await refreshQueue();
}

function renderRubricEditor() {
  if (!S) return;
  const t = S.taxonomy;
  document.getElementById("taxVersion").textContent = `v${t.version} · ${t.label_type}`;
  document.getElementById("taxLabels").value = t.labels.join("\n");
  document.getElementById("taxGuide").value = t.guidelines || "";
  const box = document.getElementById("taxDefs");
  box.innerHTML = "";
  t.labels.forEach((lab) => {
    const w = document.createElement("label");
    w.className = "defrow";
    w.title = `Definition the model reads for '${lab}'. Encode YOUR conventions here.`;
    const s = document.createElement("span");
    s.className = "deflabel"; s.textContent = lab;
    const ta = document.createElement("textarea");
    ta.rows = 2; ta.dataset.label = lab; ta.value = t.definitions[lab] || "";
    w.appendChild(s); w.appendChild(ta);
    box.appendChild(w);
  });
}

async function saveRubric() {
  const labs = document.getElementById("taxLabels").value
    .split("\n").map((x) => x.trim()).filter(Boolean);
  const defs = {};
  document.querySelectorAll("#taxDefs textarea").forEach((ta) => {
    if (labs.includes(ta.dataset.label)) defs[ta.dataset.label] = ta.value;
  });
  const r = await postJSON("/api/taxonomy", {
    labels: labs, definitions: defs,
    guidelines: document.getElementById("taxGuide").value,
  });
  if (r.error) return note("taxMsg", r.error, false);
  note("taxMsg", `saved as v${r.version} — re-run labeling (4) to apply it`, true);
  await refreshState();
  renderRubricEditor();
}

async function goldStart() {
  const n = parseInt(document.getElementById("bootN").value, 10) || 60;
  const r = await postJSON("/api/bootstrap/start", { n });
  if (r.error) return note("bootMsg", r.error, false);
  note("bootMsg", `${r.n} item(s) picked across the corpus — label them in Review`, true);
  await refreshState(); await refreshQueue();
  showPanel("review");
}

async function goldStop() {
  const r = await postJSON("/api/bootstrap/stop", {});
  if (r.error) return note("bootMsg", r.error, false);
  note("bootMsg", `done — ${r.authored} gold label(s) authored this session`, true);
  await refreshState(); await refreshQueue();
}

async function startRun() {
  const target = parseFloat(document.getElementById("runTarget").value) || 0.9;
  const r = await postJSON("/api/run", { target_precision: target });
  if (r.error) return note("runMsg", r.error, false);
  note("runMsg", `labeling ${r.total} item(s)…`, true);
  if (runPoll) clearInterval(runPoll);
  runPoll = setInterval(async () => {
    await refreshState();
    if (S && S.run && !S.run.running) {
      clearInterval(runPoll); runPoll = null;
      if (!S.run.error) note("runMsg", "run complete — the queue is ready in Review (5)", true);
      await refreshQueue();
    }
  }, 1500);
}

async function switchDataset(id) {
  const r = await postJSON("/api/dataset", { id });
  if (r.error) { await refreshState(); return; }
  await refreshState(); await refreshQueue();
}

async function refreshQueue() {
  const q = await getJSON("/api/queue");
  queue = q.queue || [];
  idx = 0;
  render();
}

// ---- span mode (NER): highlight, click-to-remove, select+number to add ----

function canonicalSpans(spans) {
  const uniq = [...new Map(spans.map(s => [`${s.start}|${s.end}|${s.type}`, s])).values()]
    .sort((a, b) => a.start - b.start || a.end - b.end ||
                    (a.type < b.type ? -1 : a.type > b.type ? 1 : 0));
  return JSON.stringify(uniq.map(s => ({start: s.start, end: s.end, type: s.type})));
}

function renderSpanText(item) {
  const box = document.getElementById("text");
  box.textContent = "";
  box.classList.add("span-text");
  const text = item.text;
  let pos = 0;
  [...currentSpans].sort((a, b) => a.start - b.start).forEach((s) => {
    if (s.start > pos) box.appendChild(document.createTextNode(text.slice(pos, s.start)));
    const m = document.createElement("span");
    m.className = "ent ent-" + (labels.indexOf(s.type) % 6);
    m.title = s.type + " — click to remove";
    m.textContent = text.slice(s.start, s.end);
    const tag = document.createElement("sup");
    tag.className = "ent-type";
    tag.textContent = s.type;
    m.appendChild(tag);
    m.onclick = () => {
      currentSpans = currentSpans.filter((x) => x !== s);
      renderSpanText(item);
      updateSpanSummary();
    };
    box.appendChild(m);
    pos = s.end;
  });
  if (pos < text.length) box.appendChild(document.createTextNode(text.slice(pos)));
}

function offsetInText(container, node, offset) {
  let total = 0, found = false;
  (function walk(n) {
    if (found) return;
    if (n === node) { total += offset; found = true; return; }
    if (n.nodeType === 3) { total += n.length; return; }
    if (n.classList && n.classList.contains("ent-type")) return;  // type tags aren't item text
    for (const c of n.childNodes) { walk(c); if (found) return; }
  })(container);
  return found ? total : -1;
}

function addSpanFromSelection(i) {
  const item = queue[idx];
  if (!item || i >= labels.length) return;
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
  const box = document.getElementById("text");
  const r = sel.getRangeAt(0);
  if (!box.contains(r.startContainer) || !box.contains(r.endContainer)) return;
  let a = offsetInText(box, r.startContainer, r.startOffset);
  let b = offsetInText(box, r.endContainer, r.endOffset);
  if (a < 0 || b < 0 || a === b) return;
  if (a > b) [a, b] = [b, a];
  if (currentSpans.some((s) => a < s.end && s.start < b)) return;  // no overlaps
  currentSpans.push({ start: a, end: b, type: labels[i] });
  sel.removeAllRanges();
  renderSpanText(item);
  updateSpanSummary();
}

function updateSpanSummary() {
  const item = queue[idx];
  const cur = canonicalSpans(currentSpans);
  const pred = canonicalSpans(JSON.parse(item.predicted_label || "[]"));
  document.getElementById("suggested").textContent =
    `${currentSpans.length} span(s)` + (cur === pred ? "" : " (edited)");
}

function submitSpans() {
  const item = queue[idx];
  if (!item) return;
  const cur = canonicalSpans(currentSpans);
  const pred = canonicalSpans(JSON.parse(item.predicted_label || "[]"));
  decide(cur === pred ? "accept" : "edit", cur);
}

function renderText(item) {
  // Pairwise items carry the two candidate responses in meta; render them
  // side by side under the prompt. Classification items stay plain text.
  const box = document.getElementById("text");
  const m = item.meta || {};
  if (m.response_a === undefined || m.response_b === undefined) {
    box.textContent = item.text;
    return;
  }
  box.textContent = "";
  if (item.text) {
    const q = document.createElement("div");
    q.className = "pair-prompt";
    q.textContent = item.text;
    box.appendChild(q);
  }
  const wrap = document.createElement("div");
  wrap.className = "pair-wrap";
  [["A", m.response_a], ["B", m.response_b]].forEach(([side, text]) => {
    const panel = document.createElement("div");
    panel.className = "pair" + (side === item.predicted_label ? " pair-suggested" : "");
    const h = document.createElement("div");
    h.className = "pair-head";
    h.textContent = side;
    const body = document.createElement("div");
    body.textContent = String(text);
    panel.appendChild(h);
    panel.appendChild(body);
    wrap.appendChild(panel);
  });
  box.appendChild(wrap);
}

function render() {
  const reviewer = document.getElementById("reviewer");
  const empty = document.getElementById("empty");
  if (!queue.length || idx >= queue.length) {
    reviewer.hidden = true; empty.hidden = false; return;
  }
  empty.hidden = true; reviewer.hidden = false;
  const item = queue[idx];
  const prog = document.getElementById("progress");
  prog.textContent = `Item ${idx + 1} / ${queue.length}`;
  if (item.audit) {
    const b = document.createElement("span");
    b.className = "audit-tag";
    b.title = "This label was auto-applied; you are verifying it (accept confirms, a pick overturns).";
    b.textContent = "AUDIT — label shipped, verify it";
    prog.appendChild(b);
  }
  const conf = document.getElementById("conf");
  if (item.bootstrap) {
    conf.textContent = "no model prediction — you author this label";
    conf.className = "conf mid";
    renderText(item);
    document.getElementById("suggested").textContent = "— (bootstrap)";
    const rat0 = document.getElementById("rationale");
    rat0.textContent = item.rationale; rat0.hidden = true;
    const box0 = document.getElementById("labels");
    box0.innerHTML = "";
    labels.forEach((lab, i) => {
      const b = document.createElement("button");
      b.className = "label-btn";
      b.innerHTML = `<span class="num">${i + 1}</span> ${lab}`;
      b.onclick = () => bootstrapPick(lab);
      box0.appendChild(b);
    });
    return;
  }
  conf.textContent = `confidence ${fmtPct(item.confidence)} · agreement ${fmtPct(item.agreement)}`;
  conf.className = "conf " + (item.confidence >= 0.66 ? "hi" : item.confidence >= 0.4 ? "mid" : "lo");
  if (labelType === "span") {
    currentSpans = JSON.parse(item.predicted_label || "[]");
    renderSpanText(item);
    updateSpanSummary();
  } else {
    renderText(item);
    document.getElementById("suggested").textContent = item.predicted_label;
  }
  const rat = document.getElementById("rationale");
  rat.textContent = item.rationale; rat.hidden = true;

  const box = document.getElementById("labels");
  box.innerHTML = "";
  labels.forEach((lab, i) => {
    const b = document.createElement("button");
    b.innerHTML = `<span class="num">${i + 1}</span> ${lab}`;
    if (labelType === "span") {
      b.className = "label-btn ent-btn ent-" + (i % 6);
      b.title = `Select text, then click (or press ${i + 1}) to mark it as ${lab}`;
      b.onclick = () => addSpanFromSelection(i);
    } else {
      b.className = "label-btn" + (lab === item.predicted_label ? " suggested-btn" : "");
      b.onclick = () => decide(lab === item.predicted_label ? "accept" : "edit", lab);
    }
    box.appendChild(b);
  });
}

async function decide(action, label) {
  const item = queue[idx];
  if (!item) return;
  await postJSON("/api/action", { item_id: item.item_id, action, label });
  queue.splice(idx, 1);
  if (idx >= queue.length) idx = Math.max(0, queue.length - 1);
  await refreshState();
  render();
}

async function bootstrapPick(label) {   // label null = skip this item
  const item = queue[idx];
  if (!item) return;
  await postJSON("/api/bootstrap", { item_id: item.item_id, label });
  queue.splice(idx, 1);
  if (idx >= queue.length) idx = Math.max(0, queue.length - 1);
  await refreshState();
  render();
}

function toggleExplain() {
  const rat = document.getElementById("rationale");
  rat.hidden = !rat.hidden;
}

async function undo() {
  try {
    const r = await postJSON("/api/undo", {});
    if (r.ok) { await refreshState(); await refreshQueue(); }
  } catch (e) { /* nothing to undo */ }
}

function reliabilitySvg(bins) {
  const NS = "http://www.w3.org/2000/svg";
  const W = 280, H = 230, m = { l: 40, r: 12, t: 12, b: 34 };
  const pw = W - m.l - m.r, ph = H - m.t - m.b;
  const X = (v) => m.l + v * pw;
  const Y = (v) => m.t + (1 - v) * ph;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  svg.setAttribute("width", W);
  const el = (tag, attrs, text) => {
    const n = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    if (text) n.textContent = text;
    svg.appendChild(n);
    return n;
  };
  // axes + perfect-calibration diagonal
  el("line", { x1: X(0), y1: Y(0), x2: X(1), y2: Y(0), class: "rel-axis" });
  el("line", { x1: X(0), y1: Y(0), x2: X(0), y2: Y(1), class: "rel-axis" });
  el("line", { x1: X(0), y1: Y(0), x2: X(1), y2: Y(1), class: "rel-diag" });
  for (const v of [0, 0.5, 1]) {
    el("text", { x: X(v), y: Y(0) + 14, class: "rel-tick", "text-anchor": "middle" }, v.toFixed(1));
    el("text", { x: X(0) - 6, y: Y(v) + 4, class: "rel-tick", "text-anchor": "end" }, v.toFixed(1));
  }
  el("text", { x: X(0.5), y: H - 4, class: "rel-label", "text-anchor": "middle" },
     "calibrated confidence");
  const yl = el("text", { x: 10, y: Y(0.5), class: "rel-label", "text-anchor": "middle" },
                "accuracy");
  yl.setAttribute("transform", `rotate(-90 10 ${Y(0.5)})`);
  const maxN = Math.max(1, ...bins.map((b) => b.count));
  bins.forEach((b) => {
    const c = el("circle", {
      cx: X(b.avg_conf), cy: Y(b.accuracy),
      r: 3 + 6 * Math.sqrt(b.count / maxN), class: "rel-dot",
    });
    c.appendChild(Object.assign(document.createElementNS(NS, "title"), {
      textContent: `conf ${b.avg_conf} · acc ${b.accuracy} · n=${b.count}`,
    }));
  });
  return svg;
}

async function toggleReport() {
  const sec = document.getElementById("report");
  if (!sec.hidden) { sec.hidden = true; return; }
  let rep;
  try { rep = await getJSON("/api/report"); } catch (e) { return; }
  if (!rep || rep.error) return;
  const body = document.getElementById("reportBody");
  body.innerHTML = "";
  const head = document.createElement("div");
  head.className = "rep-head";
  const ci = rep.coverage_ci
    ? ` (95% CI ${fmtPct(rep.coverage_ci[0])}–${fmtPct(rep.coverage_ci[1])})`
    : "";
  head.textContent =
    `coverage ${fmtPct(rep.coverage)}${ci} at ≥ ${fmtPct(rep.target_precision)} target · ` +
    `achieved ${fmtPct(rep.achieved_precision)} · ECE ${rep.ece} · gold n=${rep.n_gold}`;
  body.appendChild(head);
  body.appendChild(reliabilitySvg(rep.reliability_bins || []));
  const ul = document.createElement("ul");
  ul.className = "rep-caveats";
  (rep.caveats || []).forEach((c) => {
    const li = document.createElement("li");
    li.textContent = c;
    ul.appendChild(li);
  });
  body.appendChild(ul);
  if (rep.runs && rep.runs.length > 1) {
    const t = document.createElement("table");
    t.className = "rep-runs";
    t.innerHTML = "<tr><th>run</th><th>coverage</th><th>gold</th><th>queue</th><th>touches</th></tr>" +
      rep.runs.map((r, i) =>
        `<tr><td>${i + 1}</td><td>${fmtPct(r.coverage)}</td><td>${r.n_gold}</td>` +
        `<td>${r.n_queue}</td><td>${r.human_touches}</td></tr>`).join("");
    body.appendChild(t);
  }
  sec.hidden = false;
}

document.addEventListener("keydown", (e) => {
  // typing in a form field is never a review shortcut
  if (/^(input|textarea|select)$/i.test(e.target.tagName)) return;
  if (currentPanel() !== "review") return;
  if (e.key.toLowerCase() === "u") { undo(); return; }         // works even with an empty queue
  if (e.key.toLowerCase() === "q") { toggleReport(); return; }
  if (document.getElementById("reviewer").hidden) return;
  const item = queue[idx];
  if (!item) return;
  if (item.bootstrap) {
    if (e.key.toLowerCase() === "r") { bootstrapPick(null); }
    else if (e.key.toLowerCase() === "j") { if (idx < queue.length - 1) { idx++; render(); } }
    else if (e.key.toLowerCase() === "k") { if (idx > 0) { idx--; render(); } }
    else if (/^[1-9]$/.test(e.key)) {
      const i = parseInt(e.key, 10) - 1;
      if (i < labels.length) bootstrapPick(labels[i]);
    }
    return;
  }
  if (labelType === "span") {
    if (e.key === "Enter" || e.key.toLowerCase() === "a") { e.preventDefault(); submitSpans(); }
    else if (e.key.toLowerCase() === "r") { decide("reject", null); }
    else if (e.key.toLowerCase() === "e") { toggleExplain(); }
    else if (e.key.toLowerCase() === "j") { if (idx < queue.length - 1) { idx++; render(); } }
    else if (e.key.toLowerCase() === "k") { if (idx > 0) { idx--; render(); } }
    else if (/^[1-9]$/.test(e.key)) { addSpanFromSelection(parseInt(e.key, 10) - 1); }
    return;
  }
  if (e.key === "Enter" || e.key.toLowerCase() === "a") { e.preventDefault(); decide("accept", item.predicted_label); }
  else if (e.key.toLowerCase() === "r") { decide("reject", null); }
  else if (e.key.toLowerCase() === "e") { toggleExplain(); }
  else if (e.key.toLowerCase() === "j") { if (idx < queue.length - 1) { idx++; render(); } }
  else if (e.key.toLowerCase() === "k") { if (idx > 0) { idx--; render(); } }
  else if (/^[1-9]$/.test(e.key)) {
    const i = parseInt(e.key, 10) - 1;
    if (i < labels.length) decide(labels[i] === item.predicted_label ? "accept" : "edit", labels[i]);
  }
});

document.getElementById("target").addEventListener("input", (e) => {
  document.getElementById("targetVal").textContent = fmtPct(parseFloat(e.target.value));
});
document.getElementById("regate").addEventListener("click", async () => {
  const target = parseFloat(document.getElementById("target").value);
  await postJSON("/api/gate", { target_precision: target });
  await refreshState();
  await refreshQueue();
});
document.getElementById("explainBtn").addEventListener("click", toggleExplain);
document.getElementById("reportBtn").addEventListener("click", toggleReport);

// workflow shell wiring
document.querySelectorAll(".flow button").forEach((b) =>
  b.addEventListener("click", () => showPanel(b.dataset.panel)));
document.getElementById("dsPick").addEventListener("change", (e) => switchDataset(e.target.value));
document.getElementById("impGo").addEventListener("click", doImport);
document.getElementById("taxSave").addEventListener("click", saveRubric);
document.getElementById("bootStart").addEventListener("click", goldStart);
document.getElementById("bootStop").addEventListener("click", goldStop);
document.getElementById("runGo").addEventListener("click", startRun);

(async function init() {
  await refreshState();
  await refreshQueue();
  // land on the stage that needs attention, not always on review
  const st = flowState();
  showPanel(st.import === "attn" ? "import" : (st.gold === "attn" ? "gold"
            : (st.run === "attn" ? "run" : "review")));
  if (S && S.run && S.run.running && !runPoll) startRunPollOnly();
})();

function startRunPollOnly() {
  runPoll = setInterval(async () => {
    await refreshState();
    if (S && S.run && !S.run.running) {
      clearInterval(runPoll); runPoll = null;
      await refreshQueue();
    }
  }, 1500);
}
