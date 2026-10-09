"""Nutrition strategy: Craig's goal, calorie and macro targets, and the weekly check-in.

Targets live here, in a small JSON file the app keeps on the server, not in
NutriTrace (its API has no route to change goals). Each change starts a new
period from a date, so earlier days keep the targets that applied to them.

Targets never change on their own. The weekly check-in proposes a number from
the expenditure estimate and the goal rate; it applies only when Craig accepts
it. Nothing here goes to Max or any model.
"""

import copy
import datetime
import json
import threading
from pathlib import Path

from sources import SourceError

KCAL_PER_KG = 7700.0
MIN_KCAL = 1300  # a proposal never goes below this
WEEK = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
GOALS = ("lose", "maintain", "gain")
STYLES = ("coached", "collab", "manual")
DIETS = {"balanced": 0.327, "lowfat": 0.22, "lowcarb": 0.42, "plant": 0.30}  # share of calories from fat
SPREADS = ("even", "training", "custom")
PHASE_TYPES = ("lose", "maintain", "gain")
GOAL_NAMES = {"lose": "Lose fat", "maintain": "Maintain", "gain": "Build (gain)"}
STYLE_NAMES = {"coached": "Coached", "collab": "Collaborative", "manual": "Manual"}
MAX_CHECKINS = 200
MAX_PERIODS = 400


def _round5(v):
    return int(5 * round(v / 5))


def _pick(body, key, options):
    v = body.get(key)
    if v not in options:
        raise ValueError(f"{key} must be one of: {', '.join(options)}.")
    return v


def _num(body, key, lo, hi, whole=False):
    v = body.get(key)
    if isinstance(v, bool) or v is None or v == "":
        raise ValueError(f"{key} is required.")
    try:
        n = float(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be a number.")
    if n != n or not lo <= n <= hi:
        raise ValueError(f"{key} must be between {lo:g} and {hi:g}.")
    return int(round(n)) if whole else round(n, 2)


def check_settings(body):
    """The strategy as set in the Edit strategy steps. Raises ValueError on bad input."""
    if not isinstance(body, dict):
        raise ValueError("strategy must be an object.")
    s = {
        "goal": _pick(body, "goal", GOALS),
        "style": _pick(body, "style", STYLES),
        "kcal": _num(body, "kcal", 1000, 6000, whole=True),
        "diet": _pick(body, "diet", tuple(DIETS)),
        "gkg": _num(body, "gkg", 1.2, 2.4),
        "fib": _num(body, "fib", 10, 80, whole=True),
        "dist": _pick(body, "dist", SPREADS),
        "kg": _num(body, "kg", 30, 300, whole=False),
    }
    s["rate"] = 0 if s["goal"] == "maintain" else _num(body, "rate", 0.1, 1.0)
    s["shift"] = _num(body, "shift", 50, 500, whole=True) if s["dist"] == "training" else 200
    train = body.get("train") or []
    if not isinstance(train, list) or any(d not in WEEK for d in train):
        raise ValueError("train must be a list of weekday names (Mon to Sun).")
    s["train"] = [d for d in WEEK if d in train]
    if s["dist"] == "training" and not 0 < len(s["train"]) < 7:
        raise ValueError("Pick between 1 and 6 training days.")
    custom = body.get("custom") or {}
    if not isinstance(custom, dict):
        raise ValueError("custom must be an object of weekday: kcal.")
    if s["dist"] == "custom":
        s["custom"] = {d: _num(custom, d, 1000, 6000, whole=True) for d in WEEK}
    else:
        s["custom"] = {}
    return s


def day_targets(s, day_name):
    """One day's calories and macros under settings `s`."""
    base = s["kcal"]
    k = base
    n_t = len(s.get("train") or [])
    if s["dist"] == "training" and 0 < n_t < 7:
        k = base + s["shift"] if day_name in s["train"] else round(base - s["shift"] * n_t / (7 - n_t))
    elif s["dist"] == "custom":
        k = s["custom"].get(day_name, base)
    p = round(s["gkg"] * s["kg"])
    f = round(k * DIETS[s["diet"]] / 9)
    c = max(0, round((k - p * 4 - f * 9) / 4))
    return {"day": day_name, "kcal": int(round(k)), "protein": p, "carbs": c, "fat": f, "fibre": s["fib"]}


def week_targets(s):
    return [day_targets(s, d) for d in WEEK]


def goal_rate_kg(s, kg):
    """Weekly weight change the goal asks for, in kg (negative to lose)."""
    if s["goal"] == "maintain":
        return 0.0
    return (-1 if s["goal"] == "lose" else 1) * s["rate"] / 100 * kg


def proposal(s, est):
    """The check-in's suggested average daily calories, or None while expenditure calibrates."""
    if not est or est.get("expenditure") is None:
        return None
    kg = est.get("trend_kg") or s["kg"]
    goal_wk = goal_rate_kg(s, kg)
    kcal = max(MIN_KCAL, _round5(est["expenditure"] + goal_wk * KCAL_PER_KG / 7))
    return {"kcal": kcal, "delta": kcal - s["kcal"], "goal_kg_week": round(goal_wk, 2),
            "goal_kcal_day": round(goal_wk * KCAL_PER_KG / 7), "expenditure": est["expenditure"]}


def _period_in(data, d):
    """The period whose targets apply on day `d` (periods are in date order)."""
    found = None
    for p in data["periods"]:
        if p["from"] <= d.isoformat():
            found = p
    return found


class Strategy:
    """The strategy file. In memory only when there's no path (sample data, tests)."""

    def __init__(self, path, health):
        self.path = Path(path) if path else None
        self.h = health
        self._lock = threading.Lock()
        self._data = None

    # ------------------------------------------------------------ storage

    def _load(self):
        if self._data is None:
            if self.path is None or not self.path.exists():
                self._data = {"periods": [], "checkins": [], "next_checkin": None, "program": None}
            else:
                try:
                    self._data = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raise SourceError("The strategy file can't be read.")
        return self._data

    def _write(self):
        if self.path is None:
            return
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, indent=1), encoding="utf-8")
        tmp.chmod(0o640)
        tmp.replace(self.path)

    def _commit(self, before):
        try:
            self._write()
        except OSError:
            self._data = before
            raise SourceError("The strategy couldn't be saved on the server.")

    # ------------------------------------------------------------ reading

    def _today(self):
        return self.h.today() if self.h else datetime.date.today()

    def _estimate(self):
        if not self.h:
            return None
        try:
            return self.h.estimate()
        except SourceError:
            return None

    def _period_for(self, d):
        return _period_in(self._load(), d)

    def targets_for(self, d):
        """That day's targets in the app's nutrient names, or None if no strategy applies yet."""
        with self._lock:
            p = self._period_for(d)
        if not p:
            return None
        t = day_targets(p["settings"], WEEK[d.weekday()])
        return {k: t[k] for k in ("kcal", "protein", "carbs", "fat", "fibre")}

    def _phase_info(self, data, today):
        prog = data.get("program")
        if not prog:
            return None
        start = datetime.date.fromisoformat(prog["start"])
        out, cur = [], None
        for ph in prog["phases"]:
            end = start + datetime.timedelta(weeks=ph["weeks"])
            status = "done" if end <= today else "current" if start <= today else "planned"
            row = {**ph, "start": start.isoformat(), "end": end.isoformat(), "status": status}
            if status == "current":
                cur = row
            out.append(row)
            start = end
        return {"start": prog["start"], "phases": out, "current": cur}

    def view(self):
        today = self._today()
        est = self._estimate()
        with self._lock:
            data = copy.deepcopy(self._load())
        cur = data["periods"][-1] if data["periods"] else None
        active = _period_in(data, today)
        out = {"today": today.isoformat(), "weekday": WEEK[today.weekday()], "estimate": est,
               "checkins": list(reversed(data["checkins"][-30:])), "next_checkin": data.get("next_checkin"),
               "due": bool(cur and data.get("next_checkin") and data["next_checkin"] <= today.isoformat()),
               "program": self._phase_info(data, today), "strategy": None}
        if cur:
            s = cur["settings"]
            out["strategy"] = {**s, "from": cur["from"]}
            out["week"] = week_targets(s)
            out["active_from"] = active["from"] if active else None
            out["pending"] = cur["from"] > today.isoformat()
            out["proposal"] = None if s["style"] == "manual" else proposal(s, est)
            ph = (out["program"] or {}).get("current")
            out["phase_change"] = bool(ph and (ph["type"] != s["goal"] or (ph["type"] != "maintain" and abs(ph["rate"] - s["rate"]) > 0.001)))
        return out

    # ------------------------------------------------------------ changes

    def _start_period(self, data, settings, start):
        """New targets from `start`; a period that hadn't started yet is replaced."""
        periods = [p for p in data["periods"] if p["from"] < start.isoformat()]
        periods.append({"from": start.isoformat(), "settings": settings})
        data["periods"] = periods[-MAX_PERIODS:]

    def _log(self, data, decision, note):
        data["checkins"].append({"date": self._today().isoformat(), "decision": decision, "note": note})
        del data["checkins"][:-MAX_CHECKINS]

    def save(self, body):
        """Confirm the whole strategy (the Edit strategy steps)."""
        settings = check_settings(body.get("strategy"))
        eff = body.get("effective", "today")
        if eff not in ("today", "monday"):
            raise ValueError("effective must be today or monday.")
        today = self._today()
        start = today if eff == "today" else today + datetime.timedelta(days=7 - today.weekday())
        with self._lock:
            data = self._load()
            before = copy.deepcopy(data)
            first = not data["periods"]
            self._start_period(data, settings, start)
            if first or not data.get("next_checkin"):
                data["next_checkin"] = (start + datetime.timedelta(days=7)).isoformat()
            self._log(data, f"Strategy set: {settings['kcal']:,} kcal a day on average",
                      f"From {start.day} {start.strftime('%b')} · {GOAL_NAMES[settings['goal']]} · {STYLE_NAMES[settings['style']]}")
            self._commit(before)
        return self.view()

    def checkin(self, body):
        """Close the weekly check-in. Targets change only on accept, lower or raise."""
        action = body.get("action")
        if action not in ("accept", "keep", "minus", "plus", "phase"):
            raise ValueError("action must be accept, keep, minus, plus or phase.")
        today = self._today()
        est = self._estimate()
        with self._lock:
            data = self._load()
            if not data["periods"]:
                raise ValueError("Set a strategy first.")
            before = copy.deepcopy(data)
            s = copy.deepcopy(data["periods"][-1]["settings"])
            old = s["kcal"]
            if est and est.get("trend_kg"):
                s["kg"] = round(float(est["trend_kg"]), 1)
            if action == "accept":
                pr = proposal(s, est) if s["style"] != "manual" else None
                if pr is None:
                    raise ValueError("There's no proposal to accept yet.")
                kcal = pr["kcal"]
                if s["style"] == "collab" and body.get("kcal") not in (None, ""):
                    kcal = _num(body, "kcal", 1000, 6000, whole=True)
                s["kcal"] = kcal
            elif action in ("minus", "plus"):
                s["kcal"] = max(1000, min(6000, old + (100 if action == "plus" else -100)))
            elif action == "phase":
                ph = (self._phase_info(data, today) or {}).get("current")
                if not ph:
                    raise ValueError("No program phase is running today.")
                s["goal"] = ph["type"]
                s["rate"] = 0 if ph["type"] == "maintain" else ph["rate"]
                pr = proposal(s, est) if s["style"] != "manual" else None
                if pr:
                    s["kcal"] = pr["kcal"]
            note = (f"Expenditure {est['expenditure']:,} kcal · trend {est.get('weekly_change_kg', 0):+.2f} kg/week"
                    if est and est.get("expenditure") is not None else "Expenditure still calibrating")
            if s["kcal"] == old and action != "phase":
                decision = f"Kept {old:,} kcal (no change)"
            elif action == "phase":
                decision = f"Started {ph['name']}: {s['kcal']:,} kcal"
            else:
                decision = f"{'Accepted' if action == 'accept' else 'Changed to'} {s['kcal']:,} kcal ({s['kcal'] - old:+,})"
            if s != data["periods"][-1]["settings"]:
                self._start_period(data, s, today)
            data["next_checkin"] = (today + datetime.timedelta(days=7)).isoformat()
            self._log(data, decision, note)
            self._commit(before)
        return self.view()

    def set_program(self, body):
        """Phases that run one after another from a start date. A phase change still
        needs Craig's yes at a check-in; the program never changes targets by itself."""
        phases = body.get("phases")
        if not isinstance(phases, list) or not 1 <= len(phases) <= 8:
            raise ValueError("phases must be a list of 1 to 8 phases.")
        start = body.get("start")
        try:
            start_d = datetime.date.fromisoformat(start)
        except (TypeError, ValueError):
            raise ValueError("start must look like 2026-10-09.")
        if abs((start_d - self._today()).days) > 366:
            raise ValueError("start must be within a year of today.")
        clean = []
        for ph in phases:
            if not isinstance(ph, dict):
                raise ValueError("Each phase must be an object.")
            t = _pick(ph, "type", PHASE_TYPES)
            name = ph.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 40:
                raise ValueError("Each phase needs a name of up to 40 characters.")
            clean.append({"type": t, "name": name.strip(), "weeks": _num(ph, "weeks", 1, 52, whole=True),
                          "rate": 0 if t == "maintain" else _num(ph, "rate", 0.1, 1.0)})
        with self._lock:
            data = self._load()
            before = copy.deepcopy(data)
            data["program"] = {"start": start_d.isoformat(), "phases": clean}
            self._commit(before)
        return self.view()
