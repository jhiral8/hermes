import json
import tempfile
import unittest
from pathlib import Path

from pantry import Pantry
from sources import SourceError


def today():
    return "2026-10-09"


class PantryTests(unittest.TestCase):
    def setUp(self):
        self.p = Pantry(None, clock=today)

    def test_low_items_suggest_shopping(self):
        it = self.p.add_item({"name": "Rice", "qty": 300, "unit": "g", "low": 500})["item"]
        self.p.add_item({"name": "Oats", "qty": 2000, "unit": "g", "low": 500})
        v = self.p.view()
        self.assertTrue(next(x for x in v["items"] if x["name"] == "Rice")["low"])
        self.assertEqual([s["name"] for s in v["suggest"]], ["Rice"])
        self.p.shop_add({"name": "Rice", "pantry_id": it["id"]})
        self.assertEqual(self.p.view()["suggest"], [])

    def test_bought_goes_back_into_pantry(self):
        it = self.p.add_item({"name": "Eggs", "qty": 6, "unit": "each", "low": 4})["item"]
        s = self.p.shop_add({"name": "Eggs", "qty": 12, "unit": "each", "pantry_id": it["id"]})["item"]
        self.p.shop_tick({"id": s["id"]})
        eggs = next(x for x in self.p.view()["items"] if x["name"] == "Eggs")
        self.assertEqual(eggs["qty"], 18)
        self.assertEqual(self.p.view()["shop"][0]["done"], True)
        self.p.shop_clear_done({})
        self.assertEqual(self.p.view()["shop"], [])

    def test_use_item_never_goes_negative(self):
        it = self.p.add_item({"name": "Milk", "qty": 1, "unit": "l"})["item"]
        self.assertEqual(self.p.use_item({"id": it["id"], "amount": 3})["item"]["qty"], 0)

    def test_use_item_needs_a_quantity(self):
        it = self.p.add_item({"name": "Salt"})["item"]
        with self.assertRaises(ValueError):
            self.p.use_item({"id": it["id"], "amount": 1})

    def test_batches_count_portions_and_days_left(self):
        b = self.p.add_batch({"name": "Chilli", "portions": 4, "place": "freezer", "use_by": "2026-10-12"})["batch"]
        self.assertEqual(self.p.view()["batches"][0]["days_left"], 3)
        self.p.use_batch({"id": b["id"], "portions": 1})
        self.assertEqual(self.p.view()["batches"][0]["left"], 3)
        with self.assertRaises(ValueError):
            self.p.use_batch({"id": b["id"], "portions": 5})

    def test_bad_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.p.add_item({"name": "   "})
        with self.assertRaises(ValueError):
            self.p.add_item({"name": "Tea", "qty": -1})
        with self.assertRaises(ValueError):
            self.p.add_item({"name": "Tea", "unit": "cups"})
        with self.assertRaises(ValueError):
            self.p.add_batch({"name": "Soup", "portions": 2, "use_by": "soon"})
        with self.assertRaises(ValueError):
            self.p.remove_item({"id": "nope"})

    def test_saved_to_file_and_reloaded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pantry.json"
            Pantry(path, clock=today).add_item({"name": "Lentils", "qty": 500, "unit": "g"})
            again = Pantry(path, clock=today)
            self.assertEqual([x["name"] for x in again.view()["items"]], ["Lentils"])
            json.loads(path.read_text())

    def test_unreadable_file_is_a_source_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "pantry.json"
            path.write_text("not json")
            with self.assertRaises(SourceError):
                Pantry(path, clock=today).view()


if __name__ == "__main__":
    unittest.main()
