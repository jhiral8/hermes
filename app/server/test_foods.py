import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import hermes_app as h
from api import App
from foods import Foods, Lookup, SavedFoods, check_food, sample_opener, SAMPLE_CODE
from sources import SourceError

LOGIN = "craig@example.com"
CODE = "5012345678900"


def product(**extra):
    body = {"status": 1, "product": {"product_name": "Oat drink", "brands": "Oatly, Other",
                                     "serving_size": "250 ml",
                                     "nutriments": {"energy-kcal_100g": 46, "proteins_100g": 1,
                                                    "carbohydrates_100g": 6.6, "fat_100g": 1.5,
                                                    "fiber_100g": 0.8, "energy-kcal_serving": 115}}}
    body.update(extra)
    return json.dumps(body).encode()


class Fake:
    """Answers Open Food Facts or NutriTrace; records each request with its key."""

    def __init__(self, body=None, fail=None):
        self.body, self.fail, self.calls = body, fail, []

    def __call__(self, req, timeout=None):
        self.calls.append((req.get_method(), req.full_url, req.get_header("Authorization"),
                           req.get_header("User-agent"), req.data))
        if self.fail:
            raise urllib.error.HTTPError(req.full_url, self.fail, "no", {}, io.BytesIO(b""))
        return io.BytesIO(self.body if self.body is not None else b"{}")


def clock(t=1000.0):
    now = [t]
    return now, (lambda: now[0])


def key(text):
    f = Path(tempfile.mkdtemp(), "k")
    f.write_text(text + "\n")
    return str(f)


class Barcodes(unittest.TestCase):
    def test_bad_codes_refused_before_any_call(self):
        fake = Fake(product())
        lk = Lookup({"off_enabled": True}, opener=fake)
        for bad in ("", "abc", "1234567", "123456789012345", "../x", "5012345678900?x=1"):
            with self.assertRaises(ValueError):
                lk.product(bad)
        self.assertEqual(fake.calls, [])

    def test_lookup_is_off_until_switched_on(self):
        fake = Fake(product())
        with self.assertRaises(SourceError) as e:
            Lookup({}, opener=fake).product(CODE)
        self.assertIn("off", str(e.exception))
        self.assertEqual(fake.calls, [])

    def test_product_parsed_with_unknowns_kept_as_none(self):
        fake = Fake(product())
        p = Lookup({"off_enabled": True}, opener=fake)
        out = p.product(CODE)
        self.assertTrue(out["found"])
        self.assertEqual(out["name"], "Oat drink")
        self.assertEqual(out["brand"], "Oatly")
        self.assertEqual(out["per100"], {"kcal": 46, "protein": 1, "carbs": 6.6, "fat": 1.5, "fibre": 0.8})
        self.assertEqual(out["per_serving"], {"kcal": 115, "protein": None, "carbs": None, "fat": None, "fibre": None})
        self.assertEqual(out["source"], "Open Food Facts")
        method, url, auth, ua, _ = fake.calls[0]
        self.assertEqual(method, "GET")
        self.assertTrue(url.startswith("https://world.openfoodfacts.org/api/v2/product/" + CODE))
        self.assertEqual(ua, "HermesApp/1.0 (personal use)")
        self.assertIsNone(auth)  # no key goes to Open Food Facts

    def test_kj_only_product_gets_kcal_from_kj(self):
        body = json.dumps({"status": 1, "product": {"nutriments": {"energy_100g": 418}}}).encode()
        out = Lookup({"off_enabled": True}, opener=Fake(body)).product(CODE)
        self.assertEqual(out["per100"]["kcal"], 99.9)
        self.assertIsNone(out["per100"]["protein"])

    def test_not_found_is_not_cached(self):
        fake = Fake(json.dumps({"status": 0}).encode())
        now, clk = clock()
        p = Lookup({"off_enabled": True}, opener=fake, clock=clk)
        self.assertFalse(p.product(CODE)["found"])
        now[0] += 5
        self.assertFalse(p.product(CODE)["found"])
        self.assertEqual(len(fake.calls), 2)

    def test_cache_for_a_day_and_spacing_between_calls(self):
        fake = Fake(product())
        now, clk = clock()
        p = Lookup({"off_enabled": True}, opener=fake, clock=clk)
        p.product(CODE)
        p.product(CODE)  # cached, no call
        self.assertEqual(len(fake.calls), 1)
        with self.assertRaises(SourceError):
            p.product("5012345678901")  # a second code within a second
        now[0] += 2
        p.product("5012345678901")
        now[0] += 86401
        p.product(CODE)
        self.assertEqual(len(fake.calls), 3)  # CODE, the second code, then CODE again after a day

    def test_errors_are_mapped_without_leaking(self):
        with self.assertRaises(SourceError) as e:
            Lookup({"off_enabled": True}, opener=Fake(fail=503)).product(CODE)
        self.assertEqual(str(e.exception), "Open Food Facts answered 503")

    def test_sample_opener_answers_only_the_sample_code(self):
        now, clk = clock()
        p = Lookup({"off_enabled": True}, opener=sample_opener, clock=clk)
        self.assertTrue(p.product(SAMPLE_CODE)["found"])
        now[0] += 2
        self.assertFalse(p.product("5099999999991")["found"])


class Searching(unittest.TestCase):
    def test_search_keeps_only_loggable_products_and_is_cached(self):
        now, clk = clock()
        calls = []
        def opener(req, timeout=None):
            calls.append(req.full_url)
            return sample_opener(req, timeout)
        lk = Lookup({"off_enabled": True}, opener=opener, clock=clk)
        out = lk.search("  Sample   oat ")
        self.assertEqual(out["query"], "Sample oat")
        names = [x["name"] for x in out["items"]]
        self.assertIn("Sample porridge oats", names)
        self.assertNotIn("Sample oat bar", names)  # no calories
        self.assertTrue(all(x["source"] == "Open Food Facts" for x in out["items"]))
        self.assertIn("search_terms=Sample+oat", calls[0])
        self.assertIn("world.openfoodfacts.org/cgi/search.pl", calls[0])
        now[0] += 5
        lk.search("sample OAT")
        self.assertEqual(len(calls), 1)

    def test_search_refuses_short_long_and_off(self):
        lk = Lookup({"off_enabled": True}, opener=sample_opener)
        with self.assertRaises(ValueError):
            lk.search("a")
        with self.assertRaises(ValueError):
            lk.search("x" * 81)
        with self.assertRaises(SourceError):
            Lookup({}, opener=sample_opener).search("oats")

    def test_search_spacing_and_errors(self):
        now, clk = clock()
        lk = Lookup({"off_enabled": True}, opener=sample_opener, clock=clk)
        lk.search("yoghurt")
        with self.assertRaises(SourceError):
            lk.search("chicken")
        now[0] += 2
        self.assertEqual(lk.search("chicken")["items"][0]["name"], "Sample chicken breast fillets")
        bad = Lookup({"off_enabled": True}, opener=Fake(fail=500))
        with self.assertRaises(SourceError):
            bad.search("oats")


class Usda(unittest.TestCase):
    def test_usda_search_reads_key_file_and_parses_energy_variants(self):
        now, clk = clock()
        calls = []
        def opener(req, timeout=None):
            calls.append(req.full_url)
            return sample_opener(req, timeout)
        lk = Lookup({"usda_key_file": key("k123")}, opener=opener, clock=clk)
        out = lk.usda_search("chicken rice yogurt")
        by = {x["name"]: x for x in out["items"]}
        self.assertEqual(by["Chicken, breast, meat only, cooked, roasted"]["per100"]["kcal"], 165)  # id 2047
        self.assertEqual(by["Rice, white, cooked"]["per100"]["kcal"], 130.0)  # from kJ; capitals tidied
        self.assertIsNone(by["Rice, white, cooked"]["per100"]["fat"])
        self.assertEqual(by["Yogurt, Greek, plain, nonfat"]["source"], "USDA FoodData Central")
        self.assertIn("api_key=k123", calls[0])
        self.assertIn("dataType=Foundation", calls[0])

    def test_usda_needs_a_key_and_hides_it_in_errors(self):
        with self.assertRaises(SourceError):
            Lookup({}, opener=sample_opener).usda_search("rice")
        with self.assertRaises(SourceError) as e:
            Lookup({"usda_key_file": key("secretkey"), "retry_wait": 0}, opener=Fake(fail=500)).usda_search("rice")
        self.assertNotIn("secretkey", str(e.exception))
        with self.assertRaises(SourceError):
            Lookup({"usda_key_file": "/nonexistent/usda.key"}, opener=sample_opener).usda_search("rice")

    def test_usda_retries_once_and_uses_us_spelling(self):
        calls = []
        def flaky(req, timeout=None):
            calls.append(req.full_url)
            if len(calls) == 1:
                raise urllib.error.HTTPError(req.full_url, 500, "no", {}, io.BytesIO(b""))
            return sample_opener(req, timeout)
        lk = Lookup({"retry_wait": 0}, opener=flaky, usda_key="k")
        out = lk.usda_search("Greek yoghurt")
        self.assertEqual(len(calls), 2)
        self.assertIn("query=greek+yogurt", calls[0])
        self.assertEqual(out["query"], "Greek yoghurt")
        self.assertEqual(out["items"][0]["name"], "Yogurt, Greek, plain, nonfat")
        both_fail = Fake(fail=502)
        with self.assertRaises(SourceError):
            Lookup({"retry_wait": 0}, opener=both_fail, usda_key="k").usda_search("rice")
        self.assertEqual(len(both_fail.calls), 2)
        bad_key = Fake(fail=403)
        with self.assertRaises(SourceError):
            Lookup({"retry_wait": 0}, opener=bad_key, usda_key="k").usda_search("rice")
        self.assertEqual(len(bad_key.calls), 1)

    def test_unknown_library_refused(self):
        with self.assertRaises(ValueError):
            Foods(Lookup({})).search("rice", "other")


class Saving(unittest.TestCase):
    def store(self):
        return SavedFoods(Path(tempfile.mkdtemp(), "saved-foods.json"))

    def test_check_food_keeps_unknowns_and_refuses_nonsense(self):
        f = check_food({"name": " Oat drink ", "per100": {"kcal": "46", "protein": ""}, "per_serving": {}})
        self.assertEqual(f["name"], "Oat drink")
        self.assertEqual(f["per100"]["kcal"], 46.0)
        self.assertIsNone(f["per100"]["protein"])
        self.assertEqual(f["source"], "Entered by hand")
        with self.assertRaises(ValueError):
            check_food({"name": "  "})
        with self.assertRaises(ValueError):
            check_food({"name": "x", "per100": {"kcal": -5}})
        with self.assertRaises(ValueError):
            check_food({"name": "x", "per100": {"protein": 5000}})
        with self.assertRaises(ValueError):
            check_food({"name": "x", "per100": {"kcal": "lots"}})

    def test_save_then_list_newest_first_and_survives_a_restart(self):
        path = Path(tempfile.mkdtemp(), "saved-foods.json")
        s = SavedFoods(path)
        s.add({"name": "Oat drink", "per100": {"kcal": 46}})
        s.add({"name": "Rice", "per_serving": {"kcal": 200}})
        self.assertEqual([x["name"] for x in SavedFoods(path).list()], ["Rice", "Oat drink"])
        self.assertIsNone(SavedFoods(path).list()[1]["per100"]["protein"])  # unknown stays unknown
        self.assertEqual(oct(path.stat().st_mode & 0o777), "0o640")

    def test_bad_input_is_refused_and_nothing_is_written(self):
        s = self.store()
        with self.assertRaises(ValueError):
            s.add({"name": ""})
        self.assertEqual(s.list(), [])
        self.assertFalse(s.path.exists())

    def test_unreadable_file_says_so(self):
        path = Path(tempfile.mkdtemp(), "saved-foods.json")
        path.write_text("not json")
        with self.assertRaises(SourceError) as e:
            SavedFoods(path).list()
        self.assertIn("can't be read", str(e.exception))

    def test_unwritable_location_refuses_cleanly(self):
        s = SavedFoods(Path("/nonexistent-dir/saved-foods.json"))
        with self.assertRaises(SourceError) as e:
            s.add({"name": "Oat"})
        self.assertIn("Couldn't save", str(e.exception))
        self.assertEqual(s.list(), [])  # the failed add is rolled back

    def test_in_memory_store_with_no_path(self):
        s = SavedFoods()
        s.add({"name": "Oat"})
        self.assertEqual(len(s.list()), 1)


class Http(unittest.TestCase):
    def serve(self, foods):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, app, None, None, None, None, foods))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def call(self, base, path, body=None, login=LOGIN, action=True):
        headers = {"Tailscale-User-Login": login}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
            if action:
                headers["X-Hermes-Action"] = "1"
        r = urllib.request.Request(base + path, data=data, headers=headers, method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None

    def test_barcode_route_with_lookup_switched_on(self):
        ticks = iter(range(1000, 2000, 5))  # each lookup happens 5 seconds after the last
        base = self.serve(Foods(Lookup({"off_enabled": True}, opener=sample_opener, clock=lambda: next(ticks))))
        status, body = self.call(base, f"/api/health/barcode/{SAMPLE_CODE}")
        self.assertEqual(status, 200)
        self.assertEqual(body["product"]["name"], "Sample oat drink")
        self.assertEqual(self.call(base, "/api/health/barcode/abc")[0], 400)
        self.assertEqual(self.call(base, "/api/health/barcode/5099999999991")[1]["product"]["found"], False)

    def test_search_route(self):
        ticks = iter(range(1000, 2000, 5))
        base = self.serve(Foods(Lookup({"off_enabled": True}, opener=sample_opener, clock=lambda: next(ticks))))
        status, body = self.call(base, "/api/health/food-search?q=greek%20yoghurt")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["name"], "Sample Greek yoghurt 0%")
        self.assertEqual(self.call(base, "/api/health/food-search?q=a")[0], 400)
        base2 = self.serve(Foods(Lookup({"off_enabled": True}, opener=sample_opener, usda_key="x")))
        status, body = self.call(base2, "/api/health/food-search?q=chicken&source=usda")
        self.assertEqual(status, 200)
        self.assertEqual(body["items"][0]["fdc_id"], 2)
        self.assertEqual(self.call(base2, "/api/health/food-search?q=chicken&source=zz")[0], 400)

    def test_lookup_off_says_so_and_is_503(self):
        base = self.serve(Foods(Lookup({}, opener=sample_opener)))
        status, body = self.call(base, f"/api/health/barcode/{SAMPLE_CODE}")
        self.assertEqual(status, 503)
        self.assertIn("off", body["error"])

    def test_saved_list_and_save_not_connected_when_foods_is_off(self):
        base = self.serve(None)
        self.assertEqual(self.call(base, "/api/health/foods")[0], 503)
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"})[0], 503)

    def test_save_needs_the_app_header_and_checks_input(self):
        base = self.serve(Foods(Lookup({}), SavedFoods()))
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"}, action=False)[0], 403)
        self.assertEqual(self.call(base, "/api/health/foods", {"name": ""})[0], 400)
        status, body = self.call(base, "/api/health/foods", {"name": "Oat", "per100": {"kcal": 46}})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["saved"])
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"}, login="x@y.z")[0], 403)


if __name__ == "__main__":
    unittest.main()
