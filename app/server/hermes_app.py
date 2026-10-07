#!/usr/bin/env python3
"""Hermes app server (Phase 1).

Serves the Hermes web app (PWA) and a small read-only API. Standard library
only, so nothing needs installing on the server.

It listens on loopback and is reached through `tailscale serve`, which adds
the Tailscale-User-Login header for the signed-in tailnet user. Requests
without an allowed login are refused, so there is no sign-in form.

Phase 1 is read-only: it reports whether the existing services are up. It
holds no send power and no model keys.
"""

import argparse
import json
import mimetypes
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "0.1.0"
HERE = Path(__file__).resolve().parent
DEFAULT_WEB = HERE.parent / "web"

# Result states. Unknown is its own state and never shown as up or down.
OK, DOWN, UNKNOWN = "ok", "down", "unknown"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
        "base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("image/svg+xml", ".svg")


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

def make_handler(cfg, web_root, cache):
    allowed = {x.lower() for x in cfg["allowed_logins"]}
    web_root = Path(web_root).resolve()

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

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            # Static app shell files carry nothing private, but everything
            # still needs an allowed tailnet login.
            user = self._user()
            if user is None:
                self._send(HTTPStatus.FORBIDDEN,
                           b"Hermes: open this through Tailscale on one of Craig's devices.\n",
                           "text/plain; charset=utf-8")
                return
            if path == "/api/me":
                self._json(200, {**user, "version": VERSION})
            elif path == "/api/status":
                self._json(200, cache.get())
            elif path.startswith("/api/"):
                self._json(404, {"error": "not found"})
            else:
                self._static(path)

        def do_POST(self):
            # Phase 1 is read-only.
            self._json(405, {"error": "read-only"})

        do_PUT = do_DELETE = do_PATCH = do_POST

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
            if target.parent.name == "icons":
                cache_ctl = "max-age=86400"
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
    srv = ThreadingHTTPServer((cfg["listen_host"], cfg["listen_port"]),
                              make_handler(cfg, args.web, cache))
    print(f"hermes-app {VERSION} on {cfg['listen_host']}:{cfg['listen_port']}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
