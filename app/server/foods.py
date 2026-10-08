"""Barcode lookup and saved foods for the Food screen.

Lookup asks Open Food Facts from the server only, and only once Craig has said
yes (config foods.off_enabled). Answers are cached in memory for a day, and
calls are spaced at least a second apart. Saving writes a food to NutriTrace's
catalogue with a write-scoped key that is separate from the read-only key.
Saving stays off until the NutriTrace endpoints are confirmed (foods.save_enabled).

Nothing here is passed to Max or any model. Unknown nutrients stay None, never 0.
"""

import datetime
import io
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sources import SourceError

OFF_URL = "https://world.openfoodfacts.org/api/v2/product/{code}.json"
OFF_FIELDS = "product_name,brands,serving_size,nutriments"
USER_AGENT = "HermesApp/1.0 (personal use)"
CODE = re.compile(r"\d{8,14}")  # EAN-8, UPC-A, EAN-13, GTIN-14
NUTS = ("kcal", "protein", "carbs", "fat", "fibre")
# Open Food Facts field stems, and the names NutriTrace's catalogue uses.
OFF_STEM = {"kcal": "energy-kcal", "protein": "proteins", "carbs": "carbohydrates", "fat": "fat", "fibre": "fiber"}
TRACE_NAME = {"kcal": "calories", "protein": "proteins", "carbs": "carbohydrates", "fat": "fat", "fibre": "fiber"}
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
        req = urllib.request.Request(OFF_URL.format(code=code) + "?" + urllib.parse.urlencode({"fields": OFF_FIELDS}),
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                data = json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise SourceError(f"Open Food Facts answered {e.code}")
        except (urllib.error.URLError, OSError):
            raise SourceError("Open Food Facts is unreachable")
        except ValueError:
            raise SourceError("Open Food Facts sent something that isn't JSON")
        if not isinstance(data, dict) or data.get("status") != 1:
            out = {"found": False, "code": code}  # not cached: it may be added later
            return out
        p = data.get("product") or {}
        n = p.get("nutriments") or {}
        out = {"found": True, "code": code, "name": (p.get("product_name") or "").strip(),
               "brand": (p.get("brands") or "").split(",")[0].strip(), "serving": p.get("serving_size") or None,
               "per100": _nutriments(n, "_100g"), "per_serving": _nutriments(n, "_serving"),
               "source": "Open Food Facts", "retrieved": datetime.date.fromtimestamp(now).isoformat()}
        with self._lock:
            if len(self._cache) > 500:
                self._cache.clear()
            self._cache[code] = (now, out)
        return out


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


def _trace_nutrition(nut):
    return {TRACE_NAME[k]: v for k, v in nut.items() if v is not None}


class Catalogue:
    """Saved foods in NutriTrace. Reads use the read-only key; saving uses the write key."""

    def __init__(self, cfg, opener=None):
        self.base = cfg["url"].rstrip("/") + "/api/v1"
        self.read_key_file = cfg.get("key_file")
        self.write_key_file = cfg.get("write_key_file")
        self.list_path = cfg.get("foods_list_path")      # confirmed against NutriTrace's schema before use
        self.create_path = cfg.get("foods_create_path")  # confirmed against NutriTrace's schema before use
        self.save_enabled = bool(cfg.get("save_enabled"))
        self.timeout = float(cfg.get("timeout", 6))
        self._open = opener or urllib.request.urlopen

    def _key(self, path):
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except (OSError, TypeError):
            raise SourceError("NutriTrace key isn't readable")

    def _call(self, method, path, key_file, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self._key(key_file), "Accept": "application/json",
            "User-Agent": "hermes-app", **({"Content-Type": "application/json"} if data else {})})
        try:
            with self._open(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            why = {401: "refused the key", 403: "key lacks the scope", 404: "public API not enabled",
                   429: "rate limited"}.get(e.code, f"answered {e.code}")
            raise SourceError(f"NutriTrace {why}")
        except (urllib.error.URLError, OSError):
            raise SourceError("NutriTrace is unreachable")
        except ValueError:
            raise SourceError("NutriTrace sent something that isn't JSON")

    def saved(self):
        if not self.list_path:
            raise SourceError("Saved foods aren't connected to NutriTrace yet.")
        r = self._call("GET", self.list_path, self.read_key_file) or {}
        items = r.get("items") or r.get("foods") or []
        return [{"id": x.get("id"), "name": x.get("name") or "?", "brand": x.get("brand"),
                 "serving": x.get("serving") or x.get("serving_size"),
                 "per_serving": _nutriments_from_trace(x.get("per_serving") or x.get("nutrition") or {}),
                 "source": x.get("source"), "retrieved": x.get("retrieved")} for x in items[:200]]

    def save(self, body):
        food = check_food(body)
        if not self.save_enabled or not self.create_path:
            raise SourceError("Saving to NutriTrace isn't switched on yet. The food is kept on this screen.")
        payload = {"name": food["name"], "brand": food["brand"] or None, "serving": food["serving"] or None,
                   "source": food["source"], "retrieved": food["retrieved"],
                   "per_100g": _trace_nutrition(food["per100"]), "per_serving": _trace_nutrition(food["per_serving"])}
        r = self._call("POST", self.create_path, self.write_key_file, payload)
        return {"saved": True, "id": (r or {}).get("id") if isinstance(r, dict) else None, "name": food["name"]}


def _nutriments_from_trace(n):
    out = {}
    for k in NUTS:
        v = None
        for key in (TRACE_NAME[k], k):
            v = _num(n.get(key)) if isinstance(n, dict) else None
            if v is not None:
                break
        out[k] = v
    return out


class Foods:
    """What the Food screen's barcode and saved-foods routes call."""

    def __init__(self, lookup, catalogue=None):
        self.lookup = lookup
        self.catalogue = catalogue

    def product(self, code):
        return {"product": self.lookup.product(code)}

    def saved(self):
        if self.catalogue is None:
            raise SourceError("Saved foods aren't connected to NutriTrace yet.")
        return {"items": self.catalogue.saved()}

    def save(self, body):
        if self.catalogue is None:
            raise SourceError("Saved foods aren't connected to NutriTrace yet.")
        return {"result": self.catalogue.save(body)}


SAMPLE_CODE = "5000000000017"  # made-up code for sample-data mode


def sample_opener(req, timeout=None):
    """Sample-data stand-in for Open Food Facts: one made-up product, the rest not found."""
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
