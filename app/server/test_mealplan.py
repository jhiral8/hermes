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
from mealplan import MealPlan
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
