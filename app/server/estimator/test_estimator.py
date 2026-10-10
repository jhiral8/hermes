"""Tests on simulated data where the true expenditure is known.
Run: python3 -m unittest test_estimator.py (from this folder)."""
import random
import unittest
from datetime import date, timedelta

from estimator import (KCAL_PER_KG, Settings, estimate_on, run_daily,
                       trend_weights, weekly_check_in)

START = date(2026, 6, 1)


def simulate(days=84, tdee=2600, intake=2200, w0=85.0, noise=0.6, seed=1,
             tdee_change_day=None, new_tdee=None, log_bias=1.0):
    """Daily intake around `intake`, tissue weight follows energy balance,
    scale adds water noise of +/- `noise` kg. log_bias < 1 means under-logging."""
    rnd = random.Random(seed)
    tissue, out = w0, []
    for i in range(days):
        t = new_tdee if tdee_change_day is not None and i >= tdee_change_day else tdee
        eaten = intake + rnd.gauss(0, 250)
        tissue += (eaten - t) / KCAL_PER_KG
        out.append({"date": (START + timedelta(days=i)).isoformat(),
                    "intake": round(eaten * log_bias), "status": "complete",
                    "weight": round(tissue + rnd.gauss(0, noise), 1)})
    return out


def last(est_list):
    return est_list[-1]


class Accuracy(unittest.TestCase):
    def test_full_logging_finds_true_expenditure(self):
        e = last(run_daily(simulate(), seed=None))
        self.assertFalse(e.held)
        self.assertAlmostEqual(e.expenditure, 2600, delta=120)

    def test_average_error_over_many_runs(self):
        errs = [last(run_daily(simulate(seed=s))).expenditure - 2600 for s in range(20)]
        self.assertLess(abs(sum(errs) / len(errs)), 60)      # little bias
        self.assertLess(max(abs(e) for e in errs), 200)       # no wild runs


class Gaps(unittest.TestCase):
    def test_gaps_do_not_compress_time(self):
        """The NutriTrace bug: removing logged days must not change the answer
        much. Here 40% of days lose both intake and weigh-in."""
        full = simulate(seed=3)
        rnd = random.Random(7)
        gappy = []
        for r in full:
            if rnd.random() < 0.4:
                gappy.append({"date": r["date"], "intake": None, "status": "missing", "weight": None})
            else:
                gappy.append(r)
        a = last(run_daily(full)).expenditure
        b = last(run_daily(gappy)).expenditure
        self.assertAlmostEqual(a, b, delta=150)
        self.assertAlmostEqual(b, 2600, delta=200)

    def test_partial_days_are_not_low_intake(self):
        data = simulate(seed=4)
        for i, r in enumerate(data):
            if i % 4 == 0:   # every 4th day half logged
                r["intake"] = r["intake"] // 2
                r["status"] = "partial"
        e = last(run_daily(data))
        self.assertAlmostEqual(e.expenditure, 2600, delta=150)

    def test_fasting_day_counts_as_zero(self):
        data = simulate(seed=5, days=42)
        d = data[-3]
        d["intake"], d["status"] = 0, "fasting"
        e = estimate_on(data, data[-1]["date"])
        self.assertEqual(e.intake_days, 28)

    def test_holds_with_too_little_data(self):
        data = simulate(seed=6)
        for r in data[-28:]:
            if int(r["date"][-2:]) % 3:
                r.update(intake=None, status="missing", weight=None)
        e = estimate_on(data, data[-1]["date"], previous=2500)
        self.assertTrue(e.held)
        self.assertEqual(e.expenditure, 2500)


class Response(unittest.TestCase):
    def test_follows_a_real_change_within_three_weeks(self):
        data = simulate(days=112, tdee=2600, tdee_change_day=56, new_tdee=2900, seed=8)
        est = run_daily(data)
        after_3_weeks = est[56 + 21].expenditure
        self.assertGreater(after_3_weeks, 2750)
        self.assertAlmostEqual(est[-1].expenditure, 2900, delta=150)

    def test_seed_from_macrofactor_used_until_data_arrives(self):
        data = simulate(days=10)
        est = run_daily(data, seed=2550)
        self.assertTrue(all(e.held for e in est))
        self.assertEqual(est[-1].expenditure, 2550)


class CheckIns(unittest.TestCase):
    def test_target_change_is_capped(self):
        e = last(run_daily(simulate()))
        c = weekly_check_in(e, current_target=1800, goal_kg_per_week=-0.4)
        self.assertEqual(c.proposed_target, 1950)
        self.assertIn("capped", c.note)

    def test_no_faster_than_one_percent_a_week(self):
        e = last(run_daily(simulate()))
        c = weekly_check_in(e, current_target=2000, goal_kg_per_week=-2.0)
        self.assertGreaterEqual(c.goal_kg_per_week, -e.trend_kg * 0.01 - 1e-9)

    def test_hold_keeps_target(self):
        e = estimate_on(simulate(days=10), simulate(days=10)[-1]["date"], previous=2500)
        c = weekly_check_in(e, current_target=2100, goal_kg_per_week=-0.5)
        self.assertEqual(c.proposed_target, 2100)


class Trend(unittest.TestCase):
    def test_gap_moves_trend_like_elapsed_days(self):
        days = [{"date": "2026-06-01", "weight": 80.0}, {"date": "2026-06-11", "weight": 79.0}]
        t = trend_weights(days)
        expected = 80 + (1 - 0.9 ** 10) * (79 - 80)
        self.assertAlmostEqual(t[date(2026, 6, 11)], expected, places=6)


if __name__ == "__main__":
    unittest.main()
