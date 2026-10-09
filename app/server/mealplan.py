"""Meal-plan week: CookTrace recipes planned into breakfast, lunch, dinner and snacks.

The plan is kept by the app in a small JSON file on the server. CookTrace's
API can read its own planned entries but can't add them, so those are shown
alongside, read-only. A planned meal isn't an intake record: it becomes one
only when Craig taps Log, which adds the recipe to NutriTrace as a food (one
serving) and logs his portions. Nothing here goes to Max or any model.
"""

import copy
import datetime
import json
import secrets
import threading
from pathlib import Path

from health import MEALS, NotFound, Refused, per_serving
from sources import SourceError

MEAL_KEYS = ("breakfast", "lunch", "dinner", "snack")
MEAL_INDEX = {"breakfast": 0, "lunch": 1, "dinner": 2, "snack": 3}  # NutriTrace meal numbers
PLAN_BACK_DAYS = 14
PLAN_AHEAD_DAYS = 56
MAX_SLOTS = 2000
RECIPE_BRAND = "CookTrace recipe"


class MealPlan:
    def __init__(self, path, health, hlog):
        self.path = Path(path) if path else None
        self.h = health
        self.log = hlog
        self._lock = threading.Lock()
        self._slots = None

    # ------------------------------------------------------------ storage

    def _load(self):
        if self._slots is None:
            if self.path is None or not self.path.exists():
                self._slots = []
            else:
                try:
                    self._slots = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise SourceError("The meal plan file can't be read.")
        return self._slots

    def _commit(self, before):
        if self.path is None:
            return
        try:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps(self._slots, indent=1), encoding="utf-8")
            tmp.chmod(0o640)
            tmp.replace(self.path)
        except OSError:
            self._slots = before
            raise SourceError("The meal plan couldn't be saved on the server.")

    def _find(self, sid):
        for s in self._load():
            if s["id"] == sid:
                return s
        raise ValueError("That planned meal isn't in the plan any more.")

    # ------------------------------------------------------------ reading

    def _ct(self):
        return self.h._need(self.h.ct, "CookTrace")

    def recipes(self, q=""):
        ct = self._ct()
        q = (q or "").strip()[:80]
        out = ct.get("/recipes", ttl=120, q=q or None, limit=50) or {}
        return [{"id": r.get("id"), "name": r.get("name"), "servings": r.get("servings"), **per_serving(r)}
                for r in out.get("items") or []]

    def view(self, week=None):
        today = self.h.today()
        d = datetime.date.fromisoformat(week) if week else today
        monday = d - datetime.timedelta(days=d.weekday())
        sunday = monday + datetime.timedelta(days=6)
        days = [(monday + datetime.timedelta(days=n)).isoformat() for n in range(7)]
        with self._lock:
            slots = [dict(s) for s in self._load() if days[0] <= s["date"] <= days[-1]]
        cook = {"ok": True, "data": []}
        try:
            items = (self._ct().get("/cook-diary", ttl=120, date_from=days[0], date_to=sunday.isoformat(),
                                    kind="planned", limit=100) or {}).get("items") or []
            cook["data"] = [{"date": x.get("date"), "meal": x.get("meal_type") or "dinner", "recipe": x.get("recipe_name"),
                             "recipe_id": x.get("recipe_id"), "servings": x.get("servings")} for x in items]
        except SourceError as e:
            cook = {"ok": False, "error": str(e)}
        slots.sort(key=lambda s: (s["date"], MEAL_INDEX.get(s["meal"], 9)))
        return {"week": days, "today": today.isoformat(), "slots": slots, "cooktrace_planned": cook,
                "prev": (monday - datetime.timedelta(days=7)).isoformat(),
                "next": (monday + datetime.timedelta(days=7)).isoformat(),
                "can_log_from": (today - datetime.timedelta(days=31)).isoformat()}

    # ------------------------------------------------------------ changes

    def add(self, body):
        today = self.h.today()
        try:
            d = datetime.date.fromisoformat(body.get("date") or "")
        except (TypeError, ValueError):
            raise ValueError("date must look like 2026-10-09.")
        if not -PLAN_BACK_DAYS <= (d - today).days <= PLAN_AHEAD_DAYS:
            raise ValueError(f"Plan from {PLAN_BACK_DAYS} days back to {PLAN_AHEAD_DAYS} days ahead.")
        meal = body.get("meal")
        if meal not in MEAL_KEYS:
            raise ValueError("meal must be breakfast, lunch, dinner or snack.")
        rid = body.get("recipe_id")
        if isinstance(rid, bool) or not isinstance(rid, (int, str)) or not str(rid).strip() or len(str(rid)) > 40:
            raise ValueError("recipe_id is required.")
        try:
            portions = float(body.get("portions", 1))
        except (TypeError, ValueError):
            raise ValueError("portions must be a number.")
        if not 0.25 <= portions <= 10:
            raise ValueError("portions must be between 0.25 and 10.")
        note = body.get("note") or ""
        if not isinstance(note, str) or len(note) > 200:
            raise ValueError("note must be text of up to 200 characters.")
        try:
            recipe = self._ct().get(f"/recipes/{rid}", ttl=120)
        except NotFound:
            recipe = None
        if not recipe or not recipe.get("name"):
            raise ValueError("That recipe isn't in CookTrace.")
        slot = {"id": secrets.token_hex(6), "date": d.isoformat(), "meal": meal, "recipe_id": recipe.get("id", rid),
                "recipe": recipe["name"], "servings": recipe.get("servings"), "portions": round(portions, 2),
                "per_serving": per_serving(recipe), "status": "planned", "note": note.strip()}
        with self._lock:
            slots = self._load()
            if len(slots) >= MAX_SLOTS:
                raise ValueError("The meal plan is full. Remove some old meals first.")
            before = copy.deepcopy(slots)
            slots.append(slot)
            self._commit(before)
        return slot

    def change(self, body):
        """skip, unskip or remove a planned meal. A logged meal stays: its NutriTrace entry is real."""
        action = body.get("action")
        if action not in ("skip", "unskip", "remove"):
            raise ValueError("action must be skip, unskip or remove.")
        with self._lock:
            slots = self._load()
            before = copy.deepcopy(slots)
            s = self._find(body.get("id"))
            if s["status"] == "logged":
                raise ValueError("This meal is logged in NutriTrace, so it stays in the plan.")
            if action == "remove":
                slots.remove(s)
            else:
                s["status"] = "skipped" if action == "skip" else "planned"
            self._commit(before)
        return {"id": body.get("id"), "action": action}

    def log_meal(self, body):
        """Add the recipe to NutriTrace as one serving (once), then log the planned portions."""
        with self._lock:
            s = dict(self._find(body.get("id")))
        if s["status"] == "logged":
            raise ValueError("This meal is already logged.")
        ps = s.get("per_serving") or {}
        try:
            recipe = self._ct().get(f"/recipes/{s['recipe_id']}", ttl=60)
            ps = per_serving(recipe)
        except SourceError:
            pass  # use the snapshot taken when it was planned
        if not ps.get("kcal"):
            raise ValueError("This recipe has no calories in CookTrace yet. Open it there and run Recompute first.")
        made = self.log.add_food({"name": s["recipe"][:200], "brand": RECIPE_BRAND, "portion": 1, "unit": "serving",
                                  "nutrition": {k: ps.get(k) for k in ("kcal", "protein", "carbs", "fat", "fibre")}})
        out = self.log.log_food({"food_id": made["food"]["id"], "meal": MEAL_INDEX[s["meal"]],
                                 "quantity": s["portions"], "date": s["date"]})
        with self._lock:
            slots = self._load()
            before = copy.deepcopy(slots)
            try:
                live = self._find(s["id"])
            except ValueError:
                live = None
            if live is not None:
                live["status"] = "logged"
                live["logged_kcal"] = round(ps["kcal"] * s["portions"])
                self._commit(before)
        return {"date": out["date"], "meal": MEALS.get(MEAL_INDEX[s["meal"]]), "recipe": s["recipe"],
                "kcal": round(ps["kcal"] * s["portions"])}

    def cooked(self, body):
        """Record in CookTrace's cook diary that the planned recipe was cooked."""
        with self._lock:
            s = dict(self._find(body.get("id")))
        today = self.h.today()
        if s["date"] > today.isoformat():
            raise ValueError("You can mark a meal cooked on its day or later.")
        ct = self._ct()
        try:
            rid = int(s["recipe_id"])
        except (TypeError, ValueError):
            rid = s["recipe_id"]
        payload = {"recipe_id": rid, "date": s["date"], "meal_type": s["meal"]}
        if s.get("servings"):
            payload["servings"] = s["servings"]
        try:
            ct.send("POST", "/cook-diary", payload)
        except Refused as e:
            raise ValueError(str(e))
        with self._lock:
            slots = self._load()
            before = copy.deepcopy(slots)
            try:
                live = self._find(s["id"])
                live["cooked"] = True
                self._commit(before)
            except ValueError:
                pass
        return {"id": s["id"], "date": s["date"], "recipe": s["recipe"]}
