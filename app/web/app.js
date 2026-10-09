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
  ext: '<path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5"/>',
});
const fmtSize = (b) => (b == null ? "" : b < 1024 ? b + " B" : b < 1048576 ? Math.round(b / 1024) + " KB" : (b / 1048576).toFixed(1) + " MB");
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

const S = { workView: (() => { try { return localStorage.getItem("hermes:workView") || "list"; } catch (_) { return "list"; } })(), me: null, meta: {}, cache: {}, apprView: "pending", apprFilter: "all", workFilter: "all", workAgent: "all", workStatus: "all" };

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
  { h: "health/food", i: "health", t: "Health" },
  { h: "inbox", i: "inbox", t: "Inbox" },
  { h: "planner", i: "planner", t: "Planner" },
  { label: "Assistant & agents" },
  { h: "max", i: "max", t: "Max" },
  { h: "work", i: "work", t: "Work" },
  { h: "agents", i: "agents", t: "Agents" },
  { h: "routines", i: "routines", t: "Routines" },
  { label: "Workspace" },
  { h: "approvals", i: "approvals", t: "Approvals", count: true },
  { h: "library", i: "library", t: "Library" },
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
    <button type="button" class="search-btn" data-act="palette" aria-label="Search or jump to">${ic("search", 15)}<span>Search or jump to…</span><kbd>${IS_MAC ? "⌘" : "Ctrl "}K</kbd></button>
    <nav class="nav" aria-label="Primary">${NAV.map((x) => {
      if (x.label) return `<div class="nav-label">${esc(x.label)}</div>`;
      const cur = x.h.split("/")[0] === area;
      return `<a href="#${x.h}"${cur ? ' aria-current="page"' : ""}${x.soon ? ' class="soon"' : ""}>${ic(x.i)}<span>${esc(x.t)}</span>${
        x.count && n ? `<span class="count" aria-label="${n} pending">${n}</span>` : ""}${x.soon ? `<span class="soon-tag">Phase ${x.soon}</span>` : ""}</a>`;
    }).join("")}</nav>
    <div class="side-foot"><button type="button" class="acct" data-act="acctMenu" aria-haspopup="menu"><span class="avatar">${esc(initials(me.name || "Craig"))}</span><span class="who"><b style="display:block;font-size:13.5px;font-weight:600">${esc((me.name || "Craig").split(" ")[0])}</b><span class="xs muted">Owner · ${esc(me.login || "signed in through Tailscale")}</span></span></button></div>`;

  const title = { today: "Today", max: "Max", work: "Work", agents: "Agents", routines: "Routines", approvals: "Approvals", system: "System", health: "Health", inbox: "Inbox", planner: "Planner", library: "Library", more: "More", soon: "Coming next" }[area] || "Hermes";
  $("#top").innerHTML = `
    <a href="#today" class="phone-only" aria-label="Hermes home" style="display:flex">${logoMark(26)}</a>
    <div class="crumb"><span class="cur">${esc(title)}</span></div>
    <div class="top-r">
      ${S.meta.demo ? `<span class="demo-pill" title="The server is running on sample data, not your real board"><i></i>Sample data</span>` : ""}
      ${S.offline ? `<span class="demo-pill offline-pill"><i></i>Offline</span>` : ""}
      ${area === "max" && S.me && S.me.chat_ready !== false ? `<button type="button" class="btn sm chat-btn" data-act="newChat" title="New chat">${ic("chat")}<span class="lbl">New chat</span></button>` : ""}
      <button type="button" class="btn primary sm cap-btn" data-act="createTask" title="Create a task for an agent">${ic("plus")}<span class="lbl">New task</span></button>
      <button type="button" class="iconbtn phone-only" data-act="palette" aria-label="Search">${ic("search", 18)}</button>
      <button type="button" class="iconbtn phone-only" data-act="acctMenu" aria-label="Account and appearance"><span class="avatar" style="width:30px;height:30px;font-size:12px">${esc(initials(me.name || "Craig"))}</span></button>
    </div>`;

  $("#bnav").innerHTML = `
    <a href="#today"${area === "today" ? ' aria-current="page"' : ""}>${ic("today")}Today</a>
    <button type="button" class="cap" data-act="createTask">${ic("plus")}Task</button>
    <a href="#work"${area === "work" ? ' aria-current="page"' : ""}>${ic("work")}Work</a>
    <a href="#approvals"${area === "approvals" ? ' aria-current="page"' : ""}>${ic("approvals")}Review${n ? `<span class="count">${n}</span>` : ""}</a>
    <a href="#more"${["more", "max", "agents", "routines", "system", "soon", "health"].includes(area) ? ' aria-current="page"' : ""}>${ic("more")}More</a>`;
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

// Calorie ring for Today (from the mockup).
function ring(val, target) {
  const r = 58, C = 2 * Math.PI * r, a = target ? Math.min(1, (val || 0) / target) : 0;
  return `<div class="ring" role="img" aria-label="${esc(fmtN(val))} of ${esc(fmtN(target))} kcal">
    <svg viewBox="0 0 136 136" aria-hidden="true"><circle cx="68" cy="68" r="${r}" fill="none" stroke="var(--fill-2)" stroke-width="11"/>
    <circle cx="68" cy="68" r="${r}" fill="none" stroke="var(--accent)" stroke-width="11" stroke-linecap="round" stroke-dasharray="${Math.max(0.001, a * C)} ${C}"/></svg>
    <div class="c"><b>${fmtN(val)}</b><span>of ${fmtN(target)} kcal</span></div></div>`;
}

const hhmm = (iso) => (iso || "").slice(11, 16);
const nowHHMM = () => new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: TZ });

// Today's calendar as an agenda with a "now" line (from the mockup).
function agendaRows(events) {
  const now = nowHHMM(), rows = [];
  let placed = false;
  const line = `<div class="now" aria-label="Now ${now}"><span>${now} now</span><i></i></div>`;
  for (const e of events) {
    if (!placed && !e.all_day && hhmm(e.start) > now) { rows.push(line); placed = true; }
    const time = e.all_day ? "All day" : `${hhmm(e.start)}${e.end ? "–" + hhmm(e.end) : ""}`;
    const past = !e.all_day && e.end && hhmm(e.end) < now;
    rows.push(`<a class="row${past ? " past" : ""}" href="#planner"><span class="t">${esc(time)}</span><span class="mk external" aria-hidden="true"></span><span><b>${esc(e.title)}</b><small>${esc(e.location || "Calendar")}</small></span><span class="end xs muted"></span></a>`);
  }
  if (!placed) rows.push(line);
  return rows.join("");
}

async function screenToday() {
  const [d, f, p] = await Promise.all([
    load("today", "/api/today"),
    healthData("food:today", "food"),
    inboxApi(`/api/planner?start=${londonToday()}&days=1`),
  ]);
  const status = (d && d.status && d.status.services) || [];
  const down = status.filter((s) => s.state === "down" && s.id !== "kill");
  const kill = status.find((s) => s.id === "kill");
  const waiting = (d && d.waiting) || [];
  const now = new Date();
  const name = ((S.me && S.me.name) || "Craig").split(" ")[0];

  const goals = f && f.goals && f.goals.ok ? f.goals.data : {};
  const t = f && f.day && f.day.ok ? f.day.data.totals : null;
  const left = t && goals.kcal ? Math.max(0, goals.kcal - t.kcal) : null;
  const links = (f && f.links) || {};
  const food = `<section class="panel a-food" aria-labelledby="h-food">
    <div class="panel-h"><h2 id="h-food">Food today</h2><div class="r"><a class="btn ghost sm" href="#health/food">Open Food ${ic("chev", 16)}</a></div></div>
    ${!f ? notConnected("Health", "Couldn't reach the server.") : !t ? notConnected("NutriTrace", f.day && f.day.error) : `<div class="food-hero">${ring(t.kcal, goals.kcal)}
      <div class="stack s8" style="gap:14px;width:100%"><p class="small muted">${left != null ? `${fmtN(left)} kcal left · fixed target` : "No calorie target set"}</p>
        <div class="macros">${[["protein", "Protein"], ["carbs", "Carbs"], ["fat", "Fat"], ["fibre", "Fibre"]].map(([k, l]) => `<div class="macro"><div class="l"><span>${l}</span><span>${fmtN(t[k])}<span class="muted" style="font-weight:400"> / ${fmtN(goals[k])} g</span></span></div>${hBar(t[k], goals[k])}</div>`).join("")}</div></div></div>`}
    <div class="btns" style="margin-top:16px"><a class="btn primary" href="#health/log">${ic("plus", 16)}Log food</a><a class="btn" href="#health/scan">${ic("camera", 16)}Scan a barcode</a><a class="btn" href="#health/train">${ic("dumbbell", 16)}Training</a></div>
  </section>`;

  const events = p && p.ok && p.days && p.days[0] ? p.days[0].events : [];
  const next = events.find((e) => !e.all_day && hhmm(e.start) > nowHHMM());
  const plan = `<section class="panel a-next" aria-labelledby="h-plan"><div class="panel-h"><h2 id="h-plan">Today's plan</h2><span class="xs muted">Europe/London</span><div class="r"><a class="btn ghost sm" href="#planner">Planner ${ic("chev", 16)}</a></div></div>
    ${p && !p.ok ? notConnected("Google Calendar", p.error) : events.length ? `<div class="agenda">${agendaRows(events)}</div>` : `<div class="empty"><h3>Nothing on today</h3><p>Your calendar is clear.</p></div>`}</section>`;

  return `
  <div class="ph"><div class="ph-t">
    <div class="eyebrow">${esc(now.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: TZ }))} · ${esc(when(now.toISOString(), false))}</div>
    <h1>${greeting()}, ${esc(name)}</h1>
    <div class="summary-line">
      <span><b>${d && d.waiting_ok ? waiting.length : "?"}</b> decision${waiting.length === 1 ? "" : "s"} waiting</span>
      ${next ? `<span>Next: <b>${esc(next.title)}</b> at ${esc(hhmm(next.start))}</span>` : ""}
      <span><b>${d && d.work_ok ? d.needs_you : "?"}</b> ${d && d.needs_you === 1 ? "task needs" : "tasks need"} you</span>
      ${left != null ? `<span><b>${fmtN(left)}</b> kcal left</span>` : ""}
      ${down.length ? `<span><b>${down.length}</b> ${down.length === 1 ? "service" : "services"} down</span>` : ""}
    </div>
  </div></div>
  <a class="ask-bar" href="#max/new">${ic("chat")}<span>Ask Max or start a discussion…</span></a>
  ${kill && kill.state === "down" ? `<div class="notice err" role="status" style="margin-bottom:16px">${ic("stop")}<div><b>All agent work is stopped.</b> The kill switch is on. Resume it from your Mac.</div></div>` : ""}
  <div class="bento">
    ${food}
    <section class="panel a-dec" aria-labelledby="h-dec">
      <div class="panel-h"><h2 id="h-dec">Waiting for you</h2>${waiting.length ? `<span class="badge accent">${waiting.length}</span>` : ""}<div class="r"><a class="btn ghost sm" href="#approvals">Approvals ${ic("chev", 16)}</a></div></div>
      ${d && !d.waiting_ok ? notConnected("Part of approvals", (d.waiting_errors || []).join("; ")) : ""}
      ${waiting.length ? `<div class="list">${waiting.map((a) => approvalRow(a)).join("")}</div>` : d && d.waiting_ok ? `<div class="empty"><h3>Nothing waiting for review</h3><p>Email sends and board decisions appear here.</p></div>` : ""}
    </section>
    ${f ? weekPanel(f, f.today, "today") : ""}
    ${plan}
    <section class="panel a-team" aria-labelledby="h-team">
      <div class="panel-h"><h2 id="h-team">Team work</h2><div class="r"><a class="btn ghost sm" href="#work">Work ${ic("chev", 16)}</a></div></div>
      ${d && !d.work_ok ? notConnected("The Paperclip board", "") : ""}
      ${d && d.work && d.work.length ? `<div class="list">${d.work.map(issueRow).join("")}</div>` : d && d.work_ok ? `<p class="small muted">No open tasks. Use New task to give an agent something to do.</p>` : ""}
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
  if (sub && sub.startsWith("run/")) return screenRun(sub.slice(4));
  if (sub) return screenTask(sub);
  const d = await load("work", "/api/work");
  const w = d && d.work;
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
    <div class="ph-a"><div class="seg" role="group" aria-label="View"><button type="button" aria-pressed="${S.workView !== "board"}" data-act="workView" data-arg="list">List</button><button type="button" aria-pressed="${S.workView === "board"}" data-act="workView" data-arg="board">Board</button></div>${S.meta.board_url ? `<a class="btn" href="${esc(S.meta.board_url)}" target="_blank" rel="noopener">Open board</a>` : ""}<button type="button" class="btn primary" data-act="createTask">${ic("plus")}Create task</button></div></div>
  ${w && !w.ok ? notConnected("The Paperclip board", w.error) : ""}
  <div class="stack s24">
    <div class="row-flex">
      <div class="chips"><button type="button" class="chip" aria-pressed="${S.workFilter === "all"}" data-act="workFilter" data-arg="all">All</button><button type="button" class="chip" aria-pressed="${S.workFilter === "attention"}" data-act="workFilter" data-arg="attention">Needs you · ${needs}</button></div>
      <label class="sr" for="wf-agent">Agent</label><select class="inp" id="wf-agent" style="width:auto" data-change="workAgent"><option value="all">Any agent</option>${agents.map((a) => `<option value="${esc(a.id)}"${S.workAgent === a.id ? " selected" : ""}>${esc(a.name)}</option>`).join("")}</select>
      <label class="sr" for="wf-st">Status</label><select class="inp" id="wf-st" style="width:auto" data-change="workStatus"><option value="all">Any status</option>${statuses.map(([v, t]) => `<option value="${v}"${S.workStatus === v ? " selected" : ""}>${t}</option>`).join("")}</select>
    </div>
    ${S.workView === "board" ? `<div class="board">${[["To do", ["backlog", "todo"]], ["In progress", ["in_progress"]], ["Blocked", ["blocked"]], ["In review", ["in_review"]], ["Done", ["done"]]].map(([t, sts]) => { const col = issues.filter((i) => sts.includes(i.status)); return `<div class="col" data-status="${sts[sts.length - 1]}"><h3>${t} · ${col.length}</h3>${col.map((i) => `<a class="tcard" data-id="${esc(i.id)}" href="#work/${esc(i.id)}"><span class="mono xs muted">${esc(i.ref || "")}</span>${i.running ? ' <span class="pulse" aria-hidden="true"></span>' : ""}<br><b style="font-weight:600">${esc(i.title)}</b><br><span class="xs muted">${esc(i.agent || "Unassigned")}${i.project ? " · " + esc(i.project) : ""}</span></a>`).join("") || '<span class="xs muted">None</span>'}</div>`; }).join("")}</div>`
      : [...groups].map(([name, items]) => `<section class="panel"><div class="panel-h"><div><h2>${esc(name)}</h2></div></div><div class="list">${items.map(issueRow).join("")}</div></section>`).join("")}
    ${S.workView === "board" && window.Sortable ? `<p class="xs muted">Drag a card to another column to change its status in Paperclip.</p>` : ""}
    ${w && w.ok && !issues.length ? `<div class="empty"><h3>No tasks here</h3><p class="small">Change the filters, or create a task.</p></div>` : ""}
  </div>`;
}

const RUN_BADGE = { queued: "", running: "info", succeeded: "ok", completed: "ok", failed: "err", cancelled: "", timed_out: "warn", cancelling: "warn" };
const runBadge = (r) => badge(RUN_BADGE[r.status], r.status_text || r.status || "Unknown");
const runCost = (u) => (u && u.cost_usd != null ? usd(u.cost_usd) : "not reported");
const runTokens = (u) => (u && (u.input_tokens != null || u.output_tokens != null) ? `${fmtN(u.input_tokens)} in · ${fmtN(u.output_tokens)} out` : "—");
const backBtn = (href, label) => `<a class="btn ghost sm tab-back" href="${href}" style="margin-bottom:12px">${ic("back")}${esc(label)}</a>`;
const boardBtn = (label = "Open in Paperclip") => (S.meta.board_url ? `<a class="btn ghost" href="${esc(S.meta.board_url)}" target="_blank" rel="noopener">${label} ${ic("ext", 15)}</a>` : "");
const runRow = (r, sub) => `<a class="li" href="#work/run/${esc(r.id)}"><span class="main"><span class="t mono">${esc(String(r.id).slice(0, 8))}</span><span class="s">${esc(sub || [r.agent, when(r.started || r.created)].filter(Boolean).join(" · "))}</span></span><span class="end">${runBadge(r)}</span></a>`;

// One record from the board: "not found" and "not connected" read differently.
async function boardItem(path, back, label) {
  let d;
  try { d = await api(path); } catch (e) { d = { item: { ok: false, error: e.message } }; }
  if (d && "demo" in d) S.meta = { ...S.meta, demo: d.demo, board_url: d.board_url };
  const it = d.item || {};
  if (it.ok) return { data: it.data };
  const missing = /not found|answered 404/i.test(it.error || "");
  return { html: backBtn(back, label) + (missing ? `<div class="empty"><h3>Not found</h3><p class="small">It may have been removed from the board.</p></div>` : notConnected("The Paperclip board", it.error)) };
}

async function screenTask(id) {
  const { data: i, html } = await boardItem(`/api/work/${encodeURIComponent(id)}`, "#work", "Work");
  if (!i) return html;
  const live = i.runs.find((r) => ["running", "queued"].includes(r.status));
  const needs = i.status === "in_review";
  return `${backBtn("#work", "Work")}
  <div class="ph"><div class="ph-t"><div class="eyebrow"><span class="mono">${esc(i.ref || "")}</span>${i.project ? " · " + esc(i.project) : ""} · ${esc(i.agent || "Unassigned")} · priority ${esc(i.priority || "medium")}</div><h1>${esc(i.title)}</h1></div>
    <div class="ph-a"><button type="button" class="btn" data-act="taskComment" data-arg="${esc(i.id)}">Comment</button>${needs ? `<button type="button" class="btn" data-act="taskRevise" data-arg="${esc(i.id)}">Ask for changes</button><button type="button" class="btn primary" data-act="taskAccept" data-arg="${esc(i.id)}">Accept</button>` : ""}${live ? `<button type="button" class="btn danger" data-act="cancelRun" data-arg="${esc(live.id)}">${ic("stop")}Stop run</button>` : ""}${boardBtn()}</div></div>
  <div class="stack s24">
    <div class="row-flex">${badge(ISSUE_BADGE[i.status], i.status_text)}${needs ? badge("warn", "Needs you") : ""}${live ? badge("info", "Run running") : ""}</div>
    ${needs ? `<div class="notice info" role="status">${ic("info")}<div><b>Waiting for your review.</b> Accept it, or ask for changes and it goes back to the agent.</div></div>` : ""}
    <div class="cols"><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">Brief</h2>${i.description ? `<div class="md">${md(i.description)}</div>` : `<p class="small muted">No description.</p>`}</section>
      <section class="panel"><h2 style="margin-bottom:10px">Activity</h2>${i.comments.length ? `<ul class="timeline">${i.comments.slice().reverse().map((c) => `<li><span class="when">${esc(when(c.created))} · ${esc(c.by)}</span><div class="md small" style="margin-top:2px">${md(c.body)}</div></li>`).join("")}</ul>` : `<p class="small muted">No comments yet.</p>`}</section>
    </div><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">Runs</h2>${i.runs.length ? `<div class="list">${i.runs.map((r) => runRow(r, [r.agent, when(r.started || r.created), runCost(r.usage)].filter(Boolean).join(" · "))).join("")}</div>` : `<p class="small muted">No runs yet. The agent picks it up on its next heartbeat.</p>`}</section>
      <section class="panel"><h2 style="margin-bottom:10px">Details</h2><dl class="kv">
        <dt>Agent</dt><dd>${i.agent_id ? `<a href="#agents/${esc(i.agent_id)}">${esc(i.agent || "Agent")}</a>` : "Unassigned"}</dd>
        <dt>Project</dt><dd>${esc(i.project || "None")}</dd>
        <dt>Created</dt><dd>${esc(when(i.created) || "—")}</dd>
        <dt>Updated</dt><dd>${esc(when(i.updated) || "—")}</dd></dl></section>
    </div></div></div>`;
}

async function screenRun(id) {
  const { data: r, html } = await boardItem(`/api/runs/${encodeURIComponent(id)}`, "#work", "Work");
  if (!r) return html;
  const back = r.issue ? backBtn(`#work/${esc(r.issue.id)}`, r.issue.ref || "Task") : backBtn("#work", "Work");
  const active = ["running", "queued"].includes(r.status);
  return `${back}
  <div class="ph"><div class="ph-t"><div class="eyebrow">Run</div><h1 class="mono" style="font-size:26px">${esc(String(r.id).slice(0, 8))}</h1>
    <p class="sub">${esc(r.agent || "Agent")}${r.issue ? ` on <a href="#work/${esc(r.issue.id)}">${esc([r.issue.ref, r.issue.title].filter(Boolean).join(" · "))}</a>` : ""}</p></div>
    <div class="ph-a">${active ? `<button type="button" class="btn danger" data-act="cancelRun" data-arg="${esc(r.id)}">${ic("stop")}Stop run</button>` : ""}${boardBtn()}</div></div>
  <div class="stack s24">
    <div class="row-flex">${runBadge(r)}</div>
    ${r.status === "failed" ? `<div class="notice err" role="status">${ic("alert")}<div><b>Run failed.</b> ${esc(r.error || "No reason given.")}</div></div>` : ""}
    ${r.status === "cancelled" ? `<div class="notice info" role="status">${ic("info")}<div><b>Run stopped.</b> The task stays open; nothing is replayed.</div></div>` : ""}
    <div class="cols"><section class="panel"><h2 style="margin-bottom:12px">Timeline</h2>${r.events.length ? `<ul class="timeline">${r.events.slice().reverse().map((e) => `<li class="${e.level === "error" ? "bad" : ""}"><span class="when">${esc(when(e.at))}</span><br>${esc(e.message)}</li>`).join("")}</ul>` : `<p class="small muted">No events recorded.</p>`}</section>
      <section class="panel" style="align-self:start"><h2 style="margin-bottom:12px">Details</h2><dl class="kv">
        <dt>Agent</dt><dd>${r.agent_id ? `<a href="#agents/${esc(r.agent_id)}">${esc(r.agent || "Agent")}</a>` : "—"}</dd>
        <dt>Started by</dt><dd>${esc((r.source || "—").replace(/_/g, " "))}</dd>
        <dt>Started</dt><dd>${esc(when(r.started || r.created) || "—")}</dd>
        <dt>Ended</dt><dd>${esc(when(r.finished) || "—")}</dd>
        <dt>Tokens</dt><dd>${esc(runTokens(r.usage))}</dd>
        <dt>Reported cost</dt><dd>${r.usage && r.usage.cost_usd != null ? esc(usd(r.usage.cost_usd)) : `${badge("warn", "Not reported")}<br><span class="xs muted">Unpriced usage isn't counted as $0.00. Subscription runs (Codex, Claude) have no per-run price.</span>`}</dd></dl></section></div>
  </div>`;
}

async function screenAgent(id) {
  const { data: a, html } = await boardItem(`/api/agents/${encodeURIComponent(id)}`, "#agents", "Agents");
  if (!a) return html;
  const paused = a.status === "paused";
  const active = a.runs.find((r) => ["running", "queued"].includes(r.status));
  const pct = a.budget_cents ? Math.min(100, Math.round((100 * (a.spent_cents || 0)) / a.budget_cents)) : 0;
  return `${backBtn("#agents", "Agents")}
  <div class="ph"><div class="ph-t"><div class="row-flex" style="gap:12px"><span class="avatar lg" aria-hidden="true">${esc(initials(a.name))}</span><div><h1>${esc(a.name)}</h1><p class="sub">${esc(a.title || a.role || "")}</p></div></div></div>
    <div class="ph-a"><button type="button" class="btn" data-act="${paused ? "resumeAgent" : "pauseAgent"}" data-arg="${esc(a.id)}">${paused ? "Resume new assignments" : "Pause new assignments"}</button>${active ? `<a class="btn" href="#work/run/${esc(active.id)}">Inspect active run</a>` : ""}${boardBtn()}</div></div>
  <div class="stack s24">
    <div class="row-flex">${badge(AGENT_BADGE[a.status], a.status_text)}${a.last_heartbeat ? `<span class="small muted">Last heartbeat ${esc(when(a.last_heartbeat))}</span>` : ""}</div>
    ${paused ? `<div class="notice info" role="status">${ic("info")}<div><b>New assignments paused.</b> ${a.pause_reason ? esc(a.pause_reason) + ". " : ""}${active ? "The active run keeps going; use Stop run to stop it." : "No runs are active."}</div></div>` : ""}
    <div class="cols"><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">Responsibility</h2><dl class="kv">
        <dt>Role</dt><dd>${esc(a.title || a.role || "—")}</dd>
        <dt>Reports to</dt><dd>${esc(a.reports_to_name || "You")}</dd>
        <dt>Runtime</dt><dd>${esc(ADAPTERS[a.adapter] || a.adapter || "—")}</dd>
        ${a.capabilities && a.capabilities !== (a.title || a.role) ? `<dt>Does</dt><dd>${esc(a.capabilities)}</dd>` : ""}</dl></section>
      <section class="panel"><h2 style="margin-bottom:10px">Access</h2><div class="allow small">${(AGENT_ACCESS[a.name.toLowerCase()] || AGENT_ACCESS.other).map(([ok, t]) => `<div class="row-flex${ok ? "" : " muted"}">${ic(ok ? "check" : "lock", 16)}<span>${esc(t)}</span></div>`).join("")}</div>
        <p class="xs muted" style="margin-top:10px">Set on the server, not here. An agent can't grant itself or others access.</p></section>
      <section class="panel"><h2 style="margin-bottom:10px">Work</h2>${a.work.length ? `<div class="list">${a.work.map(issueRow).join("")}</div>` : `<p class="small muted">No assigned work.</p>`}</section>
    </div><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">Spending this month</h2>${a.budget_cents ? `<div class="kpi"><b style="font-size:30px">${money(a.spent_cents)}</b><span>of ${money(a.budget_cents)} budget</span></div><div class="bar" style="margin:10px 0"><i style="width:${pct}%"></i></div>` : `<div class="kpi"><b style="font-size:30px">${a.spent_cents ? money(a.spent_cents) : "—"}</b><span>${a.spent_cents ? "no budget set" : "runs on a subscription"}</span></div>`}<p class="xs muted" style="margin-top:8px">Reported by Paperclip. A budget is a board warning, not the provider's billing cap.</p></section>
      <section class="panel"><h2 style="margin-bottom:10px">Runs</h2>${a.runs.length ? `<div class="list">${a.runs.map((r) => runRow(r, [r.issue_ref, when(r.started || r.created)].filter(Boolean).join(" · "))).join("")}</div>` : `<p class="small muted">No runs.</p>`}</section>
      <section class="panel"><h2 style="margin-bottom:10px">Routines</h2>${a.routines.length ? `<div class="list">${a.routines.map((r) => `<a class="li" href="#routines/${esc(r.id)}"><span class="main"><span class="t">${esc(r.title)}</span><span class="s">${esc(r.schedule || "Manual")}</span></span>${routineBadge(r.status)}</a>`).join("")}</div>` : `<p class="small muted">None.</p>`}</section>
    </div></div></div>`;
}

async function screenAgents() {
  const d = await load("agents", "/api/agents");
  const a = d && d.agents;
  const list = (a && a.ok && a.data) || [];
  return `
  <div class="ph"><div class="ph-t"><h1>Agents</h1><p class="sub">Who does what, and what their work costs this month.</p></div></div>
  ${a && !a.ok ? notConnected("The Paperclip board", a.error) : ""}
  <div class="cols" style="grid-template-columns:repeat(auto-fit,minmax(230px,1fr))">${list.map((g) => `
    <a class="panel" href="#agents/${esc(g.id)}" style="display:flex;flex-direction:column;gap:10px;text-decoration:none;color:inherit">
      <div class="row-flex"><span class="avatar lg" aria-hidden="true">${esc(initials(g.name))}</span><div style="min-width:0;flex:1"><h2>${esc(g.name)}</h2><p class="small muted">${esc(g.title || "")}</p></div></div>
      ${badge(AGENT_BADGE[g.status], g.status_text)}
      <p class="small">${g.current ? `<span class="mono">${esc(g.current.ref || "")}</span> ${esc(g.current.title)}` : "No current work"}</p>
      <div class="small muted num">${g.budget_cents ? `${money(g.spent_cents)} of ${money(g.budget_cents)} this month` : g.spent_cents ? `${money(g.spent_cents)} this month · no budget set` : "Runs on a subscription · no spend recorded"}</div>
    </a>`).join("")}</div>`;
}

const routineBadge = (st) => badge(st === "active" ? "ok" : st === "paused" ? "warn" : "", st ? st[0].toUpperCase() + st.slice(1) : "Unknown");
const ADAPTERS = { hermes_local: "Hermes Agent (Max)", codex_local: "Codex CLI · sealed box", claude_local: "Claude Code · sealed box", process: "Script", http: "Web hook" };
// What each agent can reach, as set up on the server (see the build plan). Not editable here.
const AGENT_ACCESS = {
  max: [[1, "Public web search; browser on approved sites only"], [1, "Test mailbox, read-only"], [1, "salt notebooks: write, no delete"], [1, "Board: read task, comment, mark done"], [0, "Your real Gmail and calendar"], [0, "Health data"], [0, "Sending email without your fingerprint"]],
  codex: [[1, "Sealed box: GitHub, code libraries, its own model"], [1, "salt notebooks: write, no delete"], [0, "Max, Signal, approvals and backups"], [0, "Mail, calendar and health data"]],
  claude: [[1, "Sealed box: GitHub, code libraries, its own model"], [1, "salt notebooks: write, no delete"], [0, "Max, Signal, approvals and backups"], [0, "Mail, calendar and health data"]],
  other: [[0, "Private data: mail, calendar, health"]],
};

async function screenRoutine(id) {
  const { data: r, html } = await boardItem(`/api/routines/${encodeURIComponent(id)}`, "#routines", "Routines");
  if (!r) return html;
  return `${backBtn("#routines", "Routines")}
  <div class="ph"><div class="ph-t"><div class="eyebrow">Routine</div><h1>${esc(r.title || "Routine")}</h1><p class="sub">${r.agent_id ? `Assigned to <a href="#agents/${esc(r.agent_id)}">${esc(r.agent || "an agent")}</a>` : "No assignee"}</p></div>
    <div class="ph-a">${boardBtn("Edit in Paperclip")}</div></div>
  <div class="stack s24">
    <div class="row-flex">${routineBadge(r.status)}</div>
    <div class="cols"><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">What it does</h2>${r.description ? `<div class="md">${md(r.description)}</div>` : `<p class="small muted">No description.</p>`}</section>
      <section class="panel"><h2 style="margin-bottom:10px">History</h2>${r.runs.length ? `<div class="list">${r.runs.map((x) => `<${x.issue_id ? `a href="#work/${esc(x.issue_id)}"` : "div"} class="li"><span class="main"><span class="t">${esc(when(x.triggered) || "—")}</span><span class="s">${esc([x.source, x.issue_ref && `${x.issue_ref} ${x.issue_title || ""}`, x.failure].filter(Boolean).join(" · "))}</span></span>${badge(/fail/.test(x.status) ? "err" : /complet|succe/.test(x.status) ? "ok" : "", ((x.status || "unknown")[0].toUpperCase() + (x.status || "unknown").slice(1)).replace(/_/g, " "))}</${x.issue_id ? "a" : "div"}>`).join("")}</div>` : `<p class="small muted">Never run.</p>`}</section>
    </div><div class="stack s24">
      <section class="panel"><h2 style="margin-bottom:10px">Schedule</h2>${r.triggers.length ? r.triggers.map((t) => `<dl class="kv"><dt>When</dt><dd>${esc(t.label || t.kind || "—")}${t.enabled ? "" : " · off"}</dd>${t.timezone ? `<dt>Time zone</dt><dd>${esc(t.timezone)}</dd>` : ""}<dt>Next run</dt><dd>${esc(when(t.next_run) || "—")}</dd><dt>Last run</dt><dd>${esc(t.last_run ? `${when(t.last_run)}${t.last_result ? " · " + t.last_result : ""}` : "Never")}</dd></dl>`).join('<hr class="sep">') : `<p class="small muted">Runs only when started by hand.</p>`}</section>
      <p class="xs muted">Server jobs (backups, cost check, Monday digest) run outside Paperclip; see System.</p>
    </div></div></div>`;
}

async function screenRoutines() {
  const d = await load("routines", "/api/routines");
  const r = d && d.routines;
  const list = (r && r.ok && r.data) || [];
  return `
  <div class="ph"><div class="ph-t"><h1>Routines</h1><p class="sub">Recurring agent work scheduled in Paperclip.</p></div></div>
  ${r && !r.ok ? notConnected("The Paperclip board", r.error) : ""}
  ${list.length ? `<div class="tbl-wrap"><table class="tbl tbl-cards"><thead><tr><th>Routine</th><th>Assignee</th><th>Schedule</th><th>Status</th><th>Next run</th><th>Last result</th></tr></thead><tbody>${list.map((x) => `
    <tr><td data-l="Routine"><a href="#routines/${esc(x.id)}"><b>${esc(x.title)}</b></a></td><td data-l="Assignee">${esc(x.agent || "None")}</td>
    <td data-l="Schedule">${esc(x.schedule || "Manual")}${x.timezone ? `<br><span class="xs muted">${esc(x.timezone)}</span>` : ""}</td>
    <td data-l="Status">${routineBadge(x.status)}</td>
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

const SYS_TABS = [["status", "Status"], ["connections", "Connections"], ["access", "Access"], ["spending", "Spending"], ["controls", "Controls"], ["credits", "Credits"]];
const CONN_STATE = { on: ["ok", "Connected"], demo: ["ok", "Sample data"], key_missing: ["warn", "Set up · file missing"], off: ["", "Not connected"] };
const CREDITS = [
  ["Hermes Agent", "https://github.com/NousResearch/hermes-agent", "Max, on Signal and in this app", ""],
  ["Paperclip", "https://github.com/paperclipai/paperclip", "The board: agents, tasks, routines, approvals", "MIT"],
  ["salt.md", "https://github.com/saltmd/salt.md", "Notes workspace shared with agents (separate service)", "AGPL-3.0"],
  ["changedetection.io", "https://github.com/dgtlmoon/changedetection.io", "Release watcher", "Apache-2.0"],
  ["ZXing for the browser", "https://github.com/zxing-js/browser", "Barcode reading in the camera view (bundled)", "MIT"],
  ["Geist and Geist Mono", "https://github.com/vercel/geist-font", "Typefaces (bundled)", "SIL OFL 1.1"],
  ["DOMPurify", "https://github.com/cure53/DOMPurify", "Second safety filter on text agents write (bundled)", "Apache-2.0 or MPL-2.0"],
  ["Fuse.js", "https://github.com/krisk/Fuse", "Typo-tolerant search (bundled)", "Apache-2.0"],
  ["SortableJS", "https://github.com/SortableJS/Sortable", "Drag and drop on the Work board (bundled)", "MIT"],
  ["uPlot", "https://github.com/leeoniya/uPlot", "Weight and expenditure charts (bundled)", "MIT"],
  ["Open Food Facts", "https://world.openfoodfacts.org", "Product data for barcode lookup", "ODbL"],
];

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
  } else if (tab === "connections") {
    const d = await load("connections", "/api/connections");
    const list = (d && d.connections) || [];
    body = list.length ? `<section class="panel"><div class="list">${list.map((c) => `<div class="li"><span class="main"><span class="t">${esc(c.name)}</span><span class="s">${esc(c.scope)}</span></span><span class="end">${badge(...(CONN_STATE[c.state] || ["", c.state]))}</span></div>`).join("")}</div></section>
      <p class="small muted" style="margin-top:12px">Connections are set up on the server, not from here, and keys never pass through the app. "File missing" means the setting is there but its key or feed file isn't.</p>`
      : `<div class="empty"><h3>Can't reach the server</h3></div>`;
  } else if (tab === "access") {
    const row = (ok, t) => `<div class="row-flex small${ok ? "" : " muted"}">${ic(ok ? "check" : "lock", 16)}<span>${esc(t)}</span></div>`;
    body = `<div class="cols even"><section class="panel"><h2 style="margin-bottom:10px">Your access</h2><div class="allow">${["Health records (read-only here)", "Gmail and Google Calendar (read-only)", "Max's files, memory and skills (read-only)", "Board: create tasks, decide, pause, stop runs", "Block all agent work"].map((t) => row(1, t)).join("")}${row(0, "Approving emails: only on the broker's page, with your fingerprint")}</div>
        <p class="xs muted" style="margin-top:10px">Only your Tailscale login can open this app.</p></section>
      <section class="panel"><h2 style="margin-bottom:10px">Agent access</h2>${["max", "codex", "claude"].map((k) => `<h3 class="small" style="margin:10px 0 6px;font-weight:600">${k[0].toUpperCase() + k.slice(1)}</h3><div class="allow">${AGENT_ACCESS[k].map(([ok, t]) => row(ok, t)).join("")}</div>`).join("")}
        <p class="xs muted" style="margin-top:12px">Set on the server. An agent can't grant itself or others access, and a remembered preference isn't a permission. Giving an agent your private data needs your own yes first.</p></section></div>`;
  } else if (tab === "credits") {
    body = `<section class="panel"><h2 style="margin-bottom:10px">Software and data</h2><div class="tbl-wrap"><table class="tbl tbl-cards"><thead><tr><th>Component</th><th>Used for</th><th>Licence</th></tr></thead><tbody>${CREDITS.map(([n, u, w, l]) => `<tr><td data-l="Component"><a class="link" href="${esc(u)}" target="_blank" rel="noopener">${esc(n)}</a></td><td data-l="Used for">${esc(w)}</td><td data-l="Licence">${esc(l || "See project")}</td></tr>`).join("")}</tbody></table></div></section>`;
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
  return `<div class="ph"><div class="ph-t"><h1>System</h1><p class="sub">Service state, connections, access, spending and controls.</p></div></div>
    <nav class="tabs" aria-label="Sections">${SYS_TABS.map(([k, t]) => `<a href="#system/${k}"${k === tab ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>${body}`;
}

function screenMore() {
  const items = [["max", "max", "Max", "Chat with Max"], ["agents", "agents", "Agents", "Who does what and what it costs"], ["routines", "routines", "Routines", "Recurring agent work"], ["system/status", "system", "System", "Services, spending and the stop button"], ["health/food", "health", "Health", "Food, training, meals and progress"], ["inbox", "inbox", "Inbox", "Your mail, read-only"], ["planner", "planner", "Planner", "Your calendar, read-only"], ["library", "library", "Library", "Files, notes, memory and skills"]];
  return `<div class="ph"><div class="ph-t"><h1>More</h1></div></div><section class="panel"><div class="list">${items.map(([h, i, t, s]) => `<a class="li" href="#${h}"><span class="main"><span class="t">${ic(i, 16)} ${t}</span><span class="s">${s}</span></span><span class="end">${ic("chev", 16)}</span></a>`).join("")}</div></section>`;
}

function screenSoon(what) {
  const info = {
    max: ["Max", 3, "Chat with Max here as well as on Signal, with the artifacts panel from the mockup."],
    inbox: ["Inbox", 5, "Your mail, shown to you only. It never goes to Max or any model."],
    planner: ["Planner", 5, "Your calendar, once it's connected."],
  }[what] || ["This screen", "", "Coming in a later phase."];
  return `<div class="ph"><div class="ph-t"><h1>${esc(info[0])}</h1></div></div><div class="empty" style="max-width:520px"><h3>Coming in Phase ${info[1]}</h3><p class="small">${esc(info[2])}</p></div>`;
}

/* ---------- Library ---------- */

const LIB_TABS = [["files", "Files & research"], ["notes", "Notes · salt.md"], ["memory", "Memory"], ["skills", "Skills"]];

// Start a new chat with Max with the text ready to send (nothing is sent until Craig presses Send).
function askMax(text) {
  S.chatDraft = text;
  S.art = null;
  location.hash = "max/new";
  setTimeout(() => { const b = $("#max-in"); if (b) { b.focus(); b.setSelectionRange(b.value.length, b.value.length); } }, 120);
}

async function screenLibrary(rest) {
  let tab = rest[0] || "files";
  if (!LIB_TABS.some(([k]) => k === tab)) tab = "files";
  const head = (sub, actions = "") => `<div class="ph"><div class="ph-t"><h1>Library</h1><p class="sub">${sub}</p></div>${actions ? `<div class="ph-a">${actions}</div>` : ""}</div>
    <nav class="tabs" aria-label="Sections">${LIB_TABS.map(([k, t]) => `<a href="#library/${k}"${k === tab ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>`;
  const sub = "What Max has made, your shared notes, what Max remembers and the skills he uses.";

  if (tab === "files" && rest[1]) {
    const name = decodeURIComponent(rest.slice(1).join("/"));
    let a;
    try { a = await api(`/api/artifacts/${encodeURIComponent(name)}`); } catch (e) {
      return head(sub) + `<div class="btns" style="margin-bottom:12px"><a class="btn ghost sm" href="#library/files">${ic("back", 14)}Back</a></div><div class="empty"><h3>File not found</h3><p class="small">${esc(e.message)}</p></div>`;
    }
    if (!S.libArt || S.libArt.name !== a.name) S.libTab = "preview";
    S.libArt = a;
    const code = S.libTab === "code";
    return `<div class="btns" style="margin-bottom:12px"><a class="btn ghost sm" href="#library/files">${ic("back", 14)}Library</a></div><div class="ph"><div class="ph-t"><h1>${esc(a.title)}</h1><p class="sub">${esc(a.label)} · made by Max · updated ${esc(when(a.updated))}</p></div>
      <div class="ph-a"><button type="button" class="btn ghost" data-act="artCopy">${ic("copy", 15)}Copy</button><button type="button" class="btn" data-act="artDownload">${ic("download", 15)}Download</button><button type="button" class="btn primary" data-act="artAsk">Ask Max for changes</button></div></div>
      <div class="stack"><div class="row-flex" style="gap:8px;flex-wrap:wrap">${artTabs(a, code)}<span class="xs muted mono">${esc(a.name)}</span></div>
      <section class="panel lib-file">${artBody(a, code)}</section>
      <p class="xs muted">Pages preview in a sandbox with scripts and network turned off, so a page Max made can't act on the app.</p></div>`;
  }

  if (tab === "files") {
    let list, err;
    try { list = (await api("/api/artifacts")).artifacts; } catch (e) { err = e.message; }
    const body = list == null ? notConnected("Max's files", err)
      : list.length ? `<section class="panel"><div class="list">${list.map((a) => `<a class="li" href="#library/files/${encodeURIComponent(a.name)}">${ic("file", 18)}<span class="main"><span class="t">${esc(a.title)}</span><span class="s">${esc(a.label)} · Max · ${esc(when(a.updated))} · ${esc(fmtSize(a.size))}</span></span><span class="end">${ic("chev", 16)}</span></a>`).join("")}</div></section>`
      : `<div class="empty"><h3>Nothing here yet</h3><p class="small">Ask Max for a page, document or table and it appears here as well as in the chat.</p><a class="btn" href="#max/new">Open Max</a></div>`;
    return head(sub, `<a class="btn" href="#max/new">${ic("plus", 15)}Ask Max for a page</a>`) + body;
  }

  const lib = await api("/api/library").catch((e) => ({ ok: false, error: e.message }));
  if (tab === "notes") {
    const url = lib.notes_url;
    return head(sub, url ? `<a class="btn primary" href="${esc(url)}" target="_blank" rel="noopener">Open salt.md ${ic("ext", 15)}</a>` : "") + `<div class="cols even">
      <section class="panel"><h2 style="margin-bottom:8px">Notes in salt.md</h2><p class="small">Your notes workspace runs on your own server. Pages and databases live there, and you sign in with your own account and two-factor code.</p>
        ${url ? `<p class="xs muted mono" style="margin-top:10px">${esc(url)}</p>` : `<p class="small muted" style="margin-top:8px">The salt address isn't in the app's settings yet.</p>`}</section>
      <section class="panel"><h2 style="margin-bottom:8px">What agents can do there</h2><div class="allow small">
        <div class="row-flex">${ic("check", 16)}<span>Max, Codex and Claude read and write the six shared notebooks</span></div>
        <div class="row-flex">${ic("check", 16)}<span>Handoffs between agents go in the Team notebook</span></div>
        <div class="row-flex muted">${ic("lock", 16)}<span>Agents can't delete or trash pages, comments or views</span></div>
        <div class="row-flex muted">${ic("lock", 16)}<span>Your personal workspace isn't shared with them</span></div></div>
        <p class="xs muted" style="margin-top:10px">Agents can still change text. The server backs salt up every night.</p></section></div>`;
  }

  if (!lib.ok) return head(sub) + notConnected(tab === "memory" ? "Max's memory" : "Max's skills", lib.error);
  const age = lib.stale ? `<div class="notice warn" role="status">${ic("alert")}<div><b>Last copied ${lib.age_min} minutes ago.</b> The feed is behind, so this may be out of date.</div></div>` : "";

  if (tab === "memory") {
    S.libMemory = lib.memory;
    const stores = [...new Set(lib.memory.map((m) => m.store))];
    const body = lib.memory.length ? stores.map((st) => `<section class="panel"><div class="panel-h"><h2>${esc(st)}</h2><span class="badge">${lib.memory.filter((m) => m.store === st).length}</span></div><div class="list">${lib.memory.map((m, i) => m.store !== st ? "" : `<div class="li"><span class="main"><span class="t" style="white-space:pre-wrap;font-weight:450">${esc(m.text)}</span></span><span class="end"><button type="button" class="btn ghost sm" data-act="memAsk" data-arg="fix:${i}">Correct</button><button type="button" class="btn ghost sm" data-act="memAsk" data-arg="forget:${i}">Forget</button></span></div>`).join("")}</div></section>`).join("")
      : `<div class="empty"><h3>Nothing remembered</h3><p class="small">Things you ask Max to remember appear here.</p></div>`;
    return head(sub) + `<div class="stack s24">${age}${body}
      <p class="small muted" style="max-width:72ch">A remembered preference isn't a permission: Max's access is set by his sandbox and the approval broker, not by memory. Correct and Forget open a chat with Max with the request ready; nothing changes until you send it. Copied from Max every 5 minutes.</p></div>`;
  }

  // skills
  const cats = [...new Set(lib.skills.map((x) => x.category || "General"))];
  const card = (x) => `<section class="panel skill" data-q="${esc((x.name + " " + x.description + " " + x.category).toLowerCase())}"><div class="row-flex" style="margin-bottom:6px"><h2 style="flex:1;min-width:0;overflow-wrap:anywhere">${esc(x.name)}</h2>${x.version ? badge("line", "v" + x.version) : ""}</div>
    <p class="small">${esc(x.description || "No description.")}</p>
    <dl class="kv small" style="margin-top:10px"><dt>Folder</dt><dd class="mono">${esc(x.id)}</dd><dt>Updated</dt><dd>${esc(when(x.updated))}</dd></dl>
    <div class="btns" style="margin-top:12px"><button type="button" class="btn sm" data-act="skillSrc" data-arg="${esc(x.id)}">Inspect source</button></div></section>`;
  const body = lib.skills.length ? `<div class="row-flex" style="margin-bottom:14px"><label class="sr" for="skill-q">Filter skills</label><input class="inp" id="skill-q" type="search" placeholder="Filter ${lib.skills.length} skills" style="max-width:340px"></div>
    ${cats.map((c) => `<div class="skill-group"><div class="eyebrow" style="margin:6px 0 10px">${esc(c[0].toUpperCase() + c.slice(1).replace(/-/g, " "))}</div><div class="cols three">${lib.skills.filter((x) => (x.category || "General") === c).map(card).join("")}</div></div>`).join("")}`
    : `<div class="empty"><h3>No skills found</h3><p class="small">Skills Max has installed appear here.</p></div>`;
  return head(sub) + `<div class="stack s24">${age}<div>${body}</div><p class="small muted" style="max-width:72ch">Read-only. Viewing a skill doesn't turn it on or off. Instructions inside a skill are Max's tools, not your permission for anything.</p></div>`;
}

async function openSkillSource(id) {
  try {
    const x = await api(`/api/library/skill/${encodeURIComponent(id)}`);
    modal(`${x.name} · source`, `<p class="xs muted mono" style="margin-bottom:8px">skills/${esc(x.id)}/SKILL.md</p><pre class="art-code" style="max-height:60vh">${esc(x.source)}</pre>`,
      `<button type="button" class="btn primary" data-act="close">Close</button>`);
  } catch (e) { toast(e.message); }
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
    if (!b.trim()) continue;
    const lines = b.replace(/^\n+|\n+$/g, "").split("\n");
    if (lines.every((l) => /^\s*[-*•]\s+/.test(l))) out.push(`<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*•]\s+/, ""))}</li>`).join("")}</ul>`);
    else if (lines.every((l) => /^\s*\d+[.)]\s+/.test(l))) out.push(`<ol>${lines.map((l) => `<li>${inline(l.replace(/^\s*\d+[.)]\s+/, ""))}</li>`).join("")}</ol>`);
    else if (/^#{1,4}\s/.test(b) && lines.length === 1) out.push(b.startsWith("##") ? `<h3>${inline(b.replace(/^#{1,4}\s+/, ""))}</h3>` : `<h2>${inline(b.replace(/^#\s+/, ""))}</h2>`);
    else out.push(`<p>${lines.map(inline).join("<br>")}</p>`);
  }
  if (code.length) out.push(`<pre><code>${esc(code.join("\n\n").replace(/^```[^\n]*\n?/, ""))}</code></pre>`);
  return clean(out.join(""));
}

// Second safety layer for text agents write: everything is escaped above, and
// DOMPurify then drops anything that still isn't plain formatting.
function clean(html) {
  const P = window.DOMPurify;
  if (!P) return html;
  if (!P.hermesHooked) {
    P.addHook("afterSanitizeAttributes", (n) => { if (n.tagName === "A") { n.setAttribute("target", "_blank"); n.setAttribute("rel", "noopener noreferrer"); } });
    P.hermesHooked = true;
  }
  return P.sanitize(html, { ALLOWED_URI_REGEXP: /^https?:/i });
}

function chatTools(tools) {
  if (!tools || !tools.length) return "";
  return `<details class="mc-activity"><summary>${ic("check", 14)}<span>${tools.length} tool call${tools.length === 1 ? "" : "s"}</span>${ic("chev", 14)}</summary><div class="mc-act-body">${tools.map((t) => {
    let args = t.args || "";
    try { args = JSON.stringify(JSON.parse(args), null, 2); } catch (_) { /* leave as text */ }
    return `<details class="mc-act-tool"><summary><span class="mc-dot tool"></span><span><b>Max</b> <span class="mono">${esc(t.name)}</span>${t.result != null ? ` <span class="muted">→ ${esc(String(t.result).slice(0, 80))}</span>` : ` <span class="muted">· running</span>`}</span></summary>${args ? `<pre class="mc-args">${esc(args)}</pre>` : ""}</details>`;
  }).join("")}</div></details>`;
}

function chatArts(names) {
  if (!names || !names.length) return "";
  const meta = Object.fromEntries((S.artList || []).map((a) => [a.name, a]));
  return `<div class="stack s8" style="margin-top:4px">${names.map((n) => {
    const a = meta[n] || { title: n, label: "File" };
    return `<button type="button" class="mc-art${S.art && S.art.name === n ? " on" : ""}" data-act="artOpen" data-arg="${esc(n)}"><span class="mc-art-ic">${ic("file", 18)}</span><span class="main"><b>${esc(a.title)}</b><span class="xs muted">${esc(a.label)} · ${esc(n)}</span></span>${ic("chev", 16)}</button>`;
  }).join("")}</div>`;
}

function csvTable(text) {
  const rows = text.trim().split(/\r?\n/).slice(0, 500).map((l) => l.split(","));
  if (!rows.length) return "";
  return `<table class="tbl"><thead><tr>${rows[0].map((c) => `<th>${esc(c)}</th>`).join("")}</tr></thead><tbody>${rows.slice(1).map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function artPanel() {
  const st = S.art;
  if (!st) return "";
  const head = (kicker, title) => `<div class="art-h"><div style="min-width:0"><div class="xs muted">${esc(kicker)}</div><b class="art-t">${esc(title)}</b></div><span class="spacer"></span><button type="button" class="btn ghost sm art-back" data-act="artClose">${ic("back", 15)}Chat</button><button type="button" class="iconbtn art-x" data-act="artClose" aria-label="Close">${ic("close")}</button></div>`;
  if (st.name === "*") {
    const list = S.artList;
    return `<aside class="art-panel" aria-label="Max's files">${head("Made by Max", "Files")}
      ${list == null ? notConnected("Max's files", S.artErr) : list.length ? `<div class="list">${list.map((a) => `<a class="li" href="#" data-act="artOpen" data-arg="${esc(a.name)}"><span class="main"><span class="t">${esc(a.title)}</span><span class="s">${esc(a.label)} · ${esc(when(a.updated))}</span></span><span class="end">${ic("chev", 16)}</span></a>`).join("")}</div>` : `<p class="small muted">Nothing yet. Ask Max for a page, document or table and it appears here.</p>`}</aside>`;
  }
  const a = st.data;
  if (!a) return `<aside class="art-panel" aria-label="File">${head("Loading", st.name)}<p class="small muted">${esc(st.error || "Opening…")}</p></aside>`;
  const code = st.tab === "code";
  return `<aside class="art-panel" aria-label="File">${head(`${a.label} · Artifact`, a.title)}
    <div class="row-flex" style="gap:8px;flex-wrap:wrap">${artTabs(a, code)}<span class="xs muted">${esc(a.name)} · ${esc(when(a.updated))}</span></div>
    <div class="art-body">${artBody(a, code)}</div>
    <div class="btns"><button type="button" class="btn ghost sm" data-act="artCopy">Copy</button><button type="button" class="btn sm" data-act="artDownload">${ic("download", 15)}Download</button><button type="button" class="btn primary sm" data-act="artAsk">Ask for changes</button></div>
    <p class="xs muted">Pages preview in a sandbox with scripts and network turned off.</p></aside>`;
}

// A file Max made, shown safely: pages in a sandbox with scripts and network off.
function artBody(a, code) {
  if (code || a.kind === "json" || a.kind === "text") return `<pre class="art-code">${esc(a.text)}</pre>`;
  if (a.kind === "html") return `<iframe class="art-frame" sandbox="" referrerpolicy="no-referrer" title="${esc(a.title)} preview" srcdoc="${esc(a.text)}"></iframe>`;
  if (a.kind === "md") return `<div class="md art-doc">${md(a.text)}</div>`;
  if (a.kind === "svg") return `<div class="art-svg"><img alt="${esc(a.title)}" src="data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(a.text)))}"></div>`;
  if (a.kind === "csv") return `<div class="art-doc" style="overflow:auto">${csvTable(a.text)}</div>`;
  return "";
}
const artTabs = (a, code) => ["html", "md", "svg", "csv"].includes(a.kind) ? `<div class="seg" role="group"><button type="button" aria-pressed="${!code}" data-act="artTab" data-arg="preview">Preview</button><button type="button" aria-pressed="${code}" data-act="artTab" data-arg="code">Code</button></div>` : "";
// The file on screen: the Library's file page, or the chat's side panel.
const curArt = () => ((location.hash || "").startsWith("#library/files/") ? S.libArt : S.art && S.art.data);

function chatMsg(m, i, live) {
  const me = m.role === "me";
  const name = me ? ((S.me && S.me.name) || "You").split(" ")[0] : "Max";
  const t = m.at ? when(m.at, false) : "";
  const status = m.status === "stopped" ? `<p class="xs muted">Stopped.</p>` : m.status === "error" ? `<p class="small" style="color:var(--err)">${esc(m.error || "Max hit an error.")}</p>` : "";
  if (me) return `<div class="mc-msg me"><span class="mc-av me">${esc(initials(name))}</span><div class="mc-body"><div class="mc-meta"><b>${esc(name)}</b><span>${esc(t)}</span></div><div class="bubble">${esc(m.text)}</div><div class="mc-acts" role="toolbar" aria-label="Message actions"><button type="button" class="iconbtn sm" data-act="chatCopy" data-arg="${i}" title="Copy" aria-label="Copy">${ic("copy", 15)}</button></div></div></div>`;
  return `<div class="mc-msg"${live ? ' id="live"' : ""}><span class="mc-av max">${logoMark(30)}</span><div class="mc-body"><div class="mc-meta"><b>Max</b><span class="tag">Hermes</span><span>${esc(t)}</span></div>${chatTools(m.tools)}${
    live && !m.text ? `<div class="mc-think">${ic("spark", 15)}Working…</div>` : `<div class="md mc-md">${live ? md(m.text).replace(/(<\/(?:p|li|h3)>(?:<\/[uo]l>)?)$/, '<span class="mc-caret"></span>$1') : md(m.text)}</div>`}${chatArts(m.artifacts)}${status}${
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
  try { S.artList = (await api("/api/artifacts")).artifacts; S.artErr = null; } catch (e) { S.artList = null; S.artErr = e.message; }
  if (S.art && S.art.name !== "*" && (!S.art.data || S.art.stale)) {
    try { S.art.data = await api(`/api/artifacts/${encodeURIComponent(S.art.name)}`); S.art.stale = false; } catch (e) { S.art.error = e.message; }
  }
  const busy = S.chatLive && S.chatLive.cid === cid;
  const msgs = conv ? conv.messages.map((m, i) => chatMsg(m, i)).join("") + (busy ? chatMsg(S.chatLive.msg, -1, true) : conv.busy ? chatMsg({ role: "max", text: "", tools: [] }, -1, true) : "") : "";
  const convs = list.chats.map((c) => `<a class="mc-conv${c.id === cid ? " sel" : ""}" href="#max/${esc(c.id)}"><span class="t">${esc(c.title || "New chat")}</span><span class="xs muted">${esc(when(c.updated))}${list.busy.includes(c.id) ? " · answering" : ""}</span></a>`).join("") || `<p class="small muted" style="padding:8px 12px">No chats yet.</p>`;
  const running = busy || (conv && conv.busy);
  return `<div class="mc-app${cid || fresh ? " has-conv" : ""}${S.art ? " art-open" : ""}"><aside class="mc-side"><div class="mc-side-h"><span class="mc-av max">${logoMark(30)}</span><span style="flex:1;min-width:0"><b>Max</b><span class="xs muted" style="display:block">Same Max as Signal · same approvals</span></span><button type="button" class="btn ghost" data-act="newChat" aria-label="New chat" title="New chat">${ic("plus")}</button></div>
    <nav class="mc-convs" aria-label="Chats">${convs}</nav></aside>
    <section class="mc-main" aria-label="Chat">
      <header class="mc-bar"><a class="iconbtn mc-back" href="#max" aria-label="All chats">${ic("back")}</a><b class="mc-title">${esc((conv && conv.title) || "New chat")}</b><span class="spacer"></span><button type="button" class="btn ghost sm" data-act="artOpen" data-arg="*" title="Files Max has made">${ic("file", 15)}<span class="lbl">Files</span></button>${conv ? `<button type="button" class="btn ghost sm" data-act="chatDelete" data-arg="${esc(conv.id)}" title="Delete this chat"${running ? " disabled" : ""}>${ic("trash", 15)}<span class="lbl">Delete</span></button>` : ""}</header>
      <div class="mc-scroll" id="mc-scroll"><div class="mc-col" id="transcript" aria-live="polite">${conv && conv.messages.length ? msgs : `<div class="mc-empty">${logoMark(40)}<h2>How can Max help?</h2><p class="small muted">This is the same Max as on Signal, with the same sandbox and approvals. Emails still need your fingerprint.</p></div>`}</div></div>
      <div class="mc-dock"><div class="mc-col"><div class="composer mc-composer lh">
        <label class="sr" for="max-in">Message Max</label>
        <textarea id="max-in" rows="2" placeholder="Message Max" maxlength="8000">${esc(S.chatDraft || "")}</textarea>
        <div class="mc-actbar"><div class="mc-actl"><span class="xs muted desk-only">Enter to send · Shift+Enter for a new line</span></div>
          <div class="mc-actr">${running
            ? `<button type="button" class="btn sm" data-act="chatStop" data-arg="${esc(cid || "")}">${ic("stop", 15)}Stop</button>`
            : `<span class="mca-send"><button type="button" class="btn primary sm" data-act="chatSend" aria-label="Send" title="Send">${ic("send", 16)}</button></span>`}</div></div>
      </div><p class="mc-hint xs muted">Chats are kept on your server. Max can't send email without your fingerprint.</p></div></div>
    </section>${artPanel()}</div>`;
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
        else if (ev.type === "artifacts") { live.artifacts = ev.names; if (S.art && ev.names.includes(S.art.name)) S.art.stale = true; try { S.artList = (await api("/api/artifacts")).artifacts; } catch (_) { /* list stays */ } }
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

/* ---------- Progress charts (uPlot) ---------- */

function drawProgressCharts() {
  const h = S.progHistory;
  if (!window.uPlot || !h || !h.length) return;
  const css = getComputedStyle(document.documentElement);
  const col = (v, f) => (css.getPropertyValue(v).trim() || f);
  const fg3 = col("--fg-3", "#888"), line = col("--line", "#ddd"), accent = col("--accent", "#1a7f5a"), kcal = col("--m-c", "#d95926"), blue = col("--m-p", "#3987e5");
  const xs = h.map((r) => Date.parse(r.date + "T12:00:00Z") / 1000);
  const axes = (unit) => [
    { stroke: fg3, grid: { stroke: line, width: 1 }, ticks: { stroke: line }, font: "11px Geist, sans-serif", values: (u, v) => v.map((t) => new Date(t * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: TZ })) },
    { stroke: fg3, grid: { stroke: line, width: 1 }, ticks: { show: false }, font: "11px Geist, sans-serif", size: 52, values: (u, v) => v.map((x) => x == null ? "" : fmtN(x) + unit) },
  ];
  const make = (id, series, data, unit) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = "";
    const opts = { width: el.clientWidth || 600, height: 220, legend: { show: true, live: true }, cursor: { y: false, points: { size: 6 } },
      scales: { x: { time: true } }, axes: axes(unit), series: [{ value: (u, v) => v == null ? "" : new Date(v * 1000).toLocaleDateString("en-GB", { day: "numeric", month: "short" }) }, ...series] };
    const plot = new uPlot(opts, data, el);
    new ResizeObserver(() => plot.setSize({ width: el.clientWidth, height: 220 })).observe(el);
  };
  make("ch-weight", [
    { label: "Weigh-in", stroke: blue, width: 0, points: { show: true, size: 5, fill: blue }, value: (u, v) => v == null ? "—" : v.toFixed(1) + " kg" },
    { label: "Trend", stroke: accent, width: 2, spanGaps: true, points: { show: false }, value: (u, v) => v == null ? "—" : v.toFixed(2) + " kg" },
  ], [xs, h.map((r) => r.weight), h.map((r) => r.trend_kg)], "");
  make("ch-energy", [
    { label: "Intake", stroke: kcal, fill: kcal, alpha: 0.45, width: 0, paths: uPlot.paths.bars({ size: [0.7, 8] }), points: { show: false }, value: (u, v) => v == null ? "—" : fmtN(v) + " kcal" },
    { label: "Expenditure", stroke: accent, width: 2, spanGaps: true, points: { show: false }, value: (u, v) => v == null ? "—" : fmtN(v) + " kcal" },
  ], [xs, h.map((r) => (r.status === "complete" ? r.intake : null)), h.map((r) => r.expenditure)], "");
}

/* ---------- Work board: drag a card to change its status ---------- */

function wireBoard() {
  if (!window.Sortable) return;
  document.querySelectorAll(".board .col").forEach((col) => Sortable.create(col, {
    group: "board", draggable: ".tcard", animation: 150, delay: 150, delayOnTouchOnly: true,
    onEnd: async (ev) => {
      if (ev.from === ev.to) return;
      const id = ev.item.dataset.id, status = ev.to.dataset.status;
      try {
        await api(`/api/work/${encodeURIComponent(id)}/status`, { body: { status } });
        toast(`Moved to ${ISSUE_TEXT[status]}.`);
        S.cache.work = null;
      } catch (e) { toast(e.message); }
      render(); // redraw from the board's answer, so a refused move snaps back
    },
  }));
}

/* ---------- appearance, account menu and search ---------- */

const IS_MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
function getTheme() { try { return localStorage.getItem("hermes-theme") || "system"; } catch (_) { return "system"; } }
function setTheme(t) {
  try { if (t === "system") localStorage.removeItem("hermes-theme"); else localStorage.setItem("hermes-theme", t); } catch (_) { /* private mode */ }
  if (t === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}

function openAcctMenu() {
  const me = S.me || {};
  const t = getTheme();
  const opt = (v, icon, label) => `<button type="button" role="menuitemradio" aria-checked="${t === v}" data-act="theme" data-arg="${v}">${ic(icon)}${label}${t === v ? '<span style="margin-left:auto">✓</span>' : ""}</button>`;
  $("#overlay").innerHTML = `<div class="ov" style="background:transparent"><div class="scrim" style="background:transparent" data-act="close"></div></div>
    <div class="menu" role="menu" aria-label="Account">
      <div style="padding:10px 12px" class="small"><b>${esc((me.name || "Craig").split(" ")[0])}</b><div class="muted xs">${esc(me.login || "")} · Owner</div></div><div class="sep"></div>
      ${opt("light", "today", "Light appearance")}${opt("dark", "moon", "Dark appearance")}${opt("system", "system", "Match device")}<div class="sep"></div>
      <button type="button" role="menuitem" data-act="go" data-arg="system/status">${ic("system")}System</button>
      <p class="xs muted" style="padding:6px 12px 8px">Signed in through Tailscale. To sign out, sign out of Tailscale on this device.</p></div>`;
  const first = $("#overlay .menu button");
  if (first) first.focus();
}

// Everything you can jump to: screens, plus tasks, agents, routines and decisions already loaded.
function paletteIndex() {
  const out = [];
  const add = (group, label, href, sub, icon) => out.push({ group, label, href, sub: sub || "", icon });
  add("Actions", "New task for an agent", null, "", "plus");
  add("Actions", "New chat with Max", "max/new", "", "max");
  add("Actions", "Log food", "health/food", "", "health");
  for (const x of NAV) if (x.h) add("Go to", x.t, x.h, "", x.i);
  HEALTH_TABS.forEach(([k, t]) => add("Go to", `Health › ${t}`, "health/" + k, "", "health"));
  LIB_TABS.forEach(([k, t]) => add("Go to", `Library › ${t}`, "library/" + k, "", "library"));
  SYS_TABS.forEach(([k, t]) => add("Go to", `System › ${t}`, "system/" + k, "", "system"));
  const w = S.cache.work && S.cache.work.work;
  ((w && w.ok && w.data.issues) || []).forEach((i) => add("Work", `${i.ref ? i.ref + " " : ""}${i.title}`, "work/" + i.id, i.status_text || ISSUE_TEXT[i.status] || "", "work"));
  const ag = S.cache.agents && S.cache.agents.agents;
  ((ag && ag.ok && ag.data) || []).forEach((a) => add("Agents", a.name, "agents/" + a.id, a.title || "", "agents"));
  const rt = S.cache.routines && S.cache.routines.routines;
  ((rt && rt.ok && rt.data) || []).forEach((r) => add("Routines", r.title, "routines/" + r.id, r.schedule || "", "routines"));
  const ap = S.cache.approvals;
  if (ap) {
    const list = [...((ap.broker_pending && ap.broker_pending.data) || []), ...((ap.board && ap.board.data) || [])];
    list.forEach((a) => add("Decisions", a.title, `approvals/${a.source}:${encodeURIComponent(a.id)}`, APPROVAL_TEXT[a.status] || a.status, "approvals"));
  }
  (S.artIndex || []).forEach((a) => add("Library", a.title, "library/files/" + encodeURIComponent(a.name), a.label, "file"));
  return out;
}

function paletteResults(q) {
  q = (q || "").trim().toLowerCase();
  const idx = paletteIndex();
  // Typo-tolerant when Fuse.js is loaded ("aprovals" still finds Approvals); plain matching otherwise.
  const all = !q ? idx : window.Fuse ? new Fuse(idx, { keys: [{ name: "label", weight: 3 }, "sub", "group"], threshold: 0.35, ignoreLocation: true }).search(q).map((r) => r.item)
    : idx.filter((x) => `${x.label} ${x.sub} ${x.group}`.toLowerCase().includes(q));
  const groups = new Map();
  all.forEach((x) => { if (!groups.has(x.group)) groups.set(x.group, []); groups.get(x.group).push(x); });
  let i = 0;
  const html = [...groups].filter(([g]) => q || ["Actions", "Go to", "Decisions"].includes(g)).map(([g, xs]) => `<div class="grp">${esc(g)}</div>${xs.slice(0, q ? 6 : g === "Go to" ? 8 : 5).map((x) => {
    const cls = i++ === S.palHl ? ' class="hl"' : "";
    return x.href ? `<a href="#${esc(x.href)}"${cls}>${ic(x.icon, 16)}<span>${esc(x.label)}</span><span class="s">${esc(x.sub)}</span></a>`
      : `<a href="#" data-act="createTask"${cls}>${ic(x.icon, 16)}<span>${esc(x.label)}</span><span class="s">${esc(x.sub)}</span></a>`;
  }).join("")}`).join("");
  return html || `<p class="small muted" style="padding:16px">Nothing matches “${esc(q)}”. Search covers screens and the board records you can open. Mail and calendar aren't searched here.</p>`;
}

function openPalette() {
  S.palHl = 0;
  $("#overlay").innerHTML = `<div class="ov top-align" style="place-items:start center"><div class="scrim" data-act="close"></div>
    <div class="palette" role="dialog" aria-modal="true" aria-label="Search or jump to">
      <div class="pin">${ic("search", 18)}<label class="sr" for="pal-q">Search</label><input id="pal-q" autocomplete="off" placeholder="Search tasks, agents, routines, decisions or jump to…"><kbd>esc</kbd></div>
      <div class="res" id="pal-res">${paletteResults("")}</div>
      <div class="foot"><span><kbd>↑</kbd> <kbd>↓</kbd> move</span><span><kbd>↵</kbd> open</span><span class="desk-only">Only things you can open</span></div></div></div>`;
  $("#pal-q").focus();
  // Fill in records not loaded yet, quietly.
  Promise.all([
    S.cache.work ? null : load("work", "/api/work"),
    S.cache.agents ? null : load("agents", "/api/agents"),
    S.cache.routines ? null : load("routines", "/api/routines"),
    S.artIndex ? null : api("/api/artifacts").then((d) => { S.artIndex = d.artifacts; }).catch(() => { S.artIndex = []; }),
  ]).then(() => { const q = $("#pal-q"), r = $("#pal-res"); if (q && r) r.innerHTML = paletteResults(q.value); });
}

function paletteKey(e) {
  const links = [...document.querySelectorAll("#pal-res a")];
  if (!links.length) return;
  e.preventDefault();
  if (e.key === "Enter") return links[S.palHl || 0].click();
  S.palHl = Math.max(0, Math.min(links.length - 1, (S.palHl || 0) + (e.key === "ArrowDown" ? 1 : -1)));
  links.forEach((a, i) => a.classList.toggle("hl", i === S.palHl));
  links[S.palHl].scrollIntoView({ block: "nearest" });
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
    if (name === "scanLookup") {
      const code = ($("#scan-code").value || "").replace(/\s+/g, "");
      if (!/^\d{8,14}$/.test(code)) return toast("Type the number under the barcode (8 to 14 digits).");
      location.hash = `#health/draft/${code}`;
      return;
    }
    if (name === "logPick") return openLogFood(S.logFoods[Number(arg)]);
    if (name === "logFoodGo") {
      const f = S.logFood;
      const amt = Number(($("#lf-amt") || {}).value);
      const byUnit = f.unit && f.unit !== "serving" && f.portion;
      const quantity = byUnit ? amt / f.portion : amt;
      if (!(quantity > 0)) return toast("Enter an amount above zero.");
      busy(true);
      const lg = S.log || {};
      const r = await api("/api/health/log/food", { body: { food_id: f.id, meal: Number($("#lf-meal").value), quantity: Math.round(quantity * 100) / 100, date: lg.day } });
      closeModal();
      toast(`Logged ${f.name} to ${r.logged.meal || "your diary"}.`);
      location.hash = `#health/food/${r.date}`;
      return;
    }
    if (name === "foodToNt") {
      busy(true);
      try { await addToNt(ntFood(foodFromForm(), arg)); } finally { busy(false); }
      return;
    }
    if (name === "savedToNt") {
      const f = (S.savedFoods || []).find((x) => String(x.id) === String(arg));
      if (!f) return toast("That saved food isn't on screen any more.");
      busy(true);
      try { await addToNt(ntFood(f, f.barcode)); } finally { busy(false); }
      return;
    }
    if (name === "water") {
      const [ml, day] = String(arg).split("|");
      busy(true);
      const r = await api("/api/health/log/water", { body: { amount_ml: Number(ml), date: day } });
      toast(`Added ${ml} ml of water${r.total_ml != null ? ` · ${fmtN(r.total_ml)} ml logged that day` : ""}.`);
      return render();
    }
    if (name === "setOpen") { const [id, nm, top] = String(arg || "").split("|"); return openSetLogger(id, nm, top); }
    if (name === "setChange") { S.setEx = null; return openSetLogger("", "", $("#set-kg") ? $("#set-kg").value : ""); }
    if (name === "setPickEx") {
      const x = S.setFound[Number(arg)];
      const kg = $("#set-kg") ? $("#set-kg").value : "";
      openSetLogger(String(x.id), x.name, kg);
      return;
    }
    if (name === "setGo") {
      if (!S.setEx) return toast("Pick the exercise first.");
      const reps = $("#set-reps").value, kg = $("#set-kg").value, rpe = $("#set-rpe").value;
      if (reps === "") return toast("Enter the reps.");
      busy(true);
      try {
        const body = { exercise_id: S.setEx.id, reps: Number(reps), warmup: $("#set-warm").checked };
        if (kg !== "") body.weight = Number(kg);
        if (rpe !== "") body.rpe = Number(rpe);
        const r = await api("/api/health/log/set", { body });
        S.setDone = [...(S.setDone || []), { ...r.set, exercise: r.exercise || S.setEx.name }];
        $("#set-log").innerHTML = setLogList();
        $("#set-reps").value = "";
        $("#set-warm").checked = false;
        $("#set-reps").focus();
        toast(`Logged ${r.set.reps} × ${fmtKg(r.set.weight)} kg to LiftTrace.`);
      } finally { busy(false); }
      return;
    }
    if (name === "foodSave") {
      busy(true);
      const r = await api("/api/health/foods", { body: foodFromForm() });
      toast(`Saved ${r.result && r.result.name ? r.result.name : "the food"} to your saved foods.`);
      location.hash = "#health/saved";
      return;
    }
    if (name === "planEvent") return openPlanEvent(arg);
    if (name === "palette") return openPalette();
    if (name === "taskComment" || name === "taskRevise") {
      const rev = name === "taskRevise";
      return modal(rev ? "Ask for changes" : "Comment", `<div class="field"><label for="tc-t">${rev ? "What should change?" : "Comment"}</label><textarea class="inp" id="tc-t" rows="5" maxlength="8000" autofocus></textarea></div><p class="xs muted">${rev ? "Added as a comment, and the task moves back to In progress." : "Added to the task in Paperclip. The agent sees it on its next run."}</p>`,
        `<button type="button" class="btn ghost" data-act="close">Cancel</button><button type="button" class="btn primary" data-act="${rev ? "taskReviseGo" : "taskCommentGo"}" data-arg="${esc(arg)}">${rev ? "Send back" : "Add comment"}</button>`);
    }
    if (name === "taskCommentGo" || name === "taskReviseGo") {
      const text = ($("#tc-t") || {}).value || "";
      if (!text.trim()) return toast("Write something first.");
      busy(true);
      await api(`/api/work/${encodeURIComponent(arg)}/comment`, { body: { body: text } });
      if (name === "taskReviseGo") await api(`/api/work/${encodeURIComponent(arg)}/status`, { body: { status: "in_progress" } });
      closeModal();
      toast(name === "taskReviseGo" ? "Sent back to the agent." : "Comment added.");
      return render();
    }
    if (name === "taskAccept") {
      busy(true);
      await api(`/api/work/${encodeURIComponent(arg)}/status`, { body: { status: "done" } });
      toast("Accepted. Marked Done.");
      return render();
    }
    if (name === "workView") { S.workView = arg; try { localStorage.setItem("hermes:workView", arg); } catch (_) { /* private mode */ } return render(); }
    if (name === "acctMenu") return openAcctMenu();
    if (name === "theme") { setTheme(arg); return openAcctMenu(); }
    if (name === "go") { closeModal(); location.hash = arg; return; }
    if (name === "skillSrc") return openSkillSource(arg);
    if (name === "memAsk") {
      const [how, i] = arg.split(":");
      const m = (S.libMemory || [])[Number(i)];
      if (!m) return;
      return askMax(how === "forget" ? `Please forget this from your memory: "${m.text}"` : `Please correct this in your memory: "${m.text}". It should say: `);
    }
    if (name === "createTask") return openCreateTask();
    if (name === "newChat") { S.chatDraft = ""; location.hash = "max/new"; setTimeout(() => { const b = $("#max-in"); if (b) b.focus(); }, 50); return; }
    if (name === "chatSend") return chatSend();
    if (name === "artOpen") { S.art = { name: arg, tab: "preview" }; return render(); }
    if (name === "artClose") { S.art = null; return render(); }
    if (name === "artTab") { if ((location.hash || "").startsWith("#library/")) S.libTab = arg; else if (S.art) S.art.tab = arg; return render(); }
    if (name === "artCopy") { const a = curArt(); if (a) { await navigator.clipboard.writeText(a.text); toast("Copied."); } return; }
    if (name === "artDownload") {
      const a = curArt();
      if (!a) return;
      const url = URL.createObjectURL(new Blob([a.text], { type: "text/plain" }));
      const link = document.createElement("a");
      link.href = url; link.download = a.name; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      return;
    }
    if (name === "artAsk") {
      const a = curArt();
      if (!a) return;
      S.chatDraft = `About ${a.name}: `;
      if ((location.hash || "").startsWith("#library/")) return askMax(S.chatDraft);
      if (matchMedia("(max-width:1279px)").matches) S.art = null;
      await render();
      const b = $("#max-in");
      if (b) { b.focus(); b.setSelectionRange(b.value.length, b.value.length); }
      return;
    }
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
  if (e.target.closest("#pal-res a[href]:not([data-act])")) setTimeout(closeModal, 0); // same-page jumps don't fire hashchange
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
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); if ($("#pal-q")) closeModal(); else openPalette(); return; }
  if (e.target.id === "pal-q" && ["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) return paletteKey(e);
  if (e.key === "Escape" && $("#overlay").innerHTML) closeModal();
  if (e.target.id === "max-in" && e.key === "Enter" && !e.shiftKey && !e.isComposing) {
    e.preventDefault();
    chatSend().catch((err) => toast(err.message || "That didn't send."));
  }
});
document.addEventListener("input", (e) => {
  if (e.target.id === "max-in") S.chatDraft = e.target.value;
  if (e.target.id === "pal-q") { S.palHl = 0; $("#pal-res").innerHTML = paletteResults(e.target.value); }
  if (e.target.id === "log-q") {
    const q = e.target.value;
    clearTimeout(S.logT);
    S.logT = setTimeout(async () => { const html = await logSearch(q.trim()); if ($("#log-q") && $("#log-q").value === q) $("#log-res").innerHTML = html; }, 250);
  }
  if (e.target.id === "set-q") { const q = e.target.value.trim(); clearTimeout(S.setT); S.setT = setTimeout(() => setSearch(q), 250); }
  if (e.target.id === "lf-amt" && S.logFood) $("#lf-sum").textContent = logSummary(S.logFood, e.target.value).replace(/<[^>]+>/g, "");
  if (e.target.id === "skill-q") {
    const q = e.target.value.trim().toLowerCase();
    document.querySelectorAll(".panel.skill").forEach((el) => { el.hidden = !!q && !el.dataset.q.includes(q); });
    document.querySelectorAll(".skill-group").forEach((g) => { g.hidden = !g.querySelector(".panel.skill:not([hidden])"); });
  }
});

/* ---------- Health ----------
   Food, Train, Meals & Shop and Progress from NutriTrace, LiftTrace and
   CookTrace. Food, water and gym sets are logged from here into those apps;
   cooking and shopping stay in CookTrace for now. */

const HEALTH_TABS = [["food", "Food"], ["train", "Train"], ["meals", "Meals & Shop"], ["progress", "Progress"]];
const HEALTH_NUTS = [["kcal", "Calories", "kcal", "var(--m-k)"], ["protein", "Protein", "g", "var(--m-p)"],
  ["carbs", "Carbs", "g", "var(--m-c)"], ["fat", "Fat", "g", "var(--m-f)"], ["fibre", "Fibre", "g", "var(--m-fib)"]];
const fmtN = (n) => (n == null || isNaN(n) ? "—" : Math.round(Number(n)).toLocaleString("en-GB"));
const isoDay = (s) => (/^\d{4}-\d{2}-\d{2}$/.test(s || "") ? s : null);
const shiftDay = (iso, n) => { const d = new Date(iso + "T12:00:00"); d.setDate(d.getDate() + n); return d.toISOString().slice(0, 10); };
const dayName = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
const dayShort = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { weekday: "short" });
const hBar = (v, t) => {
  const p = v == null || !t ? 0 : Math.min(100, (100 * v) / t);
  return `<div class="bar" role="img" aria-label="${esc(fmtN(v))} of ${esc(fmtN(t))}"><i style="width:${p.toFixed(1)}%"></i></div>`;
};
const hNotice = (part, what) => (part && !part.ok ? notConnected(what, part.error) : "");
const appLink = (url, label, cls = "btn sm") => (url ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener">${esc(label)}</a>` : "");

function healthHead(sub, title, actions = "", subtitle = "") {
  return `<div class="ph"><div class="ph-t"><h1>${esc(title)}</h1>${subtitle ? `<p class="sub">${esc(subtitle)}</p>` : ""}</div>${actions ? `<div class="ph-a">${actions}</div>` : ""}</div>
    <nav class="tabs" aria-label="Sections">${HEALTH_TABS.map(([k, t]) => `<a href="#health/${k}"${k === sub ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>`;
}

// One request per screen. A null answer means the server didn't answer at all.
async function healthData(key, path) {
  return load("health:" + key, "/api/health/" + path);
}
const healthDown = (sub, title) => healthHead(sub, title) + notConnected("Health", "Couldn't reach the server.");

async function screenHealth(rest) {
  if (rest[0] === "scan") return screenScan();
  if (rest[0] === "draft") return screenDraft(rest[1]);
  if (rest[0] === "saved") return screenSaved();
  if (rest[0] === "log") return screenLog(isoDay(rest[1]), rest[2]);
  const sub = HEALTH_TABS.some(([k]) => k === rest[0]) ? rest[0] : "food";
  if (sub === "food") return screenFood(isoDay(rest[1]));
  if (sub === "train") return screenTrain();
  if (sub === "meals") return screenMeals();
  return screenProgress(rest[1] === "7" ? 7 : 14);
}

/* ---------- Food ---------- */

// Weekly nutrition panel (Food and Today). base is the hash prefix for day links.
function weekPanel(d, cur, base = "health/food") {
  const goals = d.goals && d.goals.ok ? d.goals.data : {};
  const week = d.week && d.week.ok ? d.week.data : [];
  const monday = week.length ? week[0].date : cur;
  const nextWeek = shiftDay(monday, 7);
  const sel = week.find((r) => r.date === cur) || { date: cur, status: "open" };
  const cellFor = (row, [k, label, unit, col]) => {
    const goal = goals[k];
    const v = row.status === "future" || row.status === "none" ? null : row[k];
    const cls = { logged: "complete", open: "open", future: "future" }[row.status] || "missing";
    const frac = v != null && goal ? Math.min(1, v / goal) : 0;
    const over = v != null && goal && v > goal * 1.05 && row.status !== "future" ? " over" : "";
    const tip = `${dayName(row.date)} · ${label}: ${v == null ? "no record" : fmtN(v) + " of " + fmtN(goal) + " " + unit}`;
    return `<span class="g-cell ${cls}${over}" title="${esc(tip)}"><span class="g-pill"><i style="height:${Math.round(frac * 100)}%;background:${col}"></i></span></span>`;
  };
  const cols = week.map((row) => `<a class="g-col${row.date === cur ? " sel" : ""}${row.status === "open" ? " today" : ""}" href="#health/food/${row.date}" aria-label="${esc(dayName(row.date))}">
      ${HEALTH_NUTS.map((n) => cellFor(row, n)).join("")}<span class="g-day"><span class="dl-l">${esc(dayShort(row.date))}</span><span class="dl-s">${esc(dayShort(row.date)[0])}</span></span></a>`).join("");
  const nums = HEALTH_NUTS.map(([k, label, unit]) => {
    const v = sel.status === "future" || sel.status === "none" ? null : sel[k];
    return `<div class="g-num"><span class="g-lab">${label}</span><b class="num">${fmtN(v)}${unit === "g" ? "<small> g</small>" : ""}</b><span class="g-sub">${goals[k] ? "of " + fmtN(goals[k]) : ""}</span></div>`;
  }).join("");
  const logged = week.filter((r) => r.status === "logged").length;
  const weekNav = base === "health/food" ? `<div class="btns" style="gap:4px">
      <a class="iconbtn" href="#health/food/${shiftDay(monday, -7)}" aria-label="Previous week">${ic("back")}</a>
      <b class="small" style="min-width:110px;text-align:center">${week.length ? esc(dayShort(monday) + " " + dayName(monday).split(" ").slice(1).join(" ") + " – " + dayName(week[6].date).split(" ").slice(1).join(" ")) : "This week"}</b>
      ${nextWeek <= d.today ? `<a class="iconbtn" href="#health/food/${nextWeek}" aria-label="Next week">${ic("chev")}</a>` : `<span class="iconbtn" aria-disabled="true" style="opacity:.35">${ic("chev")}</span>`}
    </div>` : `<b class="small">This week</b>`;
  const est = d.estimate && d.estimate.ok ? d.estimate.data : null;
  const side = est
    ? `<a class="spark-card" href="#health/progress"><span class="small"><b>Expenditure</b></span><span class="xs muted">Estimate</span>
        <span class="spark-v"><b class="num">${fmtN(est.expenditure)}</b> <span class="small muted">kcal</span></span>
        <span class="xs muted">${est.low && est.high ? `likely ${fmtN(est.low)}–${fmtN(est.high)}` : "From your logged intake and weight"}</span></a>
      ${est.trend_kg != null ? `<a class="spark-card" href="#health/progress"><span class="small"><b>Weight trend</b></span><span class="xs muted">Smoothed</span>
        <span class="spark-v"><b class="num">${Number(est.trend_kg).toFixed(1)}</b> <span class="small muted">kg</span></span>
        <span class="xs muted">${est.weekly_change_kg != null ? `${est.weekly_change_kg > 0 ? "+" : ""}${Number(est.weekly_change_kg).toFixed(2)} kg a week` : ""}</span></a>` : ""}`
    : `<div class="spark-card"><span class="small"><b>Expenditure</b></span><span class="xs muted">${esc(d.estimate ? d.estimate.error : "Not connected")}</span></div>`;
  return `<section class="panel a-week"><div class="panel-h"><h2>Weekly nutrition</h2>${weekNav}${base !== "health/food" ? `<div class="r"><a class="btn ghost sm" href="#health/food">Food ${ic("chev", 16)}</a></div>` : ""}</div>
    ${week.length ? `<div class="wk2"><div class="stack s8"><div class="g-wrap"><div class="g-cols" style="--n:7">${cols}</div>
      <div class="g-nums">${nums}<span class="g-day">${esc(dayName(cur))}</span></div></div>
      <div class="g-key"><span>Faded: partly logged or today</span><span>Cap on top: over target</span><span>Dashed: no record</span></div>
      <p class="xs muted">${logged} of ${week.filter((r) => r.status !== "future").length} days logged so far this week.</p></div>
      <div class="wk2-side">${side}</div></div>` : notConnected("NutriTrace", d.week && d.week.error)}
  </section>`;
}

async function screenFood(day) {
  const d = await healthData("food:" + (day || "today"), "food" + (day ? `?day=${encodeURIComponent(day)}` : ""));
  if (!d) return healthDown("food", "Food");
  const links = d.links || {};
  const cur = d.day && d.day.ok ? d.day.data.date : d.today;
  const goals = d.goals && d.goals.ok ? d.goals.data : {};

  const dayRec = d.day && d.day.ok ? d.day.data : null;
  const t = dayRec ? dayRec.totals : null;
  const dayBody = !d.day
    ? notConnected("NutriTrace", "")
    : !d.day.ok ? notConnected("NutriTrace", d.day.error)
    : `<section class="panel"><div class="cols even" style="align-items:center"><div>
        <div class="kpi"><b>${fmtN(t.kcal)}</b><span>of ${fmtN(goals.kcal)} kcal${goals.kcal ? ` · ${fmtN(Math.abs(goals.kcal - t.kcal))} ${t.kcal > goals.kcal ? "over" : "left"}` : ""}</span></div>
        <div style="margin-top:10px">${hBar(t.kcal, goals.kcal)}</div></div>
        <div class="macros">${[["protein", "Protein"], ["carbs", "Carbohydrate"], ["fat", "Fat"], ["fibre", "Fibre"]].map(([k, l]) => `<div class="macro"><div class="l"><span>${l}</span><span>${fmtN(t[k])}<span class="muted" style="font-weight:400"> / ${fmtN(goals[k])} g</span></span></div>${hBar(t[k], goals[k])}</div>`).join("")}</div>
      </div></section>
      ${waterPanel(t, cur, d.today)}
      <div class="cols even">${MEAL_NAMES.map((name, mi) => {
        const m = dayRec.meals.find((x) => x.meal === name) || { meal: name, items: [], kcal: 0 };
        return `<section class="panel"><div class="panel-h"><h2>${esc(name)}</h2><span class="small muted num">${m.items.length ? fmtN(m.kcal) + " kcal" : ""}</span>${canLog(cur, d.today) ? `<div class="r"><a class="btn ghost sm" href="#health/log/${cur}/${mi}">${ic("plus", 15)}Add</a></div>` : ""}</div>
        ${m.items.length ? `<div class="list">${m.items.map((i) => `<div class="li"><span class="main"><span class="t">${esc(i.name)}</span><span class="s">${esc(i.amount)}${i.brand ? " · " + esc(i.brand) : ""} · P ${fmtN(i.protein)} · C ${fmtN(i.carbs)} · F ${fmtN(i.fat)}</span></span><span class="end"><span class="kc">${fmtN(i.kcal)} kcal</span></span></div>`).join("")}</div>` : `<p class="small muted">Nothing logged.</p>`}</section>`;
      }).join("")}</div>
      ${dayRec.meals.filter((m) => !MEAL_NAMES.includes(m.meal)).map((m) => `<section class="panel"><div class="panel-h"><h2>${esc(m.meal)}</h2><span class="small muted num">${fmtN(m.kcal)} kcal</span></div><div class="list">${m.items.map((i) => `<div class="li"><span class="main"><span class="t">${esc(i.name)}</span><span class="s">${esc(i.amount)}</span></span><span class="end"><span class="kc">${fmtN(i.kcal)} kcal</span></span></div>`).join("")}</div></section>`).join("")}`;

  return healthHead("food", "Food", `${canLog(cur, d.today) ? `<a class="btn primary" href="#health/log/${cur}">${ic("plus", 16)}Log food</a>` : ""}<a class="btn" href="#health/scan">${ic("camera", 16)}Scan a barcode</a>${appLink(links.nutritrace, "Open NutriTrace", "btn")}`,
      "Logs go straight into NutriTrace.") + `
    <div class="stack s24">
      <div class="row-flex"><div class="btns">
        <a class="iconbtn" href="#health/food/${shiftDay(cur, -1)}" aria-label="Previous day">${ic("back")}</a>
        <b style="min-width:150px;text-align:center">${esc(dayName(cur))}${cur === d.today ? " · Today" : ""}</b>
        ${cur < d.today ? `<a class="iconbtn" href="#health/food/${shiftDay(cur, 1)}" aria-label="Next day">${ic("chev")}</a>` : ""}
      </div></div>
      ${hNotice(d.goals, "The goals")}
      ${weekPanel(d, cur)}
      ${dayBody}
      <p class="xs muted">Weight history isn't readable by the apps' tokens yet, so it isn't shown here.</p>
    </div>`;
}

/* ---------- Barcode scan and saved foods ----------
   The camera reads the code in the browser (BarcodeDetector, or the bundled
   ZXing build). The number goes to the server, which asks Open Food Facts only
   if lookups are switched on. Nothing here goes to Max. */

let zxingLoading = null;
function loadZxing() {
  if (window.ZXingBrowser) return Promise.resolve(window.ZXingBrowser);
  if (!zxingLoading) {
    zxingLoading = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/vendor/zxing/zxing-browser.min.js";
      s.onload = () => (window.ZXingBrowser ? resolve(window.ZXingBrowser) : reject(new Error("no reader")));
      s.onerror = () => reject(new Error("the barcode reader didn't load"));
      document.head.append(s);
    });
  }
  return zxingLoading;
}

function stopScan() {
  const s = S.scan;
  if (!s) return;
  S.scan = null;
  s.stop();
}
document.addEventListener("visibilitychange", () => { if (document.hidden) stopScan(); });

async function screenScan() {
  return healthHead("food", "Scan a barcode", "", "Point the camera at the barcode on the packet.") + `
    <div class="stack s24">
      <section class="panel"><div class="scan-box"><video id="scan-v" playsinline muted aria-label="Camera view"></video><div class="scan-aim" aria-hidden="true"></div></div>
        <p id="scan-msg" class="small muted" role="status">Starting the camera…</p></section>
      <section class="panel"><div class="field"><label for="scan-code">Or type the number under the barcode</label>
        <div class="btns"><input id="scan-code" class="inp" inputmode="numeric" autocomplete="off" maxlength="14" placeholder="e.g. 5012345678900">
        <button type="button" class="btn primary" data-act="scanLookup">Look up</button><a class="btn ghost" href="#health/draft/manual">Enter by hand</a></div></div></section>
    </div>`;
}

// Starts the camera once the scan screen is in the page. A missing camera
// leaves the typed-number and by-hand routes working.
async function startScan() {
  const video = $("#scan-v");
  const say = (t) => { const m = $("#scan-msg"); if (m) m.textContent = t; };
  if (!video) return;
  let stream;
  try {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) throw new Error("no camera");
    stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
  } catch (_) {
    say("The camera isn't available here. Type the number below, or enter the food by hand.");
    return;
  }
  const session = { stream, timer: null, reader: null, stop: () => {} };
  S.scan = session;
  session.stop = () => {
    clearInterval(session.timer);
    if (session.reader) session.reader.reset();
    stream.getTracks().forEach((t) => t.stop());
    video.srcObject = null;
  };
  const found = (code) => {
    if (S.scan !== session) return;
    stopScan();
    location.hash = `#health/draft/${code}`;
  };
  video.srcObject = stream;
  await video.play().catch(() => {});
  if (S.scan !== session) return;
  if ("BarcodeDetector" in window) {
    const detector = new BarcodeDetector({ formats: ["ean_13", "ean_8", "upc_a", "upc_e"] });
    session.timer = setInterval(async () => {
      try {
        const hits = await detector.detect(video);
        if (hits.length) found(hits[0].rawValue);
      } catch (_) { /* a frame that wasn't ready */ }
    }, 300);
    say("Hold the barcode in the box.");
    return;
  }
  try {
    const Z = await loadZxing();
    if (S.scan !== session) return;
    session.reader = new Z.BrowserMultiFormatReader();
    session.reader.decodeFromVideoElement(video, (result) => { if (result) found(result.getText()); });
    say("Hold the barcode in the box.");
  } catch (e) {
    say("This browser can't read barcodes here. Type the number below, or enter the food by hand.");
  }
}

const FOOD_ROWS = [["kcal", "Calories", "kcal"], ["protein", "Protein", "g"], ["carbs", "Carbohydrate", "g"], ["fat", "Fat", "g"], ["fibre", "Fibre", "g"]];

async function screenDraft(code) {
  const manual = !code || code === "manual";
  let note = "";
  let p = null;
  if (!manual) {
    try {
      const d = await api(`/api/health/barcode/${encodeURIComponent(code)}`);
      if (d.product && d.product.found) p = d.product;
      else note = `<div class="notice warn" role="status">${ic("alert")}<div><b>No product for ${esc(code)}.</b> Type it in from the packet.</div></div>`;
    } catch (e) {
      note = notConnected("Barcode lookup", e.message);
    }
  }
  const val = (basis, k) => (p && p[basis] && p[basis][k] != null ? p[basis][k] : "");
  const cell = (basis, k, label) => `<input class="inp" type="number" inputmode="decimal" min="0" step="0.1" data-f="${basis}.${k}" value="${esc(val(basis, k))}" aria-label="${esc(label)}">`;
  const meta = p ? `From ${esc(p.source)}, retrieved ${esc(dayName(p.retrieved))}. ` : "";
  return healthHead("food", p ? "Check the food" : "New food", "", "Blank means unknown, not zero.") + `
    <div class="stack s24">
      ${note}
      <div id="food-form" data-source="${esc(p ? p.source : "")}" data-retrieved="${esc(p ? p.retrieved : "")}">
        <section class="panel"><div class="stack s16">
          <div class="field"><label for="f-name">Name</label><input id="f-name" class="inp" data-f="name" maxlength="120" value="${esc(p ? p.name : "")}"></div>
          <div class="field"><label for="f-brand">Brand</label><input id="f-brand" class="inp" data-f="brand" maxlength="80" value="${esc(p ? p.brand : "")}"></div>
          <div class="field"><label for="f-serving">Serving</label><input id="f-serving" class="inp" data-f="serving" maxlength="60" placeholder="e.g. 250 ml" value="${esc(p && p.serving ? p.serving : "")}"></div>
        </div></section>
        <section class="panel"><div class="panel-h"><h2>Nutrition</h2></div>
          <div class="food-grid" role="table" aria-label="Nutrition per 100 g and per serving">
            <div class="food-row food-head" role="row"><span role="columnheader"></span><span role="columnheader">Per 100 g</span><span role="columnheader">Per serving</span></div>
            ${FOOD_ROWS.map(([k, label, unit]) => `<div class="food-row" role="row"><span role="rowheader">${esc(label)} <span class="muted">(${unit})</span></span>${cell("per100", k, `${label} per 100 g`)}${cell("per_serving", k, `${label} per serving`)}</div>`).join("")}
          </div>
        </section>
        <p class="xs muted">${meta}Check against the packet before saving.</p>
      </div>
      <div class="btns"><button type="button" class="btn primary" data-act="foodToNt" data-arg="${esc(manual ? "" : code)}">Add to NutriTrace and log</button><a class="btn ghost" href="#health/food">Cancel</a></div>
    </div>`;
}

function foodFromForm() {
  const root = $("#food-form");
  if (!root) throw new Error("The food form isn't on screen.");
  const food = { name: "", brand: "", serving: "", per100: {}, per_serving: {}, source: root.dataset.source || "", retrieved: root.dataset.retrieved || "" };
  root.querySelectorAll("[data-f]").forEach((el) => {
    const [a, b] = el.dataset.f.split(".");
    const v = el.value.trim();
    if (b) food[a][b] = v === "" ? null : v;
    else food[a] = v;
  });
  return food;
}

async function screenSaved() {
  let d = null;
  let err = "";
  try { d = await api("/api/health/foods"); } catch (e) { err = e.message; }
  const items = d && d.items ? d.items : [];
  return healthHead("food", "Saved foods", `<a class="btn primary" href="#health/draft/manual">New food</a><a class="btn" href="#health/scan">Scan a barcode</a>`,
      "Saved here before NutriTrace could take foods from the app. Add one to NutriTrace to log it.") + `
    <div class="stack s24">
      ${err ? notConnected("Saved foods", err) : ""}
      ${!err && !items.length ? `<div class="empty"><h3>No saved foods yet</h3><p>Scan a barcode or enter a food by hand, and save it here.</p></div>` : ""}
      ${items.length ? `<section class="panel"><div class="list">${items.map((f) => `<div class="li"><span class="main"><span class="t">${esc(f.name)}</span><span class="s">${esc(f.serving || "")}${f.brand ? (f.serving ? " · " : "") + esc(f.brand) : ""}${f.source ? " · " + esc(f.source) : ""}</span></span><span class="end"><span class="kc">${fmtN(f.per_serving && f.per_serving.kcal)} kcal</span><button type="button" class="btn sm" data-act="savedToNt" data-arg="${esc(f.id)}">Add and log</button></span></div>`).join("")}</div></section>` : ""}
    </div>`;
}

/* ---------- Logging food and water ----------
   Foods are looked up in NutriTrace's catalogue. A food from a barcode or
   typed in is added to the catalogue first, then logged. Nothing is ever
   edited or deleted from here. */

const MEAL_NAMES = ["Breakfast", "Lunch", "Dinner", "Snacks"];
const canLog = (day, today) => day <= today && day >= shiftDay(today, -31);
const mealNow = () => { const h = new Date().getHours(); return h < 11 ? 0 : h < 15 ? 1 : h < 21 ? 2 : 3; };

function waterPanel(t, cur, today) {
  const ml = t && t.water_ml != null ? t.water_ml : null;
  return `<section class="panel"><div class="row-flex" style="align-items:center;gap:12px;flex-wrap:wrap">
    <span class="main"><b>Water</b> <span class="small muted num">${ml == null ? "not readable" : fmtN(ml) + " ml"}</span></span>
    ${canLog(cur, today) ? `<span class="btns" style="margin-left:auto">${[250, 500].map((n) => `<button type="button" class="btn sm" data-act="water" data-arg="${n}|${cur}">+${n} ml</button>`).join("")}</span>` : ""}
  </div></section>`;
}

async function screenLog(day, meal) {
  const today = (S.cache["health:food:today"] && S.cache["health:food:today"].today) || new Date().toISOString().slice(0, 10);
  S.log = { day: day || today, meal: /^[0-3]$/.test(meal || "") ? Number(meal) : mealNow(), q: "" };
  let saved = [];
  try { saved = ((await api("/api/health/foods")).items) || []; } catch (_) { /* optional */ }
  S.savedFoods = saved;
  const first = await logSearch("");
  return healthHead("food", "Log food", `<a class="btn" href="#health/scan">${ic("camera", 16)}Scan a barcode</a><a class="btn" href="#health/draft/manual">${ic("plus", 16)}New food</a>`,
      `${MEAL_NAMES[S.log.meal]} · ${dayName(S.log.day)}. Pick a food from NutriTrace, or add a new one.`) + `
    <div class="stack s24">
      <section class="panel"><div class="field"><label for="log-q">Search your NutriTrace foods</label><input id="log-q" class="inp" type="search" autocomplete="off" placeholder="e.g. oats, chicken, skyr" autofocus></div>
        <div id="log-res" style="margin-top:12px">${first}</div></section>
      ${saved.length ? `<section class="panel"><div class="panel-h"><h2>Saved here, not in NutriTrace yet</h2></div><div class="list">${saved.map((f) => `<div class="li"><span class="main"><span class="t">${esc(f.name)}</span><span class="s">${esc(f.serving || "")}${f.brand ? " · " + esc(f.brand) : ""}</span></span><span class="end"><button type="button" class="btn sm" data-act="savedToNt" data-arg="${esc(f.id)}">Add and log</button></span></div>`).join("")}</div></section>` : ""}
    </div>`;
}

async function logSearch(q) {
  let d;
  try { d = await api(`/api/health/search/foods?q=${encodeURIComponent(q)}`); } catch (e) { return notConnected("NutriTrace", e.message); }
  S.logFoods = d.foods || [];
  if (!S.logFoods.length) return `<p class="small muted">${q ? `No NutriTrace food matches “${esc(q)}”. Scan its barcode or add it as a new food.` : "Your NutriTrace catalogue is empty. Scan a barcode or add a new food to start."}</p>`;
  return `<div class="list">${S.logFoods.map((f, i) => `<button type="button" class="li" data-act="logPick" data-arg="${i}" style="width:100%;text-align:left"><span class="main"><span class="t">${esc(f.name)}</span><span class="s">${fmtN(f.portion)} ${esc(f.unit || "")}${f.brand ? " · " + esc(f.brand) : ""} · P ${fmtN(f.protein)} · C ${fmtN(f.carbs)} · F ${fmtN(f.fat)}</span></span><span class="end"><span class="kc">${fmtN(f.kcal)} kcal</span>${ic("plus", 16)}</span></button>`).join("")}</div>${d.total > S.logFoods.length ? `<p class="xs muted">Showing ${S.logFoods.length} of ${fmtN(d.total)}. Type to narrow it down.</p>` : ""}`;
}

function openLogFood(f) {
  const lg = S.log || { day: new Date().toISOString().slice(0, 10), meal: mealNow() };
  S.logFood = f;
  const byUnit = f.unit && f.unit !== "serving" && f.portion;
  modal(`Log ${f.name}`, `<div class="form-grid">
    <div class="field"><label for="lf-amt">${byUnit ? `Amount (${esc(f.unit)})` : "Servings"}</label><input id="lf-amt" class="inp" type="number" inputmode="decimal" min="0" step="any" value="${byUnit ? esc(f.portion) : 1}" autofocus></div>
    <div class="field"><label for="lf-meal">Meal</label><select id="lf-meal" class="inp">${MEAL_NAMES.map((m, i) => `<option value="${i}"${i === lg.meal ? " selected" : ""}>${m}</option>`).join("")}</select></div>
    <div class="field full"><p class="small muted" id="lf-sum">${logSummary(f, byUnit ? f.portion : 1)}</p><p class="xs muted">Goes into NutriTrace for ${esc(dayName(lg.day))}.</p></div>
  </div>`, `<button type="button" class="btn ghost" data-act="close">Cancel</button><button type="button" class="btn primary" data-act="logFoodGo">Log it</button>`);
}

function logSummary(f, amt) {
  const k = f.unit && f.unit !== "serving" && f.portion ? Number(amt) / f.portion : Number(amt);
  if (!(k > 0)) return "Enter an amount.";
  const v = (x) => (x == null ? "—" : fmtN(x * k));
  return `${v(f.kcal)} kcal · P ${v(f.protein)} · C ${v(f.carbs)} · F ${v(f.fat)}`;
}

// A saved or typed-in food, as NutriTrace takes it: values per the portion.
function ntFood(food, barcode) {
  const m = /^\s*([\d.]+)\s*(g|ml)\b/i.exec(food.serving || "");
  const has = (b) => b && b.kcal != null && b.kcal !== "";
  const pick = (b) => Object.fromEntries(["kcal", "protein", "carbs", "fat", "fibre"].filter((k) => b[k] != null && b[k] !== "").map((k) => [k, Number(b[k])]));
  let portion, unit, nutrition;
  if (has(food.per_serving) && m) { portion = Number(m[1]); unit = m[2].toLowerCase(); nutrition = pick(food.per_serving); }
  else if (has(food.per100)) { portion = 100; unit = m ? m[2].toLowerCase() : "g"; nutrition = pick(food.per100); }
  else if (has(food.per_serving)) { portion = 1; unit = "serving"; nutrition = pick(food.per_serving); }
  else throw new Error("Add the calories (per 100 g or per serving) first.");
  const out = { name: (food.name || "").trim(), portion, unit, nutrition };
  if (!out.name) throw new Error("Give the food a name first.");
  if ((food.brand || "").trim()) out.brand = food.brand.trim();
  if (/^\d{8,14}$/.test(barcode || "")) out.barcode = barcode;
  return out;
}

async function addToNt(body) {
  const r = await api("/api/health/log/food-new", { body });
  if (r.existing) toast(`${body.name} is already in NutriTrace, so that one is used.`);
  const f = r.food.kcal !== undefined ? r.food : { ...body, ...body.nutrition, id: r.food.id };
  openLogFood({ ...f, id: r.food.id });
}

/* ---------- Train ---------- */

async function screenTrain() {
  const d = await healthData("train", "train");
  if (!d) return healthDown("train", "Train");
  const links = d.links || {};
  const t = d.train.ok ? d.train.data : null;
  const r = d.records && d.records.ok ? d.records.data : [];
  const head = healthHead("train", "Train", `<button type="button" class="btn primary" data-act="setOpen">${ic("plus", 16)}Log a set</button>${appLink(links.lifttrace, "Open LiftTrace", "btn")}`,
    "Sets go straight into LiftTrace, on today's session.");
  if (!t) return head + notConnected("LiftTrace", d.train.error);

  const nx = t.next;
  const next = nx
    ? `<section class="panel"><div class="panel-h"><div><div class="eyebrow">Next session</div><h2 class="h2-serif">${esc(nx.name)}</h2></div><span class="badge info">Planned</span></div>
        <p class="small muted">${esc(nx.day_label || "")}${t.program.name ? " · " + esc(t.program.name) : ""}</p>
        <div class="list" style="margin:12px 0">${nx.exercises.map((x) => `<div class="li"><span class="main"><span class="t">${esc(x.name)}</span>
          <span class="s">Target ${esc(x.target_sets != null ? x.target_sets + " sets" : "—")}${x.last ? ` · Last time ${x.last.top ? fmtN(x.last.top) + " kg · " : ""}${esc((x.last.reps || []).join(", "))} reps` : " · No record yet"}</span></span>
          <span class="end"><button type="button" class="btn sm" data-act="setOpen" data-arg="${esc(String(x.id ?? ""))}|${esc(x.name || "")}|${esc(x.last && x.last.top ? x.last.top : "")}">${ic("plus", 15)}Log set</button></span></div>`).join("")}</div>
        ${appLink(links.lifttrace, "Open session in LiftTrace", "btn lg")}</section>`
    : `<section class="panel"><p class="small muted">No active programme in LiftTrace.</p></section>`;

  const sessions = t.sessions.length
    ? `<section class="panel"><div class="panel-h"><h2>Recent sessions</h2></div><div class="list">${t.sessions.map((s) => `<div class="li"><span class="main">
        <span class="t">${esc(s.name || "Session")} · ${esc(s.date)}</span><span class="s">${s.exercises.map((e) => `${esc(e.name)} ${e.top ? fmtN(e.top) + " kg" : ""} × ${esc(e.reps.join(", "))}`).join(" · ")}</span></span>
        <span class="end"><span class="badge ${s.completed ? "ok" : "warn"}">${s.completed ? "Completed" : "Incomplete"}</span></span></div>`).join("")}</div></section>`
    : `<section class="panel"><h2>Recent sessions</h2><p class="small muted">No sessions yet.</p></section>`;

  const p = t.program;
  const programme = `<section class="panel"><div class="eyebrow">Current programme</div><h2 class="h2-serif" style="margin-bottom:6px">${esc(p.name || "No programme")}</h2>
    ${p.weeks ? `<p class="small muted">Week ${esc(p.current_week)} of ${esc(p.weeks)}</p>` : ""}
    <ol class="small" style="padding-left:18px;margin:12px 0 0">${(nx ? t.program.templates : []).map((x) => `<li>${esc(x.name)}</li>`).join("")}</ol></section>`;

  const records = r.length
    ? `<section class="panel"><h2 style="margin-bottom:8px">Personal bests</h2><div class="list">${r.map((x) => `<div class="li"><span class="main">
        <span class="t">${esc(x.name)}</span><span class="s">${esc(x.date || "")} · ${fmtN(x.weight)} kg × ${esc(x.reps)} · est. 1RM ${fmtN(x.e1rm)} kg</span></span></div>`).join("")}</div></section>`
    : "";

  return head + `<div class="stack s24"><div class="cols"><div class="stack s24">${next}${sessions}</div><div class="stack s24">${programme}${records}</div></div></div>`;
}

function openSetLogger(id, name, top) {
  S.setEx = /^\d+$/.test(id || "") ? { id: Number(id), name } : null;
  S.setDone = S.setDone || [];
  modal("Log a set", `<div class="form-grid">
    <div class="field full"><label for="set-q">Exercise</label>
      ${S.setEx ? `<div class="row-flex" style="gap:8px"><b id="set-ex">${esc(S.setEx.name)}</b><button type="button" class="btn ghost sm" data-act="setChange">Change</button></div>`
        : `<input id="set-q" class="inp" type="search" autocomplete="off" placeholder="Search LiftTrace exercises" value="${esc(name || "")}" autofocus><div id="set-res" style="margin-top:8px"></div>`}</div>
    <div class="field"><label for="set-reps">Reps</label><input id="set-reps" class="inp" type="number" inputmode="numeric" min="0" step="1"${S.setEx ? " autofocus" : ""}></div>
    <div class="field"><label for="set-kg">Weight (kg)</label><input id="set-kg" class="inp" type="number" inputmode="decimal" min="0" step="0.5" value="${esc(top || "")}"></div>
    <div class="field"><label for="set-rpe">RPE (optional)</label><input id="set-rpe" class="inp" type="number" inputmode="decimal" min="1" max="10" step="0.5"></div>
    <div class="field"><label class="row-flex" style="gap:8px;align-items:center;margin-top:26px"><input id="set-warm" type="checkbox">Warm-up set</label></div>
    <div class="field full" id="set-log">${setLogList()}</div>
  </div>`, `<button type="button" class="btn ghost" data-act="close">Done</button><button type="button" class="btn primary" data-act="setGo">Log set</button>`);
  if (!S.setEx && name) setSearch(name);
}

const fmtKg = (n) => (n == null || isNaN(n) ? "—" : String(Math.round(Number(n) * 100) / 100));
const setLogList = () => (S.setDone && S.setDone.length ? `<div class="eyebrow">Logged this time</div><div class="list">${S.setDone.map((x) => `<div class="li"><span class="main"><span class="t">${esc(x.exercise || "")}</span><span class="s">${x.warmup ? "Warm-up · " : ""}${esc(x.reps)} × ${fmtKg(x.weight)} kg${x.rpe ? " · RPE " + esc(x.rpe) : ""}</span></span></div>`).join("")}</div>` : "");

async function setSearch(q) {
  const box = $("#set-res");
  if (!box) return;
  try {
    const d = await api(`/api/health/search/exercises?q=${encodeURIComponent(q)}`);
    S.setFound = d.exercises || [];
    box.innerHTML = S.setFound.length ? `<div class="list">${S.setFound.slice(0, 8).map((x, i) => `<button type="button" class="li" data-act="setPickEx" data-arg="${i}" style="width:100%;text-align:left"><span class="main"><span class="t">${esc(x.name)}</span><span class="s">${esc(x.equipment || x.category || "")}</span></span></button>`).join("")}</div>` : `<p class="small muted">No exercise matches “${esc(q)}”.</p>`;
  } catch (e) { box.innerHTML = notConnected("LiftTrace", e.message); }
}

/* ---------- Meals & Shop ---------- */

async function screenMeals() {
  const d = await healthData("meals", "meals");
  if (!d) return healthDown("meals", "Meals & Shop");
  const links = d.links || {};
  const head = healthHead("meals", "Meals & Shop", appLink(links.cooktrace, "Open CookTrace", "btn"),
    "Plan meals, cook batches and shop for what's missing. Read-only for now.");
  const section = (title, part, render) => `<section class="panel"><h2 style="margin-bottom:8px">${title}</h2>${part && part.ok ? render(part.data) : notConnected("CookTrace", part && part.error)}</section>`;
  const byDay = (items) => {
    const groups = {};
    for (const x of items) (groups[x.date] = groups[x.date] || []).push(x);
    return Object.keys(groups).map((k) => `<div class="eyebrow" style="margin-top:12px">${esc(dayName(k))}</div><div class="list">${groups[k].map((x) =>
      `<div class="li"><span class="main"><span class="t">${esc(x.recipe || "Untitled")}</span><span class="s">${esc(x.meal_type || "")}${x.servings ? " · " + esc(x.servings) + " servings" : ""}</span></span>${x.rating ? `<span class="end small muted">${esc(x.rating)}/5</span>` : ""}</div>`).join("")}</div>`).join("") || `<p class="small muted">Nothing here.</p>`;
  };
  const shop = (items) => {
    const groups = {};
    for (const x of items) (groups[x.aisle || "Other"] = groups[x.aisle || "Other"] || []).push(x);
    return Object.keys(groups).map((a) => `<div class="eyebrow" style="margin-top:12px">${esc(a)}</div><div class="list">${groups[a].map((x) =>
      `<div class="li${x.checked ? " muted" : ""}"><span class="main"><span class="t">${esc(x.name)}</span><span class="s">${x.quantity != null ? esc(x.quantity) + (x.unit ? " " + esc(x.unit) : "") : ""}</span></span><span class="end">${x.checked ? `<span class="badge ok">Bought</span>` : ""}</span></div>`).join("")}</div>`).join("") || `<p class="small muted">Shopping list is empty.</p>`;
  };
  const recipes = (data) => `<div class="list">${data.items.map((x) => `<div class="li"><span class="main"><span class="t">${esc(x.name)}</span><span class="s">${x.servings ? esc(x.servings) + " servings" : ""}${x.kcal ? " · " + fmtN(x.kcal) + " kcal" : ""}</span></span></div>`).join("")}</div>${data.total > data.items.length ? `<p class="xs muted">Showing ${data.items.length} of ${fmtN(data.total)}.</p>` : ""}`;

  return head + `<div class="stack s24">
    <div class="cols even">
      ${section(`Planned · ${esc(dayName(d.week[0]))} – ${esc(dayName(d.week[1]))}`, d.planned, byDay)}
      ${section("Cooked recently", d.cooked, byDay)}
    </div>
    <div class="cols even">
      ${section("Shopping", d.shopping, shop)}
      ${section("Recipes", d.recipes, recipes)}
    </div>
    <p class="xs muted">A planned meal isn't an intake record. It only counts once it's logged in NutriTrace.</p>
  </div>`;
}

/* ---------- Progress ---------- */

function calorieChart(rows, target) {
  const W = 720, H = 240, L = 44, R = 12, T = 14, B = 34;
  const n = rows.length, bw = (W - L - R) / n;
  const max = Math.max((target || 2000) * 1.25, ...rows.map((r) => r.kcal || 0));
  const y = (v) => T + (1 - v / max) * (H - T - B);
  const ticks = [0, 1000, 2000].filter((v) => v < max);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Daily calories${target ? ` against a ${fmtN(target)} kcal target` : ""}">
    ${ticks.map((v) => `<line class="grid" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><text x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmtN(v)}</text>`).join("")}
    ${rows.map((r, i) => {
      const x = L + i * bw + 3, w = bw - 6;
      const lab = `<text x="${x + w / 2}" y="${H - 14}" text-anchor="middle">${+r.date.slice(8)}</text>`;
      if (r.status === "none" || r.status === "future") return `<rect x="${x}" y="${y(0) - 40}" width="${w}" height="40" fill="none" stroke="var(--line-2)" stroke-dasharray="3 3" rx="4"/><text x="${x + w / 2}" y="${y(0) - 46}" text-anchor="middle" style="font-size:9px">none</text>${lab}`;
      const fill = r.status === "logged" ? "var(--chart-1)" : "url(#hatch)";
      return `<rect x="${x}" y="${y(r.kcal || 0)}" width="${w}" height="${Math.max(0, y(0) - y(r.kcal || 0))}" fill="${fill}" rx="4"/>${lab}`;
    }).join("")}
    ${target ? `<line x1="${L}" x2="${W - R}" y1="${y(target)}" y2="${y(target)}" stroke="var(--fg)" stroke-width="1.5" stroke-dasharray="6 4"/><text x="${W - R}" y="${y(target) - 6}" text-anchor="end" style="fill:var(--fg)">Target ${fmtN(target)}</text>` : ""}
    <defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="color-mix(in oklab,var(--chart-1) 25%,transparent)"/><rect width="2.5" height="6" fill="var(--chart-1)"/></pattern></defs></svg>`;
}

async function screenProgress(n) {
  const d = await healthData("progress" + n, `progress?days=${n}`);
  if (!d) return healthDown("progress", "Progress");
  const links = d.links || {};
  const head = healthHead("progress", "Progress", "",
    "Intake and targets. Missing days stay visible and aren't averaged.");
  const seg = `<div class="row-flex">${[7, 14].map((k) => `<a class="btn sm${k === n ? " primary" : " ghost"}" href="#health/progress/${k}" aria-pressed="${k === n}">Last ${k} days</a>`).join("")}
    <span class="small muted">Averages use complete days only.</span></div>`;
  if (!d.nutrition.ok) return head + `<div class="stack s24">${seg}${notConnected("NutriTrace", d.nutrition.error)}</div>`;

  const nut = d.nutrition.data;
  const goals = d.goals && d.goals.ok ? d.goals.data : {};
  const avg = nut.avg || {};
  const est = d.estimate && d.estimate.ok ? d.estimate.data : null;
  const tile = (label, value, sub) => `<div class="panel"><div class="xs muted">${label}</div><div class="kpi" style="margin-top:6px"><b style="font-size:26px">${value}</b></div><p class="xs muted" style="margin-top:6px">${sub}</p></div>`;
  const pctOf = (v, t) => (v == null || !t ? "" : `${Math.round((100 * v) / t)}% of target`);
  const tiles = `<div class="tpl-grid" style="grid-template-columns:repeat(auto-fit,minmax(170px,1fr))">
    ${tile("Average calories", avg.kcal != null ? fmtN(avg.kcal) : "—", avg.kcal != null ? `${pctOf(avg.kcal, goals.kcal)} · ${nut.logged} complete days` : "No complete days yet")}
    ${tile("Average protein", avg.protein != null ? fmtN(avg.protein) + " g" : "—", pctOf(avg.protein, goals.protein))}
    ${tile("Average fibre", avg.fibre != null ? fmtN(avg.fibre) + " g" : "—", pctOf(avg.fibre, goals.fibre))}
    ${tile("Logging consistency", `${nut.logged}/${nut.of}`, "Complete days, today excluded")}
    ${tile("Expenditure", est ? fmtN(est.expenditure) : "—", est ? "kcal/day, from your estimate" : esc(d.estimate ? d.estimate.error : "Not connected"))}
    ${tile("Weight trend", d.weight && d.weight.ok ? fmtN(d.weight.data.trend_kg) + " kg" : "—",
      d.weight && d.weight.ok ? `trend ${d.weight.data.weekly_change_kg > 0 ? "+" : ""}${esc(d.weight.data.weekly_change_kg)} kg a week` : esc(d.weight ? d.weight.error : "Not connected"))}
  </div>`;

  const rows = nut.days;
  const chart = `<section class="panel"><div class="panel-h"><h2>Daily calories</h2></div>
    <div class="chart-wrap">${calorieChart(rows, goals.kcal)}</div>
    <div class="legend" style="margin-top:8px"><span><svg width="14" height="10"><rect width="14" height="10" rx="3" fill="var(--chart-1)"/></svg>Complete day</span><span><svg width="14" height="10"><rect width="14" height="10" rx="3" fill="url(#hatch)" stroke="var(--chart-1)"/></svg>Partial or open day</span><span><svg width="14" height="10"><rect x=".5" y=".5" width="13" height="9" rx="3" fill="none" stroke="var(--fg-3)" stroke-dasharray="2 2"/></svg>No record</span></div></section>`;

  const macros = `<section class="panel"><h2 style="margin-bottom:12px">Macros vs target · average</h2><div class="stack s8">
    ${[["protein", "Protein"], ["carbs", "Carbohydrate"], ["fat", "Fat"], ["fibre", "Fibre"]].map(([k, l]) => `<div class="macro"><div class="l" style="flex-direction:row;justify-content:space-between"><span>${l}</span><span>${fmtN(avg[k])} / ${fmtN(goals[k])} g</span></div>${hBar(avg[k], goals[k])}</div>`).join("")}
    </div></section>`;

  const log = `<section class="panel"><div class="panel-h"><h2>Days</h2></div><div class="list">${rows.slice().reverse().map((r) =>
    `<a class="li" href="#health/food/${r.date}"><span class="main"><span class="t">${esc(dayName(r.date))}</span><span class="s">${r.status === "logged" ? `${fmtN(r.kcal)} kcal · P ${fmtN(r.protein)} g` : r.status === "open" ? "Today, still open" : "No record"}</span></span><span class="end">${ic("chev", 16)}</span></a>`).join("")}</div>
    ${appLink(links.nutritrace, "See the full history in NutriTrace", "btn sm ghost")}</section>`;

  const hist = d.history && d.history.ok ? d.history.data : null;
  S.progHistory = hist;
  const trends = hist ? `<div class="cols even">
    <section class="panel"><div class="panel-h"><div><h2>Weight</h2><p class="xs muted">Last ${hist.length} days · dots are weigh-ins, the line is the trend</p></div></div><div class="uchart" id="ch-weight"></div></section>
    <section class="panel"><div class="panel-h"><div><h2>Expenditure and intake</h2><p class="xs muted">Line is the estimate the app had each day; bars are intake</p></div></div><div class="uchart" id="ch-energy"></div></section></div>`
    : `<p class="small muted">Weight and expenditure charts: ${esc(d.history ? d.history.error : "not connected")}.</p>`;

  return head + `<div class="stack s24">${seg}${tiles}${trends}${chart}<div class="cols even">${macros}${log}</div>
    ${hNotice(d.goals, "The goals")}</div>`;
}

/* ---------- Inbox (Phase 5): Craig's own mail, read-only ----------
   Shown only to Craig. Mail is kept in memory, never in localStorage, and
   nothing here goes to Max or any model. */

async function inboxApi(path) {
  try {
    return await api(path);
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function screenInbox(rest) {
  const head = () => `<div class="ph"><div class="ph-t"><h1>Inbox</h1><p class="sub">Your mail, shown to you only. It never goes to Max or any model.</p></div></div>`;
  if (rest[0] === "m" && rest[1]) {
    const m = await inboxApi(`/api/inbox/${encodeURIComponent(rest[1])}`);
    const back = `<a class="btn ghost sm" href="#inbox${S.inboxView === "unread" ? "/unread" : ""}">${ic("back", 14)}Back</a>`;
    if (!m.ok) return head() + `<div class="btns">${back}</div>` + notConnected("Gmail", m.error);
    return head() + `<div class="btns" style="margin-bottom:12px">${back}</div>
      <section class="panel"><h2 style="margin-bottom:6px">${esc(m.subject)}</h2>
      <p class="small muted" style="margin-bottom:12px">From ${esc(m.from)}${m.date ? " · " + esc(when(m.date)) : ""}</p>
      ${m.files && m.files.length ? `<p class="xs muted" style="margin-bottom:12px">Attachments (not opened here): ${m.files.map(esc).join(", ")}</p>` : ""}
      <div style="white-space:pre-wrap;line-height:1.6;overflow-wrap:anywhere">${esc(m.text || "(no text part)")}</div></section>`;
  }
  const view = rest[0] === "unread" ? "unread" : "inbox";
  S.inboxView = view;
  const d = await inboxApi(`/api/inbox?view=${view}`);
  const tabs = `<nav class="tabs" aria-label="Inbox views"><a href="#inbox"${view === "inbox" ? ' aria-current="page"' : ""}>All</a><a href="#inbox/unread"${view === "unread" ? ' aria-current="page"' : ""}>Unread</a></nav>`;
  if (!d.ok) return head() + tabs + notConnected("Gmail", d.error);
  const rows = d.messages.map((m) => `<a class="li" href="#inbox/m/${esc(m.id)}">
      <span class="main"><span class="t">${m.unread ? "<b>" : ""}${esc(m.subject)}${m.unread ? "</b>" : ""}</span>
      <span class="s">${esc(m.from)} · ${esc(m.snippet)}</span></span>
      <span class="end small muted">${m.date ? esc(when(m.date)) : ""}</span></a>`).join("");
  return head() + tabs + `<section class="panel"><div class="list">${rows || `<p class="small muted">Nothing here.</p>`}</div></section>`;
}

/* ---------- Planner (Phase 5): Craig's Google Calendar, read-only ----------
   Shown only to Craig. Events are kept in memory only, and nothing here goes
   to Max or any model. Changes are made in Google Calendar itself. */

const londonToday = () => new Date().toLocaleDateString("en-CA", { timeZone: "Europe/London" });

const minOf = (iso) => (iso ? +iso.slice(11, 13) * 60 + +iso.slice(14, 16) : 0);
const hmOf = (m) => `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
const evTime = (e) => (e.all_day ? "All day" : `${hhmm(e.start)}${e.end ? "–" + hhmm(e.end) : ""}`);

// Side-by-side lanes for overlapping events in one day (from the mockup).
function layoutDay(evs) {
  const timed = evs.filter((e) => !e.all_day).map((e) => {
    const s = minOf(e.start), en = e.end && e.end.slice(0, 10) === e.start.slice(0, 10) ? minOf(e.end) : 24 * 60;
    return { e, s, en: en > s ? en : s + 30 };
  }).sort((a, b) => a.s - b.s || b.en - a.en);
  let cluster = [], lanes = [], clusterEnd = -1;
  const out = [];
  const flush = () => { cluster.forEach((c) => { c.cols = lanes.length; out.push(c); }); cluster = []; lanes = []; };
  timed.forEach((t) => {
    if (t.s >= clusterEnd) flush();
    let lane = lanes.findIndex((end) => end <= t.s);
    if (lane < 0) { lane = lanes.length; lanes.push(t.en); } else lanes[lane] = t.en;
    t.lane = lane; cluster.push(t); clusterEnd = Math.max(clusterEnd, t.en);
  });
  flush();
  return out;
}

function timeGrid(days, today, week) {
  const H0 = 7, H1 = 22, HR = days.length === 1 ? 56 : 46, N = days.length;
  const y = (m) => (Math.min(Math.max(m, H0 * 60), H1 * 60) - H0 * 60) / 60 * HR;
  const evBtn = (e, cls, style) => `<button type="button" class="tg-ev ev-c-external ${cls}"${style ? ` style="${style}"` : ""} data-act="planEvent" data-arg="${esc(e.key)}" aria-label="${esc(e.title)}, ${esc(evTime(e))}"><b><span class="mk external" aria-hidden="true"></span>${esc(e.title)}</b>${cls === "allday" || (cls === "short" && N > 1) ? "" : `<span>${esc(evTime(e))}</span>`}</button>`;
  const head = `<div class="tg-head" style="--days:${N}"><div></div>${days.map((d) => `<div class="${d.date === today ? "today" : ""}"><a href="#planner/${week}/${d.date}" style="text-decoration:none;color:inherit;display:block" aria-label="Open ${esc(dayName(d.date))}">${esc(dayShort(d.date))}<b>${+d.date.slice(8)}</b></a></div>`).join("")}</div>`;
  const allday = `<div class="tg-allday" style="--days:${N}"><div>All day</div>${days.map((d) => `<div>${d.events.filter((e) => e.all_day).map((e) => evBtn(e, "allday")).join("")}</div>`).join("")}</div>`;
  const hours = [];
  for (let h = H0 + 1; h < H1; h++) hours.push(`<span style="top:${(h - H0) * HR}px">${String(h).padStart(2, "0")}:00</span>`);
  const nowM = minOf("0000-00-00T" + nowHHMM());
  const cols = days.map((d) => {
    const evs = layoutDay(d.events).map((t) => {
      const w = 100 / t.cols;
      const hgt = Math.max(24, y(t.en) - y(t.s) - 3);
      return evBtn(t.e, hgt < 38 ? "short" : "", `top:${y(t.s) + 1}px;height:${hgt}px;left:calc(${t.lane * w}% + 3px);width:calc(${w}% - 6px)`);
    }).join("");
    const now = d.date === today && nowM >= H0 * 60 && nowM <= H1 * 60 ? `<div class="tg-now" style="top:${y(nowM)}px" aria-hidden="true"></div>` : "";
    return `<div class="tg-col ${d.date === today ? "today" : ""}" style="--hr:${HR}px">${evs}${now}</div>`;
  }).join("");
  return `<div class="tg" role="region" aria-label="${N === 1 ? esc(dayName(days[0].date)) : "Week"}">${head}${allday}<div class="tg-body" style="--days:${N};--h:${(H1 - H0) * HR}px"><div class="tg-hours" aria-hidden="true">${hours.join("")}</div>${cols}</div></div>`;
}

function planEvent(e) {
  return `<button type="button" class="ev" data-act="planEvent" data-arg="${esc(e.key)}"><span class="mk external" aria-hidden="true"></span>
    <span style="flex:1;min-width:0"><b style="font-weight:550">${esc(e.title)}</b>${e.location ? `<span class="xs muted" style="display:block">${esc(e.location)}</span>` : ""}</span>
    <span class="when">${esc(evTime(e))}</span></button>`;
}

function openPlanEvent(key) {
  const e = (S.planEvents || {})[key];
  if (!e) return;
  modal(e.title, `<div class="row-flex"><span class="mk external" style="width:12px;height:12px"></span><b>Calendar</b>${badge("line", "Google Calendar")}</div>
    <dl class="kv"><dt>When</dt><dd>${esc(dayName(e.start.slice(0, 10)))} · ${esc(evTime(e))}</dd><dt>Time zone</dt><dd>Europe/London</dd>${e.location ? `<dt>Where</dt><dd>${esc(e.location)}</dd>` : ""}<dt>Calendar</dt><dd class="mono">${esc(e.calendar)}</dd></dl>
    <p class="small muted">Read-only here. Change it in Google Calendar.</p>`,
    `<button type="button" class="btn primary" data-act="close">Close</button>`);
}

// Overlapping timed events in a day, as pairs.
function overlaps(days) {
  const out = [];
  for (const d of days) {
    const t = d.events.filter((e) => !e.all_day && e.end);
    t.forEach((a, i) => t.slice(i + 1).forEach((b) => { if (minOf(a.start) < minOf(b.end) && minOf(b.start) < minOf(a.end)) out.push([d.date, a, b]); }));
  }
  return out;
}

async function screenPlanner(rest) {
  const week = Math.max(-4, Math.min(12, parseInt(rest[0] || "0", 10) || 0));
  const today = londonToday();
  const dow = (new Date(today + "T12:00:00").getDay() + 6) % 7; // Monday = 0
  const monday = shiftDay(today, 7 * week - dow);
  const daySel = isoDay(rest[1]);
  const head = `<div class="ph"><div class="ph-t"><h1>Planner</h1><p class="sub">Your calendar, shown to you only. It never goes to Max or any model.</p></div></div>`;
  const d = await inboxApi(`/api/planner?start=${monday}&days=7`);
  const label = week === 0 ? "This week" : week === 1 ? "Next week" : week === -1 ? "Last week" : `${dayName(monday)} – ${dayName(shiftDay(monday, 6))}`;
  const seg = `<div class="seg" role="group" aria-label="View"><a class="btn sm${daySel ? " ghost" : ""}" href="#planner/${week}"${daySel ? "" : ' aria-current="page"'}>Week</a><a class="btn sm${daySel ? "" : " ghost"}" href="#planner/${week}/${daySel || (week === 0 ? today : monday)}"${daySel ? ' aria-current="page"' : ""}>Day</a></div>`;
  const nav = `<div class="row-flex">${seg}
    <div class="btns" style="gap:4px"><a class="iconbtn" href="#planner/${week - 1}" aria-label="Previous week">${ic("back")}</a>
    <b class="small" style="min-width:9em;text-align:center">${esc(label)}</b>
    <a class="iconbtn" href="#planner/${week + 1}" aria-label="Next week">${ic("chev")}</a></div>
    <span class="small muted">Europe/London</span><div class="spacer"></div>${week ? `<a class="btn ghost sm" href="#planner">Today</a>` : ""}</div>`;
  if (!d.ok) return head + `<div class="stack s24">${nav}${notConnected("Google Calendar", d.error)}</div>`;
  S.planEvents = {};
  d.days.forEach((day) => day.events.forEach((e, i) => { e.key = `${day.date}-${i}`; S.planEvents[e.key] = e; }));
  const sel = d.days.find((x) => x.date === daySel) || d.days.find((x) => x.date === today) || d.days[0];
  const chips = `<div class="chips">${d.days.map((x) => `<a class="chip" href="#planner/${week}/${x.date}" aria-pressed="${x.date === sel.date}">${esc(dayName(x.date))}</a>`).join("")}</div>`;
  const dayList = sel.events.length ? `<div class="stack s8">${sel.events.map(planEvent).join("")}</div>`
    : `<div class="empty"><h3>Nothing on</h3><p>Nothing is in your calendar for ${esc(dayName(sel.date))}.</p></div>`;
  const clash = overlaps(daySel ? [sel] : d.days).map(([day, a, b]) => `<div class="notice warn">${ic("alert", 16)}<div><b>Overlap on ${esc(dayName(day))}:</b> ${esc(a.title)} (${esc(evTime(a))}) and ${esc(b.title)} (${esc(evTime(b))}).</div></div>`).join("");
  const legend = `<div class="legend"><span><span class="mk external"></span>Calendar</span></div>`;
  return head + `<div class="stack s24">${nav}${clash}${legend}
    <div class="desk-only">${daySel ? `${chips}<div class="cols" style="margin-top:12px">${timeGrid([sel], today, week)}${dayList}</div>` : timeGrid(d.days, today, week)}</div>
    <div class="phone-only">${chips}<div style="margin-top:12px">${dayList}</div></div></div>`;
}

/* ---------- router ---------- */

let renderSeq = 0;
async function render() {
  const route = (location.hash || "#today").slice(1) || "today";
  const [area, ...rest] = route.split("/");
  const seq = ++renderSeq;
  if (!(area === "health" && rest[0] === "scan")) stopScan();
  renderShell(route);
  let html;
  if (area === "today") html = await screenToday();
  else if (area === "work") html = await screenWork(rest.join("/"));
  else if (area === "agents") html = rest[0] ? await screenAgent(rest[0]) : await screenAgents();
  else if (area === "routines") html = rest[0] ? await screenRoutine(rest[0]) : await screenRoutines();
  else if (area === "approvals") html = await screenApprovals(rest.join("/"));
  else if (area === "system") html = await screenSystem(rest[0]);
  else if (area === "max") html = await screenMax(rest[0]);
  else if (area === "more") html = screenMore();
  else if (area === "soon") html = screenSoon(rest[0]);
  else if (area === "health") html = await screenHealth(rest);
  else if (area === "inbox") html = await screenInbox(rest);
  else if (area === "planner") html = await screenPlanner(rest);
  else if (area === "library") html = await screenLibrary(rest);
  else html = await screenToday();
  if (seq !== renderSeq) return; // a newer render started
  if (!S.cache.approvals && area !== "approvals") load("approvals", "/api/approvals").then(() => renderShell(route));
  renderShell(route);
  $("#main").innerHTML = html;
  if (area === "health" && rest[0] === "scan") startScan();
  if (area === "work" && !rest.length && S.workView === "board") wireBoard();
  if (area === "health" && rest[0] === "progress") drawProgressCharts();
  if (area === "max") {
    chatScroll();
    // An answer still running on the server (phone slept, page reloaded): check back.
    clearTimeout(S.chatPoll);
    if (!S.chatLive && S.chat && S.chat.busy) S.chatPoll = setTimeout(() => { if ((location.hash || "").startsWith("#max")) render(); }, 3000);
  }
}

async function init() {
  try { S.me = await api("/api/me"); S.meta = { demo: S.me.demo, board_url: S.me.board_url, broker_url: S.me.broker_url, stop_ready: S.me.stop_ready }; } catch (_) { S.offline = true; }
  window.addEventListener("hashchange", () => { closeModal(); window.scrollTo(0, 0); render(); });
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
