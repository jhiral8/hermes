import datetime
import json
import tempfile
import unittest
from pathlib import Path

import openpyxl

import history_feed as hf
from sources import SourceError


def make_export(days, weights=None, partial=()):
    """A small MacroFactor-shaped .xlsx: days is [(date, kcal)], weights {date: kg}."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Calories & Macros"
    ws.append(["Date", "Calories (kcal)", "Fat (g)", "Carbs (g)", "Protein (g)"])
    for d, kcal in days:
        ws.append([datetime.datetime.combine(d, datetime.time()), kcal, 1, 1, 1])
    p = wb.create_sheet("Partial Logging")
    p.append(["Date", "Partial"])
    for d in partial:
        p.append([d.strftime("%d/%m/%Y"), "Yes"])
    w = wb.create_sheet("Scale Weight")
    w.append(["Date", "Weight (kg)", "Fat Percent"])
    for d, kg in (weights or {}).items():
        w.append([datetime.datetime.combine(d, datetime.time()), kg, None])
    path = Path(tempfile.mkdtemp(), "MacroFactor.xlsx")
    wb.save(path)
    return path

D = datetime.date


def history(days, weights=None):
    return ({d["date"]: {"kcal": d["kcal"], "status": d["status"]} for d in days}, weights or {})


class Export(unittest.TestCase):
    def test_partial_and_last_days_are_marked(self):
        p = make_export([(D(2026, 10, 1), 2000), (D(2026, 10, 2), 1800), (D(2026, 10, 3), 700)],
                        {D(2026, 10, 1): 80.0}, partial=[D(2026, 10, 2)])
        days, weights = hf.read_export(p)
        self.assertEqual(days["2026-10-01"], {"kcal": 2000.0, "status": "complete"})
        self.assertEqual(days["2026-10-02"]["status"], "partial")
        self.assertEqual(days["2026-10-03"]["status"], "partial")  # the export's own last day
        self.assertEqual(weights, {"2026-10-01": 80.0})


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
        base = D(2026, 9, 1)
        days = [(base + datetime.timedelta(days=i), 2200) for i in range(30)]
        weights = {base + datetime.timedelta(days=i): 85.0 - 0.05 * i for i in range(0, 30, 3)}
        export = make_export(days, weights)
        res = hf.run(str(export), None, None, str(d / "days.json"), str(d / "estimator.json"),
                     today=D(2026, 9, 30), fetch=lambda *a: {})
        self.assertEqual(res["days"], 30)
        feed = json.loads((d / "days.json").read_text())
        self.assertEqual(feed[0]["weight"], 85.0)
        est = json.loads((d / "estimator.json").read_text())
        self.assertIn("held", est)
        self.assertIn("as_of", est)

    def test_empty_history_stops(self):
        d = Path(tempfile.mkdtemp())
        empty = make_export([])
        with self.assertRaises(SystemExit):
            hf.run(str(empty), None, None, str(d / "a"), str(d / "b"), today=D(2026, 10, 2))


if __name__ == "__main__":
    unittest.main()
