"""The Planner's own blocks: time Craig sets aside (a workout, a shop, a focus hour).

Kept by the app in a small JSON file on the server (in memory in sample-data mode).
They sit beside the Google Calendar events, which stay read-only. Nothing here
goes to Google, Max or any model.
"""

import copy
import datetime
import json
import re
import secrets
import threading
from pathlib import Path

from sources import SourceError

MAX_BLOCKS = 2000
MAX_DAYS = 60
TIME = re.compile(r"([01]\d|2[0-3]):([0-5]\d)")


def _day(v):
    try:
        return datetime.date.fromisoformat(str(v)).isoformat()
    except (TypeError, ValueError):
        raise ValueError("date must look like 2026-10-10.")


def _clock(v, what):
    m = TIME.fullmatch(str(v or "").strip())
    if not m:
        raise ValueError(f"{what} must look like 09:30.")
    return f"{m.group(1)}:{m.group(2)}"


class PlannerBlocks:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._rows = None

    def _load(self):
        if self._rows is None:
            if self.path is None or not self.path.exists():
                self._rows = []
            else:
                try:
                    self._rows = json.loads(self.path.read_text(encoding="utf-8")).get("blocks", [])
                except (OSError, ValueError, AttributeError):
                    raise SourceError("The Planner blocks file can't be read.")
        return self._rows

    def _change(self, fn):
        with self._lock:
            rows = self._load()
            before = copy.deepcopy(rows)
            out = fn(rows)
            if self.path is not None:
                try:
                    tmp = self.path.with_name(self.path.name + ".tmp")
                    tmp.write_text(json.dumps({"blocks": self._rows}, indent=1), encoding="utf-8")
                    tmp.chmod(0o640)
                    tmp.replace(self.path)
                except OSError:
                    self._rows = before
                    raise SourceError("The Planner blocks couldn't be saved on the server.")
            return out

    def add(self, body):
        title = " ".join(str(body.get("title") or "").split())[:80]
        if not title:
            raise ValueError("Name the block first.")
        day = _day(body.get("date"))
        start, end = _clock(body.get("start"), "start"), _clock(body.get("end"), "end")
        if end <= start:
            raise ValueError("The block must end after it starts.")

        def go(rows):
            if len(rows) >= MAX_BLOCKS:
                raise ValueError("Too many blocks. Remove some first.")
            row = {"id": secrets.token_hex(6), "date": day, "start": start, "end": end, "title": title}
            rows.append(row)
            return {"block": row}
        return self._change(go)

    def remove(self, body):
        bid = str(body.get("id") or "")

        def go(rows):
            if not any(r["id"] == bid for r in rows):
                raise ValueError("That block isn't in the Planner any more. Refresh and try again.")
            rows[:] = [r for r in rows if r["id"] != bid]
            return {"removed": True}
        return self._change(go)

    def week(self, start, days):
        """Blocks from start for the given number of days, in date and time order."""
        first = datetime.date.fromisoformat(start)
        n = max(1, min(int(days), MAX_DAYS))
        last = (first + datetime.timedelta(days=n - 1)).isoformat()
        with self._lock:
            rows = [dict(r) for r in self._load() if first.isoformat() <= r["date"] <= last]
        return sorted(rows, key=lambda r: (r["date"], r["start"]))
