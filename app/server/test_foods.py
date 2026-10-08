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
from foods import Catalogue, Foods, Lookup, check_food, sample_opener, SAMPLE_CODE
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


class Saving(unittest.TestCase):
    def cfg(self, **extra):
        return {"url": "http://nutritrace.test", "key_file": key("nu_read"), "write_key_file": key("nu_write"),
                "foods_list_path": "/foods", "foods_create_path": "/foods", **extra}

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

    def test_save_is_off_until_endpoints_are_confirmed(self):
        fake = Fake(b"{}")
        c = Catalogue(self.cfg(save_enabled=False), opener=fake)
        with self.assertRaises(SourceError) as e:
            c.save({"name": "Oat drink"})
        self.assertIn("isn't switched on", str(e.exception))
        self.assertEqual(fake.calls, [])

    def test_save_uses_the_write_key_and_names_nutrition_the_trace_way(self):
        fake = Fake(json.dumps({"id": "f1"}).encode())
        c = Catalogue(self.cfg(save_enabled=True), opener=fake)
        out = c.save({"name": "Oat drink", "per100": {"kcal": 46, "protein": 1}})
        self.assertEqual(out, {"saved": True, "id": "f1", "name": "Oat drink"})
        method, url, auth, _, data = fake.calls[0]
        self.assertEqual((method, url), ("POST", "http://nutritrace.test/api/v1/foods"))
        self.assertEqual(auth, "Bearer nu_write")
        sent = json.loads(data)
        self.assertEqual(sent["per_100g"], {"calories": 46.0, "proteins": 1.0})
        self.assertNotIn("carbohydrates", sent["per_100g"])  # unknown is left out, not sent as 0

    def test_list_uses_the_read_key(self):
        fake = Fake(json.dumps({"items": [{"id": "f1", "name": "Oat drink", "per_serving": {"calories": 115}}]}).encode())
        out = Catalogue(self.cfg(), opener=fake).saved()
        self.assertEqual(fake.calls[0][2], "Bearer nu_read")
        self.assertEqual(out[0]["per_serving"]["kcal"], 115)
        self.assertIsNone(out[0]["per_serving"]["fat"])

    def test_no_list_path_says_not_connected(self):
        with self.assertRaises(SourceError) as e:
            Catalogue({"url": "http://x", "key_file": key("k")}, opener=Fake()).saved()
        self.assertIn("aren't connected", str(e.exception))

    def test_save_refuses_bad_keys_clearly(self):
        c = Catalogue(self.cfg(save_enabled=True), opener=Fake(fail=403))
        with self.assertRaises(SourceError) as e:
            c.save({"name": "Oat"})
        self.assertEqual(str(e.exception), "NutriTrace key lacks the scope")
        self.assertNotIn("nu_write", str(e.exception))


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

    def test_lookup_off_says_so_and_is_503(self):
        base = self.serve(Foods(Lookup({}, opener=sample_opener)))
        status, body = self.call(base, f"/api/health/barcode/{SAMPLE_CODE}")
        self.assertEqual(status, 503)
        self.assertIn("off", body["error"])

    def test_saved_list_and_save_not_connected(self):
        base = self.serve(Foods(Lookup({"off_enabled": True}, opener=sample_opener)))
        self.assertEqual(self.call(base, "/api/health/foods")[0], 503)
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"})[0], 503)

    def test_save_needs_the_app_header_and_checks_input(self):
        fake = Fake(json.dumps({"id": "f2"}).encode())
        cat = Catalogue({"url": "http://nt.test", "key_file": key("r"), "write_key_file": key("w"),
                         "foods_create_path": "/foods", "save_enabled": True}, opener=fake)
        base = self.serve(Foods(Lookup({}), cat))
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"}, action=False)[0], 403)
        self.assertEqual(self.call(base, "/api/health/foods", {"name": ""})[0], 400)
        status, body = self.call(base, "/api/health/foods", {"name": "Oat", "per100": {"kcal": 46}})
        self.assertEqual(status, 200)
        self.assertTrue(body["result"]["saved"])
        self.assertEqual(self.call(base, "/api/health/foods", {"name": "Oat"}, login="x@y.z")[0], 403)


if __name__ == "__main__":
    unittest.main()
