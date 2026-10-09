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
from health import Health, Refused
from health_log import HealthLog
from sources import SourceError
from test_health import LOGIN, TODAY, Recorder


def setup(rec, write_keys=True):
    d = tempfile.mkdtemp()
    cfg = {}
    for name in ("nutritrace", "lifttrace", "cooktrace"):
        Path(d, name + ".key").write_text("read_secret\n")
        cfg[name] = {"url": f"http://{name}.sample", "key_file": str(Path(d, name + ".key"))}
        if write_keys:
            Path(d, name + "-write.key").write_text("write_secret\n")
            cfg[name]["write_key_file"] = str(Path(d, name + "-write.key"))
    hl = Health(cfg, opener=rec, today=TODAY)
    return hl, HealthLog(hl)


class Log(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)

    def test_add_then_log_food_with_the_write_token(self):
        rec = Recorder()
        hl, log = setup(rec)
        made = log.add_food({"name": " Skyr ", "brand": "Arla", "portion": 150, "unit": "g",
                             "nutrition": {"kcal": 95, "protein": 16, "carbs": None}})
        self.assertFalse(made["existing"])
        self.assertEqual((made["food"]["name"], made["food"]["kcal"], made["food"]["carbs"]), ("Skyr", 95, None))
        again = log.add_food({"name": "skyr", "brand": "arla", "portion": 150, "unit": "g", "nutrition": {"kcal": 95}})
        self.assertTrue(again["existing"])
        self.assertEqual(again["food"]["id"], made["food"]["id"])
        out = log.log_food({"food_id": made["food"]["id"], "meal": 2, "quantity": 1.5})
        self.assertEqual((out["date"], out["logged"]["meal"]), (TODAY.isoformat(), "Dinner"))
        writes = [(u, a) for u, a in rec.calls if "/foods" in u and "?" not in u or "/diary/" in u and u.endswith("/food")]
        self.assertTrue(writes and all(a == "Bearer write_secret" for _, a in writes))
        dinner = [m for m in hl.food()["day"]["data"]["meals"] if m["meal"] == "Dinner"][0]
        self.assertIn("Skyr", [i["name"] for i in dinner["items"]])

    def test_water_set_and_searches(self):
        hl, log = setup(Recorder())
        self.assertEqual(log.log_water({"amount_ml": 250})["total_ml"], 250)
        ex = log.search_exercises("bench")["exercises"]
        self.assertEqual(ex[0]["name"], "Bench press")
        s = log.log_set({"exercise_id": ex[0]["id"], "reps": 5, "weight": 82.5, "rpe": 8})
        self.assertEqual((s["exercise"], s["set"]["reps"], s["set"]["weight"]), ("Bench press", 5, 82.5))
        self.assertEqual(log.search_foods("oat")["foods"][0]["name"], "Oats")

    def test_checks_before_sending(self):
        rec = Recorder()
        _, log = setup(rec)
        bad = [
            (log.add_food, {"name": "", "portion": 1, "unit": "g", "nutrition": {"kcal": 1}}),
            (log.add_food, {"name": "X", "portion": 0, "unit": "g", "nutrition": {"kcal": 1}}),
            (log.add_food, {"name": "X", "portion": 1, "unit": "bucket", "nutrition": {"kcal": 1}}),
            (log.add_food, {"name": "X", "portion": 1, "unit": "g", "nutrition": {"protein": 1}}),
            (log.add_food, {"name": "X", "portion": 1, "unit": "g", "barcode": "12ab", "nutrition": {"kcal": 1}}),
            (log.log_food, {"food_id": "1; drop"}),
            (log.log_food, {"food_id": 1, "meal": 7}),
            (log.log_food, {"food_id": 1, "date": "2026-10-09"}),   # future (TODAY is the 8th)
            (log.log_food, {"food_id": 1, "date": "2026-08-01"}),   # too far back
            (log.log_food, {"food_id": 1, "date": "../x"}),
            (log.log_water, {"amount_ml": 99999}),
            (log.log_set, {"exercise_id": 1}),                       # no reps or duration
            (log.log_set, {"exercise_id": 1, "reps": 5, "warmup": "yes"}),
            (log.log_set, {"exercise_id": 1, "reps": True}),
        ]
        for fn, body in bad:
            with self.assertRaises(ValueError, msg=body):
                fn(body)
        self.assertEqual(rec.calls, [])

    def test_refusals_and_missing_write_token(self):
        _, log = setup(Recorder())
        with self.assertRaises(Refused) as e:
            log.log_food({"food_id": 999})
        self.assertIn("not found", str(e.exception))
        _, log = setup(Recorder(), write_keys=False)
        with self.assertRaises(SourceError) as e:
            log.log_water({"amount_ml": 250})
        self.assertIn("write token", str(e.exception))
        _, log = setup(Recorder(fail={"nutritrace": 404}))
        with self.assertRaises(SourceError) as e:
            log.log_water({"amount_ml": 250})
        self.assertIn("switched on", str(e.exception))


class Http(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)
        self.audit = Path(tempfile.mkdtemp(), "audit.log")
        cfg = {"allowed_logins": [LOGIN], "services": [], "audit_log": str(self.audit)}
        cache = h.StatusCache([], 15)
        hl, _ = setup(Recorder())
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, App(cfg, cache), None, None, hl))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        self.base = f"http://127.0.0.1:{srv.server_port}"

    def call(self, path, body=None, action=True, login=LOGIN):
        headers = {"Tailscale-User-Login": login, "Content-Type": "application/json"}
        if action:
            headers["X-Hermes-Action"] = "1"
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None

    def test_routes(self):
        st, made = self.call("/api/health/log/food-new", {"name": "Skyr", "portion": 150, "unit": "g",
                                                          "nutrition": {"kcal": 95}})
        self.assertEqual(st, 200)
        st, out = self.call("/api/health/log/food", {"food_id": made["food"]["id"], "meal": 0})
        self.assertEqual((st, out["logged"]["name"]), (200, "Skyr"))
        self.assertEqual(self.call("/api/health/log/water", {"amount_ml": 300})[0], 200)
        self.assertEqual(self.call("/api/health/log/set", {"exercise_id": 1, "reps": 5})[0], 200)
        self.assertEqual(self.call("/api/health/log/food", {"food_id": 999})[0], 400)
        self.assertEqual(self.call("/api/health/log/water", {"amount_ml": -1})[0], 400)
        self.assertEqual(self.call("/api/health/log/delete", {})[0], 404)
        self.assertEqual(self.call("/api/health/log/water", {"amount_ml": 300}, action=False)[0], 403)
        self.assertEqual(self.call("/api/health/log/water", {"amount_ml": 300}, login="x@y.z")[0], 403)
        st, found = self.call("/api/health/search/foods?q=sky")
        self.assertEqual((st, found["foods"][0]["name"]), (200, "Skyr"))
        self.assertEqual(self.call("/api/health/search/exercises?q=squat")[1]["exercises"][0]["name"], "Back squat")
        lines = [json.loads(x) for x in self.audit.read_text().splitlines()]
        self.assertEqual([x["action"] for x in lines],
                         ["health_food_new", "health_food", "health_water", "health_set"])
        self.assertNotIn("Skyr", self.audit.read_text())  # what was eaten stays out of the audit log


if __name__ == "__main__":
    unittest.main()
