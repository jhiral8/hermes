import datetime
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import demo_health
import hermes_app as h
from api import App
from health import Health
from health_log import HealthLog
from mealplan import DEFAULT_PREFS, MealPlan, ask_max, check_prefs, propose_week
from strategy import Strategy
from chat import DemoMax
from test_health import LOGIN, TODAY


def setup(path=None):
    hl = Health(demo_health.sample_config(), opener=demo_health.make_opener(lambda: TODAY), today=TODAY)
    return hl, MealPlan(path, hl, HealthLog(hl))


class Plan(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)

    def test_recipes_are_per_serving(self):
        _, mp = setup()
        r = {x["name"]: x for x in mp.recipes("")}
        self.assertEqual((r["Chicken tikka traybake"]["kcal"], r["Chicken tikka traybake"]["protein"]), (520, 45))
        self.assertIsNone(r["Tuna sandwich"]["kcal"])
        self.assertEqual([x["name"] for x in mp.recipes("chilli")], ["Beef chilli"])

    def test_add_view_and_file(self):
        path = Path(tempfile.mkdtemp(), "meal-plan.json")
        hl, mp = setup(str(path))
        s = mp.add({"date": TODAY.isoformat(), "meal": "dinner", "recipe_id": 1, "portions": 1.5})
        self.assertEqual((s["recipe"], s["per_serving"]["kcal"], s["status"]), ("Chicken tikka traybake", 520, "planned"))
        mp.add({"date": "2026-10-11", "meal": "breakfast", "recipe_id": 4})
        mp.add({"date": "2026-10-13", "meal": "lunch", "recipe_id": 5})  # next week
        v = mp.view()
        self.assertEqual(v["week"][0], "2026-10-05")
        self.assertEqual([x["recipe"] for x in v["slots"]], ["Chicken tikka traybake", "Overnight oats"])
        self.assertTrue(v["cooktrace_planned"]["ok"])
        self.assertEqual(len(mp.view("2026-10-14")["slots"]), 1)
        self.assertEqual(len(MealPlan(str(path), hl, None).view()["slots"]), 2)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o640")
        for bad in ({"meal": "brunch"}, {"date": "2026-01-01"}, {"date": "x"}, {"recipe_id": 99}, {"recipe_id": None},
                    {"portions": 0}, {"portions": "lots"}, {"note": "x" * 201}):
            with self.assertRaises(ValueError, msg=bad):
                mp.add({"date": TODAY.isoformat(), "meal": "lunch", "recipe_id": 2, **bad})

    def test_log_adds_recipe_to_nutritrace_once(self):
        hl, mp = setup()
        a = mp.add({"date": TODAY.isoformat(), "meal": "dinner", "recipe_id": 1, "portions": 1.5})
        b = mp.add({"date": TODAY.isoformat(), "meal": "lunch", "recipe_id": 1})
        out = mp.log_meal({"id": a["id"]})
        self.assertEqual((out["meal"], out["kcal"]), ("Dinner", 780))
        mp.log_meal({"id": b["id"]})
        made = [f for f in demo_health.CATALOG if f["name"] == "Chicken tikka traybake"]
        self.assertEqual(len(made), 1)  # second log reused the same NutriTrace food
        self.assertEqual((made[0]["unit"], made[0]["nutrition"]["calories"]), ("serving", 520))
        dinner = [m for m in hl.food()["day"]["data"]["meals"] if m["meal"] == "Dinner"][0]
        self.assertEqual(dinner["items"][-1]["name"], "Chicken tikka traybake")
        self.assertEqual(mp.view()["slots"][0]["status"], "logged")
        with self.assertRaises(ValueError):
            mp.log_meal({"id": a["id"]})
        with self.assertRaises(ValueError):
            mp.change({"id": a["id"], "action": "remove"})  # a logged meal stays

    def test_no_calories_future_and_changes(self):
        _, mp = setup()
        t = mp.add({"date": TODAY.isoformat(), "meal": "lunch", "recipe_id": 6})
        with self.assertRaises(ValueError):
            mp.log_meal({"id": t["id"]})
        f = mp.add({"date": (TODAY + datetime.timedelta(days=3)).isoformat(), "meal": "dinner", "recipe_id": 3})
        with self.assertRaises(ValueError):
            mp.log_meal({"id": f["id"]})  # NutriTrace logging refuses a future day
        with self.assertRaises(ValueError):
            mp.cooked({"id": f["id"]})
        self.assertEqual(mp.change({"id": f["id"], "action": "skip"})["action"], "skip")
        self.assertEqual([s["status"] for s in mp.view()["slots"] if s["id"] == f["id"]], ["skipped"])
        mp.change({"id": f["id"], "action": "remove"})
        self.assertNotIn(f["id"], [s["id"] for s in mp.view()["slots"]])
        with self.assertRaises(ValueError):
            mp.change({"id": "nope", "action": "skip"})
        with self.assertRaises(ValueError):
            mp.change({"id": t["id"], "action": "delete"})
        c = mp.add({"date": TODAY.isoformat(), "meal": "dinner", "recipe_id": 2})
        self.assertEqual(mp.cooked({"id": c["id"]})["recipe"], "Salmon, greens and potatoes")


class FakeMax:
    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def open(self, cid, text):
        self.prompts.append(text)
        return None

    def events(self, _):
        for i in range(0, len(self.answer), 7):
            yield {"type": "text", "delta": self.answer[i:i + 7]}
        yield {"type": "done"}


class FakeChat:
    def __init__(self, backend, killed=False):
        self.backend, self.killed = backend, killed

    def _check_kill(self):
        if self.killed:
            from chat import ChatError
            raise ChatError(423, "Agents are stopped.")


class Proposals(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)

    def ctx(self, **kw):
        recipes = [{"id": 1, "name": "A", "kcal": 500, "protein": 40}, {"id": 2, "name": "B", "kcal": 400, "protein": 10},
                   {"id": 3, "name": "C", "kcal": 300, "protein": 30}]
        base = {"days": ["2026-10-09"], "recipes": recipes, "targets": {"2026-10-09": {"kcal": 2000, "protein": 150}},
                "taken": {"2026-10-09": {"meals": {"lunch"}, "kcal": 700}}, "prefs": dict(DEFAULT_PREFS), "ratings": {},
                "training": set(), "strategy": None}
        return {**base, **kw}

    def test_planner_fits_targets(self):
        out = propose_week(self.ctx())
        self.assertEqual([x["meal"] for x in out], ["breakfast", "dinner"])
        self.assertLess(abs(sum(x["kcal"] for x in out) - 1300), 250)
        self.assertEqual(len({x["recipe_id"] for x in out}), 2)  # no repeat in a day

    def test_preferences_steer_the_planner(self):
        week = ["2026-10-09", "2026-10-10", "2026-10-11", "2026-10-12"]
        targets = {d: {"kcal": 1500, "protein": 120} for d in week}
        r = [{"id": i, "name": f"R{i}", "kcal": 500, "protein": 40} for i in range(1, 7)]
        plain = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={}))
        fav = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={},
                                    prefs={**DEFAULT_PREFS, "favourites": ["6"], "leftovers": False}))
        self.assertGreater(sum(x["recipe_id"] == 6 for x in fav), sum(x["recipe_id"] == 6 for x in plain))
        self.assertIn("favourite", [x.get("why") for x in fav if x["recipe_id"] == 6][0])
        capped = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={},
                                       prefs={**DEFAULT_PREFS, "max_repeats": 2, "favourites": ["6"]}))
        self.assertLessEqual(max(sum(x["recipe_id"] == i for x in capped) for i in range(1, 7)), 2)
        rated = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={}, ratings={"5": 5.0}))
        self.assertGreater(sum(x["recipe_id"] == 5 for x in rated), sum(x["recipe_id"] == 5 for x in plain))
        left = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={}))
        for d0, d1 in zip(week, week[1:]):
            dinner = [x for x in left if x["date"] == d0 and x["meal"] == "dinner"][0]["recipe_id"]
            lunch = [x for x in left if x["date"] == d1 and x["meal"] == "lunch"][0]
            self.assertEqual(lunch["recipe_id"], dinner)
            self.assertIn("leftovers", lunch["why"])
        only = propose_week(self.ctx(days=week, recipes=r, targets=targets, taken={},
                                     prefs={**DEFAULT_PREFS, "plan_meals": ["dinner"]}))
        self.assertEqual({x["meal"] for x in only}, {"dinner"})
        self.assertTrue(all(x["kcal"] <= 1000 for x in only))  # leaves room for meals Craig plans himself

    def test_check_prefs(self):
        p = check_prefs({"favourites": [1, "1", 2], "never": [2], "avoid": [" Mushroom ", "mushroom"],
                         "plan_meals": ["dinner", "lunch"], "max_repeats": "2", "notes": " no fish on weekdays "})
        self.assertEqual((p["favourites"], p["never"], p["avoid"], p["plan_meals"], p["max_repeats"], p["notes"]),
                         (["1"], ["2"], ["mushroom"], ["lunch", "dinner"], 2, "no fish on weekdays"))
        for bad in ({"plan_meals": []}, {"plan_meals": ["brunch"]}, {"max_repeats": 9}, {"avoid": ["x" * 41]},
                    {"favourites": [True]}, {"notes": "x" * 1001}):
            with self.assertRaises(ValueError, msg=bad):
                check_prefs({"plan_meals": ["dinner"], **bad})

    def test_propose_and_apply(self):
        hl, mp = setup()
        mp.strategy = Strategy(None, hl)
        mp.strategy.save({"strategy": {"goal": "maintain", "style": "manual", "kcal": 2400, "diet": "balanced", "gkg": 1.8,
                                       "fib": 30, "dist": "even", "kg": 80}})
        mp.add({"date": TODAY.isoformat(), "meal": "dinner", "recipe_id": 1})
        out = mp.propose({})
        days = {x["date"] for x in out["items"]}
        self.assertTrue(all(d >= TODAY.isoformat() for d in days))
        self.assertNotIn((TODAY.isoformat(), "dinner"), {(x["date"], x["meal"]) for x in out["items"]})
        self.assertEqual(out["targets"][TODAY.isoformat()], 2400)
        self.assertEqual(len(mp.view()["slots"]), 1)  # proposing saves nothing
        r = mp.apply({"items": out["items"]})
        self.assertEqual(r["added"], len(out["items"]))
        self.assertEqual(len(mp.view()["slots"]), 1 + len(out["items"]))
        with self.assertRaises(ValueError):
            mp.apply({"items": []})
        with self.assertRaises(ValueError):
            mp.propose({"week": "2026-09-28"})

    def test_ask_max(self):
        hl, mp = setup()
        answer = ('Lots of protein early in the week. {"note": "High protein", "items": ['
                  '{"date": "2026-10-09", "meal": "dinner", "recipe_id": 3, "portions": 1.5},'
                  '{"date": "2026-10-10", "meal": "lunch", "recipe_id": "5", "portions": 1},'
                  '{"date": "2026-10-01", "meal": "lunch", "recipe_id": 5, "portions": 1},'
                  '{"date": "2026-10-10", "meal": "brunch", "recipe_id": 5, "portions": 1},'
                  '{"date": "2026-10-10", "meal": "dinner", "recipe_id": 6, "portions": 1}]}')
        fake = FakeMax(answer)
        mp.strategy = Strategy(None, hl)
        mp.strategy.save({"strategy": {"goal": "lose", "rate": 0.5, "style": "coached", "kcal": 2300, "diet": "balanced",
                                       "gkg": 2.0, "fib": 30, "dist": "training", "train": ["Fri"], "shift": 200, "kg": 80}})
        mp.set_prefs({"prefs": {"favourites": [5], "never": [2], "avoid": ["tikka"], "plan_meals": ["lunch", "dinner"],
                                "notes": "No fish on weekdays"}})
        out = ask_max(mp, {}, FakeChat(fake))
        self.assertEqual([(x["recipe"], x["portions"]) for x in out["items"]],
                         [("Beef chilli", 1.5), ("Chicken and rice bowl", 1)])
        self.assertIn("3 suggestions", out["note"])
        self.assertIn("Overnight oats", fake.prompts[0])
        self.assertNotIn("Tuna sandwich", fake.prompts[0])  # no calories, so not offered
        prompt = fake.prompts[0]
        for want in ("losing fat at 0.5%", "protein 2.0 g/kg", "2026-10-09 (training day)", "No fish on weekdays",
                     "Fill lunch, dinner", "5: Chicken and rice bowl", "favourite", "Foods I avoid: tikka"):
            self.assertIn(want, prompt)
        self.assertNotIn("Salmon", prompt)  # never suggest
        self.assertNotIn("Chicken tikka", prompt)  # avoided food
        self.assertIn("Goal: losing fat at 0.5% a week", out["uses"])
        self.assertEqual(mp.view()["slots"], [])
        from sources import SourceError
        with self.assertRaises(SourceError):
            ask_max(mp, {}, FakeChat(FakeMax("Sorry, can't.")))
        from chat import ChatError
        with self.assertRaises(ChatError):
            ask_max(mp, {}, FakeChat(fake, killed=True))
        self.assertTrue(ask_max(mp, {}, FakeChat(DemoMax()))["sample"])


class Http(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)
        self.audit = Path(tempfile.mkdtemp(), "audit.log")
        cfg = {"allowed_logins": [LOGIN], "services": [], "audit_log": str(self.audit)}
        cache = h.StatusCache([], 15)
        hl, mp = setup()
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, App(cfg, cache), None, None, hl,
                                                                   mealplan=mp))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}"

    def call(self, path, body=None, action=True):
        headers = {"Tailscale-User-Login": LOGIN, "Content-Type": "application/json"}
        if action:
            headers["X-Hermes-Action"] = "1"
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_routes(self):
        st, r = self.call("/api/health/recipes?q=oats")
        self.assertEqual((st, r["recipes"][0]["kcal"]), (200, 390))
        st, s = self.call("/api/health/plan/add", {"date": TODAY.isoformat(), "meal": "breakfast", "recipe_id": 4})
        self.assertEqual(st, 200)
        self.assertEqual(self.call("/api/health/plan?week=2026-10-08")[1]["slots"][0]["recipe"], "Overnight oats")
        self.assertEqual(self.call("/api/health/plan?week=nope")[0], 400)
        self.assertEqual(self.call("/api/health/plan/add", {"meal": "x"})[0], 400)
        self.assertEqual(self.call("/api/health/plan/log", {"id": s["id"]}, action=False)[0], 403)
        st, out = self.call("/api/health/plan/log", {"id": s["id"]})
        self.assertEqual((st, out["kcal"]), (200, 390))
        self.assertEqual(self.call("/api/health/plan/wipe", {})[0], 404)
        lines = [json.loads(x) for x in self.audit.read_text().splitlines()]
        self.assertEqual([x["action"] for x in lines], ["health_plan_add", "health_plan_log"])
        self.assertNotIn("oats", self.audit.read_text().lower())


if __name__ == "__main__":
    unittest.main()
