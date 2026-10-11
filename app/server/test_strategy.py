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
from strategy import Strategy, check_settings, proposal, week_targets
from test_health import LOGIN, TODAY, Recorder

BASE = {"goal": "lose", "rate": 0.4, "style": "coached", "kcal": 2200, "diet": "balanced", "gkg": 1.8,
        "fib": 30, "dist": "even", "shift": 200, "train": [], "custom": {}, "kg": 82.4}
EST = {"as_of": "2026-10-07", "expenditure": 2729, "low": 2600, "high": 2850, "trend_kg": 82.41,
       "weekly_change_kg": -0.33, "intake_days": 14, "weigh_ins": 10}


def make(est=EST, path=None, today=TODAY):
    d = tempfile.mkdtemp()
    cfg = {}
    if est is not None:
        Path(d, "est.json").write_text(json.dumps(est))
        cfg["estimator_file"] = str(Path(d, "est.json"))
    hl = Health(cfg, opener=Recorder(), today=today)
    return hl, Strategy(path, hl)


class Maths(unittest.TestCase):
    def test_checks(self):
        self.assertEqual(check_settings(BASE)["kcal"], 2200)
        self.assertEqual(check_settings({**BASE, "goal": "maintain", "rate": None})["rate"], 0)
        for bad in ({"goal": "bulk"}, {"kcal": 900}, {"rate": 3}, {"gkg": "x"}, {"dist": "training", "train": []},
                    {"dist": "training", "train": ["Funday"]}, {"dist": "custom", "custom": {"Mon": 2000}}, {"kg": None}):
            with self.assertRaises(ValueError, msg=bad):
                check_settings({**BASE, **bad})

    def test_week_targets_keep_the_average(self):
        s = check_settings({**BASE, "dist": "training", "train": ["Mon", "Wed", "Fri", "Sat"], "shift": 200})
        wk = week_targets(s)
        self.assertEqual(wk[0]["kcal"], 2400)
        self.assertEqual(wk[1]["kcal"], 1933)
        self.assertAlmostEqual(sum(x["kcal"] for x in wk) / 7, 2200, delta=1)
        even = week_targets(check_settings(BASE))[2]
        self.assertEqual((even["protein"], even["fat"], even["fibre"]), (148, 80, 30))
        self.assertEqual(even["carbs"], round((2200 - 148 * 4 - 80 * 9) / 4))

    def test_proposal(self):
        s = check_settings(BASE)
        pr = proposal(s, EST)
        # 2729 - 0.4% of 82.41 kg a week * 7700 / 7 = 2729 - 362.6 -> 2365 (nearest 5)
        self.assertEqual((pr["kcal"], pr["delta"], pr["goal_kg_week"]), (2365, 165, -0.33))
        self.assertIsNone(proposal(s, None))
        self.assertEqual(proposal({**s, "goal": "maintain", "rate": 0}, EST)["kcal"], 2730)
        self.assertEqual(proposal({**s, "rate": 1.0}, {**EST, "expenditure": 1500})["kcal"], 1300)


class Store(unittest.TestCase):
    def test_no_strategy_yet(self):
        _, st = make()
        v = st.view()
        self.assertIsNone(v["strategy"])
        self.assertFalse(v["due"])
        self.assertIsNone(st.targets_for(TODAY))
        with self.assertRaises(ValueError):
            st.checkin({"action": "keep"})

    def test_save_and_periods(self):
        path = Path(tempfile.mkdtemp(), "strategy.json")
        hl, st = make(path=str(path))
        v = st.save({"strategy": BASE, "effective": "today"})
        self.assertEqual((v["strategy"]["kcal"], v["next_checkin"], v["due"]), (2200, "2026-10-15", False))
        self.assertEqual(st.targets_for(TODAY)["kcal"], 2200)
        self.assertIsNone(st.targets_for(TODAY - datetime.timedelta(days=1)))
        # A change from Monday leaves this week alone.
        v = st.save({"strategy": {**BASE, "kcal": 2000}, "effective": "monday"})
        self.assertTrue(v["pending"])
        self.assertEqual(st.targets_for(TODAY)["kcal"], 2200)
        self.assertEqual(st.targets_for(datetime.date(2026, 10, 12))["kcal"], 2000)
        # Saved to the file and read back by a fresh store.
        again = Strategy(str(path), hl)
        self.assertEqual(again.targets_for(datetime.date(2026, 10, 12))["kcal"], 2000)
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o640")
        with self.assertRaises(ValueError):
            st.save({"strategy": BASE, "effective": "someday"})

    def test_checkins(self):
        _, st = make()
        st.save({"strategy": BASE})
        v = st.view()
        self.assertEqual(v["proposal"]["kcal"], 2365)
        v = st.checkin({"action": "keep"})
        self.assertEqual((v["strategy"]["kcal"], v["checkins"][0]["decision"]), (2200, "Kept 2,200 kcal (no change)"))
        self.assertEqual(v["next_checkin"], "2026-10-15")
        v = st.checkin({"action": "accept", "kcal": 1800})  # coached ignores an edited number
        self.assertEqual(v["strategy"]["kcal"], 2365)
        self.assertIn("+165", v["checkins"][0]["decision"])
        v = st.checkin({"action": "minus"})
        self.assertEqual(v["strategy"]["kcal"], 2265)
        st.save({"strategy": {**BASE, "style": "collab"}})
        self.assertEqual(st.checkin({"action": "accept", "kcal": 2300})["strategy"]["kcal"], 2300)
        with self.assertRaises(ValueError):
            st.checkin({"action": "accept", "kcal": 99999})
        st.save({"strategy": {**BASE, "style": "manual"}})
        self.assertIsNone(st.view()["proposal"])
        with self.assertRaises(ValueError):
            st.checkin({"action": "accept"})
        with self.assertRaises(ValueError):
            st.checkin({"action": "delete"})

    def test_no_estimate_means_no_proposal(self):
        _, st = make(est=None)
        st.save({"strategy": BASE})
        self.assertIsNone(st.view()["proposal"])
        self.assertEqual(st.checkin({"action": "plus"})["strategy"]["kcal"], 2300)

    def test_check_in_is_due_after_a_week(self):
        _, st = make()
        st.save({"strategy": BASE})
        later = Strategy(None, Health({}, today=TODAY + datetime.timedelta(days=7)))
        later._data = st._data
        self.assertTrue(later.view()["due"])

    def test_program_needs_a_yes_to_switch(self):
        _, st = make()
        st.save({"strategy": BASE})
        v = st.set_program({"start": "2026-09-01", "phases": [
            {"type": "lose", "name": "Fat loss", "weeks": 4, "rate": 0.4},
            {"type": "maintain", "name": "Maintenance", "weeks": 2},
            {"type": "gain", "name": "Lean gain", "weeks": 12, "rate": 0.25}]})
        self.assertEqual([p["status"] for p in v["program"]["phases"]], ["done", "current", "planned"])
        self.assertTrue(v["phase_change"])
        self.assertEqual(v["strategy"]["goal"], "lose")  # nothing switched by itself
        v = st.checkin({"action": "phase"})
        self.assertEqual((v["strategy"]["goal"], v["strategy"]["kcal"], v["phase_change"]), ("maintain", 2730, False))
        for bad in ({"start": "x", "phases": []}, {"start": "2026-09-01", "phases": [{"type": "cut", "name": "a", "weeks": 1}]},
                    {"start": "2026-09-01", "phases": [{"type": "lose", "name": "", "weeks": 1, "rate": 0.4}]},
                    {"start": "2030-01-01", "phases": [{"type": "maintain", "name": "a", "weeks": 1}]}):
            with self.assertRaises(ValueError, msg=bad):
                st.set_program(bad)


class Http(unittest.TestCase):
    def setUp(self):
        demo_health.reset()
        self.addCleanup(demo_health.reset)
        self.audit = Path(tempfile.mkdtemp(), "audit.log")
        cfg = {"allowed_logins": [LOGIN], "services": [], "audit_log": str(self.audit)}
        cache = h.StatusCache([], 15)
        d = tempfile.mkdtemp()
        hcfg = {"estimator_file": str(Path(d, "est.json"))}
        Path(d, "est.json").write_text(json.dumps(EST))
        for name in ("nutritrace", "lifttrace", "cooktrace"):
            Path(d, name + ".key").write_text("read_secret\n")
            hcfg[name] = {"url": f"http://{name}.sample", "key_file": str(Path(d, name + ".key"))}
        hl = Health(hcfg, opener=Recorder(), today=TODAY)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, App(cfg, cache), None, None, hl,
                                                                   strategy=Strategy(None, hl)))
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
        st, v = self.call("/api/health/strategy")
        self.assertEqual((st, v["strategy"]), (200, None))
        food = self.call("/api/health/food")[1]
        self.assertNotEqual(food["goals"].get("source"), "strategy")  # NutriTrace's goals until a strategy exists
        self.assertEqual(self.call("/api/health/strategy", {"strategy": {**BASE, "kcal": 50}})[0], 400)
        self.assertEqual(self.call("/api/health/strategy", {"strategy": BASE}, action=False)[0], 403)
        st, v = self.call("/api/health/strategy", {"strategy": BASE, "effective": "today"})
        self.assertEqual((st, v["strategy"]["kcal"]), (200, 2200))
        food = self.call("/api/health/food")[1]
        self.assertEqual((food["goals"]["source"], food["goals"]["data"]["kcal"], food["checkin_due"]), ("strategy", 2200, False))
        self.assertEqual(self.call("/api/health/progress")[1]["goals"]["data"]["kcal"], 2200)
        st, v = self.call("/api/health/checkin", {"action": "accept"})
        self.assertEqual((st, v["strategy"]["kcal"]), (200, 2365))
        self.assertEqual(self.call("/api/health/program", {"start": "2026-10-01", "phases": [
            {"type": "lose", "name": "Fat loss", "weeks": 8, "rate": 0.4}]})[0], 200)
        lines = [json.loads(x) for x in self.audit.read_text().splitlines()]
        self.assertEqual([x["action"] for x in lines], ["health_strategy", "health_checkin", "health_program"])


if __name__ == "__main__":
    unittest.main()
