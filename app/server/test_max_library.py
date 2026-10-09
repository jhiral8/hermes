import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import hermes_app as h
from api import App
from max_library import MaxLibrary, SampleLibrary, export, frontmatter, memory_entries, write

LOGIN = "craig@example.com"


def hermes_home():
    home = Path(tempfile.mkdtemp())
    (home / "memories").mkdir()
    (home / "memories" / "MEMORY.md").write_text("Server is hermes-oracle.\n§\nDigest Monday 8am.\n§\n")
    (home / "memories" / "USER.md").write_text("Prefers short answers.")
    for rel, meta in [("artifacts", "name: artifacts\ndescription: Save pages\nversion: 1.2"),
                      ("research/arxiv", "name: arxiv\ndescription: 'Search papers'")]:
        d = home / "skills" / rel
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(f"---\n{meta}\n---\n\n# Body\n")
    (home / "skills" / ".hub" / "x").mkdir(parents=True)
    (home / "skills" / ".hub" / "x" / "SKILL.md").write_text("---\nname: hidden\n---\n")
    secret = home / "auth.json"
    secret.write_text("SECRET")
    (home / "skills" / "linked").mkdir()
    os.symlink(secret, home / "skills" / "linked" / "SKILL.md")
    return home


class Export(unittest.TestCase):
    def test_reads_memory_entries_and_skills_only(self):
        data = export(hermes_home(), now=100)
        self.assertEqual([m["text"] for m in data["memory"]],
                         ["Server is hermes-oracle.", "Digest Monday 8am.", "Prefers short answers."])
        self.assertEqual([m["store"] for m in data["memory"]], ["Max's notes", "Max's notes", "About you"])
        self.assertEqual([(s["id"], s["name"], s["version"], s["category"]) for s in data["skills"]],
                         [("artifacts", "artifacts", "1.2", ""), ("research/arxiv", "arxiv", "", "research")])
        self.assertEqual(data["skills"][1]["description"], "Search papers")
        self.assertNotIn("SECRET", json.dumps(data))  # links aren't followed, hidden folders skipped

    def test_missing_folders_give_empty_lists(self):
        self.assertEqual(export(tempfile.mkdtemp(), now=1), {"at": 1, "memory": [], "skills": []})

    def test_helpers(self):
        self.assertEqual(memory_entries("a\n\nb\n"), ["a", "b"])
        self.assertEqual(frontmatter("no front matter")[0], {})

    def test_feed_is_group_readable_and_staleness_reported(self):
        out = Path(tempfile.mkdtemp(), "library.json")
        write(export(hermes_home(), now=1000), out)
        self.assertEqual(out.stat().st_mode & 0o777, 0o640)
        got = MaxLibrary(out, clock=lambda: 1000 + 31 * 60).get()
        self.assertTrue(got["stale"])
        self.assertEqual(got["age_min"], 31)


class Http(unittest.TestCase):
    def serve(self, library, cfg_extra=None):
        cfg = {"allowed_logins": [LOGIN], "services": [], **(cfg_extra or {})}
        cache = h.StatusCache([], 15)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, App(cfg, cache), None, None,
                                                                   None, None, None, None, library))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def get(self, base, path, login=LOGIN):
        r = urllib.request.Request(base + path, headers={"Tailscale-User-Login": login})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None

    def test_list_leaves_out_skill_source_and_one_skill_has_it(self):
        base = self.serve(SampleLibrary(), {"library": {"notes_url": "https://notes.example"}})
        status, body = self.get(base, "/api/library")
        self.assertEqual(status, 200)
        self.assertEqual(body["notes_url"], "https://notes.example")
        self.assertTrue(body["memory"])
        self.assertTrue(all("source" not in s for s in body["skills"]))
        status, skill = self.get(base, "/api/library/skill/research%2Farxiv")
        self.assertEqual(status, 200)
        self.assertIn("name: arxiv", skill["source"])
        self.assertEqual(self.get(base, "/api/library/skill/nope")[0], 404)
        self.assertEqual(self.get(base, "/api/library", login="x@y.z")[0], 403)

    def test_not_shared_yet_says_so(self):
        base = self.serve(MaxLibrary("/nonexistent/library.json"))
        status, body = self.get(base, "/api/library")
        self.assertEqual(status, 200)
        self.assertFalse(body["ok"])
        self.assertIn("aren't being shared", body["error"])


if __name__ == "__main__":
    unittest.main()
