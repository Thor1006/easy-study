"use strict";

/* Silicate — the Glass Membrane operator UI.
 * Reads runtime state from the local host (/api/state, /api/runs/:id), follows
 * the Server-Sent Event stream, and sends requests, steers, and agent-limit
 * changes. All model output is rendered as text, never as HTML. */

const TOKEN = document.querySelector('meta[name="gm-token"]')?.content || "";
const SVG_NS = "http://www.w3.org/2000/svg";
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

const EXAMPLES = [
  "2x2",
  "Compare 3 sorting algorithms for nearly-sorted data",
  "Compare apples, oranges and pears for a picnic",
];
const PHASE_LABEL = { waiting: "waiting", ready: "ready", running: "running", done: "accepted", failed: "failed", cancelled: "cancelled" };
const EVENT_LABEL = {
  run_submitted: "Received", filter_started: "Filter", filter_result: "Filter result", task_created: "Routed",
  TASK_ASSIGNED: "Assigned", result_accepted: "Accepted", result_rejected: "Rejected", STEER: "Steer",
  CONSTRAINT_CHANGED: "Steer applied", ESCALATION_REQUEST: "Upshift", DOWNSHIFT_REQUEST: "Downshift",
  SWARM_REQUEST: "Swarm", SUBTASK_REQUEST: "Subtask", CAP_CHANGED: "Agent limit", rate_limited: "Rate limit",
  CORE_FAILED: "Core failed", core_failed: "Provider error", core_cancelled: "Cancelled", retry: "Retry",
  graph_committed: "Plan", graph_rejected: "Plan rejected", run_done: "Done", run_failed: "Failed",
  CANCEL: "Cancel", swarm_violation: "Swarm limit", experiment: "Experiment",
};
const NODE_W = 200, NODE_H = 62, COL_GAP = 60, ROW_GAP = 16, PAD = 12;

const ui = {
  snapshot: null, detail: null, selected: null, follow: true, mode: "steer", lastActive: null,
  openNode: null, results: new Map(), pending: new Set(), refreshTimer: null, capTimer: null, busy: false,
};

// ------------------------------------------------------------------ small helpers
function h(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

function svg(tag, attrs = {}, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) if (value !== undefined && value !== null) node.setAttribute(key, String(value));
  for (const child of children.flat()) if (child !== null && child !== undefined) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
}

const storage = {
  get(key) { try { return localStorage.getItem(key); } catch { return null; } },
  set(key, value) { try { localStorage.setItem(key, value); } catch { /* storage unavailable */ } },
};

const label = p => ({ claude: "Claude", codex: "Codex", service: "Service" }[p] || p || "");
const truncate = (text, n) => (text && text.length > n ? `${text.slice(0, n - 1)}…` : text || "");
const pct = v => (v === null || v === undefined ? "?" : `${Math.round(v * 100)}%`);
const isActive = run => !!run && (run.status === "running" || run.status === "filtering");
const runs = () => (ui.snapshot ? ui.snapshot.runs : []);
const currentRun = () => runs().find(r => r.id === ui.selected) || null;
const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;
const fmtClock = ts => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-GM-Token": TOKEN },
    body: JSON.stringify(body),
  };
  const res = await fetch(path, init);
  let data = {};
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) throw new Error(data.error || `${res.status} ${res.statusText}`);
  return data;
}

function toast(message, isError = false) {
  const box = $("#toast");
  box.textContent = message;
  box.classList.toggle("error", isError);
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { box.hidden = true; }, 4500);
}

function setConnection(ok, note) {
  const badge = $("#conn");
  badge.textContent = ok ? "Connected" : (note || "Disconnected");
  badge.classList.toggle("bad", !ok);
}

// ------------------------------------------------------------------ data flow
function scheduleRefresh(delay = 120) {
  if (ui.refreshTimer) return;
  ui.refreshTimer = setTimeout(() => { ui.refreshTimer = null; refresh(); }, delay);
}

async function refresh() {
  try {
    const snapshot = await api("/api/state");
    ui.snapshot = snapshot;
    if (snapshot.runs.length && (ui.follow || !ui.selected)) ui.selected = snapshot.runs[0].id;
    ui.detail = ui.selected ? await api(`/api/runs/${encodeURIComponent(ui.selected)}`) : null;
    render();
  } catch (err) {
    setConnection(false, "Runtime unreachable");
  }
}

function connect() {
  const since = ui.snapshot ? ui.snapshot.event_seq : 0;
  const source = new EventSource(`/api/events?since=${since}`);
  source.addEventListener("open", () => setConnection(true));
  source.addEventListener("gm", () => scheduleRefresh());
  source.addEventListener("error", () => setConnection(false, "Reconnecting…"));
}

function selectRun(id) {
  ui.selected = id;
  ui.follow = runs()[0]?.id === id;
  ui.openNode = null;
  scheduleRefresh(0);
}

// ------------------------------------------------------------------ rendering
function render() {
  if (!ui.snapshot) return;
  renderHeader();
  renderMessages();
  renderComposer();
  renderMap();
  renderAgents();
  renderDrawer();
}

function renderHeader() {
  const snap = ui.snapshot;
  const badge = $("#mode-badge");
  badge.hidden = false;
  badge.textContent = snap.demo ? "Demo mode · simulated models" : "Live · uses your subscriptions";
  badge.className = `badge ${snap.demo ? "demo" : "live"}`;
  const meters = $("#provider-meters");
  meters.replaceChildren(...Object.entries(snap.providers).map(([name, pool]) => {
    const fill = h("span", { class: "fill" });
    const mark = h("span", { class: "cap-mark" });
    fill.style.width = `${pool.ceiling ? (pool.live / pool.ceiling) * 100 : 0}%`;
    mark.style.left = `${pool.ceiling ? (pool.cap / pool.ceiling) * 100 : 100}%`;
    mark.hidden = pool.cap >= pool.ceiling;
    const tip = `${label(name)}: ${pool.live} live now · cap ${pool.cap} of ${pool.ceiling}` +
      (pool.backing_off ? " · backing off after a rate limit" : "") + ` · peak ${pool.peak}`;
    return h("div", { class: `meter ${pool.backing_off ? "warn" : ""}`, title: tip },
      h("span", { class: "meter-name", text: label(name) }),
      h("span", { class: "meter-count", text: `${pool.live}/${pool.cap}` }),
      h("span", { class: "bar" }, fill, mark));
  }));
}

function replyFor(run) {
  if (run.status === "done") {
    const parts = [run.answered_by];
    if (run.answered_by && run.answered_by.startsWith("filter")) parts.push("1 process · 0 cores");
    if (run.calls) parts.push(plural(run.calls, "provider call"));
    if (run.info && run.info.rejected_results) parts.push(`${plural(run.info.rejected_results, "stale result")} rejected`);
    return h("div", { class: "bubble reply" },
      h("div", { class: "answer", text: run.answer || "" }),
      h("div", { class: "by", text: parts.filter(Boolean).join(" · ") }));
  }
  if (run.status === "failed" || run.status === "cancelled") {
    return h("div", { class: "bubble reply failed" },
      h("strong", { text: run.status === "failed" ? "Failed" : "Cancelled" }),
      h("div", { text: run.error || "" }));
  }
  return h("div", { class: "bubble reply pending" },
    h("span", { class: "spinner", "aria-hidden": "true" }),
    h("span", { text: run.status_line || "Working…" }));
}

function renderMessages() {
  const box = $("#messages");
  const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  const list = [...runs()].reverse();
  if (!list.length) {
    box.replaceChildren(h("div", { class: "empty" },
      h("p", { text: "Try one of these to see the runtime work:" }),
      ...EXAMPLES.map(text => h("button", {
        class: "chip", type: "button", text,
        onclick: () => { $("#input").value = text; $("#input").focus(); },
      }))));
    return;
  }
  box.replaceChildren(...list.map(run => h("article", {
    class: `turn ${run.id === ui.selected ? "selected" : ""}`, tabindex: "0",
    "aria-label": `Request: ${truncate(run.text, 60)}`,
    onclick: () => selectRun(run.id),
    onkeydown: e => { if (e.key === "Enter") selectRun(run.id); },
  },
    h("div", { class: `bubble user ${run.kind === "steer" ? "steer" : ""}` },
      run.kind === "steer" ? h("span", { class: "tag", text: "Steer" }) : null, run.text),
    replyFor(run))));
  if (nearBottom || ui.follow) box.scrollTop = box.scrollHeight;
}

function renderComposer() {
  const run = currentRun();
  const active = isActive(run) && !!run.task_id;
  if (active && ui.lastActive !== run.id) { ui.mode = "steer"; ui.lastActive = run.id; }
  if (!active) ui.mode = "new";
  const sw = $("#mode-switch");
  sw.hidden = !active;
  $$("button", sw).forEach(b => b.setAttribute("aria-checked", String(b.dataset.mode === ui.mode)));
  const steering = active && ui.mode === "steer";
  $("#input").placeholder = steering
    ? "Steer the running work — e.g. “Answer in one sentence each”"
    : "Ask anything — easy questions are answered directly by the filter";
  if (!ui.busy) $("#send").textContent = steering ? "Steer" : "Send";
  $(".agent-limit").hidden = steering;
}

function renderMap() {
  const d = ui.detail;
  const graph = $("#graph");
  const status = $("#status-line");
  if (!d) {
    $("#run-meta").textContent = "";
    status.textContent = "Waiting for your first request.";
    status.className = "status-line";
    $("#constraints").replaceChildren();
    graph.replaceChildren(h("div", { class: "empty", text: "Send a request to see how Glass Membrane routes and schedules it." }));
    $("#node-detail").hidden = true;
    return;
  }
  status.textContent = d.status_line || d.status;
  status.className = `status-line status-${d.status}`;
  $("#run-meta").textContent = [
    d.id, d.task ? `state v${d.task.version}` : null,
    `peak ${plural(d.peak_processes, "process", "processes")}`,
  ].filter(Boolean).join(" · ");
  $("#constraints").replaceChildren(...(d.task ? d.task.constraints.map(c => h("span", {
    class: "constraint", title: `Added by a steer in state v${c.added_in_version} (scope: ${c.scope})`,
  }, h("b", { text: `v${c.added_in_version}` }), c.text)) : []));
  graph.replaceChildren(d.task && Object.keys(d.task.nodes).length ? drawGraph(d.task) : drawRoute(d));
  renderNodeDetail();
}

function drawRoute(d) {
  const steps = [h("div", { class: "route-step input" }, h("small", { text: "Your input" }), h("span", { text: truncate(d.text, 90) }))];
  const attempts = d.route ? d.route.attempts : [];
  for (const a of attempts) {
    const conf = a.mode === "answer" ? `answer confidence ${pct(a.answer_confidence)}` : `routing confidence ${pct(a.routing_confidence)}`;
    steps.push(h("span", { class: "arrow", "aria-hidden": "true", text: "→" }),
      h("div", { class: `route-step slot tier-${a.tier}` },
        h("small", { text: `${a.slot} · ${a.tier}` }),
        h("span", { text: `${label(a.provider)} — ${a.ok ? `${a.mode}, ${conf}` : a.failure || "failed"}` })));
  }
  if (!attempts.length && isActive(d)) {
    steps.push(h("span", { class: "arrow", "aria-hidden": "true", text: "→" }),
      h("div", { class: "route-step pending" }, h("span", { class: "spinner" }), h("span", { text: d.status_line })));
  }
  if (d.status === "done") {
    steps.push(h("span", { class: "arrow", "aria-hidden": "true", text: "→" }),
      h("div", { class: "route-step answer" },
        h("small", { text: d.kind === "steer" ? "Steer applied" : "Answer" }),
        h("span", { text: truncate(d.answer, 140) })));
  }
  return h("div", { class: "route-wrap" },
    h("div", { class: "route" }, steps),
    d.answered_by && d.answered_by.startsWith("filter")
      ? h("p", { class: "hint", text: "The front-end filter answered this directly: one process, zero reasoning cores." }) : null,
    d.route && d.route.note ? h("p", { class: "hint", text: d.route.note }) : null);
}

function layoutGraph(nodes) {
  const ids = Object.keys(nodes);
  const depth = {};
  const visit = (id, stack) => {
    if (depth[id] !== undefined) return depth[id];
    if (stack.has(id)) return 0;
    stack.add(id);
    const deps = nodes[id].deps.filter(dep => nodes[dep]);
    depth[id] = deps.length ? Math.max(...deps.map(dep => visit(dep, stack))) + 1 : 0;
    stack.delete(id);
    return depth[id];
  };
  ids.forEach(id => visit(id, new Set()));
  const planner = ids.find(id => nodes[id].role === "planner");
  if (planner) ids.forEach(id => { if (id !== planner) depth[id] += 1; });
  const columns = [];
  ids.sort((a, b) => depth[a] - depth[b] || a.localeCompare(b, undefined, { numeric: true }))
    .forEach(id => { (columns[depth[id]] ||= []).push(id); });
  const filled = columns.filter(Boolean);
  const maxRows = Math.max(1, ...filled.map(c => c.length));
  const height = PAD * 2 + maxRows * NODE_H + (maxRows - 1) * ROW_GAP;
  const pos = {};
  filled.forEach((col, ci) => {
    const top = (height - (col.length * NODE_H + (col.length - 1) * ROW_GAP)) / 2;
    col.forEach((id, ri) => { pos[id] = { x: PAD + ci * (NODE_W + COL_GAP), y: top + ri * (NODE_H + ROW_GAP) }; });
  });
  return { pos, width: PAD * 2 + filled.length * NODE_W + (filled.length - 1) * COL_GAP, height };
}

function drawGraph(task) {
  const nodes = task.nodes;
  const { pos, width, height } = layoutGraph(nodes);
  const root = svg("svg", { viewBox: `0 0 ${width} ${height}`, width, height, role: "img", "aria-label": "Task graph" });
  const edges = svg("g", { class: "edges" });
  const planner = Object.values(nodes).find(n => n.role === "planner");
  const link = (from, to, cls) => {
    const a = pos[from], b = pos[to];
    if (!a || !b) return;
    const x1 = a.x + NODE_W, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2, mx = (x1 + x2) / 2;
    edges.append(svg("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: cls }));
  };
  for (const n of Object.values(nodes)) {
    n.deps.forEach(dep => link(dep, n.id, `edge ${nodes[dep] && nodes[dep].phase === "done" ? "done" : ""}`));
    if (planner && n !== planner && !n.deps.length) link(planner.id, n.id, "edge plan");
  }
  root.append(edges);
  for (const n of Object.values(nodes)) {
    const p = pos[n.id];
    const rerun = n.superseded_results.length > 0 || n.rejections.length > 0;
    const who = n.binding && n.phase !== "waiting" && n.phase !== "ready"
      ? ` · ${label(n.binding.provider)}${n.binding.model ? ` ${n.binding.model}` : ""}` : "";
    const g = svg("g", {
      class: `node phase-${n.phase} tier-${n.tier} ${ui.openNode === n.id ? "open" : ""}`,
      transform: `translate(${p.x},${p.y})`, tabindex: "0", role: "button",
      "aria-label": `${n.title}: ${PHASE_LABEL[n.phase] || n.phase}, ${n.tier} tier`,
    },
      svg("rect", { width: NODE_W, height: NODE_H, rx: 12 }),
      svg("rect", { class: "tier-bar", width: 4, height: NODE_H - 18, x: 9, y: 9, rx: 2 }),
      svg("text", { x: 22, y: 25, class: "title" }, truncate(n.title, 23)),
      svg("text", { x: 22, y: 45, class: "sub" }, truncate(`${PHASE_LABEL[n.phase] || n.phase} · ${n.tier}${who}`, 34)),
      rerun ? svg("text", { x: NODE_W - 12, y: 25, class: "badge-text", "text-anchor": "end" }, `↻${n.attempt || ""}`) : null,
      svg("title", {}, `${n.title} — ${PHASE_LABEL[n.phase] || n.phase}${n.validity ? ` (${n.validity})` : ""}`));
    g.addEventListener("click", () => openNode(n.id));
    g.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openNode(n.id); } });
    root.append(g);
  }
  const legend = h("div", { class: "legend" },
    ...["ready", "running", "done", "failed"].map(p => h("span", { class: `key phase-${p}`, text: PHASE_LABEL[p] })),
    h("span", { class: "key stale", text: "re-run after a stale or rejected result" }));
  return h("div", { class: "graph-inner" }, root, legend);
}

function openNode(id) {
  ui.openNode = ui.openNode === id ? null : id;
  renderMap();
}

async function fetchResult(ref) {
  if (ui.results.has(ref) || ui.pending.has(ref)) return;
  ui.pending.add(ref);
  try {
    ui.results.set(ref, await api(`/api/result?ref=${encodeURIComponent(ref)}`));
    renderNodeDetail();
  } catch (err) {
    toast(err.message, true);
  } finally {
    ui.pending.delete(ref);
  }
}

function renderNodeDetail() {
  const panel = $("#node-detail");
  const d = ui.detail;
  const node = d && d.task && ui.openNode ? d.task.nodes[ui.openNode] : null;
  if (!node) { panel.hidden = true; return; }
  panel.hidden = false;
  const binding = node.binding ? [label(node.binding.provider), node.binding.model, node.binding.effort].filter(Boolean).join(" · ") : "—";
  const rows = [
    ["Role", node.role], ["Tier", node.tier], ["Phase", PHASE_LABEL[node.phase] || node.phase],
    ["Contract", `state v${node.contract_version} (task is v${d.task.version})`], ["Binding", binding],
    ["Validity", node.validity || "—"], ["Attempts", String(node.attempt)], ["Escalations", String(node.escalations)],
    ["Depends on", node.deps.join(", ") || "—"],
  ];
  const parts = [
    h("div", { class: "detail-head" },
      h("h3", { text: node.title }),
      h("button", { class: "icon-btn", type: "button", "aria-label": "Close details", text: "✕", onclick: () => openNode(node.id) })),
    node.instructions ? h("p", { class: "instructions", text: node.instructions }) : null,
    h("dl", { class: "kv" }, ...rows.flatMap(([k, v]) => [h("dt", { text: k }), h("dd", { text: v })])),
  ];
  if (node.result_ref) {
    const record = ui.results.get(node.result_ref);
    parts.push(h("h4", { text: "Accepted result" }), h("code", { class: "ref", text: node.result_ref }));
    if (record) {
      const out = (record.content && record.content.output) || {};
      parts.push(h("p", { class: "result-text", text: out.answer || out.summary || JSON.stringify(out, null, 2) }));
      if (out.uncertainty) parts.push(h("p", { class: "hint", text: `Uncertainty: ${out.uncertainty}` }));
    } else {
      parts.push(h("p", { class: "hint", text: "Loading…" }));
      fetchResult(node.result_ref);
    }
  }
  if (node.rejections.length) {
    parts.push(h("h4", { text: "Rejected results" }), h("ul", { class: "rejections" },
      ...node.rejections.map(r => h("li", {}, h("span", { text: r.reasons.join("; ") }),
        r.result_ref ? h("code", { class: "ref", text: r.result_ref }) : null))));
  }
  if (node.history.length) {
    parts.push(h("h4", { text: "History" }), h("ol", { class: "history" },
      ...node.history.map(e => h("li", { text: `${fmtClock(e.ts)} · ${e.event}${e.reason ? ` — ${e.reason}` : ""}` }))));
  }
  panel.replaceChildren(...parts.filter(Boolean));
}

function renderAgents() {
  const snap = ui.snapshot;
  const nodes = ui.detail && ui.detail.task ? ui.detail.task.nodes : {};
  const chip = (slot, kind) => {
    const busy = slot.status === "busy";
    const who = slot.binding ? `${label(slot.binding.provider)}${slot.binding.model ? ` ${slot.binding.model}` : ""}` : "";
    const job = kind === "core" ? (slot.node_id ? (nodes[slot.node_id] ? nodes[slot.node_id].title : slot.node_id) : "") : "reading input";
    return h("div", {
      class: `slot tier-${slot.reservation} ${busy ? "busy" : ""}`,
      title: busy ? `${slot.id} (${slot.reservation} slot): ${job} on ${who}${slot.children ? ` with ${slot.children} child agents` : ""}`
        : `${slot.id}: idle (${slot.reservation} reservation)`,
    },
      h("div", { class: "slot-top" }, h("b", { text: slot.id }), busy ? h("span", { class: "dot", "aria-hidden": "true" }) : null,
        slot.children ? h("span", { class: "kids", text: `+${slot.children}` }) : null),
      h("div", { class: "slot-job", text: busy ? job : "idle" }),
      busy && who ? h("div", { class: "slot-who", text: who }) : null);
  };
  $("#filter-slots").replaceChildren(...snap.slots.filters.map(s => chip(s, "filter")));
  $("#core-slots").replaceChildren(...snap.slots.cores.map(s => chip(s, "core")));
  const busyCores = snap.slots.cores.filter(s => s.status === "busy").length;
  const busyFilters = snap.slots.filters.filter(s => s.status === "busy").length;
  $("#agent-summary").textContent = `${busyCores}/12 cores · ${busyFilters}/8 filters busy`;

  const slider = $("#cap-slider");
  const run = currentRun();
  const active = isActive(run) && !!run.task_id;
  slider.max = String(snap.limits.default_max_agents);
  if (document.activeElement !== slider && !ui.capTimer) {
    slider.value = String(run ? Math.min(run.max_agents, snap.limits.default_max_agents) : snap.limits.default_max_agents);
  }
  slider.disabled = !active;
  $("#cap-value").textContent = active ? slider.value : "–";
  const sw = snap.swarm;
  $("#swarm").textContent = `Native child agents: ${sw.live_children} live · limit ${sw.max_total} total, ${sw.max_per_core} per core · ${sw.granted_total} granted so far`;
}

function knownOr(totals, value, formatter = v => Number(v).toLocaleString()) {
  if (!totals.calls) return "—";
  if (totals.unknown_usage_calls === totals.calls) return "unknown";
  return formatter(value);
}

function totalsTable(title, totals) {
  const rows = Object.entries(totals.by_provider || {});
  return h("div", { class: "ledger-block" },
    h("h4", { text: title }),
    h("table", {},
      h("thead", {}, h("tr", {}, ...["Provider", "Calls", "Input", "Cached input", "Output", "Cost estimate", "Calls with unknown usage", "Child agents"]
        .map(c => h("th", { text: c })))),
      h("tbody", {}, rows.length ? rows.map(([p, t]) => h("tr", {},
        h("td", { text: label(p) }), h("td", { text: t.calls }),
        h("td", { text: knownOr(t, t.input_tokens) }), h("td", { text: knownOr(t, t.cached_input_tokens) }),
        h("td", { text: knownOr(t, t.output_tokens) }),
        h("td", { text: t.cost_usd_estimate === null ? "unknown" : `$${t.cost_usd_estimate.toFixed(4)}` }),
        h("td", { text: t.unknown_usage_calls }),
        h("td", { text: t.children === null ? "unknown" : t.children })))
        : h("tr", {}, h("td", { colspan: "8", class: "empty", text: "No provider calls yet." })))));
}

function renderDrawer() {
  const d = ui.detail;
  const timeline = $("#timeline");
  const near = timeline.parentElement.scrollHeight - timeline.parentElement.scrollTop - timeline.parentElement.clientHeight < 40;
  const events = d ? d.events.filter(e => e.message) : [];
  timeline.replaceChildren(...(events.length ? events.map(e => h("li", { class: `ev-${e.type.toLowerCase()}` },
    h("time", { text: `+${Math.max(0, e.ts - d.created_at).toFixed(1)}s` }),
    h("span", { class: "ev-type", text: EVENT_LABEL[e.type] || e.type }),
    h("span", { class: "ev-msg", text: e.message })))
    : [h("li", { class: "empty", text: "Events for the selected run appear here." })]));
  if (near) timeline.parentElement.scrollTop = timeline.parentElement.scrollHeight;

  const ledger = $("#ledger");
  const blocks = [];
  if (d) {
    blocks.push(totalsTable("Selected run", d.ledger_totals));
    blocks.push(h("div", { class: "ledger-block" }, h("h4", { text: "Calls in this run" }), h("table", {},
      h("thead", {}, h("tr", {}, ...["Slot", "Provider", "Kind", "Node", "Input", "Output", "Seconds", "Children", "Outcome"].map(c => h("th", { text: c })))),
      h("tbody", {}, d.ledger.length ? d.ledger.map(e => h("tr", {},
        h("td", { text: e.label }), h("td", { text: [label(e.provider), e.model].filter(Boolean).join(" ") }),
        h("td", { text: e.kind }), h("td", { text: e.node || "—" }),
        h("td", { text: e.known ? (e.input_tokens ?? "unknown") : "unknown" }),
        h("td", { text: e.known ? (e.output_tokens ?? "unknown") : "unknown" }),
        h("td", { text: e.duration_s }), h("td", { text: e.children === null ? "unknown" : e.children }),
        h("td", { class: e.ok ? "ok-text" : "bad-text", text: e.ok ? "ok" : (e.failure || "failed") })))
        : h("tr", {}, h("td", { colspan: "9", class: "empty", text: "No calls yet." }))))));
  }
  blocks.push(totalsTable("All runs this session", ui.snapshot.ledger));
  blocks.push(h("p", { class: "hint", text: "Unknown means the provider did not report it — never zero. Cost is a client-side estimate; on subscription plans usage counts against your plan limits." }));
  ledger.replaceChildren(...blocks);

  const list = runs();
  $("#runs").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ...["Time", "Status", "Request", "Answered by", "Peak processes"].map(c => h("th", { text: c })))),
    h("tbody", {}, list.length ? list.map(r => h("tr", { class: "clickable", onclick: () => selectRun(r.id) },
      h("td", { text: fmtClock(r.created_at) }), h("td", { text: r.status }),
      h("td", { class: "wrap", text: truncate(r.text, 90) }), h("td", { text: r.answered_by || r.status_line }),
      h("td", { text: r.peak_processes })))
      : h("tr", {}, h("td", { colspan: "5", class: "empty", text: "No runs yet." })))));
}

// ------------------------------------------------------------------ actions
async function onSubmit(event) {
  event.preventDefault();
  const input = $("#input");
  const text = input.value.trim();
  if (!text || ui.busy) return;
  const run = currentRun();
  const steering = isActive(run) && !!run.task_id && ui.mode === "steer";
  const send = $("#send");
  ui.busy = true;
  send.disabled = true;
  send.textContent = steering ? "Steering…" : "Sending…";
  try {
    if (steering) {
      const result = await api(`/api/runs/${encodeURIComponent(run.id)}/steer`, { text });
      toast(result.message || "Steer applied");
    } else {
      const limit = parseInt($("#max-agents").value, 10);
      const result = await api("/api/runs", { text, max_agents: Number.isFinite(limit) && limit > 0 ? limit : null });
      ui.selected = result.run.id;
      ui.follow = true;
      ui.openNode = null;
    }
    input.value = "";
  } catch (err) {
    toast(err.message, true);
  } finally {
    ui.busy = false;
    send.disabled = false;
    renderComposer();
    scheduleRefresh(0);
  }
}

function initComposer() {
  $("#composer").addEventListener("submit", onSubmit);
  $("#input").addEventListener("keydown", event => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      $("#composer").requestSubmit();
    }
  });
  $$("#mode-switch button").forEach(button => button.addEventListener("click", () => {
    ui.mode = button.dataset.mode;
    renderComposer();
  }));
}

function initCapSlider() {
  const slider = $("#cap-slider");
  slider.addEventListener("input", () => {
    $("#cap-value").textContent = slider.value;
    clearTimeout(ui.capTimer);
    ui.capTimer = setTimeout(async () => {
      ui.capTimer = null;
      const run = currentRun();
      if (!run) return;
      try {
        await api(`/api/runs/${encodeURIComponent(run.id)}/cap`, { n: Number(slider.value) });
        toast(`Agent limit for this run set to ${slider.value}`);
      } catch (err) {
        toast(err.message, true);
      }
      scheduleRefresh(0);
    }, 350);
  });
}

function initDrawer() {
  const drawer = $("#drawer");
  const show = tab => {
    $$(".tabs [role=tab]").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $$(".tab-body").forEach(body => { body.hidden = body.dataset.body !== tab; });
    storage.set("gm-tab", tab);
  };
  $$(".tabs [role=tab]").forEach(b => b.addEventListener("click", () => {
    drawer.classList.remove("collapsed");
    show(b.dataset.tab);
  }));
  $("#drawer-toggle").addEventListener("click", () => {
    drawer.classList.toggle("collapsed");
    $("#drawer-toggle").textContent = drawer.classList.contains("collapsed") ? "▴" : "▾";
    storage.set("gm-drawer", drawer.classList.contains("collapsed") ? "collapsed" : "open");
  });
  show(storage.get("gm-tab") || "timeline");
  if (storage.get("gm-drawer") === "collapsed") {
    drawer.classList.add("collapsed");
    $("#drawer-toggle").textContent = "▴";
  }
}

function initTheme() {
  const saved = storage.get("gm-theme");
  if (saved === "light" || saved === "dark") document.documentElement.dataset.theme = saved;
  $("#theme-toggle").addEventListener("click", () => {
    const current = document.documentElement.dataset.theme
      || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    storage.set("gm-theme", next);
  });
}

async function init() {
  initTheme();
  initComposer();
  initCapSlider();
  initDrawer();
  await refresh();
  connect();
  setInterval(() => scheduleRefresh(), 5000);
}

init();
