"""Read-only sources other than Paperclip: the approval broker and the cost monitor.

Both are configured rather than hard-coded, because their storage belongs to
other services on the box. The broker is read through a read-only SQLite
connection with a query from the config (its columns are aliased to the names
below); the cost monitor through the JSON file its hourly script writes.
"""

import json
import sqlite3
from pathlib import Path


class SourceError(Exception):
    pass


BROKER_FIELDS = ("id", "title", "detail", "recipients", "status",
                 "requested", "expires", "decided")


class Broker:
    """Approval broker requests. Approving stays on the broker's fingerprint page."""

    def __init__(self, cfg):
        self.path = cfg["sqlite_path"]
        self.pending_sql = cfg["pending_sql"]
        self.history_sql = cfg.get("history_sql")
        self.review_url = cfg.get("review_url")  # opens the fingerprint page

    def _rows(self, sql):
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        try:
            con = sqlite3.connect(uri, uri=True, timeout=2)
        except sqlite3.Error as e:
            raise SourceError(f"broker store not readable ({type(e).__name__})")
        try:
            con.row_factory = sqlite3.Row
            rows = con.execute(sql).fetchall()
        except sqlite3.Error as e:
            raise SourceError(f"broker query failed ({type(e).__name__})")
        finally:
            con.close()
        out = []
        for r in rows:
            keys = r.keys()
            item = {k: (r[k] if k in keys else None) for k in BROKER_FIELDS}
            item["source"] = "broker"
            item["kind"] = "Send email"
            if item["id"] is not None:
                item["id"] = str(item["id"])
            out.append(item)
        return out

    def pending(self):
        return self._rows(self.pending_sql)

    def history(self):
        return self._rows(self.history_sql) if self.history_sql else []


class CostFile:
    """The hourly cost monitor's latest numbers, mapped by the config."""

    def __init__(self, cfg):
        self.path = Path(cfg["path"])
        self.fields = cfg.get("fields", {})  # app name -> key in the file
        self.label = cfg.get("label", "Max on OpenRouter")

    def read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except OSError:
            raise SourceError("cost file not readable")
        except ValueError:
            raise SourceError("cost file isn't JSON")
        out = {"label": self.label}
        for name, key in self.fields.items():
            cur = data
            for part in str(key).split("."):
                cur = cur.get(part) if isinstance(cur, dict) else None
            out[name] = cur
        return out
