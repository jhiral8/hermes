"""Pantry, cooked batches and the shopping list, kept by the app.

Everything here is the app's own: a small JSON file on the server (in memory in
sample-data mode). Nothing goes to NutriTrace, CookTrace or Max. Shopping
suggestions come from pantry items that have run low, and Craig ticks off
what he bought, which puts it back in the pantry.
"""

import copy
import datetime
import json
import secrets
import threading
from pathlib import Path

from sources import SourceError

MAX_ITEMS = 300
MAX_BATCHES = 200
MAX_SHOP = 200
UNITS = ("g", "kg", "ml", "l", "each", "pack", "tin", "bag", "jar")
PLACES = ("cupboard", "fridge", "freezer", "other")


def _text(body, key, limit, required=False):
    v = body.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ValueError(f"{key} is required.")
        return None
    if not isinstance(v, str):
        raise ValueError(f"{key} must be text.")
    return " ".join(v.split())[:limit]


def _amount(body, key, required=False, hi=100000):
    v = body.get(key)
    if v is None or v == "":
        if required:
            raise ValueError(f"{key} is required.")
        return None
    if isinstance(v, bool):
        raise ValueError(f"{key} must be a number.")
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number.")
    if n != n or n < 0 or n > hi:
        raise ValueError(f"{key} must be between 0 and {hi:g}.")
    return round(n, 2)


def _choice(body, key, options, default):
    v = body.get(key) or default
    if v not in options:
        raise ValueError(f"{key} must be one of: {', '.join(options)}.")
    return v


class Pantry:
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path else None
        self._clock = clock or (lambda: datetime.date.today().isoformat())
        self._lock = threading.Lock()
        self._data = None

    # ------------------------------------------------------------ storage

    def _load(self):
        if self._data is None:
            empty = {"items": [], "batches": [], "shop": []}
            if self.path is None or not self.path.exists():
                self._data = empty
            else:
                try:
                    self._data = {**empty, **json.loads(self.path.read_text(encoding="utf-8"))}
                except (OSError, ValueError):
                    raise SourceError("The pantry file can't be read.")
        return self._data

    def _commit(self, before):
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
            tmp.chmod(0o640)
            tmp.replace(self.path)
        except OSError:
            self._data = before
            raise SourceError("The pantry couldn't be saved on the server.")

    def _change(self, fn):
        with self._lock:
            data = self._load()
            before = copy.deepcopy(data)
            out = fn(data)
            self._commit(before)
            return out

    @staticmethod
    def _find(rows, rid):
        for r in rows:
            if r["id"] == rid:
                return r
        raise ValueError("That isn't in the list any more. Refresh and try again.")

    def _new_id(self):
        return secrets.token_hex(6)

    # ------------------------------------------------------------ reading

    def view(self):
        with self._lock:
            data = self._load()
            items = [dict(x, low=x["qty"] is not None and x.get("low") is not None and x["qty"] <= x["low"])
                     for x in data["items"]]
            today = self._clock()
            batches = []
            for b in data["batches"]:
                left = b["left"]
                days = None
                if b.get("use_by"):
                    try:
                        days = (datetime.date.fromisoformat(b["use_by"]) - datetime.date.fromisoformat(today)).days
                    except ValueError:
                        days = None
                batches.append(dict(b, days_left=days))
            # Low items not already on the list, shown as suggestions.
            listed = {s["name"].lower() for s in data["shop"] if not s["done"]}
            suggest = [{"id": x["id"], "name": x["name"], "unit": x["unit"]} for x in items
                       if x["low"] and x["name"].lower() not in listed]
            return {"items": sorted(items, key=lambda x: (not x["low"], x["name"].lower())),
                    "batches": sorted(batches, key=lambda b: (b["left"] <= 0, b.get("use_by") or "9999", b["name"].lower())),
                    "shop": sorted(data["shop"], key=lambda s: (s["done"], s["name"].lower())),
                    "suggest": suggest, "places": list(PLACES), "units": list(UNITS), "today": today}

    # ------------------------------------------------------------ pantry

    def add_item(self, body):
        name = _text(body, "name", 80, required=True)
        def go(d):
            if len(d["items"]) >= MAX_ITEMS:
                raise ValueError("The pantry is full. Remove something first.")
            row = {"id": self._new_id(), "name": name, "qty": _amount(body, "qty"),
                   "unit": _choice(body, "unit", UNITS, "each"), "low": _amount(body, "low"),
                   "place": _choice(body, "place", PLACES, "cupboard")}
            d["items"].append(row)
            return {"item": row}
        return self._change(go)

    def set_item(self, body):
        def go(d):
            row = self._find(d["items"], body.get("id"))
            if "qty" in body:
                row["qty"] = _amount(body, "qty")
            if "low" in body:
                row["low"] = _amount(body, "low")
            if body.get("name"):
                row["name"] = _text(body, "name", 80, required=True)
            if body.get("place"):
                row["place"] = _choice(body, "place", PLACES, row["place"])
            return {"item": row}
        return self._change(go)

    def use_item(self, body):
        """Take an amount out of the pantry, e.g. what went into a meal."""
        take = _amount(body, "amount", required=True)
        def go(d):
            row = self._find(d["items"], body.get("id"))
            if row["qty"] is None:
                raise ValueError("Set how much of this you have first.")
            row["qty"] = max(0, round(row["qty"] - take, 2))
            return {"item": row}
        return self._change(go)

    def remove_item(self, body):
        def go(d):
            self._find(d["items"], body.get("id"))
            d["items"] = [x for x in d["items"] if x["id"] != body.get("id")]
            return {"removed": True}
        return self._change(go)

    # ------------------------------------------------------------ batches

    def add_batch(self, body):
        name = _text(body, "name", 80, required=True)
        portions = _amount(body, "portions", required=True, hi=200)
        if not portions or portions < 1:
            raise ValueError("portions must be at least 1.")
        use_by = body.get("use_by") or None
        if use_by is not None:
            try:
                datetime.date.fromisoformat(use_by)
            except (TypeError, ValueError):
                raise ValueError("use_by must be a date, like 2026-10-15.")
        def go(d):
            if len(d["batches"]) >= MAX_BATCHES:
                raise ValueError("Too many batches. Clear some out first.")
            row = {"id": self._new_id(), "name": name, "portions": portions, "left": portions,
                   "place": _choice(body, "place", PLACES, "fridge"), "use_by": use_by,
                   "made": self._clock()}
            d["batches"].append(row)
            return {"batch": row}
        return self._change(go)

    def use_batch(self, body):
        """Eat or give away some portions from a batch."""
        take = _amount(body, "portions", required=True, hi=200) or 1
        def go(d):
            row = self._find(d["batches"], body.get("id"))
            if row["left"] < take:
                raise ValueError(f"Only {row['left']:g} portion{'s' if row['left'] != 1 else ''} left.")
            row["left"] = round(row["left"] - take, 2)
            return {"batch": row}
        return self._change(go)

    def remove_batch(self, body):
        def go(d):
            self._find(d["batches"], body.get("id"))
            d["batches"] = [x for x in d["batches"] if x["id"] != body.get("id")]
            return {"removed": True}
        return self._change(go)

    # ------------------------------------------------------------ shopping

    def shop_add(self, body):
        name = _text(body, "name", 80, required=True)
        def go(d):
            if len(d["shop"]) >= MAX_SHOP:
                raise ValueError("The shopping list is full. Clear the ticked items first.")
            row = {"id": self._new_id(), "name": name, "qty": _amount(body, "qty"),
                   "unit": _choice(body, "unit", UNITS, "each"), "done": False,
                   "pantry_id": body.get("pantry_id") or None}
            d["shop"].append(row)
            return {"item": row}
        return self._change(go)

    def shop_tick(self, body):
        """Bought: the item goes back into the pantry with the amount bought (or one, if none was set)."""
        def go(d):
            row = self._find(d["shop"], body.get("id"))
            row["done"] = not row["done"] if body.get("done") is None else bool(body.get("done"))
            if row["done"]:
                p = next((x for x in d["items"] if x["id"] == row.get("pantry_id")), None) or \
                    next((x for x in d["items"] if x["name"].lower() == row["name"].lower()), None)
                if p is None:
                    p = {"id": self._new_id(), "name": row["name"], "qty": None, "unit": row["unit"],
                         "low": None, "place": "cupboard"}
                    d["items"].append(p)
                if row.get("qty") is not None:
                    p["qty"] = round((p["qty"] or 0) + row["qty"], 2)
                elif p["qty"] is not None:
                    p["qty"] = round(p["qty"] + 1, 2)
            return {"item": row}
        return self._change(go)

    def shop_remove(self, body):
        def go(d):
            self._find(d["shop"], body.get("id"))
            d["shop"] = [x for x in d["shop"] if x["id"] != body.get("id")]
            return {"removed": True}
        return self._change(go)

    def shop_clear_done(self, body):
        def go(d):
            before = len(d["shop"])
            d["shop"] = [x for x in d["shop"] if not x["done"]]
            return {"cleared": before - len(d["shop"])}
        return self._change(go)
