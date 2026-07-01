"use strict";
// Keyboard-first review loop (docs/07). Pre-fills the model's suggested label;
// the human confirms with one keystroke or corrects by picking another label.

let queue = [];
let idx = 0;
let labels = [];

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
  labels = s.taxonomy.labels;
  const c = s.counts;
  document.getElementById("stats").textContent =
    `${c.items} items · ${c.auto_applied} auto · ${c.queued} queued · ${c.finalized} finalized`;
  if (s.gate) {
    document.getElementById("coverage").innerHTML =
      `Auto-labeled <b>${s.gate.n_auto}</b> (<b>${fmtPct(s.gate.coverage)}</b> of dataset) ` +
      `at ≥ ${fmtPct(s.gate.target_precision)} precision · ${s.gate.n_queue} routed to you`;
    document.getElementById("target").value = s.gate.target_precision;
    document.getElementById("targetVal").textContent = fmtPct(s.gate.target_precision);
  }
}

async function refreshQueue() {
  const q = await getJSON("/api/queue");
  queue = q.queue || [];
  idx = 0;
  render();
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
  document.getElementById("progress").textContent = `Item ${idx + 1} / ${queue.length}`;
  const conf = document.getElementById("conf");
  conf.textContent = `confidence ${fmtPct(item.confidence)} · agreement ${fmtPct(item.agreement)}`;
  conf.className = "conf " + (item.confidence >= 0.66 ? "hi" : item.confidence >= 0.4 ? "mid" : "lo");
  renderText(item);
  document.getElementById("suggested").textContent = item.predicted_label;
  const rat = document.getElementById("rationale");
  rat.textContent = item.rationale; rat.hidden = true;

  const box = document.getElementById("labels");
  box.innerHTML = "";
  labels.forEach((lab, i) => {
    const b = document.createElement("button");
    b.className = "label-btn" + (lab === item.predicted_label ? " suggested-btn" : "");
    b.innerHTML = `<span class="num">${i + 1}</span> ${lab}`;
    b.onclick = () => decide(lab === item.predicted_label ? "accept" : "edit", lab);
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

function toggleExplain() {
  const rat = document.getElementById("rationale");
  rat.hidden = !rat.hidden;
}

document.addEventListener("keydown", (e) => {
  if (document.getElementById("reviewer").hidden) return;
  const item = queue[idx];
  if (!item) return;
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

(async function init() { await refreshState(); await refreshQueue(); })();
