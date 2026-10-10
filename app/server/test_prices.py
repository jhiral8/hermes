import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import prices
from prices import Prices, clean_prices
from sources import SourceError


def day(d):
    return lambda: d


class PriceTests(unittest.TestCase):
    def test_typed_price_is_kept_with_date(self):
        p = Prices(None, clock=day("2026-10-09"))
        row = p.add({"item": "Chicken breast", "store": "Tesco", "price": "4.50", "pack": "600 g"})["price"]
        self.assertEqual((row["price"], row["store"], row["date"], row["source"]), (4.5, "Tesco", "2026-10-09", "typed"))

    def test_bad_input_is_refused(self):
        p = Prices(None, clock=day("2026-10-09"))
        with self.assertRaises(ValueError):
            p.add({"item": "", "store": "Tesco", "price": "1"})
        with self.assertRaises(ValueError):
            p.add({"item": "Milk", "store": "Tesco", "price": "a lot"})
        with self.assertRaises(ValueError):
            p.add({"item": "Milk", "store": "Corner shop", "price": "1"})

    def test_compare_uses_latest_price_per_shop(self):
        p = Prices(None, clock=day("2026-10-01"))
        p.add({"item": "Eggs", "store": "Tesco", "price": "2.50"})
        p.add({"item": "Eggs", "store": "Aldi", "price": "2.10"})
        p._clock = day("2026-10-08")
        p.add({"item": "Eggs", "store": "Tesco", "price": "2.00"})
        out = p.compare()["items"][0]
        self.assertEqual(out["cheapest"], "Tesco")
        self.assertEqual(out["cheapest_price"], 2.0)
        self.assertEqual(out["shops"]["Aldi"]["price"], 2.1)

    def test_history_is_in_date_order(self):
        p = Prices(None, clock=day("2026-10-01"))
        p.add({"item": "Rice", "store": "Asda", "price": "1.20"})
        p._clock = day("2026-10-05")
        p.add({"item": "Rice", "store": "Asda", "price": "1.35"})
        pts = p.history("Rice")["points"]
        self.assertEqual([x["price"] for x in pts], [1.2, 1.35])

    def test_max_check_keeps_only_asked_items_and_sane_prices(self):
        rows = clean_prices({"prices": [
            {"item": "Milk", "store": "Tesco", "price": "£1.25", "pack": "2 pint"},
            {"item": "Milk", "store": "Tesco", "price": -4},
            {"item": "Bread", "store": "Waitrose", "price": 1.1},          # not asked about
            {"item": "Eggs", "store": "Corner shop", "price": 2.0}],       # unknown shop
            }, ["Milk", "Eggs"])
        self.assertEqual([(r["item"], r["store"], r["price"]) for r in rows],
                         [("Milk", "Tesco", 1.25), ("Eggs", "Other", 2.0)])

    def test_check_saves_what_max_found(self):
        p = Prices(None, clock=day("2026-10-09"))
        fake = {"prices": [{"item": "Lentils", "store": "Aldi", "price": 0.89, "pack": "500 g"}]}
        with mock.patch.object(prices, "_ask", return_value=fake):
            out = p.check({"items": ["Lentils", "Coconut milk"]}, chat=object())
        self.assertEqual(out["found"], 1)
        self.assertEqual(out["prices"][0]["source"], "max")
        self.assertEqual(p.compare()["items"][0]["cheapest_price"], 0.178)  # per 100 g

    def test_check_with_nothing_found_says_so(self):
        p = Prices(None, clock=day("2026-10-09"))
        with mock.patch.object(prices, "_ask", return_value={"prices": []}):
            with self.assertRaises(SourceError):
                p.check({"items": ["Lentils"]}, chat=object())

    def test_saved_to_file_and_reloaded(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "prices.json"
            Prices(path, clock=day("2026-10-09")).add({"item": "Oats", "store": "Lidl", "price": "1.00"})
            self.assertEqual(Prices(path).compare()["items"][0]["cheapest"], "Lidl")
            json.loads(path.read_text())

    def test_unreadable_file_is_a_source_error(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "prices.json"
            path.write_text("[]")
            with self.assertRaises(SourceError):
                Prices(path).view()


if __name__ == "__main__":
    unittest.main()


class StoreNameTests(unittest.TestCase):
    def test_loose_shop_names_map_to_the_app_list(self):
        from prices import store_name
        self.assertEqual(store_name("ASDA Groceries"), "Asda")
        self.assertEqual(store_name("Sainsbury's"), "Sainsbury's")
        self.assertEqual(store_name("Sainsburys"), "Sainsbury's")
        self.assertEqual(store_name("M&S"), "M&S")
        self.assertEqual(store_name("Marks & Spencer"), "M&S")
        self.assertEqual(store_name("Co-op Food"), "Co-op")
        self.assertEqual(store_name("Corner shop"), "Other")
        self.assertEqual(store_name("Thermos market"), "Other")
        self.assertEqual(store_name(None), "Other")


class UnitPriceTests(unittest.TestCase):
    def test_cheaper_per_kg_wins_even_with_a_bigger_price_tag(self):
        p = Prices(None, clock=day("2026-10-10"))
        p.add({"item": "Lentils", "store": "Asda", "price": "2.00", "pack": "500 g"})
        p.add({"item": "Lentils", "store": "Tesco", "price": "2.60", "pack": "1 kg"})
        out = p.compare()["items"][0]
        self.assertEqual(out["cheapest"], "Tesco")
        self.assertEqual(out["cheapest_price"], 0.26)
        self.assertEqual(out["basis"], "100 g")
        self.assertTrue(out["pack_sizes_differ"])

    def test_litres_compare_per_100_ml(self):
        p = Prices(None, clock=day("2026-10-10"))
        p.add({"item": "Milk", "store": "Aldi", "price": "1.10", "pack": "2 l"})
        p.add({"item": "Milk", "store": "Lidl", "price": "0.60", "pack": "1 l"})
        self.assertEqual(p.compare()["items"][0]["cheapest"], "Aldi")

    def test_unreadable_packs_that_differ_are_not_ranked(self):
        p = Prices(None, clock=day("2026-10-10"))
        p.add({"item": "Bread", "store": "Tesco", "price": "1.10", "pack": "1 tin"})
        p.add({"item": "Bread", "store": "Asda", "price": "0.90", "pack": "2 tins"})
        out = p.compare()["items"][0]
        self.assertIsNone(out["cheapest"])
        self.assertIn("note", out)
