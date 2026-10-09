#!/usr/bin/env python3
"""Hermes app server (Phase 1).

Serves the Hermes web app (PWA) and a small read-only API. Standard library
only, so nothing needs installing on the server.

It listens on loopback and is reached through `tailscale serve`, which adds
the Tailscale-User-Login header for the signed-in tailnet user. Requests
without an allowed login are refused, so there is no sign-in form.

It reports whether the existing services are up, reads the Paperclip board
and the approval broker, and takes a few of Craig's own actions (see api.py).
It holds no send power and no model keys.
"""

import argparse
import datetime
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from api import ActionError, App
from sources import SourceError
from artifacts import ArtifactError, Artifacts
from chat import ChatError, DemoMax, MaxChat
from demo import Demo
from health import Health, Refused
from health_log import HealthLog
from strategy import Strategy
from mealplan import MealPlan, ask_max
import demo_health
from inbox import Gmail, SampleMail
from planner import Calendar, SampleCalendar
from max_library import LibraryError, MaxLibrary, SampleLibrary
import foods as food_lookup
import meal_estimate
from foods import Foods, Lookup, SavedFoods

VERSION = "0.8.0"
STREAMED = object()  # a handler already wrote the response
HERE = Path(__file__).resolve().parent
DEFAULT_WEB = HERE.parent / "web"

# Result states. Unknown is its own state and never shown as up or down.
OK, DOWN, UNKNOWN = "ok", "down", "unknown"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; font-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(self), microphone=(), geolocation=()",
}

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("font/woff2", ".woff2")


def load_config(path):
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg.setdefault("listen_host", "127.0.0.1")
    cfg.setdefault("listen_port", 3010)
    cfg.setdefault("allowed_logins", [])
    cfg.setdefault("services", [])
    cfg.setdefault("cache_seconds", 15)
    if not cfg["allowed_logins"]:
        raise ValueError("allowed_logins must list at least one Tailscale login")
    return cfg


# ---------------------------------------------------------------- checks

def check_http(svc, timeout=3.0):
    """Up if the URL answers with any status below 500."""
    req = urllib.request.Request(svc["url"], method="GET",
                                 headers={"User-Agent": "hermes-app-check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, OSError, ValueError) as e:
        return DOWN, f"no answer ({type(e).__name__})"
    if code < 500:
        return OK, f"answered {code}"
    return DOWN, f"answered {code}"


def check_systemd(svc, runner=subprocess.run):
    """Up if `systemctl is-active` says active. User units use --user."""
    cmd = ["systemctl"]
    if svc.get("user_unit"):
        cmd.append("--user")
    cmd += ["is-active", svc["unit"]]
    try:
        out = runner(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        return UNKNOWN, f"can't ask systemd ({type(e).__name__})"
    state = (out.stdout or "").strip()
    if state == "active":
        return OK, "active"
    if state in ("inactive", "failed", "deactivating"):
        return DOWN, state
    return UNKNOWN, state or "no answer"


def check_last_run(svc, runner=subprocess.run, now=None):
    """For a timer-run job: Up if its last run succeeded recently enough.

    Uses `systemctl show`, which needs no extra permissions (the journal would).
    After a reboot systemd forgets the last run, so this shows Unknown until
    the next one.
    """
    now = now if now is not None else time.time()
    cmd = ["systemctl", "show", svc["unit"], "--timestamp=unix",
           "-p", "Result", "-p", "ExecMainExitTimestamp", "-p", "ExecMainStatus"]
    try:
        out = runner(cmd, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError) as e:
        return UNKNOWN, f"can't ask systemd ({type(e).__name__})"
    props = dict(line.split("=", 1) for line in (out.stdout or "").splitlines() if "=" in line)
    stamp = props.get("ExecMainExitTimestamp", "").lstrip("@")
    if not stamp.replace(".", "", 1).isdigit():
        return UNKNOWN, "no run since the server restarted"
    age_h = (now - float(stamp)) / 3600
    if props.get("Result") != "success" or props.get("ExecMainStatus", "0") != "0":
        return DOWN, f"last run failed ({props.get('Result', '?')}), {age_h:.0f}h ago"
    if age_h > float(svc.get("max_age_hours", 26)):
        return DOWN, f"last run {age_h:.0f}h ago"
    return OK, f"last run {age_h:.0f}h ago" if age_h >= 1 else "ran within the hour"


def check_file_age(svc, now=None):
    """Up if the newest file matching `glob` in `dir` is recent enough."""
    now = now if now is not None else time.time()
    d = Path(svc["dir"])
    try:
        if not d.is_dir():
            return UNKNOWN, "folder not readable"
        files = [p for p in d.glob(svc.get("glob", "*")) if p.is_file()]
    except OSError as e:
        return UNKNOWN, f"can't read ({type(e).__name__})"
    if not files:
        return DOWN, "no files yet"
    newest = max(p.stat().st_mtime for p in files)
    hours = (now - newest) / 3600
    limit = float(svc.get("max_age_hours", 26))
    detail = f"newest {hours:.1f} h old"
    return (OK if hours <= limit else DOWN), detail


def check_flag(svc):
    """A flag file: present means the named state is on (e.g. kill switch)."""
    p = Path(svc["path"])
    try:
        present = p.exists()
    except OSError as e:
        return UNKNOWN, f"can't read ({type(e).__name__})"
    if present:
        return DOWN, svc.get("on_text", "on")
    return OK, svc.get("off_text", "off")


CHECKS = {
    "http": check_http,
    "systemd": check_systemd,
    "file_age": check_file_age,
    "last_run": check_last_run,
    "flag": check_flag,
}


def run_check(svc):
    fn = CHECKS.get(svc.get("kind"))
    if fn is None:
        return UNKNOWN, "not set up yet"
    try:
        return fn(svc)
    except KeyError as e:
        return UNKNOWN, f"missing setting {e}"


class StatusCache:
    def __init__(self, services, ttl):
        self.services = services
        self.ttl = ttl
        self._lock = threading.Lock()
        self._at = 0.0
        self._data = None

    def forget(self):
        with self._lock:
            self._data = None

    def get(self):
        with self._lock:
            if self._data and time.time() - self._at < self.ttl:
                return self._data
            results = []
            for svc in self.services:
                state, detail = run_check(svc)
                results.append({
                    "id": svc.get("id") or svc.get("name", "?"),
                    "name": svc.get("name", "?"),
                    "group": svc.get("group", "Services"),
                    "state": state,
                    "label": svc.get("labels", {}).get(state),
                    "detail": detail,
                })
            self._at = time.time()
            self._data = {"checked_at": int(self._at), "services": results}
            return self._data


# ---------------------------------------------------------------- HTTP

MAX_BODY = 16 * 1024
MAX_PHOTO_BODY = 1_700_000  # a shrunk meal or label photo, base64
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


DEMO_PAGE = """<!doctype html><title>Desktop options one-pager</title>
<style>body{font:15px/1.5 system-ui;margin:24px;color:#1b1b1b}h1{font-size:22px}.c{display:flex;gap:12px}
.c div{border:1px solid #ddd;border-radius:10px;padding:12px;flex:1}</style>
<h1>Hermes on the desktop</h1><p>Sample page made by the sample-data Max.</p>
<div class="c"><div><b>PWA</b><br>No install, browser sandbox.</div><div><b>Tauri</b><br>Small, full offline.</div>
<div><b>Electron</b><br>Most mature, heaviest.</div></div><script>document.body.style.background='red'</script>"""


def make_artifacts(cfg, app):
    folder = (cfg.get("max_chat") or {}).get("artifacts_dir")
    if app.demo and not folder:
        folder = tempfile.mkdtemp(prefix="hermes-demo-artifacts-")
        Path(folder, "desktop-options.html").write_text(DEMO_PAGE, encoding="utf-8")
    return Artifacts(folder) if folder else None


def make_chat(cfg, app, artifacts=None):
    """Chat with Max, if configured (or scripted in sample-data mode)."""
    mc = cfg.get("max_chat")
    if app.demo:
        folder = (mc or {}).get("store_dir") or tempfile.mkdtemp(prefix="hermes-demo-chat-")
        backend = DemoMax(artifacts.dir if artifacts else None)
        return MaxChat({"store_dir": folder}, backend=backend, audit=app.audit, artifacts=artifacts)
    if not mc:
        return None
    return MaxChat(mc, audit=app.audit, artifacts=artifacts)


def make_health(cfg, app):
    """Read-only health screens, if the Trace apps are configured."""
    if app.demo:
        return Health(demo_health.sample_config(), opener=demo_health.make_opener(_today_london))
    hc = cfg.get("health")
    return Health(hc) if hc else None


def make_inbox(cfg, app):
    """Craig's real Gmail, read-only, if signed in (sample mail in sample-data mode)."""
    if app.demo:
        return SampleMail()
    ic = cfg.get("inbox")
    return Gmail(ic) if ic else None


def make_planner(cfg, app):
    """Craig's Google Calendar, read-only, if signed in (a sample week in sample-data mode)."""
    if app.demo:
        return SampleCalendar(today=_today_london)
    pc = cfg.get("planner")
    return Calendar(pc) if pc else None


def make_library(cfg, app):
    """Max's memory and skills, read from the exporter's feed (a sample in sample-data mode)."""
    if app.demo:
        return SampleLibrary()
    feed = (cfg.get("library") or {}).get("feed")
    return MaxLibrary(feed) if feed else None


def make_foods(cfg, app):
    """Barcode lookup and saved foods for the Food screen (sample data in sample-data mode)."""
    if app.demo:
        return Foods(Lookup({"off_enabled": True}, opener=food_lookup.sample_opener, usda_key="sample"))
    fc = cfg.get("foods")
    if not fc:
        return None
    return Foods(Lookup(fc), SavedFoods(fc.get("store_path")))


def make_strategy(cfg, app, health):
    """Calorie and macro targets plus the weekly check-in, kept in a file next to
    the saved foods (in memory in sample-data mode)."""
    if health is None:
        return None
    if app.demo:
        return Strategy(None, health)
    path = (cfg.get("health") or {}).get("strategy_file")
    if not path:
        # Next to the app's other files: the saved foods, or else the action log.
        near = (cfg.get("foods") or {}).get("store_path") or cfg.get("audit_log")
        path = str(Path(near).with_name("strategy.json")) if near else None
    return Strategy(path, health)


def make_mealplan(cfg, app, health, hlog, strategy=None):
    """The meal-plan week, kept in a file next to the strategy (in memory in sample-data mode)."""
    if health is None:
        return None
    if app.demo:
        return MealPlan(None, health, hlog, strategy)
    path = (cfg.get("health") or {}).get("meal_plan_file")
    if not path:
        near = (cfg.get("foods") or {}).get("store_path") or cfg.get("audit_log")
        path = str(Path(near).with_name("meal-plan.json")) if near else None
    return MealPlan(path, health, hlog, strategy)


def _today_london():
    return Health({}).today()


def make_handler(cfg, web_root, cache, app=None, chat=None, artifacts=None, health=None, inbox=None, foods=None, planner=None, library=None, strategy=None, mealplan=None):
    allowed = {x.lower() for x in cfg["allowed_logins"]}
    web_root = Path(web_root).resolve()
    if app is None:
        app = App(cfg, cache)
    if artifacts is None:
        artifacts = make_artifacts(cfg, app)
    if chat is None:
        chat = make_chat(cfg, app, artifacts)
    if health is None:
        health = make_health(cfg, app)
    if inbox is None:
        inbox = make_inbox(cfg, app)
    if foods is None:
        foods = make_foods(cfg, app)
    if planner is None:
        planner = make_planner(cfg, app)
    if library is None:
        library = make_library(cfg, app)
    library_cfg = cfg.get("library") or {}
    hlog = HealthLog(health) if health is not None else None
    if strategy is None:
        strategy = make_strategy(cfg, app, health)
    if mealplan is None:
        mealplan = make_mealplan(cfg, app, health, hlog, strategy)

    get_routes = {
        "/api/today": app.today,
        "/api/agents": app.agents,
        "/api/work": app.work,
        "/api/approvals": app.approvals,
        "/api/routines": app.routines,
        "/api/spending": app.spending,
        "/api/status": cache.get,
        "/api/connections": app.connections,
    }

    class Handler(BaseHTTPRequestHandler):
        server_version = "hermes-app"
        sys_version = ""

        def log_message(self, fmt, *args):  # quiet, no request bodies
            if os.environ.get("HERMES_APP_LOG"):
                super().log_message(fmt, *args)

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self._send(code, body, "application/json",
                       {"Cache-Control": "no-store"})

        def _user(self):
            login = (self.headers.get("Tailscale-User-Login") or "").strip()
            if login.lower() in allowed:
                return {"login": login,
                        "name": self.headers.get("Tailscale-User-Name") or login}
            return None

        def _refuse(self):
            self._send(HTTPStatus.FORBIDDEN,
                       b"Hermes: open this through Tailscale on one of Craig's devices.\n",
                       "text/plain; charset=utf-8")

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            # Static app shell files carry nothing private, but everything
            # still needs an allowed tailnet login.
            user = self._user()
            if user is None:
                self._refuse()
                return
            if path == "/api/me":
                self._json(200, {**user, "version": VERSION, **app.meta(), "chat_ready": chat is not None})
            elif path == "/api/artifacts" or path.startswith("/api/artifacts/"):
                self._artifact_get(path[len("/api/artifacts"):].strip("/"))
            elif path == "/api/chat" or path.startswith("/api/chat/"):
                self._chat_get(path[len("/api/chat"):].strip("/"))
            elif path == "/api/inbox" or path.startswith("/api/inbox/"):
                self._inbox_get(path[len("/api/inbox"):].strip("/"))
            elif path == "/api/planner":
                self._planner_get()
            elif path == "/api/library" or path.startswith("/api/library/skill/"):
                self._library_get(urllib.parse.unquote(path[len("/api/library/skill/"):]) if "/skill/" in path else None)
            elif path in ("/api/health/foods", "/api/health/food-search") or path.startswith("/api/health/barcode/"):
                self._foods_get(path[len("/api/health/"):])
            elif path.startswith("/api/health/"):
                self._health_get(path[len("/api/health/"):])
            elif path in get_routes:
                self._json(200, get_routes[path]())
            elif re.fullmatch(r"/api/(work|runs|agents|routines)/[A-Za-z0-9_-]{1,64}", path):
                kind, rid = path.split("/")[2:4]
                out = {"work": app.issue, "runs": app.run, "agents": app.agent, "routines": app.routine}[kind](rid)
                self._json(200 if out["item"]["ok"] or out["item"]["error"] != "not found" else 404, out)
            elif path.startswith("/api/"):
                self._json(404, {"error": "not found"})
            else:
                self._static(path)

        def _same_origin(self):
            # Actions must come from the app itself: a custom header (which a
            # cross-site form can't send) and, when present, a matching Origin.
            if self.headers.get("X-Hermes-Action") != "1":
                return False
            origin = self.headers.get("Origin")
            if origin:
                host = self.headers.get("Host", "")
                return origin.split("://", 1)[-1] == host
            return True

        def do_POST(self):
            user = self._user()
            if user is None:
                self._refuse()
                return
            if not self._same_origin():
                self._json(403, {"error": "Actions must come from the Hermes app."})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            limit = MAX_PHOTO_BODY if self.path.split("?", 1)[0] == "/api/health/log/photo" else MAX_BODY
            if length < 0 or length > limit:
                self._json(413, {"error": "Request too large."})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                self._json(400, {"error": "Body must be a JSON object."})
                return
            parts = self.path.split("?", 1)[0].strip("/").split("/")
            try:
                if parts[:2] == ["api", "chat"]:
                    result = self._chat_post(user, parts[2:], body)
                elif parts == ["api", "health", "foods"]:
                    result = self._foods_do(lambda: foods.save(body))
                elif parts[:2] == ["api", "health"] and len(parts) == 3 and parts[2] in ("strategy", "checkin", "program"):
                    result = self._strategy_post(user, parts[2], body)
                elif parts[:3] == ["api", "health", "plan"] and len(parts) == 4:
                    result = self._plan_post(user, parts[3], body)
                elif parts[:3] == ["api", "health", "log"] and len(parts) == 4:
                    result = self._health_log(user, parts[3], body)
                else:
                    result = self._dispatch(user, parts, body)
            except (ActionError, ChatError) as e:
                self._json(e.code, {"error": str(e)})
                return
            if result is STREAMED:
                return
            if result is None:
                self._json(404, {"error": "not found"})
            else:
                self._json(200, result)

        def _dispatch(self, user, parts, body):
            # parts starts with "api"
            p = parts[1:] if parts and parts[0] == "api" else []
            # Ids go into Paperclip URLs, so only plain id characters pass.
            if any(not SAFE_ID.fullmatch(x) for x in p):
                raise ActionError(400, "Bad id.")
            if p == ["work", "tasks"]:
                return app.create_task(user, body)
            if len(p) == 3 and p[0] == "approvals" and p[1] == "board":
                return app.decide(user, p[2], body)
            if len(p) == 3 and p[0] == "agents" and p[2] in ("pause", "resume"):
                return app.pause(user, p[1], p[2] == "pause")
            if len(p) == 3 and p[0] == "work" and p[2] == "status":
                return app.set_status(user, p[1], body)
            if len(p) == 3 and p[0] == "work" and p[2] == "comment":
                return app.comment(user, p[1], body)
            if len(p) == 3 and p[0] == "runs" and p[2] == "cancel":
                return app.cancel_run(user, p[1])
            if p == ["stop"]:
                cache.forget()
                return app.stop(user, body)
            return None

        def _need_chat(self):
            if chat is None:
                raise ChatError(503, "Chat with Max isn't connected on the server yet.")
            return chat

        def _chat_get(self, rest):
            try:
                c = self._need_chat()
                if not rest:
                    self._json(200, {"ok": True, **c.list(), **app.meta()})
                elif SAFE_ID.fullmatch(rest):
                    self._json(200, c.get(rest))
                else:
                    self._json(404, {"error": "not found"})
            except ChatError as e:
                self._json(e.code, {"ok": False, "error": str(e), **app.meta()})

        def _inbox_get(self, rest):
            if inbox is None:
                self._json(503, {"ok": False, "error": "Gmail isn't signed in on the server yet.", **app.meta()})
                return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                if not rest:
                    out = inbox.list(view=(q.get("view") or ["inbox"])[0], page=(q.get("page") or [None])[0])
                elif SAFE_ID.fullmatch(rest):
                    out = inbox.read(rest)
                else:
                    self._json(400, {"error": "Bad id."})
                    return
            except SourceError as e:
                self._json(503, {"ok": False, "error": str(e), **app.meta()})
                return
            # Mail is never written to disk or cached; the browser keeps it in memory.
            self._json(200, {"ok": True, **out, **app.meta()})

        def _planner_get(self):
            if planner is None:
                self._json(503, {"ok": False, "error": "Google Calendar isn't signed in on the server yet.", **app.meta()})
                return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                out = planner.week(start=(q.get("start") or [None])[0], days=(q.get("days") or [7])[0])
            except ValueError as e:
                self._json(400, {"ok": False, "error": str(e)})
                return
            except SourceError as e:
                self._json(503, {"ok": False, "error": str(e), **app.meta()})
                return
            # Events are never written to disk or cached.
            self._json(200, {"ok": True, **out, **app.meta()})

        def _library_get(self, skill):
            notes = {"notes_url": library_cfg.get("notes_url")}
            try:
                if library is None:
                    raise LibraryError("Max's memory and skills aren't being shared with the app yet")
                data = library.get()
            except LibraryError as e:
                self._json(404 if skill else 200, {"ok": False, "error": str(e), **notes, **app.meta()})
                return
            if skill is None:
                skills = [{k: v for k, v in x.items() if k != "source"} for x in data.get("skills", [])]
                self._json(200, {"ok": True, **data, "skills": skills, **notes, **app.meta()})
                return
            hit = next((x for x in data.get("skills", []) if x.get("id") == skill), None)
            self._json(200 if hit else 404, hit or {"ok": False, "error": "That skill isn't there any more."})

        def _health_get(self, what):
            if health is None:
                self._json(503, {"ok": False, "error": "Health isn't connected on the server yet.", **app.meta()})
                return
            q = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
            try:
                if what == "food":
                    day = (q.get("day") or [None])[0]
                    if day is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                        raise ValueError
                    out = health.food(day)
                elif what == "train":
                    out = health.train()
                elif what == "meals":
                    out = health.meals()
                elif what == "progress":
                    out = health.progress(int((q.get("days") or ["14"])[0]))
                elif what == "strategy":
                    out = self._strategy_do(strategy.view) if strategy else None
                    if out is None:
                        self._json(503, {"ok": False, "error": "Health isn't connected on the server yet.", **app.meta()})
                        return
                elif what == "plan/prefs":
                    if mealplan is None:
                        self._json(503, {"ok": False, "error": "Health isn't connected on the server yet.", **app.meta()})
                        return
                    try:
                        out = {"prefs": mealplan.prefs(), "recipes": mealplan.recipes(""), "ratings": mealplan.ratings()}
                    except SourceError as e:
                        self._json(503, {"ok": False, "error": str(e), **app.meta()})
                        return
                elif what in ("plan", "recipes"):
                    if mealplan is None:
                        self._json(503, {"ok": False, "error": "Health isn't connected on the server yet.", **app.meta()})
                        return
                    arg = (q.get("week" if what == "plan" else "q") or [None])[0]
                    if what == "plan" and arg is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", arg):
                        raise ValueError
                    try:
                        out = mealplan.view(arg) if what == "plan" else {"recipes": mealplan.recipes(arg)}
                    except SourceError as e:
                        self._json(503, {"ok": False, "error": str(e), **app.meta()})
                        return
                elif what in ("log/recent", "log/meals"):
                    if hlog is None:
                        raise ActionError(503, "Health isn't connected on the server yet.")
                    try:
                        out = hlog.recent_foods() if what == "log/recent" else hlog.saved_meals((q.get("q") or [""])[0])
                    except SourceError as e:
                        self._json(503, {"ok": False, "error": str(e), **app.meta()})
                        return
                elif what in ("search/foods", "search/exercises"):
                    term = (q.get("q") or [""])[0]
                    try:
                        out = hlog.search_foods(term) if what == "search/foods" else hlog.search_exercises(term)
                    except SourceError as e:
                        self._json(503, {"ok": False, "error": str(e), **app.meta()})
                        return
                else:
                    self._json(404, {"error": "not found"})
                    return
            except ValueError:
                self._json(400, {"error": "Bad date."})
                return
            except ActionError as e:
                self._json(e.code, {"ok": False, "error": str(e), **app.meta()})
                return
            if what in ("food", "progress") and strategy is not None:
                self._strategy_goals(out)
            self._json(200, {"ok": True, **out, **app.meta()})

        def _strategy_goals(self, out):
            """The app's own targets replace NutriTrace's goals once a strategy is set."""
            try:
                day = datetime.date.fromisoformat((out.get("day") or {}).get("data", {}).get("date") or out["today"])
                t = strategy.targets_for(day)
                view = strategy.view()
            except (SourceError, KeyError, ValueError):
                return
            if t:
                out["goals"] = {"ok": True, "data": t, "source": "strategy"}
            out["checkin_due"] = view["due"]

        def _strategy_do(self, fn):
            try:
                return fn()
            except SourceError as e:
                raise ActionError(503, str(e))
            except ValueError as e:
                raise ActionError(400, str(e))

        def _foods_do(self, fn):
            if foods is None:
                raise ActionError(503, "Food lookup isn't connected on the server yet.")
            try:
                return {"ok": True, **fn(), **app.meta()}
            except SourceError as e:
                raise ActionError(503, str(e))
            except ValueError as e:
                raise ActionError(400, str(e))

        def _health_log(self, user, kind, body):
            """Adds one entry to NutriTrace or LiftTrace. Never edits or deletes."""
            fn = {"food-new": "add_food", "food": "log_food", "water": "log_water", "set": "log_set",
                  "quick": "quick_add", "meal": "log_meal", "copy": "copy_meal",
                  "describe": "describe", "photo": "photo", "estimate": "estimate"}.get(kind)
            if fn is None:
                return None
            if hlog is None:
                raise ActionError(503, "Health isn't connected on the server yet.")
            try:
                if kind in ("describe", "photo"):
                    out = getattr(meal_estimate, fn)(body, chat)  # Max estimates; nothing is logged
                elif kind == "estimate":
                    out = meal_estimate.log_items(body, hlog)
                else:
                    out = getattr(hlog, fn)(body)
            except ChatError as e:
                raise ActionError(e.code, str(e))
            except ValueError as e:
                raise ActionError(400, str(e))
            except Refused as e:
                raise ActionError(409 if e.code == 409 else 400, str(e))
            except SourceError as e:
                raise ActionError(503, str(e))
            # The audit line says what kind of entry was added, not what was eaten or lifted.
            detail = {"kind": body.get("kind") or "meal"} if kind == "photo" else {"date": out.get("date")}
            app.audit(user, "health_" + kind.replace("-", "_"), detail)
            return {"ok": True, **out, **app.meta()}

        def _strategy_post(self, user, kind, body):
            """Targets change only here: Craig confirming a strategy, a check-in or a program."""
            if strategy is None:
                raise ActionError(503, "Health isn't connected on the server yet.")
            fn = {"strategy": strategy.save, "checkin": strategy.checkin, "program": strategy.set_program}[kind]
            out = self._strategy_do(lambda: fn(body))
            detail = {"choice": body.get("action")} if kind == "checkin" else {}
            app.audit(user, "health_" + kind, detail)
            return {"ok": True, **out, **app.meta()}

        def _plan_post(self, user, kind, body):
            """Meal-plan changes. Logging adds the recipe to NutriTrace; cooked goes to CookTrace's diary."""
            fn = {"add": "add", "change": "change", "log": "log_meal", "cooked": "cooked",
                  "propose": "propose", "apply": "apply", "ask-max": "ask-max", "prefs": "set_prefs"}.get(kind)
            if fn is None:
                return None
            if mealplan is None:
                raise ActionError(503, "Health isn't connected on the server yet.")
            try:
                out = ask_max(mealplan, body, chat) if kind == "ask-max" else getattr(mealplan, fn)(body)
            except ChatError as e:
                raise ActionError(e.code, str(e))
            except ValueError as e:
                raise ActionError(400, str(e))
            except Refused as e:
                raise ActionError(400, str(e))
            except SourceError as e:
                raise ActionError(503, str(e))
            # Like the other health lines: what kind of change, never what was eaten.
            if kind != "propose":  # the app planner changes nothing; asking Max is logged (data goes to Max)
                app.audit(user, "health_plan_" + kind.replace("-", "_"), {"date": out.get("date")})
            return {"ok": True, **out, **app.meta()}

        def _foods_get(self, what):
            try:
                if what == "foods":
                    out = self._foods_do(foods.saved if foods else None)
                elif what == "food-search":
                    qs = urllib.parse.parse_qs(self.path.split("?", 1)[1] if "?" in self.path else "")
                    out = self._foods_do(lambda: foods.search(qs.get("q", [""])[0], qs.get("source", ["off"])[0]))
                else:
                    out = self._foods_do(lambda: foods.product(what[len("barcode/"):]))
            except ActionError as e:
                self._json(e.code, {"ok": False, "error": str(e), **app.meta()})
                return
            self._json(200, out)

        def _artifact_get(self, name):
            try:
                if artifacts is None:
                    raise ArtifactError(503, "Max's artifacts folder isn't connected yet.")
                if not name:
                    self._json(200, {"ok": True, "artifacts": artifacts.list()})
                else:
                    self._json(200, artifacts.get(urllib.parse.unquote(name)))
            except ArtifactError as e:
                self._json(e.code, {"ok": False, "error": str(e)})

        def _chat_post(self, user, p, body):
            c = self._need_chat()
            if any(not SAFE_ID.fullmatch(x) for x in p):
                raise ChatError(400, "Bad id.")
            if p == []:
                return c.new()
            if len(p) == 2 and p[1] == "stop":
                return c.stop(user, p[0])
            if len(p) == 2 and p[1] == "delete":
                return c.delete(user, p[0])
            if len(p) == 2 and p[1] == "send":
                conv, upstream = c.begin(user, p[0], body.get("text"))
                self._stream(lambda emit: c.run(conv, upstream, emit))
                return STREAMED
            return None

        def _stream(self, run):
            """Server-Sent Events to the browser, one JSON object per event."""
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            for k, v in SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()

            def emit(ev):
                self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
                self.wfile.flush()
            run(emit)

        do_PUT = do_DELETE = do_PATCH = lambda self: self._json(405, {"error": "not allowed"})

        def _static(self, path):
            rel = path.lstrip("/") or "index.html"
            target = (web_root / rel).resolve()
            if web_root not in target.parents and target != web_root:
                self._send(404, b"not found\n", "text/plain")
                return
            if target.is_dir():
                target = target / "index.html"
            if not target.is_file():
                # Single-page app: unknown paths get the shell.
                target = web_root / "index.html"
            ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype.endswith(("javascript", "json")):
                ctype += "; charset=utf-8"
            # The shell and service worker must revalidate so updates land.
            cache_ctl = "no-cache"
            if target.parent.name in ("icons", "fonts"):
                cache_ctl = "max-age=604800"
            extra = {"Cache-Control": cache_ctl}
            if target.name == "sw.js":
                extra["Service-Worker-Allowed"] = "/"
            self._send(200, target.read_bytes(), ctype, extra)

    return Handler


def main(argv=None):
    ap = argparse.ArgumentParser(description="Hermes app server")
    ap.add_argument("--config", default=os.environ.get(
        "HERMES_APP_CONFIG", str(HERE / "config.json")))
    ap.add_argument("--web", default=str(DEFAULT_WEB))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    if cfg["listen_host"] not in ("127.0.0.1", "::1", "localhost"):
        raise SystemExit("listen_host must be loopback; Tailscale serve fronts it")
    cache = StatusCache(cfg["services"], cfg["cache_seconds"])
    demo = Demo() if cfg.get("demo") else None
    app = App(cfg, cache, demo=demo)
    artifacts = make_artifacts(cfg, app)
    srv = ThreadingHTTPServer((cfg["listen_host"], cfg["listen_port"]),
                              make_handler(cfg, args.web, cache, app, make_chat(cfg, app, artifacts), artifacts,
                                           make_health(cfg, app), make_inbox(cfg, app), make_foods(cfg, app),
                                           make_planner(cfg, app), make_library(cfg, app)))
    print(f"hermes-app {VERSION} on {cfg['listen_host']}:{cfg['listen_port']}"
          f"{' (SAMPLE DATA)' if demo else ''}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
