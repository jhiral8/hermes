"""Health screens: NutriTrace, LiftTrace and CookTrace, read-only.

The app reads the three apps' public REST APIs (/api/v1) on loopback with
Craig's read-only "hermes-read" tokens. Logging stays in the apps until the
app matches or beats them. Health data goes only to Craig's browser: none of
it is passed to Max or any model.

What the tokens can't read (weight history, fasting, workout calories) shows
as "not readable yet" rather than as zero.
"""

import datetime
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from sources import SourceError

MEALS = {0: "Breakfast", 1: "Lunch", 2: "Dinner", 3: "Snacks"}
NUTRIENTS = {  # app name -> keys the Trace apps use (first found wins)
    "kcal": ("calories", "energy", "kcal", "calorie_goal", "calories_goal", "energy-kcal"),
    "protein": ("proteins", "protein", "protein_g"),
    "carbs": ("carbohydrates", "carbs", "carbohydrate", "carbs_g"),
    "fat": ("fat", "fats", "fat_g"),
    "fibre": ("fiber", "fibre", "fiber_g"),
}


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _find(obj, keys, depth=0):
    """First numeric value under any of `keys`, searching nested dicts."""
    if not isinstance(obj, dict) or depth > 3:
        return None
    for k in keys:
        if k in obj and _num(obj[k]) is not None:
            return _num(obj[k])
    for v in obj.values():
        if isinstance(v, dict):
            found = _find(v, keys, depth + 1)
            if found is not None:
                return found
    return None


def nutrients(obj):
    return {name: _find(obj or {}, keys) for name, keys in NUTRIENTS.items()}


class TraceApp:
    """One Trace app's /api/v1, with a small time-based cache."""

    def __init__(self, name, cfg, opener=None):
        self.name = name
        self.base = cfg["url"].rstrip("/") + "/api/v1"
        self.key_file = cfg.get("key_file")
        self.key = cfg.get("key")  # sample-data mode only; real tokens live in key files
        self.web_url = cfg.get("web_url")
        self.timeout = float(cfg.get("timeout", 5))
        self._open = opener or urllib.request.urlopen
        self._cache = {}
        self._lock = threading.Lock()

    def _key(self):
        if self.key:
            return self.key
        try:
            return Path(self.key_file).read_text(encoding="utf-8").strip()
        except (OSError, TypeError):
            raise SourceError(f"{self.name} token file not readable")

    def get(self, path, ttl=60, **params):
        q = {k: v for k, v in params.items() if v is not None}
        url = self.base + path + ("?" + urllib.parse.urlencode(q) if q else "")
        now = time.time()
        with self._lock:
            hit = self._cache.get(url)
            if hit and now - hit[0] < ttl:
                return hit[1]
        req = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._key(),
                                                   "Accept": "application/json", "User-Agent": "hermes-app"})
        try:
            with self._open(req, timeout=self.timeout) as r:
                data = json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            why = {401: "refused the token", 403: "token lacks the scope", 404: "public API not enabled",
                   429: "rate limited"}.get(e.code, f"answered {e.code}")
            raise SourceError(f"{self.name} {why}")
        except (urllib.error.URLError, OSError) as e:
            raise SourceError(f"{self.name} unreachable ({type(e).__name__})")
        except ValueError:
            raise SourceError(f"{self.name} sent something that isn't JSON")
        with self._lock:
            self._cache[url] = (now, data)
        return data


def _section(fn):
    try:
        return {"ok": True, "data": fn()}
    except (SourceError, KeyError, TypeError, ValueError) as e:
        return {"ok": False, "error": str(e) or type(e).__name__}


class Health:
    def __init__(self, cfg, opener=None, today=None):
        self.nt = TraceApp("NutriTrace", cfg["nutritrace"], opener) if cfg.get("nutritrace") else None
        self.lt = TraceApp("LiftTrace", cfg["lifttrace"], opener) if cfg.get("lifttrace") else None
        self.ct = TraceApp("CookTrace", cfg["cooktrace"], opener) if cfg.get("cooktrace") else None
        self.estimator_file = cfg.get("estimator_file")
        self.tz = cfg.get("timezone", "Europe/London")
        self._today = today  # tests pin the date

    def today(self):
        if self._today:
            return self._today
        try:
            from zoneinfo import ZoneInfo
            return datetime.datetime.now(ZoneInfo(self.tz)).date()
        except Exception:
            return datetime.date.today()

    def _need(self, app, name):
        if app is None:
            raise SourceError(f"{name} not connected yet")
        return app

    def links(self):
        return {k: getattr(a, "web_url", None) for k, a in (("nutritrace", self.nt), ("lifttrace", self.lt), ("cooktrace", self.ct))}

    # ------------------------------------------------------------ food

    def goals(self):
        g = self._need(self.nt, "NutriTrace").get("/goals", ttl=300)
        return nutrients((g or {}).get("goals") or {})

    def day_totals(self, d):
        nt = self._need(self.nt, "NutriTrace")
        t = nt.get(f"/diary/{d.isoformat()}/totals", ttl=self._ttl(d))
        return {"date": d.isoformat(), **nutrients(t.get("totals") or {}),
                "items": t.get("item_count") or 0, "water_ml": t.get("water_ml")}

    def _ttl(self, d):
        """Today changes as Craig logs; older days rarely do (60 calls/min limit)."""
        age = (self.today() - d).days
        return 45 if age <= 0 else 600 if age <= 2 else 3600

    def _status(self, d, row):
        if d > self.today():
            return "future"
        if d == self.today():
            return "open"
        return "logged" if row and row["items"] else "none"

    def food(self, day=None):
        d = datetime.date.fromisoformat(day) if day else self.today()
        monday = d - datetime.timedelta(days=d.weekday())

        def build_day():
            nt = self._need(self.nt, "NutriTrace")
            diary = nt.get(f"/diary/{d.isoformat()}", ttl=self._ttl(d))
            meals = {}
            for i in diary.get("items") or []:
                m = i.get("meal")
                label = MEALS.get(m, f"Meal {m}") if isinstance(m, int) else (m or "Other")
                meals.setdefault(label, []).append({
                    "name": i.get("name") or "?", "brand": i.get("brand"),
                    "amount": " ".join(str(x) for x in (i.get("quantity"), i.get("unit")) if x not in (None, "")),
                    "source": i.get("source"), **nutrients(i.get("nutrition") or {})})
            order = list(MEALS.values())
            return {"date": d.isoformat(), "totals": self.day_totals(d),
                    "meals": [{"meal": k, "items": meals[k], "kcal": sum((x["kcal"] or 0) for x in meals[k])}
                              for k in sorted(meals, key=lambda k: order.index(k) if k in order else 99)]}

        def build_week():
            days = []
            for n in range(7):
                dd = monday + datetime.timedelta(days=n)
                row = self.day_totals(dd) if dd <= self.today() else {"date": dd.isoformat(), "items": 0}
                row["status"] = self._status(dd, row)
                days.append(row)
            return days

        return {"day": _section(build_day), "week": _section(build_week), "goals": _section(self.goals),
                "estimate": _section(self.estimate), "links": self.links(), "today": self.today().isoformat()}

    def estimate(self):
        """The expenditure estimator's latest output, when it runs on the server."""
        if not self.estimator_file:
            raise SourceError("expenditure estimator isn't running yet")
        try:
            data = json.loads(Path(self.estimator_file).read_text(encoding="utf-8"))
        except OSError:
            raise SourceError("expenditure estimator hasn't written a result yet")
        if data.get("held") or data.get("expenditure") is None:
            raise SourceError("expenditure estimate is held until there's more data")
        return {k: data.get(k) for k in ("as_of", "expenditure", "low", "high", "trend_kg",
                                         "weekly_change_kg", "intake_days", "weigh_ins")}

    # ------------------------------------------------------------ progress

    def progress(self, days=14):
        days = max(7, min(int(days), 28))

        def build():
            t = self.today()
            rows = []
            for n in range(days, 0, -1):
                d = t - datetime.timedelta(days=n - 1)
                r = self.day_totals(d)
                r["status"] = self._status(d, r)
                rows.append(r)
            done = [r for r in rows if r["status"] == "logged"]

            def avg(k):
                vals = [r[k] for r in done if r.get(k) is not None]
                return round(sum(vals) / len(vals), 1) if vals else None
            return {"days": rows, "logged": len(done), "of": days - 1,
                    "avg": {k: avg(k) for k in NUTRIENTS}}

        return {"nutrition": _section(build), "goals": _section(self.goals),
                "estimate": _section(self.estimate), "links": self.links(), "today": self.today().isoformat(),
                "weight": _section(self.weight), "history": _section(self.history)}

    def history(self):
        """Up to 90 days of weight, trend, intake and expenditure for the Progress charts."""
        if not self.estimator_file:
            raise SourceError("expenditure estimator isn't running yet")
        try:
            data = json.loads(Path(self.estimator_file).read_text(encoding="utf-8"))
        except OSError:
            raise SourceError("expenditure estimator hasn't written a result yet")
        rows = data.get("history")
        if not rows:
            raise SourceError("history appears after the next nightly run")
        return rows

    def weight(self):
        """Weight trend from the estimator's feed: the estimate's own trend, not the raw scale."""
        est = self.estimate()
        if est.get("trend_kg") is None:
            raise SourceError("not enough weigh-ins yet")
        return {"trend_kg": est["trend_kg"], "weekly_change_kg": est.get("weekly_change_kg"),
                "weigh_ins": est.get("weigh_ins")}

    # ------------------------------------------------------------ training

    def train(self):
        def build():
            lt = self._need(self.lt, "LiftTrace")
            recent = (lt.get("/workouts/recent", ttl=120, limit=12) or {}).get("workouts") or []
            sessions = []
            for w in recent[:6]:
                det = lt.get(f"/workouts/{w['date']}", ttl=600)
                ex = []
                for e in det.get("exercises") or []:
                    work = [s for s in e.get("sets") or [] if not s.get("warmup")]
                    top = max((_num(s.get("weight")) or 0 for s in work), default=0)
                    ex.append({"name": e.get("exercise_name"), "id": e.get("exercise_id"), "top": top or None,
                               "reps": [s.get("reps") for s in work if s.get("reps") is not None],
                               "rpe": [s.get("rpe") for s in work if s.get("rpe") is not None]})
                sessions.append({"date": w.get("date"), "name": w.get("name") or det.get("name"),
                                 "completed": bool(w.get("completed")), "volume": w.get("total_volume"),
                                 "duration_min": det.get("duration_min"), "exercises": ex})
            prog = lt.get("/programs/active", ttl=300) or {}
            nxt = None
            templates = prog.get("templates") or []
            if prog.get("active") and templates:
                names = [x.get("name") for x in templates]
                last = next((s["name"] for s in sessions if s["name"] in names), None)
                i = (names.index(last) + 1) % len(names) if last else 0
                tpl = templates[i]
                last_by_ex = {}
                for s in reversed(sessions):
                    for e in s["exercises"]:
                        last_by_ex[e["id"]] = {"date": s["date"], "top": e["top"], "reps": e["reps"]}
                nxt = {"name": tpl.get("name"), "day_label": tpl.get("day_label"),
                       "exercises": [{"name": x.get("exercise_name"), "target_sets": x.get("target_sets"),
                                      "last": last_by_ex.get(x.get("exercise_id"))} for x in tpl.get("exercises") or []]}
            return {"sessions": sessions, "program": {
                "active": bool(prog.get("active")), "name": prog.get("name"),
                "current_week": prog.get("current_week"), "weeks": prog.get("duration_weeks"),
                "templates": [{"name": x.get("name"), "day_label": x.get("day_label")} for x in templates]},
                "next": nxt}

        def records():
            lt = self._need(self.lt, "LiftTrace")
            r = lt.get("/records", ttl=600) or {}
            out = [{"name": x.get("name"), "weight": x.get("maxWeight"), "reps": x.get("maxReps"),
                    "e1rm": x.get("e1rm"), "date": x.get("date")} for x in r.get("records") or []]
            out.sort(key=lambda x: x["date"] or "", reverse=True)
            return out[:12]

        return {"train": _section(build), "records": _section(records), "links": self.links(),
                "today": self.today().isoformat()}

    # ------------------------------------------------------------ meals

    def meals(self):
        t = self.today()
        monday = t - datetime.timedelta(days=t.weekday())
        sunday = monday + datetime.timedelta(days=6)

        def diary(kind):
            ct = self._need(self.ct, "CookTrace")
            items = (ct.get("/cook-diary", ttl=120, date_from=monday.isoformat(), date_to=sunday.isoformat(),
                            kind=kind, limit=100) or {}).get("items") or []
            items.sort(key=lambda x: (x.get("date") or "", x.get("meal_type") or ""))
            return [{"date": x.get("date"), "recipe": x.get("recipe_name"), "recipe_id": x.get("recipe_id"),
                     "servings": x.get("servings"), "meal_type": x.get("meal_type"), "rating": x.get("rating")} for x in items]

        def shopping():
            ct = self._need(self.ct, "CookTrace")
            items = (ct.get("/shopping", ttl=60, include_checked="true") or {}).get("items") or []
            return [{"name": x.get("name"), "quantity": x.get("quantity"), "unit": x.get("unit"),
                     "aisle": x.get("aisle"), "checked": bool(x.get("checked"))} for x in items]

        def recipes():
            ct = self._need(self.ct, "CookTrace")
            r = ct.get("/recipes", ttl=300, limit=24) or {}
            return {"total": r.get("total"), "items": [{"id": x.get("id"), "name": x.get("name"),
                                                        "servings": x.get("servings"), **nutrients(x.get("nutrition") or {})}
                                                       for x in r.get("items") or []]}

        return {"planned": _section(lambda: diary("planned")), "cooked": _section(lambda: diary("cooked")),
                "shopping": _section(shopping), "recipes": _section(recipes), "links": self.links(),
                "week": [monday.isoformat(), sunday.isoformat()], "today": t.isoformat()}
