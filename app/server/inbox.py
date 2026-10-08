"""Inbox: Craig's real Gmail, read-only, shown only in the app.

The app signs in once with the gmail.readonly scope (its token file holds a
client id, client secret and refresh token, owned root:hermes-app 0640). It
calls only Gmail's read endpoints, and nothing it reads goes to Max or any
model. Nothing here can send, label, move or delete mail.

Mail is never cached on disk, and the browser keeps it only in memory.
"""

import base64
import datetime
import html
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sources import SourceError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
TOKEN_URL = "https://oauth2.googleapis.com/token"
MAX_BODY = 200_000  # characters
MESSAGE_ID = re.compile(r"[0-9a-f]{6,32}")  # Gmail message ids are hex
VIEWS = {"inbox": "in:inbox", "unread": "in:inbox is:unread"}


def _iso(ms):
    try:
        return datetime.datetime.fromtimestamp(int(ms) / 1000, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _b64(data):
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")
    except (TypeError, ValueError):
        return ""


def _text_from_html(s):
    s = re.sub(r"(?is)<(script|style|head).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</(p|div|li|tr|h[1-6])>", "\n", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def _body(payload):
    """The message's plain text: text/plain if there is one, else the HTML with tags removed."""
    plain, rich, files = [], [], []

    def walk(part):
        mime = part.get("mimeType", "")
        name = part.get("filename")
        if name:
            files.append(name)
        data = (part.get("body") or {}).get("data")
        if data and not name:
            if mime == "text/plain":
                plain.append(_b64(data))
            elif mime == "text/html":
                rich.append(_text_from_html(_b64(data)))
        for sub in part.get("parts") or []:
            walk(sub)
    walk(payload or {})
    text = "\n".join(plain) if plain else "\n".join(rich)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:MAX_BODY], files


def _headers(payload):
    return {h.get("name", "").lower(): h.get("value", "") for h in (payload or {}).get("headers") or []}


class Gmail:
    """Read-only Gmail client with its own token refresh."""

    def __init__(self, cfg, opener=None, clock=time.time):
        self.token_file = cfg["token_file"]
        self.timeout = float(cfg.get("timeout", 8))
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._access, self._expires = None, 0.0
        self._lock = threading.Lock()

    def _call(self, req):
        try:
            with self._open(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            why = {400: "refused the request", 401: "refused the sign-in", 403: "refused access (scope or quota)",
                   429: "is rate limiting us", 404: "couldn't find that message"}.get(e.code, f"answered {e.code}")
            raise SourceError(f"Gmail {why}")
        except (urllib.error.URLError, OSError):
            raise SourceError("Gmail is unreachable")
        except ValueError:
            raise SourceError("Gmail sent something that isn't JSON")

    def _creds(self):
        try:
            c = json.loads(Path(self.token_file).read_text(encoding="utf-8"))
            return c["client_id"], c["client_secret"], c["refresh_token"]
        except (OSError, ValueError, KeyError, TypeError):
            raise SourceError("Gmail sign-in isn't set up yet")

    def _token(self):
        with self._lock:
            if self._access and self._clock() < self._expires - 60:
                return self._access
        cid, secret, refresh = self._creds()
        body = urllib.parse.urlencode({"client_id": cid, "client_secret": secret,
                                       "refresh_token": refresh, "grant_type": "refresh_token"}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        data = self._call(req)
        with self._lock:
            self._access = data["access_token"]
            self._expires = self._clock() + int(data.get("expires_in", 3600))
            return self._access

    def _get(self, path, **params):
        q = {k: v for k, v in params.items() if v is not None}
        url = API + path + ("?" + urllib.parse.urlencode(q, doseq=True) if q else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._token(), "Accept": "application/json"})
        return self._call(req)

    def list(self, view="inbox", limit=15, page=None):
        if view not in VIEWS:
            raise SourceError("Unknown view")
        limit = max(1, min(int(limit), 30))
        r = self._get("/messages", q=VIEWS[view], maxResults=limit, pageToken=page) or {}
        out = []
        for m in r.get("messages") or []:
            if not MESSAGE_ID.fullmatch(m.get("id", "")):
                continue
            meta = self._get(f"/messages/{m['id']}", format="metadata",
                             metadataHeaders=["From", "Subject", "Date"]) or {}
            h = _headers(meta.get("payload"))
            out.append({"id": meta.get("id"), "from": h.get("from", ""), "subject": h.get("subject") or "(no subject)",
                        "date": _iso(meta.get("internalDate")), "snippet": html.unescape(meta.get("snippet", "")),
                        "unread": "UNREAD" in (meta.get("labelIds") or [])})
        return {"messages": out, "next": r.get("nextPageToken"), "view": view}

    def read(self, mid):
        if not MESSAGE_ID.fullmatch(mid or ""):
            raise SourceError("Not a message id")
        m = self._get(f"/messages/{mid}", format="full") or {}
        h = _headers(m.get("payload"))
        text, files = _body(m.get("payload"))
        return {"id": m.get("id"), "from": h.get("from", ""), "to": h.get("to", ""), "subject": h.get("subject") or "(no subject)",
                "date": _iso(m.get("internalDate")), "text": text, "files": files[:20]}


SAMPLE = [
    {"id": "9f3a21c4b0d1e7aa", "from": "Dr Patel's surgery <appointments@example.org>", "subject": "Your blood test results are ready",
     "date": "2026-10-08T07:40:00Z", "snippet": "Your results are now available to view online.", "unread": True,
     "text": "Hello Craig,\n\nYour blood test results are now available to view online. Please log in to the patient portal to read them.\n\nThe surgery.", "files": []},
    {"id": "9f3a1f0c2e8b7d10", "from": "Octopus Energy <hello@example.com>", "subject": "Your October statement",
     "date": "2026-10-07T18:12:00Z", "snippet": "Your statement for September is ready.", "unread": False,
     "text": "Your statement for September is ready to view.", "files": ["statement-2026-09.pdf"]},
    {"id": "9f39e8d7c6b5a432", "from": "Jordan <jordan@example.com>", "subject": "Sunday lunch?",
     "date": "2026-10-06T12:30:00Z", "snippet": "Are you free this Sunday?", "unread": False,
     "text": "Are you free this Sunday? Thinking lunch at 1.", "files": []},
]


class SampleMail:
    """Sample mail for sample-data mode. Same answers shape as Gmail."""

    def list(self, view="inbox", limit=15, page=None):
        if view not in VIEWS:
            raise SourceError("Unknown view")
        rows = [{k: v for k, v in m.items() if k not in ("text", "files")} for m in SAMPLE]
        if view == "unread":
            rows = [m for m in rows if m["unread"]]
        return {"messages": rows, "next": None, "view": view}

    def read(self, mid):
        for m in SAMPLE:
            if m["id"] == mid:
                return {k: m[k] for k in ("id", "from", "subject", "date", "text", "files")} | {"to": "craig@example.com"}
        raise SourceError("That message isn't there any more.")
