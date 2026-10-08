// Hermes app, Phase 2: the control centre from the Hermes Workspace mockup,
// on real data. Screens: Today, Work, Agents, Routines, Approvals, System.
"use strict";

/* ---------- small helpers ---------- */

const $ = (sel, root = document) => root.querySelector(sel);
const ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
const esc = (v) => (v == null ? "" : String(v).replace(/[&<>"']/g, (c) => ESC[c]));
const ic = (n, s = 18) =>
  `<svg class="ic" width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${(window.ICON_PATHS || {})[n] || ""}</svg>`;
// Icons the mockup's set doesn't have.
Object.assign(window.ICON_PATHS || {}, {
  copy: '<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  trash: '<path d="M3 6h18M8 6V4h8v2M19 6l-1 14H6L5 6"/>',
});
const logoMark = (size = 28) =>
  `<svg class="logo" width="${size}" height="${size}" viewBox="0 0 32 32" role="img" aria-label="Hermes"><rect width="32" height="32" rx="9" fill="var(--ink)"/><g transform="translate(-1.1 1.5)"><path d="M5.5 9h3.4v5.8h7.2V9h3.4v14h-3.4v-5.2H8.9V23H5.5z" fill="var(--on-ink)"/><path d="M19.5 9.2C22.4 6.9 25.9 5.8 29.4 5.9c-.6 1.3-1.5 2.4-2.6 3.2 1.1 0 2-.1 2.9-.4-.8 1.4-2 2.5-3.4 3.3.9.1 1.8 0 2.6-.3-1.3 2-3.4 3.4-5.8 3.9-1.2.3-2.4.3-3.6.1z" fill="var(--logo-wing)"/></g></svg>`;

const TZ = "Europe/London";
function when(iso, withDay = true) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const t = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
  const today = new Date().toLocaleDateString("en-GB", { timeZone: TZ });
  if (!withDay || d.toLocaleDateString("en-GB", { timeZone: TZ }) === today) return t;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: TZ }) + ", " + t;
}
function minsUntil(iso) {
  const d = Date.parse(iso);
  return isNaN(d) ? null : Math.round((d - Date.now()) / 60000);
}
function money(cents, cur = "$") {
  if (cents == null) return "unknown";
  return cur + (cents / 100).toFixed(2);
}
function usd(v) {
  return v == null || v === "" ? "unknown" : "$" + Number(v).toFixed(2);
}
function initials(name) {
  const w = String(name || "?").trim().split(/\s+/);
  return (w.length > 1 ? w[0][0] + w[1][0] : w[0].slice(0, 2)).toUpperCase();
}

const ISSUE_TEXT = { backlog: "Backlog", todo: "To do", in_progress: "In progress", in_review: "In review", done: "Done", blocked: "Blocked", cancelled: "Cancelled" };
const ISSUE_BADGE = { done: "ok", in_review: "accent", in_progress: "info", blocked: "warn", todo: "", backlog: "", cancelled: "" };
const AGENT_BADGE = { active: "ok", idle: "ok", running: "info", paused: "warn", error: "err", pending_approval: "accent" };
const APPROVAL_BADGE = { pending: "warn", approved: "ok", sent: "ok", executed: "ok", outcome_unknown: "warn", rejected: "err", denied: "err", expired: "", cancelled: "", revision_requested: "accent" };
const APPROVAL_TEXT = { pending: "Pending", approved: "Approved", sent: "Sent", executed: "Sent", outcome_unknown: "Sent? Check inbox", rejected: "Rejected", denied: "Denied", expired: "Expired", cancelled: "Cancelled", revision_requested: "Changes asked" };
const badge = (cls, text) => `<span class="badge ${cls || ""}">${esc(text)}</span>`;
const authChip = (src) => src === "broker"
  ? `<span class="auth broker">${ic("lock", 13)}Broker</span>`
  : `<span class="auth paperclip">${ic("work", 13)}Paperclip</span>`;
const notConnected = (what, err) =>
  `<div class="notice warn" role="status">${ic("alert")}<div><b>${esc(what)} isn't connected.</b> ${esc(err || "")} Nothing here is shown as zero while it's unknown.</div></div>`;

/* ---------- state and data ---------- */

const S = { me: null, meta: {}, cache: {}, apprView: "pending", apprFilter: "all", workFilter: "all", workAgent: "all", workStatus: "all" };

async function api(path, opts = {}) {
  const init = { credentials: "same-origin", cache: "no-store", headers: {} };
  if (opts.body) {
    init.method = "POST";
    init.headers["Content-Type"] = "application/json";
    init.headers["X-Hermes-Action"] = "1";
    init.body = JSON.stringify(opts.body);
  }
  const res = await fetch(path, init);
  let data = null;
  try { data = await res.json(); } catch (_) { /* not JSON */ }
  if (!res.ok) throw new Error((data && data.error) || `The server answered ${res.status}.`);
  return data;
}

async function load(key, path) {
  try {
    const d = await api(path);
    S.cache[key] = d;
    if (d && "demo" in d) S.meta = { demo: d.demo, board_url: d.board_url, broker_url: d.broker_url, stop_ready: d.stop_ready };
    try { localStorage.setItem("hermes:" + key, JSON.stringify(d)); } catch (_) { /* private mode */ }
    S.offline = false;
    return d;
  } catch (e) {
    S.offline = true;
    try { return JSON.parse(localStorage.getItem("hermes:" + key) || "null"); } catch (_) { return null; }
  }
}

function toast(msg) {
  const t = document.createElement("div");
  t.className = "toast";
  t.textContent = msg;
  $("#toasts").append(t);
  setTimeout(() => t.remove(), 4200);
}

/* ---------- shell ---------- */

const NAV = [
  { h: "today", i: "today", t: "Today" },
  { h: "soon/health", i: "health", t: "Health", soon: 4 },
  { h: "soon/inbox", i: "inbox", t: "Inbox", soon: 5 },
  { h: "soon/planner", i: "planner", t: "Planner", soon: 5 },
  { label: "Assistant & agents" },
  { h: "max", i: "max", t: "Max" },
  { h: "work", i: "work", t: "Work" },
  { h: "agents", i: "agents", t: "Agents" },
  { h: "routines", i: "routines", t: "Routines" },
  { label: "Workspace" },
  { h: "approvals", i: "approvals", t: "Approvals", count: true },
  { h: "soon/library", i: "library", t: "Library", soon: 5 },
  { h: "system/status", i: "system", t: "System" },
];

function pendingCount() {
  const a = S.cache.approvals;
  if (!a) return 0;
  const b = (a.broker_pending && a.broker_pending.data) || [];
  const p = ((a.board && a.board.data) || []).filter((x) => x.status === "pending");
  return b.length + p.length;
}

function renderShell(route) {
  const area = route.split("/")[0];
  const n = pendingCount();
  const me = S.me || {};
  $("#side").innerHTML = `
    <div class="brand">${logoMark()}<b>Hermes</b><span>Personal</span></div>
    <nav class="nav" aria-label="Primary">${NAV.map((x) => {
      if (x.label) return `<div class="nav-label">${esc(x.label)}</div>`;
      const cur = x.h.split("/")[0] === area && (!x.soon || route === x.h);
      return `<a href="#${x.h}"${cur ? ' aria-current="page"' : ""}${x.soon ? ' class="soon"' : ""}>${ic(x.i)}<span>${esc(x.t)}</span>${
        x.count && n ? `<span class="count" aria-label="${n} pending">${n}</span>` : ""}${x.soon ? `<span class="soon-tag">Phase ${x.soon}</span>` : ""}</a>`;
    }).join("")}</nav>
    <div class="side-foot"><div class="acct"><span class="avatar">${esc(initials(me.name || "Craig"))}</span><span class="who"><b style="display:block;font-size:13.5px;font-weight:600">${esc((me.name || "Craig").split(" ")[0])}</b><span class="xs muted">Owner · ${esc(me.login || "signed in through Tailscale")}</span></span></div></div>`;

  const title = { today: "Today", max: "Max", work: "Work", agents: "Agents", routines: "Routines", approvals: "Approvals", system: "System", more: "More", soon: "Coming next" }[area] || "Hermes";
  $("#top").innerHTML = `
    <a href="#today" class="phone-only" aria-label="Hermes home" style="display:flex">${logoMark(26)}</a>
    <div class="crumb"><span class="cur">${esc(title)}</span></div>
    <div class="top-r">
      ${S.meta.demo ? `<span class="demo-pill" title="The server is running on sample data, not your real board"><i></i>Sample data</span>` : ""}
      ${S.offline ? `<span class="demo-pill offline-pill"><i></i>Offline</span>` : ""}
      ${area === "max" && S.me && S.me.chat_ready !== false ? `<button type="button" class="btn sm chat-btn" data-act="newChat" title="New chat">${ic("chat")}<span class="lbl">New chat</span></button>` : ""}
      <button type="button" class="btn primary sm cap-btn" data-act="createTask" title="Create a task for an agent">${ic("plus")}<span class="lbl">New task</span></button>
    </div>`;

  $("#bnav").innerHTML = `
    <a href="#today"${area === "today" ? ' aria-current="page"' : ""}>${ic("today")}Today</a>
    <button type="button" class="cap" data-act="createTask">${ic("plus")}Task</button>
    <a href="#work"${area === "work" ? ' aria-current="page"' : ""}>${ic("work")}Work</a>
    <a href="#approvals"${area === "approvals" ? ' aria-current="page"' : ""}>${ic("approvals")}Review${n ? `<span class="count">${n}</span>` : ""}</a>
    <a href="#more"${["more", "max", "agents", "routines", "system", "soon"].includes(area) ? ' aria-current="page"' : ""}>${ic("more")}More</a>`;
}

/* ---------- screens ---------- */

function greeting() {
  const h = Number(new Date().toLocaleString("en-GB", { hour: "numeric", hour12: false, timeZone: TZ }));
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function approvalRow(a, href = true) {
  const sub = a.source === "broker"
    ? [a.recipients, a.expires && minsUntil(a.expires) != null ? `expires in ${Math.max(0, minsUntil(a.expires))} min` : ""]
    : [a.kind, a.agent];
  const inner = `<span class="main"><span class="t">${esc(a.title)}</span><span class="s">${authChip(a.source)} <span class="mono">${esc(a.source === "broker" ? a.id : (a.id || "").slice(0, 8))}</span>${sub.filter(Boolean).map((x) => " · " + esc(x)).join("")}</span></span><span class="end">${badge(APPROVAL_BADGE[a.status], APPROVAL_TEXT[a.status] || a.status)}</span>`;
  return href ? `<a class="li" href="#approvals/${a.source}:${encodeURIComponent(a.id)}">${inner}</a>` : `<div class="li">${inner}</div>`;
}

function issueRow(i) {
  const bits = [i.agent || "Unassigned", i.updated ? "updated " + when(i.updated) : "", i.needs_you ? "needs you" : "", i.running ? "run running" : ""].filter(Boolean);
  return `<a class="li" href="#work/${esc(i.id)}"><span class="main"><span class="t">${i.running ? '<span class="pulse" aria-hidden="true"></span> ' : ""}${i.ref ? `<span class="mono muted">${esc(i.ref)}</span> ` : ""}${esc(i.title)}</span><span class="s">${esc(bits.join(" · "))}</span></span><span class="end">${badge(ISSUE_BADGE[i.status], i.status_text || ISSUE_TEXT[i.status] || i.status)}</span></a>`;
}

async function screenToday() {
  const d = await load("today", "/api/today");
  const status = (d && d.status && d.status.services) || [];
  const down = status.filter((s) => s.state === "down" && s.id !== "kill");
  const kill = status.find((s) => s.id === "kill");
  const waiting = (d && d.waiting) || [];
  const now = new Date();
  const name = ((S.me && S.me.name) || "Craig").split(" ")[0];
  const sp = d && d.spending && d.spending.max && d.spending.max.ok ? d.spending.max.data : null;
  return `
  <div class="ph"><div class="ph-t">
    <div class="eyebrow">${esc(now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: TZ }))} · ${esc(when(now.toISOString(), false))}</div>
    <h1>${greeting()}, ${esc(name)}</h1>
    <div class="summary-line">
      <span><b>${d && d.waiting_ok ? waiting.length : "?"}</b> decisions waiting</span>
      <span><b>${d && d.work_ok ? d.needs_you : "?"}</b> ${d && d.needs_you === 1 ? "task needs" : "tasks need"} you</span>
      <span>${down.length ? `<b>${down.length}</b> ${down.length === 1 ? "service" : "services"} down` : status.length ? "<b>All</b> services up" : "Server status unknown"}</span>
      ${sp && sp.today_usd != null ? `<span><b>${esc(usd(sp.today_usd))}</b> Max today</span>` : ""}
    </div>
  </div></div>
  <button type="button" class="ask-bar" data-act="soonMax">${ic("chat")}<span>Ask Max or start a discussion… (Phase 3)</span></button>
  ${kill && kill.state === "down" ? `<div class="notice err" role="status" style="margin-bottom:16px">${ic("stop")}<div><b>All agent work is stopped.</b> The kill switch is on. Resume it from your Mac.</div></div>` : ""}
  <div class="bento bento-p2">
    <section class="panel a-dec" aria-labelledby="h-dec">
      <div class="panel-h"><h2 id="h-dec">Waiting for you</h2>${waiting.length ? `<span class="badge accent">${waiting.length}</span>` : ""}<div class="r"><a class="btn ghost sm" href="#approvals">Approvals ${ic("chev", 16)}</a></div></div>
      ${d && !d.waiting_ok ? notConnected("Part of approvals", (d.waiting_errors || []).join("; ")) : ""}
      ${waiting.length ? `<div class="list">${waiting.map((a) => approvalRow(a)).join("")}</div>` : d && d.waiting_ok ? `<p class="small muted">Nothing waiting. Email sends and board decisions appear here.</p>` : ""}
    </section>
    <section class="panel a-team" aria-labelledby="h-team">
      <div class="panel-h"><h2 id="h-team">Team work</h2><div class="r"><a class="btn ghost sm" href="#work">Work ${ic("chev", 16)}</a></div></div>
      ${d && !d.work_ok ? notConnected("The Paperclip board", "") : ""}
      ${d && d.work && d.work.length ? `<div class="list">${d.work.map(issueRow).join("")}</div>` : d && d.work_ok ? `<p class="small muted">No open tasks. Use New task to give an agent something to do.</p>` : ""}
    </section>
    <section class="panel a-sys" aria-labelledby="h-sys">
      <div class="panel-h"><h2 id="h-sys">Server</h2><div class="r"><a class="btn ghost sm" href="#system/status">System ${ic("chev", 16)}</a></div></div>
      ${statusList(status.slice(0, 6))}
      ${d && d.status ? `<p class="xs muted" style="margin-top:8px">Checked at ${esc(when(new Date(d.status.checked_at * 1000).toISOString(), false))}</p>` : `<p class="small muted">Can't reach the server.</p>`}
    </section>
    <section class="panel flat a-brief" aria-labelledby="h-brief">
      <div class="panel-h"><h2 id="h-brief">Morning brief</h2>${badge("", "Locked")}</div>
      <p class="small muted">A private brief would need mail, calendar and health context, which stays locked for Max. Your own records aren't affected.</p>
    </section>
  </div>`;
}

function statusList(items) {
  if (!items.length) return "";
  const label = (s) => s.label || { ok: "Up", down: "Down", unknown: "Unknown" }[s.state] || "Unknown";
  const cls = (s) => (s.id === "kill" ? (s.state === "down" ? "err" : "ok") : { ok: "ok", down: "err", unknown: "warn" }[s.state]);
  return `<div class="list">${items.map((s) => `<div class="li"><span class="main"><span class="t">${esc(s.name)}</span><span class="s">${esc(s.detail || "")}</span></span><span class="end">${badge(cls(s), label(s))}</span></div>`).join("")}</div>`;
}

async function screenWork(sub) {
  const d = await load("work", "/api/work");
  const w = d && d.work;
  if (sub) return screenTask(w, sub);
  let issues = (w && w.ok && w.data.issues) || [];
  const agents = (w && w.ok && w.data.agents) || [];
  const needs = issues.filter((i) => i.needs_you).length;
  if (S.workFilter === "attention") issues = issues.filter((i) => i.needs_you);
  if (S.workAgent !== "all") issues = issues.filter((i) => i.agent_id === S.workAgent);
  if (S.workStatus !== "all") issues = issues.filter((i) => i.status === S.workStatus);
  const groups = new Map();
  for (const i of issues) {
    const k = i.project || "No project";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(i);
  }
  const statuses = [["backlog", "Backlog"], ["todo", "To do"], ["in_progress", "In progress"], ["blocked", "Blocked"], ["in_review", "In review"], ["done", "Done"], ["cancelled", "Cancelled"]];
  return `
  <div class="ph"><div class="ph-t"><div class="eyebrow">Paperclip board</div><h1>Work</h1><p class="sub">Agent work from Paperclip: projects, tasks and runs.</p></div>
    <div class="ph-a">${S.meta.board_url ? `<a class="btn" href="${esc(S.meta.board_url)}" target="_blank" rel="noopener">Open board</a>` : ""}<button type="button" class="btn primary" data-act="createTask">${ic("plus")}Create task</button></div></div>
  ${w && !w.ok ? notConnected("The Paperclip board", w.error) : ""}
  <div class="stack s24">
    <div class="row-flex">
      <div class="chips"><button type="button" class="chip" aria-pressed="${S.workFilter === "all"}" data-act="workFilter" data-arg="all">All</button><button type="button" class="chip" aria-pressed="${S.workFilter === "attention"}" data-act="workFilter" data-arg="attention">Needs you · ${needs}</button></div>
      <label class="sr" for="wf-agent">Agent</label><select class="inp" id="wf-agent" style="width:auto" data-change="workAgent"><option value="all">Any agent</option>${agents.map((a) => `<option value="${esc(a.id)}"${S.workAgent === a.id ? " selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
      <label class="sr" for="wf-st">Status</label><select class="inp" id="wf-st" style="width:auto" data-change="workStatus"><option value="all">Any status</option>${statuses.map(([v, t]) => `<option value="${v}"${S.workStatus === v ? " selected" : ""}>${t}</option>`).join("")}</select>
    </div>
    ${[...groups].map(([name, items]) => `<section class="panel"><div class="panel-h"><div><h2>${esc(name)}</h2></div></div><div class="list">${items.map(issueRow).join("")}</div></section>`).join("")}
    ${w && w.ok && !issues.length ? `<div class="empty"><h3>No tasks here</h3><p class="small">Change the filters, or create a task.</p></div>` : ""}
  </div>`;
}

function screenTask(w, id) {
  const i = w && w.ok && w.data.issues.find((x) => x.id === id);
  if (!i) return `<a class="btn ghost sm" href="#work">${ic("back")}Work</a><div class="empty" style="margin-top:16px"><h3>Task not found</h3><p class="small">It may have moved off the recent list.</p></div>`;
  return `
  <a class="btn ghost sm tab-back" href="#work" style="margin-bottom:12px">${ic("back")}Work</a>
  <div class="ph"><div class="ph-t"><div class="eyebrow"><span class="mono">${esc(i.ref || "")}</span>${i.project ? " · " + esc(i.project) : ""}</div><h1>${esc(i.title)}</h1>
    <div class="row-flex" style="margin-top:10px">${badge(ISSUE_BADGE[i.status], i.status_text || ISSUE_TEXT[i.status] || i.status)}${i.running ? badge("info", "Run running") : ""}</div></div></div>
  <section class="panel"><dl class="kv"><dt>Agent</dt><dd>${esc(i.agent || "Unassigned")}</dd><dt>Updated</dt><dd>${esc(when(i.updated))}</dd><dt>Priority</dt><dd>${esc(i.priority || "medium")}</dd></dl>
  <div class="btns" style="margin-top:14px">${i.running && i.run_id ? `<button type="button" class="btn danger" data-act="cancelRun" data-arg="${esc(i.run_id)}">${ic("stop")}Stop run</button>` : ""}${S.meta.board_url ? `<a class="btn" href="${esc(S.meta.board_url)}" target="_blank" rel="noopener">Open in Paperclip</a>` : ""}</div></section>`;
}

async function screenAgents() {
  const d = await load("agents", "/api/agents");
  const a = d && d.agents;
  const list = (a && a.ok && a.data) || [];
  return `
  <div class="ph"><div class="ph-t"><h1>Agents</h1><p class="sub">Who does what, and what their work costs this month.</p></div></div>
  ${a && !a.ok ? notConnected("The Paperclip board", a.error) : ""}
  <div class="cols" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr))">${list.map((g) => `
    <section class="panel" style="display:flex;flex-direction:column;gap:10px">
      <div class="row-flex"><span class="avatar lg" aria-hidden="true">${esc(initials(g.name))}</span><div style="min-width:0;flex:1"><h2>${esc(g.name)}</h2><p class="small muted">${esc(g.title || "")}</p></div></div>
      ${badge(AGENT_BADGE[g.status], g.status_text)}
      <p class="small">${g.current ? `<span class="mono">${esc(g.current.ref || "")}</span> ${esc(g.current.title)}` : "No current work"}</p>
      <div class="small muted num">${g.budget_cents ? `${money(g.spent_cents)} of ${money(g.budget_cents)} this month` : g.spent_cents ? `${money(g.spent_cents)} this month · no budget set` : "Runs on a subscription · no spend recorded"}</div>
    </section>`).join("")}</div>`;
}

async function screenRoutines() {
  const d = await load("routines", "/api/routines");
  const r = d && d.routines;
  const list = (r && r.ok && r.data) || [];
  return `
  <div class="ph"><div class="ph-t"><h1>Routines</h1><p class="sub">Recurring agent work scheduled in Paperclip.</p></div></div>
  ${r && !r.ok ? notConnected("The Paperclip board", r.error) : ""}
  ${list.length ? `<div class="tbl-wrap"><table class="tbl tbl-cards"><thead><tr><th>Routine</th><th>Assignee</th><th>Schedule</th><th>Status</th><th>Next run</th><th>Last result</th></tr></thead><tbody>${list.map((x) => `
    <tr><td data-l="Routine"><b>${esc(x.title)}</b></td><td data-l="Assignee">${esc(x.agent || "None")}</td>
    <td data-l="Schedule">${esc(x.schedule || "Manual")}${x.timezone ? `<br><span class="xs muted">${esc(x.timezone)}</span>` : ""}</td>
    <td data-l="Status">${badge(x.status === "active" ? "ok" : x.status === "paused" ? "warn" : "", x.status ? x.status[0].toUpperCase() + x.status.slice(1) : "Unknown")}</td>
    <td data-l="Next run">${esc(x.next_run ? when(x.next_run) : "—")}</td>
    <td data-l="Last result" class="small">${esc(x.last_result ? `${x.last_result[0].toUpperCase() + x.last_result.slice(1)} · ${when(x.last_run)}` : "Never run")}</td></tr>`).join("")}</tbody></table></div>` : r && r.ok ? `<div class="empty"><h3>No routines yet</h3><p class="small">Recurring work you set up in Paperclip shows here.</p></div>` : ""}
  <p class="small muted" style="margin-top:12px">Server jobs such as backups, the cost check and the Monday digest run outside Paperclip; their health is under System.</p>`;
}

async function screenApprovals(sel) {
  const d = await load("approvals", "/api/approvals");
  const board = (d && d.board && d.board.ok && d.board.data) || [];
  const bp = (d && d.broker_pending && d.broker_pending.ok && d.broker_pending.data) || [];
  const bh = (d && d.broker_history && d.broker_history.ok && d.broker_history.data) || [];
  const pending = [...bp, ...board.filter((a) => a.status === "pending")];
  const history = [...bh, ...board.filter((a) => a.status !== "pending")].sort((a, b) => String(b.decided || b.requested).localeCompare(String(a.decided || a.requested)));
  let items = S.apprView === "pending" ? pending : history;
  if (S.apprFilter !== "all") items = items.filter((a) => a.source === S.apprFilter);
  const all = [...pending, ...history];
  const cur = sel && all.find((a) => `${a.source}:${a.id}` === decodeURIComponent(sel));
  const errs = d ? [d.board, d.broker_pending].filter((s) => s && !s.ok) : [];
  return `
  <div class="ph"><div class="ph-t"><h1>Approvals</h1><p class="sub">Every decision in one place. Each keeps its own authority: the broker for email sends, Paperclip for agent decisions.</p></div></div>
  ${errs.map((s) => notConnected(s === d.board ? "The Paperclip board" : "The approval broker", s.error)).join("")}
  <div class="split${cur ? " has-detail" : ""}">
    <div class="pane-l"><div class="pane-head">
      <div class="seg" role="group"><button type="button" aria-pressed="${S.apprView === "pending"}" data-act="apprView" data-arg="pending">Pending · ${pending.length}</button><button type="button" aria-pressed="${S.apprView === "history"}" data-act="apprView" data-arg="history">History</button></div>
      <label class="sr" for="ap-f">Authority</label><select class="inp" id="ap-f" style="width:auto;min-height:32px;padding:4px 8px;font-size:13px" data-change="apprFilter"><option value="all">All</option><option value="broker"${S.apprFilter === "broker" ? " selected" : ""}>Email sends</option><option value="paperclip"${S.apprFilter === "paperclip" ? " selected" : ""}>Agent decisions</option></select></div>
      <div class="list">${items.map((a) => `<a class="li${cur && cur === a ? " sel" : ""}" href="#approvals/${a.source}:${encodeURIComponent(a.id)}"><span class="main"><span class="row-flex" style="gap:6px;margin-bottom:4px">${authChip(a.source)}${badge(APPROVAL_BADGE[a.status], APPROVAL_TEXT[a.status] || a.status)}</span><span class="t">${esc(a.title)}</span>${a.agent ? `<span class="s">${esc(a.agent)}${a.kind ? " · " + esc(a.kind) : ""}</span>` : ""}${a.recipients ? `<span class="s">${esc(a.recipients)}</span>` : ""}<span class="s mono">${esc(a.source === "broker" ? a.id : a.id.slice(0, 8))} · requested ${esc(when(a.requested))}${a.status === "pending" && a.expires ? ` · expires ${esc(when(a.expires, false))} (${Math.max(0, minsUntil(a.expires))} min)` : ""}</span></span></a>`).join("") || `<p class="small muted" style="padding:16px">${S.apprView === "pending" ? "Nothing waiting." : "No decisions yet."}</p>`}</div>
    </div>
    <div class="pane-r">${cur ? approvalDetail(cur) : `<div class="empty" style="max-width:380px;margin:64px auto"><h3>Select a decision</h3><p class="small">You'll see exactly what was asked, who asked, and its history.</p></div>`}</div>
  </div>`;
}

function approvalDetail(a) {
  const pending = a.status === "pending";
  let actions = "";
  let note = "";
  if (a.source === "broker") {
    actions = pending
      ? (S.meta.broker_url ? `<a class="btn primary" href="${esc(S.meta.broker_url)}" target="_blank" rel="noopener">${ic("lock")}Review with fingerprint</a>` : `<span class="small muted">Approve from the notification on your phone.</span>`)
      : "";
    note = `<div class="notice info" role="status">${ic("info")}<div>Email sends are approved only on the broker's own page with your fingerprint. This app can show them but can't approve them.</div></div>`;
  } else {
    actions = pending
      ? `<button type="button" class="btn ghost" data-act="decide" data-arg="${esc(a.id)}|reject">Reject</button><button type="button" class="btn primary" data-act="decide" data-arg="${esc(a.id)}|approve">Approve</button>`
      : "";
    note = `<div class="notice info" role="status">${ic("info")}<div>This decision is recorded in Paperclip. It doesn't allow any email to be sent or open up private data.</div></div>`;
  }
  return `<div class="stack s24">
    <a class="btn ghost sm tab-back" href="#approvals" style="align-self:flex-start">${ic("back")}All approvals</a>
    <div><div class="eyebrow"><span class="mono">${esc(a.source === "broker" ? a.id : a.id.slice(0, 8))}</span> · ${esc(a.kind || "")}</div><h2 class="h2-serif" style="font-size:22px">${esc(a.title)}</h2>
      <div class="row-flex" style="margin-top:10px">${authChip(a.source)}${badge(APPROVAL_BADGE[a.status], APPROVAL_TEXT[a.status] || a.status)}</div></div>
    ${actions ? `<div class="btns">${actions}</div>` : ""}
    ${pending && a.source === "paperclip" ? `<div class="field"><label for="dec-note">Note (optional)</label><input class="inp" id="dec-note" maxlength="2000" placeholder="Why, for the board's record"></div>` : ""}
    ${note}
    <section class="panel"><h2 style="margin-bottom:12px">Request</h2><dl class="kv">
      ${a.detail ? `<dt>Details</dt><dd>${esc(a.detail)}</dd>` : ""}
      ${a.recipients ? `<dt>To</dt><dd>${esc(a.recipients)}</dd>` : ""}
      ${a.agent ? `<dt>Asked by</dt><dd>${esc(a.agent)}</dd>` : ""}
      <dt>Requested</dt><dd>${esc(when(a.requested))}</dd>
      ${a.expires && pending ? `<dt>Expires</dt><dd>${esc(when(a.expires))} (in ${Math.max(0, minsUntil(a.expires))} min)</dd>` : ""}
      ${a.decided ? `<dt>Decided</dt><dd>${esc(when(a.decided))}</dd>` : ""}
      ${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ""}
    </dl></section></div>`;
}

const SYS_TABS = [["status", "Status"], ["spending", "Spending"], ["controls", "Controls"]];

async function screenSystem(tab) {
  if (!SYS_TABS.some(([k]) => k === tab)) tab = "status";
  let body = "";
  if (tab === "status") {
    const st = await load("status", "/api/status");
    const svc = (st && st.services) || [];
    const groups = new Map();
    for (const s of svc) {
      if (!groups.has(s.group)) groups.set(s.group, []);
      groups.get(s.group).push(s);
    }
    const backup = svc.find((s) => s.id === "backup");
    body = `<div class="cols even">
      <div class="stack s24">${[...groups].map(([g, items]) => `<section class="panel"><h2 style="margin-bottom:10px">${esc(g)}</h2>${statusList(items)}</section>`).join("") || `<div class="empty"><h3>Can't reach the server</h3></div>`}</div>
      <section class="panel" style="align-self:start"><h2 style="margin-bottom:10px">Backups</h2><dl class="kv">
        <dt>Nightly backup</dt><dd>${backup ? esc(backup.state === "ok" ? "On time · " + backup.detail : backup.state === "down" ? "Late · " + backup.detail : "Unknown · " + backup.detail) : "Not set up"}</dd>
        <dt>Checked</dt><dd>${st ? esc(when(new Date(st.checked_at * 1000).toISOString())) : "unknown"}</dd></dl>
        <p class="xs muted" style="margin-top:10px">A backup is only shown as on time when its newest record is less than a day old. Unknown means the app can't see it yet, not that it failed.</p>
        <div class="btns" style="margin-top:10px"><button type="button" class="btn sm" data-act="recheck">Check again</button></div></section></div>`;
  } else if (tab === "spending") {
    const d = await load("spending", "/api/spending");
    const b = d && d.board, ag = d && d.agents, mx = d && d.max;
    const bd = b && b.ok && b.data;
    const pct = bd && bd.budget_cents ? Math.min(100, (100 * bd.spend_cents) / bd.budget_cents) : null;
    const m = mx && mx.ok && mx.data;
    body = `<div class="stack s24">
      <section class="panel"><div class="eyebrow">${esc(new Date().toLocaleDateString("en-GB", { month: "long", year: "numeric" }))} to date · Paperclip board</div>
        ${bd ? `<div class="kpi"><b>${money(bd.spend_cents)}</b><span>reported${bd.budget_cents ? ` · of ${money(bd.budget_cents)} board budget` : ""}</span></div>${pct != null ? `<div style="margin:12px 0"><div class="bar" role="img" aria-label="${pct.toFixed(0)} percent of budget"><i style="width:${pct.toFixed(1)}%"></i></div></div>` : ""}${bd.pricing_complete === false ? `<p class="xs muted">Some runs aren't priced yet, so the real figure may be higher.</p>` : ""}` : b ? notConnected("The Paperclip board", b.error) : ""}
        <p class="xs muted" style="margin-top:8px">Codex and Claude run on your own subscriptions, so their work shows little or no spend here.</p></section>
      <section class="panel"><h2 style="margin-bottom:10px">${esc((m && m.label) || "Max on OpenRouter")}</h2>
        ${m ? `<dl class="kv"><dt>Today</dt><dd>${esc(usd(m.today_usd))}</dd><dt>This month</dt><dd>${esc(usd(m.month_usd))}${m.month_cap_usd != null ? ` of ${esc(usd(m.month_cap_usd))} alert level` : ""}</dd>${m.balance_usd != null ? `<dt>Credit left</dt><dd>${esc(usd(m.balance_usd))}</dd>` : ""}</dl>` : mx ? notConnected("The cost monitor", mx.error) : ""}</section>
      ${ag && ag.ok ? `<div class="tbl-wrap"><table class="tbl tbl-cards"><thead><tr><th>Agent</th><th>Status</th><th class="r">This month</th></tr></thead><tbody>${ag.data.map((a) => `<tr><td data-l="Agent">${esc(a.name)}</td><td data-l="Status">${esc(a.status_text)}</td><td class="r" data-l="This month">${money(a.spent_cents)}${a.budget_cents ? ` <span class="xs muted">of ${money(a.budget_cents)}</span>` : ""}</td></tr>`).join("")}</tbody></table></div>` : ""}
    </div>`;
  } else {
    const [ag, st, wk] = await Promise.all([load("agents", "/api/agents"), load("status", "/api/status"), load("work", "/api/work")]);
    const kill = ((st && st.services) || []).find((s) => s.id === "kill");
    const stopped = kill && kill.state === "down";
    const agents = (ag && ag.agents && ag.agents.ok && ag.agents.data) || [];
    const runs = ((wk && wk.work && wk.work.ok && wk.work.data.issues) || []).filter((i) => i.running && i.run_id);
    body = `<div class="stack s24">
      <section class="panel"><div class="panel-h"><div><h2>Block all agent work</h2><p class="small muted">Runs the server's kill switch: stops Max, the board's agents, the sealed box and the email broker. Resuming stays on your Mac.</p></div>
        <div class="r">${stopped ? badge("err", "Stopped") : `<button type="button" class="btn danger solid" data-act="stopAsk"${S.meta.stop_ready ? "" : " disabled"}>${ic("stop")}Block all agent work</button>`}</div></div>
        ${!S.meta.stop_ready && !stopped ? `<p class="xs muted">The stop button needs a one-off server change before it works. Until then, use KILL-MAX on your Mac.</p>` : ""}</section>
      <div class="cols even">
        <section class="panel"><h2 style="margin-bottom:6px">Agent assignments</h2>${ag && ag.agents && !ag.agents.ok ? notConnected("The Paperclip board", ag.agents.error) : ""}<div class="list">${agents.map((a) => `<div class="li"><span class="main"><span class="t">${esc(a.name)}</span><span class="s">${a.status === "paused" ? "Paused" : "Taking assignments"}</span></span><button type="button" class="btn ghost sm" data-act="${a.status === "paused" ? "resumeAgent" : "pauseAgent"}" data-arg="${esc(a.id)}">${a.status === "paused" ? "Resume" : "Pause assignments"}</button></div>`).join("")}</div></section>
        <section class="panel"><h2 style="margin-bottom:6px">Active runs</h2><div class="list">${runs.map((i) => `<div class="li"><span class="main"><span class="t mono">${esc(i.run_id.slice(0, 12))}</span><span class="s">${esc(i.agent || "")} · ${esc(i.ref || i.title)}</span></span><button type="button" class="btn sm" data-act="cancelRun" data-arg="${esc(i.run_id)}">Stop run</button></div>`).join("") || `<p class="small muted">No runs active.</p>`}</div></section>
      </div></div>`;
  }
  return `<div class="ph"><div class="ph-t"><h1>System</h1><p class="sub">Service state, spending and controls.</p></div></div>
    <nav class="tabs" aria-label="Sections">${SYS_TABS.map(([k, t]) => `<a href="#system/${k}"${k === tab ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>${body}`;
}

function screenMore() {
  const items = [["max", "max", "Max", "Chat with Max"], ["agents", "agents", "Agents", "Who does what and what it costs"], ["routines", "routines", "Routines", "Recurring agent work"], ["system/status", "system", "System", "Services, spending and the stop button"], ["soon/health", "health", "Health", "Food, training and progress in Phase 4"]];
  return `<div class="ph"><div class="ph-t"><h1>More</h1></div></div><section class="panel"><div class="list">${items.map(([h, i, t, s]) => `<a class="li" href="#${h}"><span class="main"><span class="t">${ic(i, 16)} ${t}</span><span class="s">${s}</span></span><span class="end">${ic("chev", 16)}</span></a>`).join("")}</div></section>`;
}

function screenSoon(what) {
  const info = {
    max: ["Max", 3, "Chat with Max here as well as on Signal, with the artifacts panel from the mockup."],
    health: ["Health", 4, "Food, Train and Progress on NutriTrace, LiftTrace and CookTrace, once it matches or beats them."],
    inbox: ["Inbox", 5, "Your mail, shown to you only. It never goes to Max or any model."],
    planner: ["Planner", 5, "Your calendar, once it's connected."],
    library: ["Library", 5, "salt.md notes, signed in as you."],
  }[what] || ["This screen", "", "Coming in a later phase."];
  return `<div class="ph"><div class="ph-t"><h1>${esc(info[0])}</h1></div></div><div class="empty" style="max-width:520px"><h3>Coming in Phase ${info[1]}</h3><p class="small">${esc(info[2])}</p></div>`;
}

/* ---------- Max chat ---------- */

// Markdown for Max's replies. Everything is escaped first, so only the
// formatting below can produce markup; links must be http(s).
function md(text) {
  const blocks = String(text || "").replace(/\r/g, "").split(/\n{2,}/);
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<i>$2</i>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  let inCode = false, code = [];
  const out = [];
  for (const b of blocks) {
    if (inCode || b.startsWith("```")) {
      code.push(b);
      const joined = code.join("\n\n");
      if ((joined.match(/```/g) || []).length >= 2) {
        out.push(`<pre><code>${esc(joined.replace(/^```[^\n]*\n?/, "").replace(/\n?```\s*$/, ""))}</code></pre>`);
        inCode = false; code = [];
      } else inCode = true;
      continue;
    }
    const lines = b.split("\n");
    if (lines.every((l) => /^\s*[-*•]\s+/.test(l))) out.push(`<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("")}</ul>`);
    else if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) out.push(`<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`);
    else if (/^#{1,4}\s/.test(b)) out.push(`<h3>${inline(b.replace(/^#{1,4}\s+/, ""))}</h3>`);
    else out.push(`<p>${lines.map(inline).join("<br>")}</p>`);
  }
  if (code.length) out.push(`<pre><code>${esc(code.join("\n\n").replace(/^```[^\n]*\n?/, ""))}</code></pre>`);
  return out.join("");
}

function chatTools(tools) {
  if (!tools || !tools.length) return "";
  return `<details class="mc-activity"><summary>${ic("check", 14)}<span>${tools.length} tool call${tools.length === 1 ? "" : "s"}</span>${ic("chev", 14)}</summary><div class="mc-act-body">${tools.map((t) => {
    let args = t.args || "";
    try { args = JSON.stringify(JSON.parse(args), null, 2); } catch (_) { /* leave as text */ }
    return `<details class="mc-act-tool"><summary><span class="mc-dot tool"></span><span><b>Max</b> <span class="mono">${esc(t.name)}</span>${t.result != null ? ` <span class="muted">→ ${esc(String(t.result).slice(0, 80))}</span>` : ` <span class="muted">· running</span>`}</span></summary>${args ? `<pre class="mc-args">${esc(args)}</pre>` : ""}</details>`;
  }).join("")}</div></details>`;
}

function chatMsg(m, i, live) {
  const me = m.role === "me";
  const name = me ? ((S.me && S.me.name) || "You").split(" ")[0] : "Max";
  const t = m.at ? when(m.at, false) : "";
  const status = m.status === "stopped" ? `<p class="xs muted">Stopped.</p>` : m.status === "error" ? `<p class="small" style="color:var(--err)">${esc(m.error || "Max hit an error.")}</p>` : "";
  if (me) return `<div class="mc-msg me"><span class="mc-av me">${esc(initials(name))}</span><div class="mc-body"><div class="mc-meta"><b>${esc(name)}</b><span>${esc(t)}</span></div><div class="bubble">${esc(m.text)}</div><div class="mc-acts" role="toolbar" aria-label="Message actions"><button type="button" class="iconbtn sm" data-act="chatCopy" data-arg="${i}" title="Copy" aria-label="Copy">${ic("copy", 15)}</button></div></div></div>`;
  return `<div class="mc-msg"${live ? ' id="live"' : ""}><span class="mc-av max">${logoMark(30)}</span><div class="mc-body"><div class="mc-meta"><b>Max</b><span class="tag">Hermes</span><span>${esc(t)}</span></div>${chatTools(m.tools)}${
    live && !m.text ? `<div class="mc-think">${ic("spark", 15)}Working…</div>` : `<div class="md mc-md">${live ? md(m.text).replace(/(<\/(?:p|li|h3)>(?:<\/[uo]l>)?)$/, '<span class="mc-caret"></span>$1') : md(m.text)}</div>`}${status}${
    live ? "" : `<div class="mc-acts" role="toolbar" aria-label="Message actions"><button type="button" class="iconbtn sm" data-act="chatCopy" data-arg="${i}" title="Copy" aria-label="Copy">${ic("copy", 15)}</button></div>`}</div></div>`;
}

async function screenMax(cid) {
  const fresh = cid === "new";
  if (fresh) cid = null;
  let list;
  try { list = await api("/api/chat"); } catch (e) {
    return `<div class="ph"><div class="ph-t"><h1>Max</h1></div></div>${notConnected("Chat with Max", e.message)}<p class="small muted" style="margin-top:12px">Signal works as before.</p>`;
  }
  let conv = null;
  if (cid) {
    try { conv = await api(`/api/chat/${encodeURIComponent(cid)}`); } catch (e) { toast(e.message); }
  }
  S.chat = conv;
  const busy = S.chatLive && S.chatLive.cid === cid;
  const msgs = conv ? conv.messages.map((m, i) => chatMsg(m, i)).join("") + (busy ? chatMsg(S.chatLive.msg, -1, true) : conv.busy ? chatMsg({ role: "max", text: "", tools: [] }, -1, true) : "") : "";
  const convs = list.chats.map((c) => `<a class="mc-conv${c.id === cid ? " sel" : ""}" href="#max/${esc(c.id)}"><span class="t">${esc(c.title || "New chat")}</span><span class="xs muted">${esc(when(c.updated))}${list.busy.includes(c.id) ? " · answering" : ""}</span></a>`).join("") || `<p class="small muted" style="padding:8px 12px">No chats yet.</p>`;
  const running = busy || (conv && conv.busy);
  return `<div class="mc-app${cid || fresh ? " has-conv" : ""}"><aside class="mc-side"><div class="mc-side-h"><span class="mc-av max">${logoMark(30)}</span><span style="flex:1;min-width:0"><b>Max</b><span class="xs muted" style="display:block">Same Max as Signal · same approvals</span></span><button type="button" class="btn ghost" data-act="newChat" aria-label="New chat" title="New chat">${ic("plus")}</button></div>
    <nav class="mc-convs" aria-label="Chats">${convs}</nav></aside>
    <section class="mc-main" aria-label="Chat">
      <header class="mc-bar"><a class="iconbtn mc-back" href="#max" aria-label="All chats">${ic("back")}</a><b class="mc-title">${esc((conv && conv.title) || "New chat")}</b><span class="spacer"></span>${conv ? `<button type="button" class="btn ghost sm" data-act="chatDelete" data-arg="${esc(conv.id)}" title="Delete this chat"${running ? " disabled" : ""}>${ic("trash", 15)}<span class="lbl">Delete</span></button>` : ""}</header>
      <div class="mc-scroll" id="mc-scroll"><div class="mc-col" id="transcript" aria-live="polite">${conv && conv.messages.length ? msgs : `<div class="mc-empty">${logoMark(40)}<h2>How can Max help?</h2><p class="small muted">This is the same Max as on Signal, with the same sandbox and approvals. Emails still need your fingerprint.</p></div>`}</div></div>
      <div class="mc-dock"><div class="mc-col"><div class="composer mc-composer lh">
        <label class="sr" for="max-in">Message Max</label>
        <textarea id="max-in" rows="2" placeholder="Message Max" maxlength="8000">${esc(S.chatDraft || "")}</textarea>
        <div class="mc-actbar"><div class="mc-actl"><span class="xs muted desk-only">Enter to send · Shift+Enter for a new line</span></div>
          <div class="mc-actr">${running
            ? `<button type="button" class="btn sm" data-act="chatStop" data-arg="${esc(cid || "")}">${ic("stop", 15)}Stop</button>`
            : `<span class="mca-send"><button type="button" class="btn primary sm" data-act="chatSend" aria-label="Send" title="Send">${ic("send", 16)}</button></span>`}</div></div>
      </div><p class="mc-hint xs muted">Chats are kept on your server. Max can't send email without your fingerprint.</p></div></div>
    </section></div>`;
}

function chatScroll() {
  const sc = $("#mc-scroll");
  if (sc) sc.scrollTop = sc.scrollHeight;
}

let liveFrame = 0;
function paintLive() {
  if (liveFrame) return;
  liveFrame = requestAnimationFrame(() => {
    liveFrame = 0;
    const el = $("#live");
    if (!el || !S.chatLive) return;
    const sc = $("#mc-scroll");
    const atEnd = !sc || sc.scrollHeight - sc.scrollTop - sc.clientHeight < 80;
    el.outerHTML = chatMsg(S.chatLive.msg, -1, true);
    if (atEnd) chatScroll();
  });
}

async function chatSend() {
  const box = $("#max-in");
  const text = (box ? box.value : "").trim();
  if (!text || S.chatLive) return;
  let cid = (location.hash.match(/^#max\/([A-Za-z0-9_-]+)/) || [])[1];
  if (!cid || cid === "new") {
    const c = await api("/api/chat", { body: {} });
    cid = c.id;
    history.replaceState(null, "", "#max/" + cid);
  }
  S.chatDraft = "";
  S.chatLive = { cid, msg: { role: "max", text: "", tools: [], at: new Date().toISOString() } };
  const res = await fetch(`/api/chat/${encodeURIComponent(cid)}/send`, {
    method: "POST", credentials: "same-origin", cache: "no-store",
    headers: { "Content-Type": "application/json", "X-Hermes-Action": "1" },
    body: JSON.stringify({ text }),
  }).catch(() => null);
  if (!res || !res.ok || !(res.headers.get("Content-Type") || "").startsWith("text/event-stream")) {
    let msg = "Max isn't reachable right now.";
    try { msg = (await res.json()).error || msg; } catch (_) { /* no body */ }
    S.chatLive = null;
    S.chatDraft = text;
    toast(msg);
    return render();
  }
  await render();
  chatScroll();
  const live = S.chatLive.msg;
  const byId = {};
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let k;
      while ((k = buf.indexOf("\n\n")) >= 0) {
        const chunk = buf.slice(0, k);
        buf = buf.slice(k + 2);
        if (!chunk.startsWith("data: ")) continue;
        let ev;
        try { ev = JSON.parse(chunk.slice(6)); } catch (_) { continue; }
        if (ev.type === "text") live.text += ev.delta;
        else if (ev.type === "tool") { const t = { name: ev.name, args: ev.args, result: null }; byId[ev.id] = t; live.tools.push(t); }
        else if (ev.type === "tool_done" && byId[ev.id]) byId[ev.id].result = ev.result;
        else if (ev.type === "end" && ev.status === "error") toast(ev.message || "Max hit an error.");
        paintLive();
      }
    }
  } catch (_) {
    toast("Lost the connection. Max keeps going; the answer appears here when it's done.");
  }
  S.chatLive = null;
  await render();
  chatScroll();
}

/* ---------- dialogs ---------- */

function modal(title, body, foot) {
  $("#overlay").innerHTML = `<div class="ov"><div class="scrim" data-act="close"></div><div class="modal" role="dialog" aria-modal="true" aria-labelledby="m-title"><div class="m-h"><h2 id="m-title">${esc(title)}</h2><button type="button" class="iconbtn" data-act="close" aria-label="Close">${ic("close")}</button></div><div class="m-b">${body}</div><div class="m-f">${foot}</div></div></div>`;
  const f = $("#overlay [autofocus]");
  if (f) f.focus();
}
const closeModal = () => { $("#overlay").innerHTML = ""; };

async function openCreateTask() {
  const d = S.cache.work || (await load("work", "/api/work"));
  const agents = (d && d.work && d.work.ok && d.work.data.agents) || [];
  modal("Create task", `<div class="form-grid">
    <div class="field full"><label for="ct-t">Title</label><input class="inp" id="ct-t" maxlength="240" autofocus></div>
    <div class="field"><label for="ct-a">Assignee</label><select class="inp" id="ct-a"><option value="">Let the board decide</option>${agents.map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`).join("")}</select></div>
    <div class="field full"><label for="ct-d">What you want back</label><textarea class="inp" id="ct-d" rows="4" maxlength="8000" placeholder="For example: a short brief with cited public sources"></textarea></div>
    <div class="field full"><span class="lab">Allowed context</span><div class="chips"><span class="chip" aria-pressed="true">Public web</span><span class="chip" aria-pressed="true">Task workspace</span><span class="chip locked">${ic("lock", 14)}Private data locked</span></div></div>
  </div>`, `<button type="button" class="btn ghost" data-act="close">Cancel</button><button type="button" class="btn primary" data-act="taskSubmit">Create task</button>`);
}

/* ---------- actions ---------- */

async function act(name, arg, el) {
  const busy = (on) => { if (el) el.disabled = on; };
  try {
    if (name === "close") return closeModal();
    if (name === "createTask") return openCreateTask();
    if (name === "newChat") { S.chatDraft = ""; location.hash = "max/new"; setTimeout(() => { const b = $("#max-in"); if (b) b.focus(); }, 50); return; }
    if (name === "chatSend") return chatSend();
    if (name === "chatStop") { busy(true); await api(`/api/chat/${encodeURIComponent(arg)}/stop`, { body: {} }); return; }
    if (name === "chatCopy") {
      const m = S.chat && S.chat.messages[Number(arg)];
      if (m) { await navigator.clipboard.writeText(m.text); toast("Copied."); }
      return;
    }
    if (name === "chatDelete") {
      if (!confirm("Delete this chat from the app? Max's own memory isn't changed.")) return;
      busy(true);
      await api(`/api/chat/${encodeURIComponent(arg)}/delete`, { body: {} });
      location.hash = "max";
      return;
    }
    if (name === "workFilter") { S.workFilter = arg; return render(); }
    if (name === "apprView") { S.apprView = arg; return render(); }
    if (name === "recheck") { busy(true); return render(); }
    if (name === "taskSubmit") {
      const title = $("#ct-t").value.trim();
      if (!title) { $("#ct-t").focus(); return toast("Give the task a title."); }
      busy(true);
      const r = await api("/api/work/tasks", { body: { title, description: $("#ct-d").value, agent_id: $("#ct-a").value || null } });
      closeModal();
      toast(`Created ${r.ref || "the task"} on the board.`);
      location.hash = "work";
      return render();
    }
    if (name === "decide") {
      const [id, decision] = arg.split("|");
      busy(true);
      const note = $("#dec-note") ? $("#dec-note").value : "";
      await api(`/api/approvals/board/${encodeURIComponent(id)}`, { body: { decision, note } });
      toast(decision === "approve" ? "Approved in Paperclip." : "Rejected in Paperclip.");
      return render();
    }
    if (name === "pauseAgent" || name === "resumeAgent") {
      busy(true);
      await api(`/api/agents/${encodeURIComponent(arg)}/${name === "pauseAgent" ? "pause" : "resume"}`, { body: {} });
      toast(name === "pauseAgent" ? "Assignments paused." : "Assignments resumed.");
      return render();
    }
    if (name === "cancelRun") {
      busy(true);
      await api(`/api/runs/${encodeURIComponent(arg)}/cancel`, { body: {} });
      toast("Asked the run to stop. Check again in a moment to see it confirmed.");
      return render();
    }
    if (name === "stopAsk") {
      return modal("Block all agent work?", `<p>This runs the server's kill switch. It will:</p><ol style="padding-left:18px;display:flex;flex-direction:column;gap:6px"><li>Stop Max, including Signal replies.</li><li>Stop the board's agents and the sealed box.</li><li>Freeze the email broker, so approved emails won't send.</li></ol><p class="small muted">Emails already sent can't be recalled. Resuming is done from your Mac.</p>`,
        `<button type="button" class="btn ghost" data-act="close">Cancel</button><button type="button" class="btn danger solid" data-act="stopGo">${ic("stop")}Block all agent work</button>`);
    }
    if (name === "stopGo") {
      busy(true);
      await api("/api/stop", { body: { confirm: "STOP" } });
      closeModal();
      toast("Stop requested. The kill switch shows as On once the server confirms.");
      return render();
    }
  } catch (e) {
    busy(false);
    toast(e.message || "That didn't work.");
  }
}

document.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el) return;
  e.preventDefault();
  act(el.dataset.act, el.dataset.arg, el);
});
document.addEventListener("change", (e) => {
  const el = e.target.closest("[data-change]");
  if (!el) return;
  S[el.dataset.change] = el.value;
  render();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && $("#overlay").innerHTML) closeModal();
  if (e.target.id === "max-in" && e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    chatSend().catch((err) => toast(err.message || "That didn't send."));
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "max-in") S.chatDraft = e.target.value;
});

/* ---------- router ---------- */

let renderSeq = 0;
async function render() {
  const route = (location.hash || "#today").slice(1) || "today";
  const [area, ...rest] = route.split("/");
  const seq = ++renderSeq;
  renderShell(route);
  let html;
  if (area === "today") html = await screenToday();
  else if (area === "work") html = await screenWork(rest.join("/"));
  else if (area === "agents") html = await screenAgents();
  else if (area === "routines") html = await screenRoutines();
  else if (area === "approvals") html = await screenApprovals(rest.join("/"));
  else if (area === "system") html = await screenSystem(rest[0]);
  else if (area === "max") html = await screenMax(rest[0]);
  else if (area === "more") html = screenMore();
  else if (area === "soon") html = screenSoon(rest[0]);
  else html = await screenToday();
  if (seq !== renderSeq) return; // a newer render started
  if (!S.cache.approvals && area !== "approvals") load("approvals", "/api/approvals").then(() => renderShell(route));
  renderShell(route);
  $("#main").innerHTML = html;
  if (area === "max") {
    chatScroll();
    // An answer still running on the server (phone slept, page reloaded): check back.
    clearTimeout(S.chatPoll);
    if (!S.chatLive && S.chat && S.chat.busy) S.chatPoll = setTimeout(() => { if ((location.hash || "").startsWith("#max")) render(); }, 3000);
  }
}

async function init() {
  try { S.me = await api("/api/me"); S.meta = { demo: S.me.demo, board_url: S.me.board_url, broker_url: S.me.broker_url, stop_ready: S.me.stop_ready }; } catch (_) { S.offline = true; }
  window.addEventListener("hashchange", () => { window.scrollTo(0, 0); render(); });
  await render();
  setInterval(() => {
    const typing = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
    const chatting = (location.hash || "").startsWith("#max");
    if (!document.hidden && !$("#overlay").innerHTML && !typing && !chatting) render();
  }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) render(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

init();
