// Hermes app, Phase 1: Today summary and System status. Read-only.
"use strict";

const LAST_KEY = "hermes:last-status";
const LABEL = { ok: "Up", down: "Down", unknown: "Unknown" };
const VIEWS = ["today", "system"];

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}

function timeText(sec) {
  if (!sec) return "";
  const d = new Date(sec * 1000);
  return "Checked at " + d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

function saveLast(data) {
  try { localStorage.setItem(LAST_KEY, JSON.stringify(data)); } catch (_) { /* private mode */ }
}
function loadLast() {
  try { return JSON.parse(localStorage.getItem(LAST_KEY) || "null"); } catch (_) { return null; }
}

function show(view) {
  if (!VIEWS.includes(view)) view = "today";
  for (const v of VIEWS) $("view-" + v).hidden = v !== view;
  document.querySelectorAll("[data-view]").forEach((a) => {
    if (a.dataset.view === view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
}

function renderSummary(data, offline) {
  const list = (data && data.services) || [];
  const down = list.filter((s) => s.state === "down");
  const unknown = list.filter((s) => s.state === "unknown");
  let line;
  if (!list.length) line = offline ? "Can't reach the server" : "No services set up yet";
  else if (down.length) line = down.map((s) => s.name).join(", ") + (down.length === 1 ? " is down" : " are down");
  else if (unknown.length) line = `All checked services are up; ${unknown.length} not known`;
  else line = `All ${list.length} services are up`;
  if (offline && list.length) line = "Offline. Last check: " + line;
  $("summary-line").textContent = line;
  $("summary-time").textContent = data ? timeText(data.checked_at) : "";
}

function renderSystem(data, offline) {
  $("offline").hidden = !offline;
  $("system-time").textContent = data ? timeText(data.checked_at) : "";
  const root = $("groups");
  root.replaceChildren();
  const groups = new Map();
  for (const s of (data && data.services) || []) {
    if (!groups.has(s.group)) groups.set(s.group, []);
    groups.get(s.group).push(s);
  }
  for (const [name, items] of groups) {
    const g = el("section", "group");
    g.append(el("h2", null, name));
    const rows = el("div", "rows");
    for (const s of items) {
      const r = el("div", "row");
      r.append(el("span", "row-name", s.name));
      r.append(el("span", "pill " + s.state, s.label || LABEL[s.state] || "Unknown"));
      r.append(el("span", "row-detail", s.detail || ""));
      rows.append(r);
    }
    g.append(rows);
    root.append(g);
  }
}

async function getJSON(url) {
  const res = await fetch(url, { cache: "no-store", credentials: "same-origin" });
  if (!res.ok) throw new Error(url + " " + res.status);
  return res.json();
}

async function refresh() {
  const btn = $("refresh");
  btn.disabled = true;
  try {
    const data = await getJSON("/api/status");
    saveLast(data);
    renderSummary(data, false);
    renderSystem(data, false);
  } catch (_) {
    const last = loadLast();
    renderSummary(last, true);
    renderSystem(last, true);
  } finally {
    btn.disabled = false;
  }
}

async function whoami() {
  try {
    const me = await getJSON("/api/me");
    const first = (me.name || me.login).split(/[ @]/)[0];
    $("who").textContent = `Signed in through Tailscale as ${me.login} · v${me.version}`;
    $("greeting").textContent = greeting() + ", " + first;
  } catch (_) {
    $("who").textContent = "Sign-in not confirmed (offline?)";
  }
}

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

function init() {
  $("today-date").textContent = new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" });
  $("greeting").textContent = greeting();
  $("refresh").addEventListener("click", refresh);
  window.addEventListener("hashchange", () => show(location.hash.slice(1)));
  show(location.hash.slice(1));
  whoami();
  refresh();
  setInterval(() => { if (!document.hidden) refresh(); }, 60000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
}

init();
