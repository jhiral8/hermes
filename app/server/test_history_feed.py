import datetime
import io
import json
import tempfile
import unittest
from pathlib import Path

import history_feed as hf
from sources import SourceError

D = datetime.date


def history(days, weights=None):
    return ({d["date"]: {"kcal": d["kcal"], "status": d["status"]} for d in days}, weights or {})


class Build(unittest.TestCase):
    def test_nutritrace_wins_on_overlap_and_mf_fills_the_rest(self):
        h = history([{"date": "2026-10-01", "kcal": 2000, "status": "complete"},
                     {"date": "2026-10-02", "kcal": 1800, "status": "complete"}], {"2026-10-01": 80.0})
        nt = {"2026-10-02": {"kcal": 2100, "items": 3}}
        out = {r["date"]: r for r in hf.build_days(h, nt, D(2026, 10, 3))}
        self.assertEqual(out["2026-10-01"]["intake"], 2000)
        self.assertEqual(out["2026-10-01"]["weight"], 80.0)
        self.assertEqual(out["2026-10-02"]["intake"], 2100)  # NutriTrace wins
        self.assertEqual(out["2026-10-02"]["status"], "complete")

    def test_nutritrace_day_with_no_items_is_not_a_logged_day(self):
        h = history([{"date": "2026-10-01", "kcal": 2000, "status": "complete"}])
        nt = {"2026-10-01": {"kcal": 0, "items": 0}}
        out = hf.build_days(h, nt, D(2026, 10, 2))
        self.assertEqual(out[0]["intake"], 2000)

    def test_days_with_no_data_are_missing_and_today_is_partial(self):
        h = history([{"date": "2026-10-01", "kcal": 2000, "status": "complete"}])
        out = hf.build_days(h, {"2026-10-03": {"kcal": 900, "items": 2}}, D(2026, 10, 3))
        self.assertEqual([r["status"] for r in out], ["complete", "missing", "partial"])
        self.assertIsNone(out[1]["intake"])
        self.assertEqual(out[2]["intake"], 900)

    def test_mf_partial_days_stay_partial(self):
        h = history([{"date": "2026-10-01", "kcal": 700, "status": "partial"}])
        out = hf.build_days(h, {}, D(2026, 10, 2))
        self.assertEqual(out[0]["status"], "partial")

    def test_no_history_gives_no_days(self):
        self.assertEqual(hf.build_days(({}, {}), {}, D(2026, 10, 2)), [])


class Fetch(unittest.TestCase):
    def test_reads_each_day_and_skips_unreadable_ones(self):
        seen = []

        class FakeApp:
            def __init__(self, *a, **k):
                pass

            def get(self, path, ttl=0):
                seen.append(path)
                if "10-02" in path:
                    raise SourceError("NutriTrace refused the token")
                return {"totals": {"calories": 1500}, "item_count": 2}
        orig = hf.TraceApp
        hf.TraceApp = FakeApp
        try:
            pauses = []
            out = hf.fetch_nutritrace("http://nt.test", "/k", D(2026, 10, 1), D(2026, 10, 3),
                                      pause=1.1, sleep=pauses.append)
        finally:
            hf.TraceApp = orig
        self.assertEqual(sorted(out), ["2026-10-01", "2026-10-03"])
        self.assertEqual(out["2026-10-01"], {"kcal": 1500, "items": 2})
        self.assertEqual(len(pauses), 2)  # paced between calls, not before the first


class Run(unittest.TestCase):
    def test_end_to_end_writes_the_feed_and_the_estimate(self):
        d = Path(tempfile.mkdtemp())
        hist = d / "history.json"
        days = [{"date": (D(2026, 9, 1) + datetime.timedelta(days=i)).isoformat(), "kcal": 2200,
                 "status": "complete"} for i in range(30)]
        weights = {days[i]["date"]: 85.0 - 0.05 * i for i in range(0, 30, 3)}
        hist.write_text(json.dumps({"days": days, "weights": weights}))
        res = hf.run(str(hist), None, None, str(d / "days.json"), str(d / "estimator.json"),
                     today=D(2026, 9, 30), fetch=lambda *a: {})
        self.assertEqual(res["days"], 30)
        feed = json.loads((d / "days.json").read_text())
        self.assertEqual(feed[0]["weight"], 85.0)
        est = json.loads((d / "estimator.json").read_text())
        self.assertIn("held", est)
        self.assertIn("as_of", est)

    def test_empty_history_stops(self):
        d = Path(tempfile.mkdtemp())
        (d / "h.json").write_text(json.dumps({"days": [], "weights": {}}))
        with self.assertRaises(SystemExit):
            hf.run(str(d / "h.json"), None, None, str(d / "a"), str(d / "b"), today=D(2026, 10, 2))


if __name__ == "__main__":
    unittest.main()
