"""Shop prices: what things cost at each supermarket, kept as a history.

Prices come from two places. Craig can type one in, or Max checks the shop
sites he's approved (Trolley first, which shows Tesco, Sainsbury's, Asda,
Iceland and Waitrose; then the supermarkets directly) and reports the price it
finds. Each price is kept with the date and where it came from, so the app can
show history, compare stores and find the cheapest week. Nothing here buys or
orders anything.
"""

import datetime
import json
import re
import secrets
import threading
from pathlib import Path

from meal_estimate import _ask
from sources import SourceError

MAX_ROWS = 5000
STORES = ("Trolley", "Tesco", "Sainsbury's", "Asda", "Morrisons", "Waitrose", "Ocado", "Aldi", "Lidl",
          "Iceland", "Co-op", "M&S", "Other")
SOURCES = ("typed", "max")
# Shop names as Max or Craig might write them; matched loosely (case and punctuation ignored).
STORE_ALIASES = {"Trolley": ("trolley",), "Tesco": ("tesco",), "Sainsbury's": ("sainsburys", "sainsbury"),
                 "Asda": ("asda",), "Morrisons": ("morrisons", "morrison"), "Waitrose": ("waitrose",),
                 "Ocado": ("ocado",), "Aldi": ("aldi",), "Lidl": ("lidl",), "Iceland": ("iceland",),
                 "Co-op": ("coop", "cooperative"), "M&S": ("marksandspencer", "marksspencer", "ms")}
CHECK_RULES = (
    "Check the current shelf price of each item below on the UK supermarket sites, Trolley first. "
    "Answer with one JSON object and nothing else, shaped like "
    '{"prices":[{"item":"Chicken breast","store":"Tesco","price":4.50,"pack":"600 g","url":"https://..."}]}. '
    "price is the pounds price for the pack shown, as a number. Use only prices you actually saw; "
    "leave an item out if you couldn't see its price, never guess. Don't buy anything or add anything "
    "to a basket. Items:\n"
)


def _money(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        n = float(str(v).replace("£", "").strip())
    except ValueError:
        return None
    return round(n, 2) if 0 <= n <= 500 else None


def store_name(raw):
    """The shop's name as the app lists it, or 'Other' if it isn't one of them."""
    key = re.sub(r"[^a-z]", "", str(raw or "").lower())
    if not key:
        return "Other"
    for name, aliases in STORE_ALIASES.items():
        if any(a == key or (len(a) > 3 and a in key) for a in aliases):
            return name
    return "Other"


def clean_prices(obj, wanted):
    """Keep only well-formed price rows for items that were asked about."""
    names = {w.lower(): w for w in wanted}
    out = []
    for row in (obj or {}).get("prices") or []:
        if not isinstance(row, dict):
            continue
        item = names.get(str(row.get("item") or "").strip().lower())
        price = _money(row.get("price"))
        if not item or price is None:
            continue
        out.append({"item": item, "store": store_name(row.get("store")), "price": price,
                    "pack": str(row.get("pack") or "").strip()[:40] or None,
                    "url": str(row.get("url") or "").strip()[:300] or None})
    return out


class Prices:
    def __init__(self, path=None, clock=None):
        self.path = Path(path) if path else None
        self._clock = clock or (lambda: datetime.date.today().isoformat())
        self._lock = threading.Lock()
        self._rows = None

    # ------------------------------------------------------------ storage

    def _load(self):
        if self._rows is None:
            if self.path is None or not self.path.exists():
                self._rows = []
            else:
                try:
                    self._rows = json.loads(self.path.read_text(encoding="utf-8")).get("rows", [])
                except (OSError, ValueError, AttributeError):
                    raise SourceError("The prices file can't be read.")
        return self._rows

    def _save(self, before):
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"rows": self._rows}, indent=1), encoding="utf-8")
            tmp.chmod(0o640)
            tmp.replace(self.path)
        except OSError:
            self._rows = before
            raise SourceError("The prices couldn't be saved on the server.")

    def _add(self, rows):
        with self._lock:
            before = list(self._load())
            if len(before) + len(rows) > MAX_ROWS:
                raise ValueError("The price history is full. Clear some old prices first.")
            self._rows = before + rows
            self._save(before)
            return rows

    # ------------------------------------------------------------ recording

    def add(self, body):
        item = " ".join(str(body.get("item") or "").split())[:80]
        if not item:
            raise ValueError("Name the item first.")
        price = _money(body.get("price"))
        if price is None:
            raise ValueError("Type the price in pounds, like 4.50.")
        store = str(body.get("store") or "Other")
        if store not in STORES:
            raise ValueError("Pick a shop from the list.")
        row = {"id": secrets.token_hex(6), "item": item, "store": store, "price": price,
               "pack": " ".join(str(body.get("pack") or "").split())[:40] or None,
               "date": self._clock(), "source": "typed"}
        self._add([row])
        return {"price": row}

    def check(self, body, chat):
        """Ask Max to check the shelf prices of the named items (Max reads the approved shop sites)."""
        items = [" ".join(str(x).split())[:80] for x in (body.get("items") or []) if str(x).strip()][:12]
        if not items:
            raise ValueError("Pick at least one item to check.")
        got = _ask(chat, CHECK_RULES + "\n".join(f"- {x}" for x in items))
        today = self._clock()
        rows = [dict(r, id=secrets.token_hex(6), date=today, source="max") for r in clean_prices(got, items)]
        if not rows:
            raise SourceError("Max didn't find prices for those. Try again, or type the price in.")
        self._add(rows)
        return {"found": len(rows), "prices": rows, "asked": len(items)}

    # ------------------------------------------------------------ reading

    def view(self, item=None):
        with self._lock:
            rows = list(self._load())
        if item:
            key = item.strip().lower()
            rows = [r for r in rows if r["item"].lower() == key]
        return {"rows": sorted(rows, key=lambda r: r["date"], reverse=True)[:500]}

    def compare(self):
        """For each item, its latest price at each shop, and the cheapest shop now."""
        with self._lock:
            rows = list(self._load())
        latest = {}
        for r in sorted(rows, key=lambda r: r["date"]):
            latest[(r["item"].lower(), r["store"])] = r
        items = {}
        for (key, _store), r in latest.items():
            items.setdefault(r["item"], {})[r["store"]] = {"price": r["price"], "pack": r["pack"], "date": r["date"]}
        out = []
        for name, shops in sorted(items.items(), key=lambda x: x[0].lower()):
            best = min(shops.items(), key=lambda kv: kv[1]["price"])
            out.append({"item": name, "shops": shops, "cheapest": best[0], "cheapest_price": best[1]["price"]})
        return {"items": out}

    def history(self, item):
        rows = self.view(item)["rows"]
        return {"item": item, "points": [{"date": r["date"], "store": r["store"], "price": r["price"]}
                                         for r in sorted(rows, key=lambda r: r["date"])]}
