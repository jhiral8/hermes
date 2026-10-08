import datetime
import json
import tempfile
import unittest
from pathlib import Path

import estimate_feed as ef

MODULE = Path("/mnt/project-files/health/estimator")


def days(n=30, start=datetime.date(2026, 9, 1)):
    out = []
    for i in range(n):
        d = start + datetime.timedelta(days=i)
        out.append({"date": d.isoformat(), "intake": 2200, "status": "complete", "weight": round(80.0 - 0.02 * i, 2)})
    return out


@unittest.skipUnless((MODULE / "estimator.py").exists(), "estimator not in this checkout")
class Run(unittest.TestCase):
    def test_estimate_has_only_summary_fields(self):
        out = ef.run(days(), MODULE)
        self.assertEqual(set(out) - {"as_of", "held", "reason", "intake_days", "weigh_ins", "expenditure",
                                     "trend_kg", "weekly_change_kg"}, set())
        self.assertIsNotNone(out["expenditure"])
        self.assertLess(out["weekly_change_kg"], 0)

    def test_too_little_data_is_held_not_guessed(self):
        out = ef.run(days(5), MODULE)
        self.assertTrue(out["held"])

    def test_empty_feed(self):
        self.assertFalse(ef.run([], MODULE)["ok"])

    def test_write_atomic_is_readable_by_group(self):
        p = Path(tempfile.mkdtemp(), "estimator.json")
        ef.write_atomic(p, {"expenditure": 2400})
        self.assertEqual(json.loads(p.read_text()), {"expenditure": 2400})
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o640")


if __name__ == "__main__":
    unittest.main()
