"""Shared read-only Google client: token refresh and GET calls.

Each service (Gmail, Calendar) has its own token file holding a client id,
client secret and refresh token, owned root:hermes-app 0640, signed in with
that service's read-only scope only. Only GET calls are made.
"""

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sources import SourceError

TOKEN_URL = "https://oauth2.googleapis.com/token"


class GoogleReadOnly:
    NAME = "Google"
    API = ""
    NOT_FOUND = "couldn't find that"

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
                   429: "is rate limiting us", 404: self.NOT_FOUND}.get(e.code, f"answered {e.code}")
            raise SourceError(f"{self.NAME} {why}")
        except (urllib.error.URLError, OSError):
            raise SourceError(f"{self.NAME} is unreachable")
        except ValueError:
            raise SourceError(f"{self.NAME} sent something that isn't JSON")

    def _creds(self):
        try:
            c = json.loads(Path(self.token_file).read_text(encoding="utf-8"))
            return c["client_id"], c["client_secret"], c["refresh_token"]
        except (OSError, ValueError, KeyError, TypeError):
            raise SourceError(f"{self.NAME} sign-in isn't set up yet")

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
        url = self.API + path + ("?" + urllib.parse.urlencode(q, doseq=True) if q else "")
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._token(), "Accept": "application/json"})
        return self._call(req)
