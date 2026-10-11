import http.server
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import salt_guard as g


def rpc(name, args=None, mid=1):
    return json.dumps({"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                       "params": {"name": name, "arguments": args or {}}}).encode()


class Upstream(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _reply(self):
        n = int(self.headers.get("Content-Length") or 0)
        Upstream.seen.append((self.command, self.path, self.rfile.read(n) if n else b""))
        body = b'{"result":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_GET = do_PUT = do_PATCH = _reply


class Guard(unittest.TestCase):
    def start(self, mode):
        up = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
        threading.Thread(target=up.serve_forever, daemon=True).start()
        self.addCleanup(up.shutdown)
        audit = Path(tempfile.mkdtemp(), "audit.jsonl")
        gs = g.Server(("127.0.0.1", 0), g.make_handler(f"http://127.0.0.1:{up.server_port}", mode, str(audit)))
        threading.Thread(target=gs.serve_forever, daemon=True).start()
        self.addCleanup(gs.shutdown)
        self.addCleanup(gs.server_close)
        Upstream.seen = []
        self.audit = audit
        return f"http://127.0.0.1:{gs.server_port}"

    def post(self, base, body, method="POST", path="/mcp"):
        req = urllib.request.Request(base + path, data=body, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())

    def test_trash_counts_refuses_deletes_and_trash(self):
        base = self.start("trash-counts")
        for name in ("delete_view", "delete_comment", "set_trashed"):
            out = self.post(base, rpc(name, {"trashed": True}))
            self.assertIn("error", out, name)
            self.assertIn(name, out["error"]["message"])
        self.assertEqual(Upstream.seen, [])  # nothing reached salt

    def test_restore_from_trash_is_allowed(self):
        base = self.start("trash-counts")
        self.assertEqual(self.post(base, rpc("set_trashed", {"trashed": False})), {"result": "ok"})

    def test_trash_allowed_mode_passes_trash_but_not_deletes(self):
        base = self.start("trash-allowed")
        self.assertEqual(self.post(base, rpc("set_trashed", {"trashed": True})), {"result": "ok"})
        self.assertIn("error", self.post(base, rpc("delete_comment")))

    def test_ordinary_tools_pass_through_unchanged(self):
        base = self.start("trash-counts")
        body = rpc("create_page", {"title": "Team handoff"})
        self.assertEqual(self.post(base, body), {"result": "ok"})
        self.assertEqual(Upstream.seen[0][2], body)

    def test_batch_with_one_delete_is_refused_whole(self):
        base = self.start("trash-counts")
        batch = json.dumps([json.loads(rpc("search")), json.loads(rpc("delete_view", mid=2))]).encode()
        out = self.post(base, batch)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], 2)
        self.assertEqual(Upstream.seen, [])

    def test_http_delete_refused(self):
        base = self.start("trash-allowed")
        req = urllib.request.Request(base + "/api/pages/x", method="DELETE")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        self.assertEqual(e.exception.code, 403)
        self.assertEqual(Upstream.seen, [])

    def test_refusals_are_audited_without_tokens(self):
        base = self.start("trash-counts")
        self.post(base, rpc("delete_comment"))
        lines = [json.loads(x) for x in self.audit.read_text().splitlines()]
        self.assertEqual(lines[-1]["outcome"], "refused")
        self.assertEqual(lines[-1]["detail"], "delete_comment")
        self.assertNotIn("Authorization", self.audit.read_text())


if __name__ == "__main__":
    unittest.main()
