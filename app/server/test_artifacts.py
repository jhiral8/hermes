import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import hermes_app as h
from api import App
from artifacts import ArtifactError, Artifacts

LOGIN = "craig@example.com"


class Folder(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        (self.d / "one-pager.html").write_text("<title>Desktop &amp; web</title><p>x")
        (self.d / "notes.md").write_text("# Weekly notes\n\nhello")
        (self.d / "run.sh").write_text("rm -rf /")
        (self.d / ".hidden.md").write_text("# no")
        os.symlink("/etc/passwd", self.d / "passwd.txt")
        self.a = Artifacts(self.d)

    def test_lists_only_plain_artifact_files(self):
        names = {a["name"]: a for a in self.a.list()}
        self.assertEqual(set(names), {"one-pager.html", "notes.md"})
        self.assertEqual(names["one-pager.html"]["title"], "Desktop & web")
        self.assertEqual(names["notes.md"]["title"], "Weekly notes")

    def test_get_refuses_anything_else(self):
        self.assertEqual(self.a.get("notes.md")["text"].splitlines()[0], "# Weekly notes")
        for bad in ("run.sh", "passwd.txt", "../notes.md", ".hidden.md", "missing.md", ""):
            with self.assertRaises(ArtifactError, msg=bad):
                self.a.get(bad)

    def test_since(self):
        t = time.time()
        os.utime(self.d / "notes.md", (t - 100, t - 100))
        os.utime(self.d / "one-pager.html", (t + 1, t + 1))
        self.assertEqual(self.a.since(t), ["one-pager.html"])
        self.assertEqual(Artifacts("/nonexistent").since(t), [])


class Http(unittest.TestCase):
    def test_routes(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.md").write_text("# A")
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache)
        web = Path(__file__).resolve().parent.parent / "web"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, web, cache, app, None, Artifacts(d)))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_port}"

        def get(path, login=LOGIN):
            r = urllib.request.Request(base + path, headers={"Tailscale-User-Login": login})
            try:
                with urllib.request.urlopen(r) as resp:
                    return resp.status, json.loads(resp.read())
            except urllib.error.HTTPError as e:
                return e.code, None
        self.assertEqual(get("/api/artifacts")[1]["artifacts"][0]["name"], "a.md")
        self.assertEqual(get("/api/artifacts/a.md")[1]["text"], "# A")
        self.assertEqual(get("/api/artifacts/%2e%2e%2fx.md")[0], 400)
        self.assertEqual(get("/api/artifacts", login="x@y.z")[0], 403)


if __name__ == "__main__":
    unittest.main()
