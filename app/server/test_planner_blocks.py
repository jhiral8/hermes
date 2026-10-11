import json
import tempfile
import unittest
from pathlib import Path

from planner_blocks import PlannerBlocks
from sources import SourceError


class BlockTests(unittest.TestCase):
    def test_add_list_and_remove(self):
        b = PlannerBlocks()
        row = b.add({"title": "Gym", "date": "2026-10-12", "start": "07:00", "end": "08:00"})["block"]
        b.add({"title": "Shop", "date": "2026-10-20", "start": "10:00", "end": "11:00"})
        self.assertEqual([r["title"] for r in b.week("2026-10-12", 7)], ["Gym"])
        b.remove({"id": row["id"]})
        self.assertEqual(b.week("2026-10-12", 7), [])

    def test_refuses_bad_times_and_dates(self):
        b = PlannerBlocks()
        with self.assertRaises(ValueError):
            b.add({"title": "x", "date": "2026-10-12", "start": "9am", "end": "10:00"})
        with self.assertRaises(ValueError):
            b.add({"title": "x", "date": "2026-10-12", "start": "10:00", "end": "09:00"})
        with self.assertRaises(ValueError):
            b.add({"title": "x", "date": "12/10", "start": "09:00", "end": "10:00"})
        with self.assertRaises(ValueError):
            b.add({"title": "  ", "date": "2026-10-12", "start": "09:00", "end": "10:00"})
        with self.assertRaises(ValueError):
            b.remove({"id": "nope"})

    def test_saved_to_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "planner-blocks.json"
            PlannerBlocks(path).add({"title": "Focus", "date": "2026-10-12", "start": "13:00", "end": "14:00"})
            again = PlannerBlocks(path)
            self.assertEqual(again.week("2026-10-12", 1)[0]["title"], "Focus")
            json.loads(path.read_text())

    def test_unreadable_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "planner-blocks.json"
            path.write_text("[]")
            with self.assertRaises(SourceError):
                PlannerBlocks(path).week("2026-10-12", 1)


if __name__ == "__main__":
    unittest.main()
