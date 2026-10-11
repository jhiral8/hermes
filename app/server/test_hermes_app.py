import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import hermes_app as h

LOGIN = "craig@example.com"


class Upstream(BaseHTTPRequestHandler):
    code = 200

    def do_GET(self):
        self.send_response(type(self).code)
        self.end_headers()

    def log_message(self, *a):
        pass


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Checks(unittest.TestCase):
    def test_http_up_down_and_unreachable(self):
        up = serve(Upstream)
        self.addCleanup(up.shutdown)
        url = f"http://127.0.0.1:{up.server_port}/"
        self.assertEqual(h.check_http({"url": url})[0], h.OK)

        class Broken(Upstream):
            code = 503
        bad = serve(Broken)
        self.addCleanup(bad.shutdown)
        self.assertEqual(h.check_http({"url": f"http://127.0.0.1:{bad.server_port}/"})[0], h.DOWN)

        # A 401 still means the service is running.
        class Locked(Upstream):
            code = 401
        lk = serve(Locked)
        self.addCleanup(lk.shutdown)
        self.assertEqual(h.check_http({"url": f"http://127.0.0.1:{lk.server_port}/"})[0], h.OK)

        self.assertEqual(h.check_http({"url": "http://127.0.0.1:1/"}, timeout=1)[0], h.DOWN)

    def test_systemd_states(self):
        def fake(out):
            return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
        self.assertEqual(h.check_systemd({"unit": "x"}, runner=fake("active\n"))[0], h.OK)
        self.assertEqual(h.check_systemd({"unit": "x"}, runner=fake("failed\n"))[0], h.DOWN)
        self.assertEqual(h.check_systemd({"unit": "x"}, runner=fake(""))[0], h.UNKNOWN)

        def boom(*a, **k):
            raise FileNotFoundError
        self.assertEqual(h.check_systemd({"unit": "x"}, runner=boom)[0], h.UNKNOWN)

    def test_last_run(self):
        def fake(out):
            return lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=out, stderr="")
        now = 1760000000
        ok = f"Result=success\nExecMainExitTimestamp=@{now - 7200}\nExecMainStatus=0\n"
        self.assertEqual(h.check_last_run({"unit": "b"}, runner=fake(ok), now=now), (h.OK, "last run 2h ago"))
        self.assertEqual(h.check_last_run({"unit": "b"}, runner=fake(ok), now=now + 30 * 3600)[0], h.DOWN)
        bad = f"Result=exit-code\nExecMainExitTimestamp=@{now - 60}\nExecMainStatus=1\n"
        self.assertEqual(h.check_last_run({"unit": "b"}, runner=fake(bad), now=now)[0], h.DOWN)
        never = "Result=success\nExecMainExitTimestamp=\nExecMainStatus=0\n"
        self.assertEqual(h.check_last_run({"unit": "b"}, runner=fake(never), now=now)[0], h.UNKNOWN)

    def test_file_age(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(h.check_file_age({"dir": d})[0], h.DOWN)
            p = Path(d, "backup.log")
            p.write_text("x")
            now = time.time()
            self.assertEqual(h.check_file_age({"dir": d, "max_age_hours": 26}, now=now)[0], h.OK)
            self.assertEqual(h.check_file_age({"dir": d, "max_age_hours": 26}, now=now + 30 * 3600)[0], h.DOWN)
        self.assertEqual(h.check_file_age({"dir": "/nonexistent/x"})[0], h.UNKNOWN)

    def test_flag(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d, "KILLED")
            self.assertEqual(h.check_flag({"path": str(p)})[0], h.OK)
            p.write_text("")
            self.assertEqual(h.check_flag({"path": str(p)})[0], h.DOWN)

    def test_unset_service_is_unknown_not_down(self):
        self.assertEqual(h.run_check({"kind": "nope"})[0], h.UNKNOWN)
        self.assertEqual(h.run_check({"kind": "http"})[0], h.UNKNOWN)


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = {"allowed_logins": [LOGIN], "services": [
            {"id": "flag", "name": "Kill switch", "kind": "flag", "path": "/nonexistent/flag"},
            {"id": "todo", "name": "Later", "kind": "todo"},
        ]}
        cache = h.StatusCache(cfg["services"], 15)
        web = Path(__file__).resolve().parent.parent / "web"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, web, cache))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def get(self, path, login=LOGIN, method="GET"):
        headers = {"Tailscale-User-Login": login} if login else {}
        req = urllib.request.Request(self.base + path, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_refuses_without_tailscale_login(self):
        self.assertEqual(self.get("/", login=None)[0], 403)
        self.assertEqual(self.get("/api/status", login="someone@else.com")[0], 403)

    def test_login_match_ignores_case(self):
        self.assertEqual(self.get("/api/me", login=LOGIN.upper())[0], 200)

    def test_me_and_status(self):
        code, _, body = self.get("/api/me")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body)["login"], LOGIN)
        code, hdrs, body = self.get("/api/status")
        data = json.loads(body)
        self.assertEqual([s["state"] for s in data["services"]], [h.OK, h.UNKNOWN])
        self.assertEqual(hdrs["Cache-Control"], "no-store")

    def test_static_and_headers(self):
        code, hdrs, body = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn(b"<title>Hermes</title>", body)
        self.assertIn("frame-ancestors 'none'", hdrs["Content-Security-Policy"])
        code, hdrs, _ = self.get("/manifest.webmanifest")
        self.assertTrue(hdrs["Content-Type"].startswith("application/manifest+json"))
        code, hdrs, _ = self.get("/sw.js")
        self.assertEqual(hdrs["Cache-Control"], "no-cache")

    def test_no_path_escape(self):
        code, _, body = self.get("/../server/hermes_app.py")
        self.assertNotIn(b"import", body)
        code, _, body = self.get("/%2e%2e/server/hermes_app.py")
        self.assertNotIn(b"import", body)

    def test_post_needs_the_app_header(self):
        self.assertEqual(self.get("/api/status", method="POST")[0], 403)


class Config(unittest.TestCase):
    def test_requires_a_login(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"services": []}, f)
        self.addCleanup(os.unlink, f.name)
        with self.assertRaises(ValueError):
            h.load_config(f.name)

    def test_example_config_parses(self):
        cfg = h.load_config(Path(__file__).with_name("config.example.json"))
        self.assertEqual(cfg["listen_host"], "127.0.0.1")
        self.assertGreater(len(cfg["services"]), 5)


if __name__ == "__main__":
    unittest.main()
