"""Read-only sources other than Paperclip: the approval broker and the cost monitor.

Both are configured rather than hard-coded, because their storage belongs to
other services on the box. The broker is read through a read-only SQLite
connection with a query from the config (its columns are aliased to the names
below), or, where its database is private to the broker, through the feed file
broker_export.py writes. The cost monitor is read from the CSV its hourly
script appends to, or from a JSON file.
"""

import csv
import datetime
import json
import sqlite3
import time
from pathlib import Path


class SourceError(Exception):
    pass


BROKER_FIELDS = ("id", "title", "detail", "recipients", "status",
                 "requested", "expires", "decided")


class Broker:
    """Approval broker requests. Approving stays on the broker's fingerprint page."""

    def __init__(self, cfg):
        self.feed = cfg.get("feed_path")  # written by broker_export.py
        self.feed_max_age = float(cfg.get("feed_max_age_seconds", 300))
        self.path = cfg.get("sqlite_path")
        self.pending_sql = cfg.get("pending_sql")
        self.history_sql = cfg.get("history_sql")
        self.review_url = cfg.get("review_url")  # opens the fingerprint page
        if not self.feed and not (self.path and self.pending_sql):
            raise KeyError("feed_path or sqlite_path")

    def _feed(self, key, now=None):
        now = now if now is not None else time.time()
        try:
            data = json.loads(Path(self.feed).read_text(encoding="utf-8"))
        except OSError:
            raise SourceError("broker feed not readable")
        except ValueError:
            raise SourceError("broker feed isn't JSON")
        if now - float(data.get("at") or 0) > self.feed_max_age:
            raise SourceError("broker feed is out of date")
        return [self._item(r) for r in data.get(key) or []]

    @staticmethod
    def _item(r):
        item = {k: r.get(k) for k in BROKER_FIELDS}
        rc = item["recipients"]
        if isinstance(rc, str) and rc.startswith("["):
            try:
                rc = json.loads(rc)
            except ValueError:
                pass
        if isinstance(rc, list):
            rc = ", ".join(str(x) for x in rc)
        item["recipients"] = rc
        item["source"] = "broker"
        item["kind"] = "Send email"
        if item["id"] is not None:
            item["id"] = str(item["id"])
        return item

    def query(self, sql):
        """Rows from the broker database as plain dicts (read-only connection)."""
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
        return [{k: r[k] for k in r.keys() if k in BROKER_FIELDS} for r in rows]

    def pending(self):
        if self.feed:
            return self._feed("pending")
        return [self._item(r) for r in self.query(self.pending_sql)]

    def history(self):
        if self.feed:
            return self._feed("history")
        return [self._item(r) for r in self.query(self.history_sql)] if self.history_sql else []


class CostFile:
    """The hourly cost monitor's latest numbers.

    `csv_path`: the monitor's balance log, one row per check:
    epoch, provider, remaining, used (used is the key's running total). Spend
    in a period is the sum of rises in `used` between checks, so a new key
    (whose total starts again from zero) doesn't count as negative spend.
    `path` + `fields`: a JSON file mapped by dotted keys.
    """

    def __init__(self, cfg):
        self.csv_path = Path(cfg["csv_path"]) if cfg.get("csv_path") else None
        self.path = Path(cfg["path"]) if cfg.get("path") else None
        if not (self.csv_path or self.path):
            raise KeyError("csv_path or path")
        self.provider = (cfg.get("provider") or "").lower() or None
        self.tz = cfg.get("timezone", "Europe/London")
        self.cap = cfg.get("month_cap_usd")
        self.fields = cfg.get("fields", {})  # app name -> key in the file
        self.label = cfg.get("label", "Max on OpenRouter")

    def _zone(self):
        try:
            from zoneinfo import ZoneInfo
            return ZoneInfo(self.tz)
        except Exception:
            return datetime.timezone.utc

    def read_csv(self, now=None):
        now = now if now is not None else time.time()
        rows = []
        try:
            with open(self.csv_path, newline="", encoding="utf-8") as f:
                for r in csv.reader(f):
                    if len(r) < 4:
                        continue
                    try:
                        at, prov, used = float(r[0]), r[1].strip().lower(), float(r[3])
                    except ValueError:
                        continue  # header or a damaged line
                    try:
                        remaining = float(r[2])
                    except ValueError:
                        remaining = None  # a key with no limit has no balance
                    if self.provider and prov != self.provider:
                        continue
                    rows.append((at, remaining, used))
        except OSError:
            raise SourceError("cost log not readable")
        if not rows:
            raise SourceError("cost log has no readings yet")
        rows.sort()
        tz = self._zone()
        local = datetime.datetime.fromtimestamp(now, tz)
        day0 = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        month0 = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
        today = month = 0.0
        for (_, _, u0), (at, _, u1) in zip(rows, rows[1:]):
            rise = max(0.0, u1 - u0)
            if at >= month0:
                month += rise
            if at >= day0:
                today += rise
        last = rows[-1]
        return {"label": self.label, "today_usd": round(today, 4), "month_usd": round(month, 4),
                "month_cap_usd": self.cap, "balance_usd": last[1],
                "checked": datetime.datetime.fromtimestamp(last[0], datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def read(self):
        if self.csv_path:
            return self.read_csv()
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
