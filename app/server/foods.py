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
USDA_SEARCH_URL = "https://api.nal.usda.gov/fdc/v1/foods/search"
# Generic foods only (raw, cooked, typical dishes); branded USDA foods are mostly US products.
USDA_TYPES = "Foundation,SR Legacy,Survey (FNDDS)"
# FoodData Central nutrient ids. Energy comes under several ids depending on the data type.
USDA_IDS = {"protein": (1003,), "carbs": (1005,), "fat": (1004,), "fibre": (1079,), "kcal": (1008, 2048, 2047)}
USDA_KJ = 1062
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

    def __init__(self, cfg, opener=None, clock=time.time, usda_key=None):
        self.enabled = bool(cfg.get("off_enabled"))
        self.usda_key_file = cfg.get("usda_key_file")
        self._usda_key = usda_key
        self._usda_last = 0.0
        self.retry_wait = float(cfg.get("retry_wait", 0.5))
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

    def _usda_key_now(self):
        if self._usda_key:
            return self._usda_key
        if not self.usda_key_file:
            return None
        try:
            return Path(self.usda_key_file).read_text(encoding="utf-8").strip() or None
        except OSError:
            raise SourceError("The USDA key file can't be read on the server.")

    def usda_search(self, q):
        """Generic foods by name from USDA FoodData Central, per 100 g. Uses the key already on the server."""
        q = " ".join((q or "").split())
        if len(q) < 2:
            raise ValueError("Type at least two letters to search.")
        if len(q) > 80:
            raise ValueError("That search is too long.")
        key = self._usda_key_now()
        if not key:
            raise SourceError("USDA search isn't set up on the server.")
        ck = "usda:" + q.lower()
        now = self._clock()
        with self._lock:
            hit = self._cache.get(ck)
            if hit and now - hit[0] < 86400:
                return hit[1]
            if now - self._usda_last < 1.0:
                raise SourceError("Searches are going a bit fast. Try again in a moment.")
            self._usda_last = now
        url = USDA_SEARCH_URL + "?" + urllib.parse.urlencode(
            {"query": us_spelling(q), "dataType": USDA_TYPES, "pageSize": SEARCH_SIZE, "api_key": key})
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        for attempt in (1, 2):  # USDA's search fails now and then; one quick retry usually works
            try:  # errors never include the URL, which carries the key
                with self._open(req, timeout=self.timeout) as r:
                    data = json.loads(r.read() or b"null")
                break
            except urllib.error.HTTPError as e:
                if attempt == 2 or e.code in (400, 401, 403, 404, 429):
                    raise SourceError(f"USDA FoodData Central answered {e.code}")
            except (urllib.error.URLError, OSError):
                if attempt == 2:
                    raise SourceError("USDA FoodData Central is unreachable")
            except ValueError:
                if attempt == 2:
                    raise SourceError("USDA FoodData Central sent something that isn't JSON")
            time.sleep(self.retry_wait)
        items = []
        for f in (data or {}).get("foods") or []:
            if isinstance(f, dict):
                item = _usda_food(f, now)
                if item["name"] and item["per100"]["kcal"] is not None:
                    items.append(item)
        out = {"query": q, "items": items, "source": "USDA FoodData Central"}
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[ck] = (now, out)
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


# UK words USDA doesn't know, swapped only in what is sent to USDA.
UK_US = {"yoghurt": "yogurt", "courgette": "zucchini", "aubergine": "eggplant", "coriander": "cilantro",
         "mince": "ground", "prawn": "shrimp", "prawns": "shrimp", "rocket": "arugula", "porridge": "oatmeal",
         "crisps": "chips", "chickpea": "chickpeas", "swede": "rutabaga", "beetroot": "beets",
         "spring onion": "scallion", "spring onions": "scallions", "jacket potato": "baked potato",
         "fibre": "fiber", "flavoured": "flavored", "wholemeal": "whole wheat", "single cream": "light cream",
         "double cream": "heavy cream", "icing sugar": "powdered sugar", "biscuit": "cookie", "biscuits": "cookies"}


def us_spelling(q):
    out = q.lower()
    for uk in sorted(UK_US, key=len, reverse=True):
        out = re.sub(r"\b" + re.escape(uk) + r"\b", UK_US[uk], out)
    return out


def _usda_food(f, now):
    """One FoodData Central search hit. Values are per 100 g."""
    vals = {}
    for n in f.get("foodNutrients") or []:
        if isinstance(n, dict) and n.get("nutrientId") is not None:
            vals.setdefault(int(n["nutrientId"]), (_num(n.get("value")), (n.get("unitName") or "").upper()))
    per100 = {}
    for k, ids in USDA_IDS.items():
        per100[k] = next((vals[i][0] for i in ids if i in vals and vals[i][0] is not None
                          and (k != "kcal" or vals[i][1] in ("KCAL", ""))), None)
    if per100["kcal"] is None and vals.get(USDA_KJ, (None,))[0] is not None:
        per100["kcal"] = round(vals[USDA_KJ][0] / 4.184, 1)
    name = (f.get("description") or "").strip()
    return {"found": True, "code": "", "fdc_id": f.get("fdcId"), "name": name[:1].upper() + name[1:].lower() if name.isupper() else name,
            "brand": "USDA", "serving": None, "per100": per100, "per_serving": {k: None for k in NUTS},
            "source": "USDA FoodData Central", "retrieved": datetime.date.fromtimestamp(now).isoformat()}


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

    def search(self, q, source="off"):
        if source == "usda":
            return self.lookup.usda_search(q)
        if source != "off":
            raise ValueError("Unknown food library.")
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
SAMPLE_USDA = [
    {"fdcId": 1, "description": "Yogurt, Greek, plain, nonfat", "dataType": "SR Legacy",
     "foodNutrients": [{"nutrientId": 1008, "unitName": "KCAL", "value": 59}, {"nutrientId": 1003, "unitName": "G", "value": 10.2},
                       {"nutrientId": 1005, "unitName": "G", "value": 3.6}, {"nutrientId": 1004, "unitName": "G", "value": 0.4}]},
    {"fdcId": 2, "description": "Chicken, breast, meat only, cooked, roasted", "dataType": "Foundation",
     "foodNutrients": [{"nutrientId": 2047, "unitName": "KCAL", "value": 165}, {"nutrientId": 1003, "unitName": "G", "value": 31},
                       {"nutrientId": 1005, "unitName": "G", "value": 0}, {"nutrientId": 1004, "unitName": "G", "value": 3.6}]},
    {"fdcId": 3, "description": "RICE, WHITE, COOKED", "dataType": "Foundation",
     "foodNutrients": [{"nutrientId": 1062, "unitName": "KJ", "value": 544}, {"nutrientId": 1003, "unitName": "G", "value": 2.7}]},
]


def sample_opener(req, timeout=None):
    """Sample-data stand-in for Open Food Facts: one made-up product, the rest not found."""
    if "api.nal.usda.gov" in req.full_url:
        q = urllib.parse.parse_qs(req.full_url.split("?", 1)[1]).get("query", [""])[0].lower()
        found = [f for f in SAMPLE_USDA if any(w in f["description"].lower() for w in q.split())]
        return io.BytesIO(json.dumps({"totalHits": len(found), "foods": found}).encode())
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
