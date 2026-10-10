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
    def __init__(self, path, health, hlog, strategy=None):
        self.strategy = strategy  # day targets, when a strategy is set
        self.path = Path(path) if path else None
        self._prefs = None
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

    def _targets(self, days):
        out = {}
        for d in days:
            t = self.strategy.targets_for(datetime.date.fromisoformat(d)) if self.strategy else None
            if not t:
                try:
                    t = self.h.goals()
                except SourceError:
                    t = None
            if t and t.get("kcal"):
                out[d] = {"kcal": t["kcal"], "protein": t.get("protein")}
        return out

    def propose(self, body):
        """The app planner's suggestion for the rest of a week. Nothing is saved."""
        ctx = plan_context(self, body)
        return {"week": ctx["week"], "items": propose_week(ctx), "source": "app", "targets": ctx["kcal_targets"],
                "uses": ctx["uses"]}

    # ------------------------------------------------------------ preferences

    def _prefs_path(self):
        return self.path.with_name("meal-prefs.json") if self.path else None

    def prefs(self):
        with self._lock:
            if self._prefs is None:
                p = self._prefs_path()
                try:
                    self._prefs = ({**DEFAULT_PREFS, **json.loads(p.read_text(encoding="utf-8"))}
                                   if p and p.exists() else copy.deepcopy(DEFAULT_PREFS))
                except (OSError, ValueError):
                    raise SourceError("The meal preferences file can't be read.")
            return copy.deepcopy(self._prefs)

    def set_prefs(self, body):
        clean = check_prefs(body.get("prefs"))
        self.prefs()
        with self._lock:
            before = self._prefs
            self._prefs = clean
            p = self._prefs_path()
            if p:
                try:
                    tmp = p.with_name(p.name + ".tmp")
                    tmp.write_text(json.dumps(clean, indent=1), encoding="utf-8")
                    tmp.chmod(0o640)
                    tmp.replace(p)
                except OSError:
                    self._prefs = before
                    raise SourceError("The meal preferences couldn't be saved on the server.")
        return {"prefs": clean}

    def ratings(self):
        """Craig's CookTrace ratings over the last six months: recipe id -> average stars."""
        today = self.h.today()
        try:
            items = (self._ct().get("/cook-diary", ttl=600, date_from=(today - datetime.timedelta(days=183)).isoformat(),
                                    date_to=today.isoformat(), kind="cooked", limit=500) or {}).get("items") or []
        except SourceError:
            return {}
        stars = {}
        for x in items:
            if x.get("rating") and x.get("recipe_id") is not None:
                stars.setdefault(str(x["recipe_id"]), []).append(float(x["rating"]))
        return {k: round(sum(v) / len(v), 1) for k, v in stars.items()}

    def apply(self, body):
        """Add an accepted proposal, one planned meal per item."""
        items = body.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_APPLY:
            raise ValueError(f"items must be a list of 1 to {MAX_APPLY} meals.")
        for it in items:
            if not isinstance(it, dict):
                raise ValueError("Each item must be an object.")
        added = [self.add({k: it.get(k) for k in ("date", "meal", "recipe_id", "portions")}) for it in items]
        return {"added": len(added), "date": added[0]["date"]}

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


# ---------------------------------------------------------------- proposals

MEAL_SHARE = {"breakfast": 0.25, "lunch": 0.35, "dinner": 0.40}  # snacks are left to Craig
PORTIONS = (0.5, 1, 1.5, 2, 2.5, 3)
MAX_APPLY = 28
BREAKFAST_WORDS = ("oat", "porridge", "granola", "muesli", "egg", "omelette", "yoghurt", "yogurt", "pancake",
                   "toast", "smoothie", "breakfast", "bagel", "waffle", "chia", "skyr", "cereal")
DEFAULT_PREFS = {"favourites": [], "never": [], "avoid": [], "plan_meals": ["breakfast", "lunch", "dinner"],
                 "leftovers": True, "max_repeats": 3, "notes": ""}
GOAL_WORDS = {"lose": "losing fat", "maintain": "maintaining weight", "gain": "building (lean gain)"}


def breakfast_like(name):
    n = (name or "").lower()
    return any(w in n for w in BREAKFAST_WORDS)


def check_prefs(body):
    """Meal preferences as set on the Meal plan screen. Raises ValueError on bad input."""
    if not isinstance(body, dict):
        raise ValueError("prefs must be an object.")

    def ids(key):
        v = body.get(key) or []
        if not isinstance(v, list) or len(v) > 200 or any(isinstance(x, bool) or not isinstance(x, (int, str)) or len(str(x)) > 40 for x in v):
            raise ValueError(f"{key} must be a list of recipe ids.")
        return list(dict.fromkeys(str(x) for x in v))

    avoid = body.get("avoid") or []
    if not isinstance(avoid, list) or len(avoid) > 50 or any(not isinstance(x, str) or not x.strip() or len(x) > 40 for x in avoid):
        raise ValueError("avoid must be a list of up to 50 foods.")
    meals = body.get("plan_meals") or []
    if not isinstance(meals, list) or any(m not in MEAL_SHARE for m in meals) or not meals:
        raise ValueError("Plan at least one of breakfast, lunch and dinner.")
    try:
        reps = int(body.get("max_repeats", 3))
    except (TypeError, ValueError):
        raise ValueError("max_repeats must be a number.")
    if not 1 <= reps <= 7:
        raise ValueError("max_repeats must be between 1 and 7.")
    notes = body.get("notes") or ""
    if not isinstance(notes, str) or len(notes) > 1000:
        raise ValueError("notes must be text of up to 1000 characters.")
    fav, never = ids("favourites"), ids("never")
    return {"favourites": [x for x in fav if x not in never], "never": never,
            "avoid": list(dict.fromkeys(x.strip().lower() for x in avoid)), "plan_meals": [m for m in MEAL_SHARE if m in meals],
            "leftovers": bool(body.get("leftovers", True)), "max_repeats": reps, "notes": notes.strip()}


def plan_context(plan, body):
    """Everything a proposal needs: open days and meals, day targets, the
    Strategy goal and training days, recipes allowed by the preferences, and
    Craig's CookTrace ratings. Shared by the app planner and Ask Max."""
    v = plan.view(body.get("week"))
    days = [d for d in v["week"] if d >= v["today"]]
    if not days:
        raise ValueError("That week is over. Pick this week or a later one.")
    targets = plan._targets(days)
    if not targets:
        raise ValueError("Set a Strategy (or a calorie goal in NutriTrace) first, so there's a target to plan to.")
    prefs = plan.prefs()
    never, avoid = set(prefs["never"]), prefs["avoid"]
    all_recipes = [r for r in plan.recipes("") if r.get("kcal")]
    recipes = [r for r in all_recipes if str(r["id"]) not in never and not any(w in r["name"].lower() for w in avoid)]
    if not recipes:
        raise ValueError("None of your CookTrace recipes has calories yet (or your preferences rule them all out), so there's nothing to plan from.")
    ratings = plan.ratings()
    taken, rows = {}, []
    for s in v["slots"]:
        t = taken.setdefault(s["date"], {"meals": set(), "kcal": 0, "dinner": None})
        t["meals"].add(s["meal"])
        if s["status"] != "skipped":
            t["kcal"] += ((s.get("per_serving") or {}).get("kcal") or 0) * s["portions"]
            if s["meal"] == "dinner":
                t["dinner"] = str(s["recipe_id"])
        rows.append(f"{s['date']} {s['meal']}: {s['recipe']} x{s['portions']}")
    strat = None
    if plan.strategy:
        try:
            strat = plan.strategy.view().get("strategy")
        except SourceError:
            strat = None
    training = set(strat["train"]) if strat and strat.get("dist") == "training" else set()
    weekday = lambda d: ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")[datetime.date.fromisoformat(d).weekday()]
    est = None
    try:
        est = plan.h.estimate()
    except SourceError:
        pass
    uses = []
    if strat:
        uses.append(f"Goal: {GOAL_WORDS[strat['goal']]}" + (f" at {strat['rate']}% a week" if strat["goal"] != "maintain" else ""))
        uses.append(f"Protein {strat['gkg']} g per kg")
    if training:
        uses.append("Training days: " + ", ".join(d for d in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun") if d in training))
    if prefs["favourites"]:
        uses.append(f"{len(prefs['favourites'])} favourite{'s' if len(prefs['favourites']) > 1 else ''}")
    if ratings:
        uses.append("Your CookTrace ratings")
    if never or avoid:
        uses.append("Avoiding " + ", ".join(([f"{len(never)} recipe{'s' if len(never) > 1 else ''}"] if never else []) + avoid[:5]))
    if prefs["plan_meals"] != list(MEAL_SHARE):
        uses.append("Planning " + ", ".join(prefs["plan_meals"]) + " only")
    if prefs["leftovers"]:
        uses.append("Leftovers for lunch")
    return {"week": v["week"], "days": days, "targets": targets, "kcal_targets": {d: targets[d]["kcal"] for d in targets},
            "recipes": recipes, "ratings": ratings, "prefs": prefs, "taken": taken, "rows": rows, "strategy": strat,
            "training": {d for d in days if weekday(d) in training}, "estimate": est, "uses": uses}


def propose_week(ctx):
    """Fill the open slots from the allowed recipes.

    For each slot, pick the recipe and portion closest to that meal's share of
    the day's calories. Protein short of the share counts against a choice
    (more so when losing fat), favourites and well-rated recipes count for it,
    repeats beyond the weekly limit are ruled out, and with leftovers on, the
    previous night's dinner is preferred for lunch. No model involved; same
    input, same plan.
    """
    recipes, prefs, ratings = ctx["recipes"], ctx["prefs"], ctx["ratings"]
    fav = set(prefs["favourites"])
    strat = ctx.get("strategy") or {}
    pweight = 0.8 if strat.get("goal") == "lose" else 0.5
    week_uses, out, last_dinner = {}, [], None
    for d in ctx["days"]:
        t = ctx["targets"].get(d) or {}
        have = ctx["taken"].get(d) or {"meals": set(), "kcal": 0, "dinner": None}
        if not t.get("kcal"):
            last_dinner = have.get("dinner")
            continue
        empty = [m for m in prefs["plan_meals"] if m not in have["meals"]]
        planned_shares = sum(MEAL_SHARE[m] for m in MEAL_SHARE if m not in prefs["plan_meals"])
        left = max(0, t["kcal"] - have["kcal"]) * (1 - planned_shares)  # leave room for meals Craig plans himself
        share_sum = sum(MEAL_SHARE[m] for m in empty) or 1
        today_used = set()
        dinner_today = have.get("dinner")
        for m in empty:
            goal = left * MEAL_SHARE[m] / share_sum
            pgoal = (t.get("protein") or 0) * MEAL_SHARE[m]
            fits = [r for r in recipes if breakfast_like(r["name"]) == (m == "breakfast")] or recipes
            fits = [r for r in fits if week_uses.get(str(r["id"]), 0) < prefs["max_repeats"]] or fits
            best = None
            for r in fits:
                rid = str(r["id"])
                for p in PORTIONS:
                    kcal = r["kcal"] * p
                    score = abs(kcal - goal) / max(goal, 1)
                    if pgoal and r.get("protein") is not None:
                        score += pweight * max(0, pgoal - r["protein"] * p) / pgoal
                    score += 0.35 * week_uses.get(rid, 0) + (1.0 if rid in today_used else 0)
                    score -= 0.25 if rid in fav else 0
                    if rid in ratings:
                        score -= 0.1 * (ratings[rid] - 3)
                    if prefs["leftovers"] and m == "lunch" and rid == last_dinner:
                        score -= 0.6  # last night's leftovers
                    score += 0.02 * abs(p - 1)  # prefer a plain single portion when it's as good
                    if best is None or score < best[0]:
                        best = (score, r, p)
            _, r, p = best
            rid = str(r["id"])
            week_uses[rid] = week_uses.get(rid, 0) + 1
            today_used.add(rid)
            if m == "dinner":
                dinner_today = rid
            item = {"date": d, "meal": m, "recipe_id": r["id"], "recipe": r["name"], "portions": p,
                    "kcal": round(r["kcal"] * p), "protein": round((r.get("protein") or 0) * p)}
            why = [w for w, ok in (("favourite", rid in fav), ("leftovers", prefs["leftovers"] and m == "lunch" and rid == last_dinner),
                                   (f"rated {ratings.get(rid)}★", ratings.get(rid, 0) >= 4), ("training day", d in ctx["training"] and m == "dinner")) if ok]
            if why:
                item["why"] = ", ".join(why)
            out.append(item)
        last_dinner = dinner_today
    return out


def _parse_plan(text):
    """The first JSON object in Max's answer that has an "items" list."""
    dec = json.JSONDecoder()
    i = text.find("{")
    while i != -1:
        try:
            obj, _ = dec.raw_decode(text[i:])
            if isinstance(obj, dict) and isinstance(obj.get("items"), list):
                return obj
        except ValueError:
            pass
        i = text.find("{", i + 1)
    return None


def max_prompt(ctx):
    p, strat, est = ctx["prefs"], ctx.get("strategy"), ctx.get("estimate")
    meals = ", ".join(p["plan_meals"])
    lines = [
        "Plan my meals. This request comes from the Hermes app's Meal plan screen.",
        f"Use only the recipes listed (by id). Fill {meals} on the days listed, skipping meals already planned.",
        "Fit my fitness goal: hit each day's calorie target, get protein close to it, and put the bigger, carb-heavier meals on training days.",
        "Follow my preferences below. Use favourites and well-rated recipes more often.",
        f"Use any one recipe at most {p['max_repeats']} times this week."
        + (" Leftovers are fine: last night's dinner can be the next day's lunch." if p["leftovers"] else " Don't use leftovers for lunch."),
        "Portions are servings of the recipe, from 0.5 to 3 in steps of 0.5.",
        "Don't use any tools and don't change anything: the app shows your plan to me and I accept it there.",
        "Answer with one or two short sentences on how the plan fits my goal, then a JSON object exactly like:",
        '{"note": "...", "items": [{"date": "YYYY-MM-DD", "meal": "breakfast|lunch|dinner", "recipe_id": 1, "portions": 1}]}',
        "",
    ]
    if strat:
        lines.append(f"Goal: {GOAL_WORDS[strat['goal']]}" + (f" at {strat['rate']}% of body weight a week" if strat["goal"] != "maintain" else "")
                     + f"; protein {strat['gkg']} g/kg; diet style {strat['diet']}; fibre {strat['fib']} g a day.")
    if est:
        lines.append(f"Body: expenditure {est.get('expenditure')} kcal/day, trend weight {est.get('trend_kg')} kg, changing {est.get('weekly_change_kg')} kg a week.")
    lines.append("Days and targets: " + "; ".join(
        f"{d}{' (training day)' if d in ctx['training'] else ''}: {ctx['targets'][d]['kcal']:.0f} kcal, protein {ctx['targets'][d].get('protein') or '?'} g"
        for d in ctx["days"] if d in ctx["targets"]))
    lines.append("Already planned: " + ("; ".join(ctx["rows"]) if ctx["rows"] else "nothing"))
    if p["avoid"]:
        lines.append("Foods I avoid: " + ", ".join(p["avoid"]) + ".")
    if p["notes"]:
        lines.append("My notes on how I like to eat: " + p["notes"])
    fav = set(p["favourites"])
    lines.append("Recipes (id, name, per serving kcal/protein/carbs/fat, makes servings, my rating, favourite):")
    for r in ctx["recipes"]:
        rid = str(r["id"])
        tags = ([f"rated {ctx['ratings'][rid]}/5"] if rid in ctx["ratings"] else []) + (["favourite"] if rid in fav else [])
        lines.append(f"- {r['id']}: {r['name']} | {r['kcal']:.0f} kcal, P {r.get('protein') or 0:.0f}, C {r.get('carbs') or 0:.0f}, "
                     f"F {r.get('fat') or 0:.0f} | makes {r.get('servings') or '?'}" + (" | " + ", ".join(tags) if tags else ""))
    return "\n".join(lines)


def ask_max(plan, body, chat):
    """Max's proposal for the rest of a week. Max gets the recipes, targets,
    goal, training days, preferences, ratings and what's planned (Craig allowed
    Max his health and food data on 2026-10-09). Nothing is saved: the app
    shows the proposal and Craig accepts it."""
    if chat is None:
        raise SourceError("Max isn't connected to the app yet.")
    chat._check_kill()
    ctx = plan_context(plan, body)
    base = {"week": ctx["week"], "source": "max", "targets": ctx["kcal_targets"], "uses": ctx["uses"]}
    if getattr(chat.backend, "sample", False):
        # Sample-data mode has no real Max: a labelled stand-in built the app's way.
        alt = dict(ctx, recipes=list(reversed(ctx["recipes"])))
        return {**base, "items": propose_week(alt), "sample": True,
                "note": "Sample data: this stands in for Max's plan. On the server Max writes it."}
    answer = []
    for ev in chat.backend.events(chat.backend.open("meal-plan-" + secrets.token_hex(4), max_prompt(ctx))):
        if ev["type"] == "text":
            answer.append(ev["delta"])
        elif ev["type"] == "error":
            raise SourceError("Max: " + ev["message"])
    got = _parse_plan("".join(answer))
    if not got:
        raise SourceError("Max didn't send a plan the app could read. Try again.")
    ids = {str(r["id"]): r for r in ctx["recipes"]}
    items, dropped = [], 0
    for it in got["items"][:MAX_APPLY]:
        r = ids.get(str(it.get("recipe_id"))) if isinstance(it, dict) else None
        ok = (r and it.get("date") in ctx["days"] and it.get("meal") in ctx["prefs"]["plan_meals"]
              and it["meal"] not in (ctx["taken"].get(it["date"]) or {"meals": set()})["meals"]
              and (it["date"], it["meal"]) not in {(x["date"], x["meal"]) for x in items})
        try:
            p = round(float(it.get("portions", 1)) * 2) / 2
        except (TypeError, ValueError):
            ok = False
        if not ok or not 0.5 <= p <= 3:
            dropped += 1
            continue
        items.append({"date": it["date"], "meal": it["meal"], "recipe_id": r["id"], "recipe": r["name"], "portions": p,
                      "kcal": round(r["kcal"] * p), "protein": round((r.get("protein") or 0) * p)})
    if not items:
        raise SourceError("Max's plan didn't use any of your recipes on the open days. Try again.")
    note = str(got.get("note") or "")[:400]
    if dropped:
        note = (note + f" ({dropped} suggestion{'s' if dropped > 1 else ''} didn't fit and were left out.)").strip()
    items.sort(key=lambda x: (x["date"], MEAL_INDEX[x["meal"]]))
    return {**base, "items": items, "note": note}
