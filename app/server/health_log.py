"""Health logging from the app: foods into NutriTrace, water, and gym sets
into LiftTrace.

Writes use the "hermes-write" tokens (mcp:write), separate from the read
tokens. Everything is checked here before it is sent, and the Trace apps
check again. Nothing is deleted or edited from here, only added.
"""

import datetime
import re

from health import MEALS, Refused, nutrients
from sources import SourceError

DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
BARCODE = re.compile(r"\d{8,14}")
UNITS = ("g", "ml", "serving", "piece", "slice", "cup", "tbsp", "tsp", "oz")
# App nutrient names -> NutriTrace catalogue keys.
NT_KEYS = {"kcal": "calories", "protein": "proteins", "carbs": "carbohydrates", "fat": "fat", "fibre": "fiber"}
MAX_BACKDATE_DAYS = 31


def _text(body, key, limit, required=False):
    v = body.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise ValueError(f"{key} is required.")
        return None
    if not isinstance(v, str):
        raise ValueError(f"{key} must be text.")
    v = v.strip()
    if len(v) > limit:
        raise ValueError(f"{key} is longer than {limit} characters.")
    return v


def _number(body, key, lo, hi, required=False, whole=False):
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
    if n != n or not lo <= n <= hi:
        raise ValueError(f"{key} must be between {lo:g} and {hi:g}.")
    if whole:
        if n != int(n):
            raise ValueError(f"{key} must be a whole number.")
        return int(n)
    return round(n, 2)


class HealthLog:
    def __init__(self, health):
        self.h = health

    def _date(self, body):
        """Today unless a date is given; no future days, and at most a month back."""
        v = body.get("date")
        today = self.h.today()
        if v in (None, ""):
            return today.isoformat()
        if not isinstance(v, str) or not DATE.fullmatch(v):
            raise ValueError("date must look like 2026-10-09.")
        d = datetime.date.fromisoformat(v)
        if d > today:
            raise ValueError("Can't log a future day.")
        if (today - d).days > MAX_BACKDATE_DAYS:
            raise ValueError(f"Can't log more than {MAX_BACKDATE_DAYS} days back from here.")
        return v

    # ------------------------------------------------------------ search

    def search_foods(self, q):
        nt = self.h._need(self.h.nt, "NutriTrace")
        q = (q or "").strip()[:80]
        out = nt.get("/foods", ttl=30, q=q or None, limit=25) or {}
        return {"foods": [_food(x) for x in out.get("items") or []], "total": out.get("total")}

    def search_exercises(self, q):
        lt = self.h._need(self.h.lt, "LiftTrace")
        q = (q or "").strip()[:60]
        out = lt.get("/exercises", ttl=300, query=q, limit=20) or {}
        return {"exercises": [{"id": x.get("id"), "name": x.get("name"), "category": x.get("category"),
                               "equipment": x.get("equipment"), "set_type": x.get("set_type")}
                              for x in out.get("exercises") or []]}

    def recent_foods(self, days=14):
        """Foods logged in the last two weeks, newest first, one row per food, ready to log again."""
        nt = self.h._need(self.h.nt, "NutriTrace")
        today = self.h.today()
        seen = {}
        for n in range(days):
            d = today - datetime.timedelta(days=n)
            try:
                diary = nt.get(f"/diary/{d.isoformat()}", ttl=self.h._ttl(d)) or {}
            except SourceError:
                if n == 0:
                    raise
                continue
            for i in reversed(diary.get("items") or []):
                fid = i.get("food_server_id") or i.get("food_id")
                if not isinstance(fid, int):
                    continue
                if fid in seen:
                    seen[fid]["times"] += 1
                    continue
                per = nutrients(i.get("nutrition") or {})  # NutriTrace keeps nutrition per serving
                portion = i.get("portion")
                seen[fid] = {"id": fid, "name": i.get("name") or "?", "brand": i.get("brand"),
                             "portion": portion, "unit": i.get("unit"), **per,
                             "last": d.isoformat(), "times": 1, "meal": MEALS.get(i.get("meal"))}
        return {"foods": list(seen.values())[:40]}

    def saved_meals(self, q):
        """NutriTrace's saved meals (not recipes), favourites and most used first."""
        nt = self.h._need(self.h.nt, "NutriTrace")
        q = (q or "").strip()[:80]
        out = nt.get("/meals/search", ttl=60, query=q or None, limit=30) or {}
        return {"meals": [{"id": m.get("id"), "name": m.get("name"), "favourite": bool(m.get("favorite")),
                           "uses": m.get("usage_count"), **nutrients(m.get("nutrition") or {})}
                          for m in out.get("items") or [] if not m.get("is_recipe")]}

    # ------------------------------------------------------------ writes

    def log_meal(self, body):
        """Log one of NutriTrace's saved meals onto a day."""
        nt = self.h._need(self.h.nt, "NutriTrace")
        meal_id = _number(body, "meal_id", 1, 10 ** 9, required=True, whole=True)
        meal = _number(body, "meal", 0, max(MEALS), whole=True)
        d = self._date(body)
        payload = {"meal_id": meal_id}
        if meal is not None:
            payload["meal"] = meal
        out = nt.send("POST", f"/diary/{d}/meal", payload) or {}
        return {"date": d, "meal": MEALS.get(meal), "count": out.get("count") or len(out.get("items") or []) or None}

    def quick_add(self, body):
        """Calories (and macros if known) with no food picked. Kept in NutriTrace as a "Quick add" food
        whose name carries its values, so the same numbers reuse the same food."""
        kcal = _number(body, "kcal", 1, 5000, required=True)
        nut = {"kcal": kcal}
        for k in ("protein", "carbs", "fat", "fibre"):
            v = _number(body, k, 0, 500)
            if v is not None:
                nut[k] = v
        label = _text(body, "name", 60) or "Quick add"
        bits = [f"{kcal:g} kcal"] + [f"{k[0].upper()} {nut[k]:g}" for k in ("protein", "carbs", "fat") if k in nut]
        made = self.add_food({"name": f"{label} · {' · '.join(bits)}", "brand": "Quick add", "portion": 1,
                              "unit": "serving", "nutrition": nut})
        out = self.log_food({"food_id": made["food"]["id"], "meal": body.get("meal"), "quantity": 1,
                             "date": body.get("date")})
        return out

    def copy_meal(self, body):
        """Log again every food from one meal on an earlier day (same amounts) onto a day."""
        nt = self.h._need(self.h.nt, "NutriTrace")
        src = body.get("from_date")
        if not isinstance(src, str) or not DATE.fullmatch(src):
            raise ValueError("from_date must look like 2026-10-09.")
        from_meal = _number(body, "from_meal", 0, max(MEALS), required=True, whole=True)
        meal = _number(body, "meal", 0, max(MEALS), whole=True)
        d = self._date(body)
        diary = nt.get(f"/diary/{src}", ttl=30) or {}
        items = [i for i in diary.get("items") or [] if i.get("meal") == from_meal]
        if not items:
            raise ValueError("That meal has nothing logged.")
        logged, skipped = [], []
        for i in items:
            fid = i.get("food_server_id") or i.get("food_id")
            if not isinstance(fid, int):
                skipped.append(i.get("name") or "?")
                continue
            payload = {"food_id": fid, "meal": meal if meal is not None else from_meal,
                       "quantity": i.get("quantity") or 1}
            if isinstance(i.get("portion"), (int, float)):
                payload["portion"] = i["portion"]
            try:
                nt.send("POST", f"/diary/{d}/food", payload)
            except Refused:
                if "portion" not in payload:
                    skipped.append(i.get("name") or "?")
                    continue
                payload.pop("portion")
                try:
                    nt.send("POST", f"/diary/{d}/food", payload)
                except Refused:
                    skipped.append(i.get("name") or "?")
                    continue
            logged.append(i.get("name") or "?")
        if not logged:
            raise ValueError("None of those foods could be logged again (they weren't picked from the catalogue).")
        return {"date": d, "meal": MEALS.get(meal if meal is not None else from_meal), "logged": logged,
                "skipped": skipped}

    def add_food(self, body):
        """Put a food into NutriTrace's catalogue (values per the stated portion).
        If the same name and brand already exist, that food is returned instead."""
        nt = self.h._need(self.h.nt, "NutriTrace")
        name = _text(body, "name", 200, required=True)
        brand = _text(body, "brand", 100)
        portion = _number(body, "portion", 0.1, 10000, required=True)
        unit = _text(body, "unit", 20, required=True)
        if unit not in UNITS:
            raise ValueError(f"unit must be one of: {', '.join(UNITS)}.")
        barcode = _text(body, "barcode", 14)
        if barcode and not BARCODE.fullmatch(barcode):
            raise ValueError("barcode must be 8 to 14 digits.")
        nut = body.get("nutrition") or {}
        if not isinstance(nut, dict):
            raise ValueError("nutrition must be an object.")
        clean = {}
        for k, nk in NT_KEYS.items():
            v = _number(nut, k, 0, 10000 if k == "kcal" else 1000)
            if v is not None:
                clean[nk] = v
        if "calories" not in clean:
            raise ValueError("kcal is required.")
        payload = {"name": name, "portion": portion, "unit": unit, "nutrition": clean}
        if brand:
            payload["brand"] = brand
        if barcode:
            payload["barcode"] = barcode
        try:
            made = nt.send("POST", "/foods", payload)
        except Refused as e:
            if e.code == 409 and e.detail.get("id"):
                return {"food": {"id": e.detail["id"], "name": name, "brand": brand}, "existing": True}
            raise
        return {"food": _food(made or {}), "existing": False}

    def log_food(self, body):
        nt = self.h._need(self.h.nt, "NutriTrace")
        food_id = _number(body, "food_id", 1, 10 ** 9, required=True, whole=True)
        meal = _number(body, "meal", 0, max(MEALS), whole=True)
        quantity = _number(body, "quantity", 0.05, 50)
        d = self._date(body)
        payload = {"food_id": food_id, "meal": meal if meal is not None else 0}
        if quantity is not None:
            payload["quantity"] = quantity
        out = nt.send("POST", f"/diary/{d}/food", payload) or {}
        logged = out.get("logged") or {}
        return {"date": d, "logged": {"name": logged.get("name"), "meal": MEALS.get(payload["meal"]),
                                      "quantity": logged.get("quantity"), "portion": logged.get("portion"),
                                      "unit": logged.get("unit")}}

    def log_water(self, body):
        nt = self.h._need(self.h.nt, "NutriTrace")
        ml = _number(body, "amount_ml", 10, 3000, required=True, whole=True)
        d = self._date(body)
        out = nt.send("POST", f"/diary/{d}/water", {"amount_ml": ml}) or {}
        return {"date": d, "amount_ml": ml, "total_ml": out.get("total_ml_on_day")}

    def log_set(self, body):
        lt = self.h._need(self.h.lt, "LiftTrace")
        ex = _number(body, "exercise_id", 1, 10 ** 9, required=True, whole=True)
        duration = _number(body, "duration_sec", 1, 86400, whole=True)
        reps = _number(body, "reps", 0, 500, required=duration is None, whole=True)
        weight = _number(body, "weight", 0, 1000)
        rpe = _number(body, "rpe", 1, 10)
        warmup = body.get("warmup", False)
        if not isinstance(warmup, bool):
            raise ValueError("warmup must be true or false.")
        d = self._date(body)
        payload = {"exercise_id": ex, "warmup": warmup}
        if duration is not None:
            payload["duration_sec"] = duration
        else:
            payload["reps"] = reps
        for k, v in (("weight", weight), ("rpe", rpe)):
            if v is not None:
                payload[k] = v
        out = lt.send("POST", f"/workouts/{d}/sets", payload) or {}
        return {"date": d, "set": {k: payload.get(k) for k in ("reps", "weight", "rpe", "duration_sec", "warmup")},
                "exercise": out.get("exercise_name"), "sets_on_exercise": out.get("sets_on_exercise")}


def _food(x):
    return {"id": x.get("id"), "name": x.get("name"), "brand": x.get("brand"), "barcode": x.get("barcode"),
            "portion": x.get("portion"), "unit": x.get("unit"), **nutrients(x.get("nutrition") or {})}


__all__ = ["HealthLog", "Refused", "SourceError"]
