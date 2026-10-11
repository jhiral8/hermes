"""A PIN for the Hermes app's Lock button.

Tailscale already keeps strangers out. The PIN covers the case where someone
else picks up a phone that's still open on the app. The PIN is kept on the
server as a salted hash, never in the app. After a few wrong tries the check
waits a minute before it answers again.
"""

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
from pathlib import Path

from sources import SourceError

PIN = re.compile(r"\d{4,8}")
ITERATIONS = 200_000
MAX_MISSES = 5
COOLDOWN = 60


class Lock:
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path else None
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._misses = 0
        self._wait_until = 0.0
        self._memory = None  # sample-data mode keeps the PIN in memory

    def _load(self):
        if self.path is None:
            return self._memory
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise SourceError("The lock PIN file can't be read.")

    def _save(self, rec):
        if self.path is None:
            self._memory = rec
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(rec), encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(self.path)
        except OSError:
            raise SourceError("The lock PIN couldn't be saved on the server.")

    @staticmethod
    def _hash(pin, salt):
        return hashlib.pbkdf2_hmac("sha256", pin.encode(), salt, ITERATIONS)

    def _matches(self, pin, rec):
        salt = bytes.fromhex(rec["salt"])
        return hmac.compare_digest(self._hash(pin, salt), bytes.fromhex(rec["hash"]))

    def status(self):
        return {"set": self._load() is not None}

    def set_pin(self, body):
        """Set the PIN, or change it when the current one is given."""
        pin = str(body.get("pin") or "")
        if not PIN.fullmatch(pin):
            raise ValueError("Use 4 to 8 digits.")
        with self._lock:
            rec = self._load()
            if rec is not None and not self._matches(str(body.get("current") or ""), rec):
                raise ValueError("The current PIN is wrong.")
            salt = secrets.token_bytes(16)
            self._save({"salt": salt.hex(), "hash": self._hash(pin, salt).hex()})
        return {"set": True}

    def check(self, body):
        """Unlock if the PIN matches. Returns {'ok': bool} and, while waiting after misses, 'wait' seconds."""
        pin = str(body.get("pin") or "")
        with self._lock:
            now = self._clock()
            if now < self._wait_until:
                return {"ok": False, "wait": math.ceil(self._wait_until - now)}
            rec = self._load()
            if rec is None:
                return {"ok": False, "set": False}
            if PIN.fullmatch(pin) and self._matches(pin, rec):
                self._misses = 0
                return {"ok": True}
            self._misses += 1
            if self._misses >= MAX_MISSES:
                self._misses = 0
                self._wait_until = now + COOLDOWN
                return {"ok": False, "wait": COOLDOWN}
            return {"ok": False}
