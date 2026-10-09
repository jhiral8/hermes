"""Sample Trace-app answers for sample-data mode (config "demo": true).

A stand-in for urllib's opener: it answers the same /api/v1 paths the real
NutriTrace, LiftTrace and CookTrace do, with made-up numbers.
"""

import datetime
import io
import json
import random
import urllib.parse

FOODS = [
    (0, "Greek yoghurt", "Fage", 200, "g", 190, 20, 8, 10, 0),
    (0, "Oats", None, 50, "g", 190, 6, 33, 3, 5),
    (1, "Chicken breast", None, 180, "g", 297, 56, 0, 6, 0),
    (1, "Basmati rice", None, 150, "g", 195, 4, 42, 1, 1),
    (1, "Broccoli", None, 100, "g", 34, 3, 4, 0, 3),
    (2, "Salmon fillet", None, 140, "g", 290, 30, 0, 18, 0),
    (2, "New potatoes", None, 200, "g", 150, 4, 32, 0, 4),
    (3, "Protein bar", "Grenade", 1, "bar", 220, 21, 18, 8, 2),
    (3, "Apple", None, 1, "medium", 80, 0, 21, 0, 4),
]


def _day_items(d):
    rnd = random.Random(d.toordinal())
    items = []
    for meal, name, brand, q, unit, kcal, p, c, f, fib in FOODS:
        if rnd.random() < 0.15:
            continue
        k = rnd.uniform(0.85, 1.2)
        items.append({"name": name, "brand": brand, "meal": meal, "quantity": round(k, 2), "portion": q,
                      "food_server_id": FOODS.index((meal, name, brand, q, unit, kcal, p, c, f, fib)) + 1,
                      "unit": unit, "source": "off", "nutrition": {
                          "calories": kcal, "proteins": p, "carbohydrates": c, "fat": f, "fiber": fib}})
    return items


def _totals(items):
    t = {k: 0 for k in ("calories", "proteins", "carbohydrates", "fat", "fiber")}
    for i in items:
        for k in t:
            t[k] += (i["nutrition"].get(k) or 0) * (i.get("quantity") or 1)
    return {k: round(v, 1) for k, v in t.items()}


WORKOUTS = [
    ("Upper A", [("bench", "Bench press", 80, 6), ("row", "Barbell row", 70, 8), ("ohp", "Overhead press", 45, 8),
                 ("pull", "Pull-up", 0, 8)]),
    ("Lower A", [("squat", "Back squat", 110, 5), ("rdl", "Romanian deadlift", 100, 8), ("lunge", "Walking lunge", 20, 10)]),
    ("Upper B", [("incline", "Incline dumbbell press", 30, 10), ("pull", "Pull-up", 0, 8), ("row", "Barbell row", 72.5, 8)]),
    ("Lower B", [("dead", "Deadlift", 140, 3), ("squat", "Back squat", 100, 8), ("calf", "Calf raise", 60, 12)]),
]


def _workouts(today):
    out = []
    d, n = today - datetime.timedelta(days=1), 0
    while len(out) < 8:
        if d.weekday() in (0, 1, 3, 4):
            name, ex = WORKOUTS[(len(out) * -1 + 3) % 4]
            out.append((d, name, ex))
        d -= datetime.timedelta(days=1)
        n += 1
    return out


# Sample-mode writes live only in memory, so logging can be tried without a
# Trace app. They vanish when the app restarts.
CATALOG = [{"id": n + 1, "name": name, "brand": brand, "portion": q, "unit": unit, "barcode": None,
            "nutrition": {"calories": kcal, "proteins": p, "carbohydrates": c, "fat": f, "fiber": fib}}
           for n, (_, name, brand, q, unit, kcal, p, c, f, fib) in enumerate(FOODS)]
# Saved meals: (food id, quantity) pairs.
MEALS_SAVED = [{"id": 1, "name": "Usual breakfast", "favorite": 1, "usage_count": 12, "items": [(1, 1), (2, 1)]},
               {"id": 2, "name": "Chicken, rice and greens", "favorite": 0, "usage_count": 5, "items": [(3, 1), (4, 1), (5, 1)]}]
LOGGED = {}   # date -> extra diary items
WATER = {}    # date -> ml


def reset():
    """Forget sample-mode writes (tests)."""
    del CATALOG[len(FOODS):]
    LOGGED.clear()
    WATER.clear()


def _exercises():
    seen = {}
    for _, ex in WORKOUTS:
        for i, nm, _, _ in ex:
            seen.setdefault(i, {"id": len(seen) + 1, "key": i, "name": nm, "category": "strength",
                                "equipment": None, "set_type": "reps"})
    return list(seen.values())


# CookTrace keeps nutrition for the whole recipe, not per serving.
RECIPES = [
    {"id": 1, "name": "Chicken tikka traybake", "servings": 4, "nutrition": {"calories": 2080, "proteins": 180, "carbohydrates": 152, "fat": 72, "fiber": 24}},
    {"id": 2, "name": "Salmon, greens and potatoes", "servings": 2, "nutrition": {"calories": 1220, "proteins": 80, "carbohydrates": 90, "fat": 52, "fiber": 14}},
    {"id": 3, "name": "Beef chilli", "servings": 6, "nutrition": {"calories": 2880, "proteins": 228, "carbohydrates": 240, "fat": 96, "fiber": 60}},
    {"id": 4, "name": "Overnight oats", "servings": 1, "nutrition": {"calories": 390, "proteins": 28, "carbohydrates": 48, "fat": 9, "fiber": 8}},
    {"id": 5, "name": "Chicken and rice bowl", "servings": 2, "nutrition": {"calories": 1300, "proteins": 92, "carbohydrates": 134, "fat": 44, "fiber": 8}},
    {"id": 6, "name": "Tuna sandwich", "servings": 1, "nutrition": {}}]


def write(host, path, body):
    parts = path.strip("/").split("/")
    if host.startswith("nutritrace") and parts == ["foods"]:
        for f in CATALOG:
            if f["name"].lower() == body["name"].lower() and (f["brand"] or "").lower() == (body.get("brand") or "").lower():
                raise _http(409, {"error": "already exists", "code": "duplicate", "id": f["id"]})
        food = {"id": len(CATALOG) + 1, "name": body["name"], "brand": body.get("brand"), "portion": body["portion"],
                "unit": body["unit"], "barcode": body.get("barcode"), "nutrition": body["nutrition"]}
        CATALOG.append(food)
        return food
    if host.startswith("nutritrace") and parts[0] == "diary" and parts[2] == "food":
        f = next((x for x in CATALOG if x["id"] == body["food_id"]), None)
        if f is None:
            raise _http(400, {"error": f"food_id {body['food_id']} not found in your catalog."})
        k = body.get("quantity") or 1
        item = {"name": f["name"], "brand": f["brand"], "meal": body.get("meal", 0), "quantity": k,
                "portion": f["portion"], "unit": f["unit"], "source": "hermes", "food_server_id": f["id"],
                "nutrition": dict(f["nutrition"])}
        LOGGED.setdefault(parts[1], []).append(item)
        return {"ok": True, "date": parts[1], "logged": {"food_id": f["id"], "name": f["name"], "meal": item["meal"],
                                                          "portion": f["portion"], "unit": f["unit"], "quantity": k}}
    if host.startswith("nutritrace") and parts[0] == "diary" and parts[2] == "meal":
        m = next((x for x in MEALS_SAVED if x["id"] == body.get("meal_id")), None)
        if m is None:
            raise _http(400, {"error": f"meal_id {body.get('meal_id')} not found in your catalog."})
        for fid, k in m["items"]:
            write(host, f"/diary/{parts[1]}/food", {"food_id": fid, "meal": body.get("meal", 0), "quantity": k})
        return {"ok": True, "date": parts[1], "count": len(m["items"])}
    if host.startswith("nutritrace") and parts[0] == "diary" and parts[2] == "water":
        WATER[parts[1]] = WATER.get(parts[1], 0) + body["amount_ml"]
        return {"ok": True, "date": parts[1], "total_ml_on_day": WATER[parts[1]]}
    if host.startswith("lifttrace") and parts[0] == "workouts" and parts[2] == "sets":
        ex = next((x for x in _exercises() if x["id"] == body["exercise_id"]), None)
        if ex is None:
            raise _http(400, {"error": f"No exercise with id {body['exercise_id']} in the catalog."})
        return {"ok": True, "date": parts[1], "exercise_id": ex["id"], "exercise_name": ex["name"], "sets_on_exercise": 1}
    if host.startswith("cooktrace") and parts == ["cook-diary"]:
        r = next((x for x in RECIPES if x["id"] == body.get("recipe_id")), None)
        if r is None:
            raise _http(400, {"error": f"recipe_id {body.get('recipe_id')} not found."})
        return {"ok": True, "logged": {"date": body.get("date"), "recipe_id": r["id"], "recipe_name": r["name"], "kind": "cooked"}}
    raise ValueError("no sample write for " + host + path)


def _http(code, body):
    import urllib.error
    return urllib.error.HTTPError("http://sample", code, "sample", {}, io.BytesIO(json.dumps(body).encode()))


def make_opener(today_fn):
    def opener(req, timeout=None):
        u = urllib.parse.urlparse(req.full_url)
        q = dict(urllib.parse.parse_qsl(u.query))
        path = u.path.split("/api/v1", 1)[-1]
        host = u.netloc
        today = today_fn()
        if req.get_method() != "GET":
            body = write(host, path, json.loads(req.data or b"{}"))
        else:
            body = answer(host, path, q, today)
        return io.BytesIO(json.dumps(body).encode())
    return opener


def answer(host, path, q, today):
    parts = path.strip("/").split("/")
    if host.startswith("nutritrace"):
        if parts[0] == "foods":
            term = (q.get("q") or "").lower()
            items = [f for f in CATALOG if term in f["name"].lower() or term in (f["brand"] or "").lower()]
            return {"items": items[:int(q.get("limit") or 25)], "total": len(items)}
        if parts[0] == "meals":
            term = (q.get("query") or "").lower()
            out = []
            for m in MEALS_SAVED:
                if term not in m["name"].lower():
                    continue
                nut = {}
                for fid, k in m["items"]:
                    for n, v in CATALOG[fid - 1]["nutrition"].items():
                        nut[n] = round(nut.get(n, 0) + v * k, 1)
                out.append({"id": m["id"], "name": m["name"], "is_recipe": False, "nutrition": nut,
                            "favorite": bool(m["favorite"]), "usage_count": m["usage_count"]})
            return {"items": out, "count": len(out)}
        if parts[0] == "goals":
            return {"goals": {"calories": 2300, "proteins": 170, "carbohydrates": 240, "fat": 75, "fiber": 30},
                    "water_goal_ml": 2500}
        if parts[0] == "diary":
            d = datetime.date.fromisoformat(parts[1])
            items = [] if d > today or d.toordinal() % 9 == 0 else _day_items(d)
            if d == today:
                items = [i for i in items if i["meal"] <= 1]
            items = items + LOGGED.get(d.isoformat(), [])
            if len(parts) == 3:
                return {"date": d.isoformat(), "totals": _totals(items),
                        "water_ml": (1500 if items else 0) + WATER.get(d.isoformat(), 0),
                        "item_count": len(items)}
            return {"date": d.isoformat(), "items": items}
    if host.startswith("lifttrace"):
        ws = _workouts(today)
        if parts[0] == "workouts" and parts[1] == "recent":
            return {"workouts": [{"date": d.isoformat(), "name": n, "completed": True, "exercise_count": len(e),
                                  "total_volume": sum(w * r * 3 for _, _, w, r in e)} for d, n, e in ws]}
        if parts[0] == "workouts":
            for d, n, e in ws:
                if d.isoformat() == parts[1]:
                    bump = (today - d).days < 4
                    return {"date": parts[1], "name": n, "duration_min": 55, "exercises": [
                        {"exercise_id": i, "exercise_name": nm, "sets": [{"reps": r, "weight": w * 0.5, "warmup": True}] +
                         [{"reps": r, "weight": w + (2.5 if bump and w else 0), "completed": True, "rpe": 7 + s} for s in range(3)]}
                        for i, nm, w, r in e]}
            return {"exercises": []}
        if parts[0] == "exercises":
            term = (q.get("query") or "").lower()
            return {"exercises": [{k: v for k, v in x.items() if k != "key"} for x in _exercises()
                                  if term in x["name"].lower()][:int(q.get("limit") or 20)]}
        if parts[0] == "programs":
            return {"active": True, "name": "Upper/Lower 4-day", "duration_weeks": 8, "current_week": 3, "templates": [
                {"template_id": str(k), "name": n, "day_label": ["Mon", "Tue", "Thu", "Fri"][k],
                 "exercises": [{"exercise_id": i, "exercise_name": nm, "target_sets": 3} for i, nm, _, _ in e]}
                for k, (n, e) in enumerate(WORKOUTS)]}
        if parts[0] == "records":
            return {"records": [
                {"name": "Deadlift", "maxWeight": 145, "maxReps": 3, "e1rm": 159.5, "date": (today - datetime.timedelta(days=6)).isoformat()},
                {"name": "Back squat", "maxWeight": 112.5, "maxReps": 5, "e1rm": 126.6, "date": (today - datetime.timedelta(days=10)).isoformat()},
                {"name": "Bench press", "maxWeight": 82.5, "maxReps": 6, "e1rm": 99.0, "date": (today - datetime.timedelta(days=3)).isoformat()},
                {"name": "Overhead press", "maxWeight": 47.5, "maxReps": 8, "e1rm": 60.2, "date": (today - datetime.timedelta(days=17)).isoformat()}]}
    if host.startswith("cooktrace"):
        if parts[0] == "recipes":
            if len(parts) > 1:
                r = next((x for x in RECIPES if str(x["id"]) == parts[1]), None)
                if r is None:
                    raise _http(404, {"error": "not_found", "code": "not_found"})
                return r
            term = (q.get("q") or "").lower()
            items = [r for r in RECIPES if term in r["name"].lower()]
            return {"total": len(items), "items": items}
        if parts[0] == "cook-diary":
            start = datetime.date.fromisoformat(q["date_from"])
            end = datetime.date.fromisoformat(q["date_to"])
            names = [(1, "Chicken tikka traybake"), (2, "Salmon, greens and potatoes"), (3, "Beef chilli")]
            items, d, k = [], start, 0
            while d <= end:
                if (q["kind"] == "planned" and d >= today) or (q["kind"] == "cooked" and d < today and d.weekday() in (0, 2, 5)):
                    rid, nm = names[k % 3]
                    items.append({"date": d.isoformat(), "recipe_id": rid, "recipe_name": nm, "servings": 2,
                                  "meal_type": "dinner", "rating": 4 if q["kind"] == "cooked" else None})
                    k += 1
                d += datetime.timedelta(days=1)
            return {"items": items}
        if parts[0] == "shopping":
            return {"items": [
                {"id": "s1", "name": "Chicken thighs", "quantity": 1, "unit": "kg", "aisle": "Meat", "checked": False},
                {"id": "s2", "name": "Salmon fillets", "quantity": 4, "unit": None, "aisle": "Fish", "checked": False},
                {"id": "s3", "name": "Greek yoghurt", "quantity": 2, "unit": "tub", "aisle": "Dairy", "checked": True},
                {"id": "s4", "name": "Broccoli", "quantity": 2, "unit": None, "aisle": "Veg", "checked": False},
                {"id": "s5", "name": "Basmati rice", "quantity": 1, "unit": "kg", "aisle": "Cupboard", "checked": False}]}
    raise ValueError("no sample answer for " + host + path)


def sample_estimator():
    """A sample estimate file (with 90 days of history) so Progress has charts in sample-data mode."""
    import json
    import math
    import tempfile
    from pathlib import Path

    import estimate_feed
    today = datetime.date.today()
    days = []
    for k in range(120, -1, -1):
        d = today - datetime.timedelta(days=k)
        wobble = 0.35 * math.sin(k * 1.7) + 0.2 * math.cos(k * 0.6)
        days.append({"date": d.isoformat(), "intake": 2350 + 180 * math.sin(k * 0.9) if k % 9 else None,
                     "status": "partial" if k == 0 else ("missing" if k % 9 == 0 else "complete"),
                     "weight": round(84.6 - 0.032 * (120 - k) + wobble, 1) if k % 3 != 1 else None})
    out = estimate_feed.run(days, Path(__file__).resolve().parent / "estimator", today=today)
    path = Path(tempfile.mkdtemp(prefix="hermes-demo-est-"), "estimator.json")
    path.write_text(json.dumps(out), encoding="utf-8")
    return str(path)


def sample_config():
    return {"estimator_file": sample_estimator(),
            "nutritrace": {"url": "http://nutritrace.sample", "key": "sample", "write_key": "sample", "web_url": None},
            "lifttrace": {"url": "http://lifttrace.sample", "key": "sample", "write_key": "sample", "web_url": None},
            "cooktrace": {"url": "http://cooktrace.sample", "key": "sample", "write_key": "sample", "web_url": None}}
