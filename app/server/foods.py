"""Barcode lookup and saved foods for the Food screen.

Lookup asks Open Food Facts from the server only, and only once Craig has said
yes (config foods.off_enabled). Answers are cached in memory for a day, and
calls are spaced at least a second apart. Saved foods are kept in a small file on
this server (foods.store_path), because this NutriTrace version has no key route
to create a food.

Nothing here is passed to Max or any model. Unknown nutrients stay None, never 0.
"""

import datetime
import io
import json
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sources import SourceError

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_FIELDS = "product_name,brands,serving_size,nutriments"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
SEARCH_SIZE = 20
USER_AGENT = "HermesApp/1.0 (personal use)"
CODE = re.compile(r"\d{8,14}")  # EAN-8, UPC-A, EAN-13, GTIN-14
NUTS = ("kcal", "protein", "carbs", "fat", "fibre")
# Open Food Facts field stems, and the names NutriTrace's catalogue uses.
OFF_STEM = {"kcal": "energy-kcal", "protein": "proteins", "carbs": "carbohydrates", "fat": "fat", "fibre": "fiber"}
MAX_KCAL = 2000  # per 100 g or per serving; anything above is a typo
MAX_G = 100      # protein, carbs, fat, fibre per 100 g


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _nutriments(n, suffix):
    """Values for one basis ('_100g' or '_serving'). A missing value stays None."""
    out = {}
    for k in NUTS:
        v = _num(n.get(OFF_STEM[k] + suffix))
        if v is None and k == "kcal":
            kj = _num(n.get("energy" + suffix))  # some products only give kJ
            v = round(kj / 4.184, 1) if kj is not None else None
        out[k] = v
    return out


class Lookup:
    """Open Food Facts product lookup, for the barcode scanner."""

    def __init__(self, cfg, opener=None, clock=time.time):
        self.enabled = bool(cfg.get("off_enabled"))
        self.timeout = float(cfg.get("timeout", 6))
        self._open = opener or urllib.request.urlopen
        self._clock = clock
        self._cache = {}
        self._last = 0.0
        self._lock = threading.Lock()

    def product(self, code):
        if not CODE.fullmatch(code or ""):
            raise ValueError("That isn't a barcode number.")
        if not self.enabled:
            raise SourceError("Barcode lookup is off on the server. Type the food in by hand.")
        now = self._clock()
        with self._lock:
            hit = self._cache.get(code)
            if hit and now - hit[0] < 86400:
                return hit[1]
            if now - self._last < 1.0:
                raise SourceError("Lookups are going a bit fast. Try again in a moment.")
            self._last = now
        data = self._get(OFF_URL.format(code=code) + "?" + urllib.parse.urlencode({"fields": OFF_FIELDS}))
        if not isinstance(data, dict) or data.get("status") != 1:
            out = {"found": False, "code": code}  # not cached: it may be added later
            return out
        out = _product(data.get("product") or {}, code, now)
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[code] = (now, out)
        return out

    def _get(self, url):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SourceError(f"Open Food Facts answered {e.code}")
        except (urllib.error.URLError, OSError):
            raise SourceError("Open Food Facts is unreachable")
        except ValueError:
            raise SourceError("Open Food Facts sent something that isn't JSON")

    def search(self, q):
        """Food search by name. Only products with calories come back, so each can be logged."""
        q = " ".join((q or "").split())
        if len(q) < 2:
            raise ValueError("Type at least two letters to search.")
        if len(q) > 80:
            raise ValueError("That search is too long.")
        if not self.enabled:
            raise SourceError("Open Food Facts is off on the server.")
        key = "q:" + q.lower()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < 86400:
                return hit[1]
            if now - self._last < 1.0:
                raise SourceError("Searches are going a bit fast. Try again in a moment.")
            self._last = now
        data = self._get(OFF_SEARCH_URL + "?" + urllib.parse.urlencode(
            {"search_terms": q, "search_simple": 1, "action": "process", "json": 1,
             "page_size": SEARCH_SIZE, "fields": "code," + OFF_FIELDS}))
        items = []
        for p in (data or {}).get("products") or []:
            if not isinstance(p, dict):
                continue
            item = _product(p, str(p.get("code") or ""), now)
            if not item["name"] or (item["per100"]["kcal"] is None and item["per_serving"]["kcal"] is None):
                continue
            if not CODE.fullmatch(item["code"]):
                item["code"] = ""
            items.append(item)
        out = {"query": q, "items": items, "source": "Open Food Facts"}
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[key] = (now, out)
        return out


def _product(p, code, now):
    n = p.get("nutriments") or {}
    return {"found": True, "code": code, "name": (p.get("product_name") or "").strip(),
            "brand": (p.get("brands") or "").split(",")[0].strip(), "serving": p.get("serving_size") or None,
            "per100": _nutriments(n, "_100g"), "per_serving": _nutriments(n, "_serving"),
            "source": "Open Food Facts", "retrieved": datetime.date.fromtimestamp(now).isoformat()}


def _clean_text(v, limit, field):
    s = (v or "").strip() if isinstance(v, str) else ""
    if len(s) > limit:
        raise ValueError(f"{field} is too long.")
    return s


def _clean_nutrients(obj, field):
    if not isinstance(obj, dict):
        raise ValueError(f"{field} must be an object.")
    out = {}
    for k in NUTS:
        v = obj.get(k)
        if v in (None, ""):
            out[k] = None  # unknown stays unknown
            continue
        n = _num(v)
        cap = MAX_KCAL if k == "kcal" else MAX_G
        if n is None or n < 0 or n > cap:
            raise ValueError(f"{k} in {field} isn't a sensible number.")
        out[k] = round(n, 1)
    return out


def check_food(body):
    """A food as the draft form sends it, checked before anything is saved."""
    name = _clean_text(body.get("name"), 120, "Name")
    if not name:
        raise ValueError("Give the food a name.")
    return {
        "name": name,
        "brand": _clean_text(body.get("brand"), 80, "Brand"),
        "serving": _clean_text(body.get("serving"), 60, "Serving"),
        "per100": _clean_nutrients(body.get("per100") or {}, "per 100 g"),
        "per_serving": _clean_nutrients(body.get("per_serving") or {}, "per serving"),
        "source": _clean_text(body.get("source"), 60, "Source") or "Entered by hand",
        "retrieved": _clean_text(body.get("retrieved"), 10, "Date") or None,
    }


class SavedFoods:
    """Foods Craig has saved. Kept in a small JSON file on this server, or in memory with no path.

    This NutriTrace version has no key route that can create a food, so the list
    lives here for now. It holds only what Craig typed or looked up. Nothing here
    goes to Max or any model.
    """

    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._items = None

    def _load(self):
        if self._items is None:
            if self.path is None or not self.path.exists():
                self._items = []
            else:
                try:
                    self._items = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise SourceError("The saved foods file can't be read.")
        return self._items

    def _write(self):
        if self.path is None:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._items, indent=1), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.path)

    def list(self):
        with self._lock:
            return [dict(x) for x in self._load()]

    def add(self, body):
        food = check_food(body)
        with self._lock:
            items = self._load()
            item = {"id": secrets.token_hex(8), "saved": datetime.date.today().isoformat(), **food}
            items.insert(0, item)
            del items[500:]  # keep the list bounded
            try:
                self._write()
            except OSError:
                items.pop(0)
                raise SourceError("Couldn't save the food on the server.")
        return item


class Foods:
    """What the Food screen's barcode and saved-foods routes call."""

    def __init__(self, lookup, saved=None):
        self.lookup = lookup
        self.saved_foods = saved or SavedFoods()

    def product(self, code):
        return {"product": self.lookup.product(code)}

    def search(self, q):
        return self.lookup.search(q)

    def saved(self):
        return {"items": self.saved_foods.list()}

    def save(self, body):
        return {"result": self.saved_foods.add(body)}


SAMPLE_CODE = "5000000000017"  # made-up code for sample-data mode
# Made-up search results for sample-data mode (one has no calories, so it is left out).
SAMPLE_SEARCH = [
    {"code": "5000000000024", "product_name": "Sample Greek yoghurt 0%", "brands": "Sample Co", "serving_size": "150 g",
     "nutriments": {"energy-kcal_100g": 57, "proteins_100g": 10.3, "carbohydrates_100g": 3.6, "fat_100g": 0.2}},
    {"code": "5000000000031", "product_name": "Sample porridge oats", "brands": "Sample Mill", "serving_size": "40 g",
     "nutriments": {"energy-kcal_100g": 374, "proteins_100g": 11, "carbohydrates_100g": 60, "fat_100g": 8, "fiber_100g": 9}},
    {"code": "5000000000048", "product_name": "Sample chicken breast fillets", "brands": "Sample Farm",
     "nutriments": {"energy-kcal_100g": 106, "proteins_100g": 24, "carbohydrates_100g": 0, "fat_100g": 1.1}},
    {"code": "5000000000055", "product_name": "Sample oat bar", "brands": "Sample Co", "nutriments": {}},
]


def sample_opener(req, timeout=None):
    """Sample-data stand-in for Open Food Facts: one made-up product, the rest not found."""
    if "/cgi/search.pl" in req.full_url:
        q = urllib.parse.parse_qs(req.full_url.split("?", 1)[1]).get("search_terms", [""])[0].lower()
        found = [p for p in SAMPLE_SEARCH if any(w in p["product_name"].lower() for w in q.split())]
        return io.BytesIO(json.dumps({"count": len(found), "products": found}).encode())
    code = req.full_url.split("/product/", 1)[-1].split(".json", 1)[0]
    if code != SAMPLE_CODE:
        body = {"status": 0}
    else:
        body = {"status": 1, "product": {"product_name": "Sample oat drink", "brands": "Sample Co, Other",
                                         "serving_size": "250 ml",
                                         "nutriments": {"energy-kcal_100g": 46, "energy_100g": 192,
                                                        "proteins_100g": 1.0, "carbohydrates_100g": 6.6,
                                                        "fat_100g": 1.5, "fiber_100g": 0.8,
                                                        "energy-kcal_serving": 115}}}
    return io.BytesIO(json.dumps(body).encode())
