import datetime
import io
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
from health import Health, TraceApp, nutrients
from sources import SourceError

LOGIN = "craig@example.com"
TODAY = datetime.date(2026, 10, 8)  # a Thursday


class Recorder:
    """Sample answers, plus a record of every call and its token."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}
        self.opener = demo_health.make_opener(lambda: TODAY)

    def __call__(self, req, timeout=None):
        self.calls.append((req.full_url, req.get_header("Authorization")))
        for host, code in self.fail.items():
            if host in req.full_url:
                raise urllib.error.HTTPError(req.full_url, code, "no", {}, io.BytesIO(b""))
        return self.opener(req, timeout)


def health(rec, **extra):
    d = tempfile.mkdtemp()
    cfg = {}
    for name in ("nutritrace", "lifttrace", "cooktrace"):
        key = Path(d, name + ".key")
        key.write_text(f"{name[:2]}_pat_secret\n")
        cfg[name] = {"url": f"http://{name}.sample", "key_file": str(key), "web_url": f"https://x/{name}"}
    cfg.update(extra)
    return Health(cfg, opener=rec, today=TODAY)


class Units(unittest.TestCase):
    def test_nutrient_names(self):
        self.assertEqual(nutrients({"calories": "500", "proteins": 30, "carbohydrates": 50, "fat": 10, "fiber": 4}),
                         {"kcal": 500, "protein": 30, "carbs": 50, "fat": 10, "fibre": 4})
        self.assertEqual(nutrients({"energy": {"kcal": 210}})["kcal"], 210)
        self.assertIsNone(nutrients(None)["protein"])

    def test_token_from_file_and_errors(self):
        rec = Recorder(fail={"lifttrace": 403})
        hl = health(rec)
        hl.food()
        self.assertTrue(all(auth == "Bearer nu_pat_secret" for url, auth in rec.calls))
        self.assertTrue(all("/api/v1/" in url for url, _ in rec.calls))
        t = hl.train()
        self.assertFalse(t["train"]["ok"])
        self.assertIn("scope", t["train"]["error"])
        missing = TraceApp("NutriTrace", {"url": "http://x", "key_file": "/nonexistent"})
        with self.assertRaises(SourceError):
            missing.get("/goals")

    def test_cache(self):
        rec = Recorder()
        hl = health(rec)
        hl.food()
        n = len(rec.calls)
        hl.food()
        self.assertEqual(len(rec.calls), n)


class Screens(unittest.TestCase):
    def setUp(self):
        self.h = health(Recorder())

    def test_food_week_and_day(self):
        f = self.h.food()
        week = f["week"]["data"]
        self.assertEqual(week[0]["date"], "2026-10-05")
        self.assertEqual([d["status"] for d in week][3:], ["open", "future", "future", "future"])
        self.assertTrue(all(d["status"] in ("logged", "none") for d in week[:3]))
        day = f["day"]["data"]
        self.assertEqual(day["date"], "2026-10-08")
        self.assertEqual([m["meal"] for m in day["meals"]], ["Breakfast", "Lunch"])
        self.assertEqual(f["goals"]["data"]["kcal"], 2300)
        self.assertFalse(f["estimate"]["ok"])
        self.assertEqual(f["links"]["lifttrace"], "https://x/lifttrace")
        past = self.h.food("2026-10-06")["day"]["data"]
        self.assertIn("Snacks", [m["meal"] for m in past["meals"]])

    def test_progress_averages_logged_days_only(self):
        p = self.h.progress(14)
        rows = p["nutrition"]["data"]["days"]
        self.assertEqual(len(rows), 14)
        self.assertEqual(rows[-1]["status"], "open")
        logged = [r for r in rows if r["status"] == "logged"]
        self.assertEqual(p["nutrition"]["data"]["logged"], len(logged))
        avg = sum(r["kcal"] for r in logged) / len(logged)
        self.assertAlmostEqual(p["nutrition"]["data"]["avg"]["kcal"], avg, places=0)
        self.assertFalse(p["weight"]["ok"])  # no estimator file in the sample config

    def test_train(self):
        t = self.h.train()["train"]["data"]
        self.assertTrue(t["program"]["active"])
        self.assertEqual(t["sessions"][0]["name"], "Lower B")
        self.assertEqual(t["next"]["name"], "Upper A")
        bench = t["next"]["exercises"][0]
        self.assertEqual(bench["name"], "Bench press")
        self.assertIsNotNone(bench["last"])
        self.assertNotIn(40.0, [e["top"] for e in t["sessions"][0]["exercises"]])  # warm-ups left out
        self.assertEqual(self.h.train()["records"]["data"][0]["name"], "Bench press")

    def test_meals(self):
        m = self.h.meals()
        self.assertEqual(m["week"], ["2026-10-05", "2026-10-11"])
        self.assertTrue(all(x["date"] >= "2026-10-08" for x in m["planned"]["data"]))
        self.assertTrue(all("2026-10-05" <= x["date"] < "2026-10-08" for x in m["cooked"]["data"]))
        self.assertEqual(len(m["shopping"]["data"]), 5)
        self.assertEqual(m["recipes"]["data"]["items"][0]["kcal"], 520)

    def test_estimate_file(self):
        f = Path(tempfile.mkdtemp(), "est.json")
        f.write_text(json.dumps({"as_of": "2026-10-07", "expenditure": 2610, "trend_kg": 80.1,
                                 "weekly_change_kg": -0.4, "held": False, "secret": "x"}))
        hl = health(Recorder(), estimator_file=str(f))
        e = hl.estimate()
        self.assertEqual(e["expenditure"], 2610)
        self.assertNotIn("secret", e)
        self.assertEqual(hl.weight()["trend_kg"], 80.1)

    def test_held_estimate_is_not_shown_as_a_number(self):
        f = Path(tempfile.mkdtemp(), "est.json")
        f.write_text(json.dumps({"as_of": "2026-10-07", "expenditure": None, "held": True}))
        with self.assertRaises(SourceError):
            health(Recorder(), estimator_file=str(f)).estimate()

    def test_not_connected(self):
        p = Health({}, today=TODAY).progress()
        self.assertFalse(p["nutrition"]["ok"])
        self.assertIn("not connected", p["nutrition"]["error"])


class Http(unittest.TestCase):
    def test_routes(self):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache)
        web = Path(__file__).resolve().parent.parent / "web"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, web, cache, app, None, None,
                                                                  health(Recorder())))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"

        def get(path, login=LOGIN):
            r = urllib.request.Request(base + path, headers={"Tailscale-User-Login": login})
            try:
                with urllib.request.urlopen(r) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        self.assertTrue(get("/api/health/food")[1]["ok"])
        self.assertEqual(get("/api/health/food?day=2026-10-06")[1]["day"]["data"]["date"], "2026-10-06")
        self.assertEqual(get("/api/health/food?day=../x")[0], 400)
        self.assertEqual(get("/api/health/food?day=2026-13-40")[0], 400)
        for what in ("train", "meals", "progress"):
            self.assertTrue(get("/api/health/" + what)[1]["ok"], what)
        self.assertEqual(get("/api/health/other")[0], 404)
        self.assertEqual(get("/api/health/food", login="x@y.z")[0], 403)

    def test_not_configured(self):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        handler = h.make_handler(cfg, Path("."), cache, App(cfg, cache))
        srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        r = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/api/health/food",
                                   headers={"Tailscale-User-Login": LOGIN})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(r)
        self.assertEqual(e.exception.code, 503)


if __name__ == "__main__":
    unittest.main()
