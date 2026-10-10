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


from pantry import ingredients_of, recipe_check  # noqa: E402


class RecipeCheckTests(unittest.TestCase):
    def setUp(self):
        self.p = Pantry(None, clock=today)
        self.p.add_item({"name": "Chicken breast", "qty": 500, "unit": "g", "low": 100})
        self.p.add_item({"name": "Rice", "qty": 50, "unit": "g", "low": 200})
        self.p.add_item({"name": "Eggs", "unit": "each"})

    def test_reads_plain_and_nested_rows(self):
        rows = ingredients_of({"ingredients": ["2 onions", {"name": "Chicken breast", "amount": "300", "unit": "g"},
                                               {"ingredient": {"name": "Rice"}, "quantity": 200}]})
        self.assertEqual([r["name"] for r in rows], ["2 onions", "Chicken breast", "Rice"])
        self.assertEqual(rows[1]["amount"], "300 g")

    def test_unknown_shape_is_unreadable_not_an_error(self):
        self.assertIsNone(ingredients_of({"name": "Dal", "steps": ["boil"]}))
        self.assertIsNone(ingredients_of(None))
        out = recipe_check(self.p.view(), {"steps": []})
        self.assertFalse(out["readable"])
        self.assertIn("couldn't read", out["message"].lower())

    def test_pantry_coverage(self):
        out = self.p.check({"ingredients": [
            {"name": "chicken breasts", "amount": "300 g"},  # plural matches the singular pantry item
            {"name": "Rice", "amount": "200 g"},             # in the pantry but running low
            {"name": "Eggs"},                                # in the pantry, amount not set
            {"name": "Coconut milk"}]})                      # not in the pantry
        status = {r["name"]: r["status"] for r in out["ingredients"]}
        self.assertEqual(status, {"chicken breasts": "have", "Rice": "low", "Eggs": "have", "Coconut milk": "missing"})
        self.assertEqual(out["missing"], ["Coconut milk"])
        self.assertEqual(out["low"], ["Rice"])
        self.assertEqual(out["have"], 2)

    def test_empty_pantry_marks_everything_missing(self):
        empty = Pantry(None, clock=today)
        out = empty.check({"ingredients": [{"name": "Lentils"}]})
        self.assertEqual(out["missing"], ["Lentils"])
