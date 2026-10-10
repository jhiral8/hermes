import io
import json
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
from chat import ChatError, ChatStore, HermesMax, MaxChat

LOGIN = "craig@example.com"
USER = {"login": LOGIN, "name": "Craig"}


def sse(*events):
    out = b": hello\n\n"
    for name, data in events:
        out += f"event: {name}\ndata: {json.dumps(data)}\n\n".encode()
    return out


class Stream(io.BytesIO):
    closed_by_app = False

    def close(self):
        self.closed_by_app = True
        super().close()


class FakeOpener:
    def __init__(self, body=b"", code=200):
        self.body, self.code, self.reqs = body, code, []

    def __call__(self, req, timeout=None):
        self.reqs.append(req)
        if self.code != 200:
            raise urllib.error.HTTPError(req.full_url, self.code, "x", {}, None)
        return Stream(self.body)


TURN = sse(
    ("response.created", {"type": "response.created"}),
    ("response.output_item.added", {"item": {"type": "function_call", "call_id": "c1", "name": "web_search", "arguments": "{\"q\":\"x\"}"}}),
    ("response.output_item.done", {"item": {"type": "function_call_output", "call_id": "c1", "output": "3 results"}}),
    ("response.output_text.delta", {"delta": "Hello "}),
    ("response.output_text.delta", {"delta": "Craig."}),
    ("response.completed", {"type": "response.completed"}),
)


class Chat(unittest.TestCase):
    def make(self, body=TURN, code=200, kill=None):
        d = tempfile.mkdtemp()
        kf = Path(d, "key")
        kf.write_text("sk-test\n")
        op = FakeOpener(body, code)
        backend = HermesMax({"url": "http://127.0.0.1:8642", "key_file": str(kf)}, opener=op)
        c = MaxChat({"store_dir": d + "/chat", "kill_flag": kill}, backend=backend)
        return c, op

    def test_turn_streams_and_is_saved(self):
        c, op = self.make()
        conv = c.new()
        conv2, up = c.begin(USER, conv["id"], "  hi Max  ")
        seen = []
        reply = c.run(conv2, up, seen.append)
        self.assertEqual(reply["text"], "Hello Craig.")
        self.assertEqual(reply["tools"], [{"name": "web_search", "args": "{\"q\":\"x\"}", "result": "3 results"}])
        self.assertEqual([e["type"] for e in seen], ["tool", "tool_done", "text", "text", "end"])
        req = op.reqs[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer sk-test")
        sent = json.loads(req.data)
        self.assertTrue(sent["input"].startswith("[Sources for this message: you may use only "))
        self.assertTrue(sent["input"].endswith("\n\nhi Max"))
        self.assertEqual((sent["conversation"], sent["stream"]), ("hermes-app-" + conv["id"], True))
        saved = c.get(conv["id"])
        self.assertEqual(saved["title"], "hi Max")
        self.assertEqual([m["role"] for m in saved["messages"]], ["me", "max"])
        self.assertFalse(saved["busy"])
        self.assertTrue(up.closed_by_app)

    def test_switches_default_then_follow_the_chat(self):
        c, op = self.make()
        conv = c.new()
        self.assertEqual(c.get(conv["id"])["scope"], ["web", "task", "files", "notes", "memory"])
        conv2, up = c.begin(USER, conv["id"], "hi", ["health", "web"])
        sent = json.loads(op.reqs[-1].data)["input"]
        self.assertIn("you may use only public web, health.", sent)
        self.assertIn("Don't use this task, selected files, notes, personal notes, mail, calendar, memory", sent)
        c.run(conv2, up, lambda ev: None)
        self.assertEqual(c.get(conv["id"])["scope"], ["web", "health"])
        # Later messages with no switches sent keep the saved ones.
        c.begin(USER, conv["id"], "again")
        self.assertIn("you may use only public web, health.", json.loads(op.reqs[-1].data)["input"])

    def test_set_scope_saves_without_a_message(self):
        c, _ = self.make()
        conv = c.new()
        self.assertEqual(c.set_scope(USER, conv["id"], ["health"]), {"scope": ["health"]})
        self.assertEqual(c.get(conv["id"])["scope"], ["health"])
        self.assertEqual(c.get(conv["id"])["messages"], [])

    def test_locked_and_unknown_sources_are_refused(self):
        c, _ = self.make()
        conv = c.new()
        with self.assertRaises(ChatError) as locked:
            c.set_scope(USER, conv["id"], ["mail"])
        self.assertEqual(locked.exception.code, 409)
        self.assertIn("Mail stays locked", str(locked.exception))
        with self.assertRaises(ChatError) as unknown:
            c.begin(USER, conv["id"], "hi", ["email"])
        self.assertEqual(unknown.exception.code, 400)
        self.assertEqual(c.get(conv["id"])["messages"], [])
        self.assertEqual(c.get(conv["id"])["scope_options"][6], {"key": "mail", "label": "mail",
                                                                  "locked": "Mail stays locked until the task rules for it are done."})

    def test_browser_leaving_does_not_lose_the_answer(self):
        c, _ = self.make()
        conv = c.new()
        conv2, up = c.begin(USER, conv["id"], "hi")

        def gone(ev):
            raise BrokenPipeError
        c.run(conv2, up, gone)
        self.assertEqual(c.get(conv["id"])["messages"][-1]["text"], "Hello Craig.")

    def test_stop_ends_the_turn(self):
        c, _ = self.make()
        conv = c.new()
        conv2, up = c.begin(USER, conv["id"], "hi")
        seen = []

        def emit(ev):
            seen.append(ev)
            if ev["type"] == "tool":
                c.stop(USER, conv["id"])
        reply = c.run(conv2, up, emit)
        self.assertEqual(reply["status"], "stopped")
        self.assertEqual(seen[-1], {"type": "end", "status": "stopped"})

    def test_refusals(self):
        d = tempfile.mkdtemp()
        flag = Path(d, "paused")
        c, _ = self.make(kill=str(flag))
        conv = c.new()
        with self.assertRaises(ChatError):
            c.begin(USER, conv["id"], "   ")
        with self.assertRaises(ChatError):
            c.begin(USER, conv["id"], "x" * 9000)
        flag.write_text("")
        with self.assertRaises(ChatError) as e:
            c.begin(USER, conv["id"], "hi")
        self.assertEqual(e.exception.code, 423)
        with self.assertRaises(ChatError):
            c.get("nope")

    def test_upstream_errors_are_plain(self):
        c, _ = self.make(code=401)
        conv = c.new()
        with self.assertRaises(ChatError) as e:
            c.begin(USER, conv["id"], "hi")
        self.assertIn("key", str(e.exception))
        self.assertFalse(c.get(conv["id"])["busy"])  # not stuck busy
        c, _ = self.make(body=sse(("response.failed", {"response": {"error": {"message": "model down"}}})))
        conv = c.new()
        conv2, up = c.begin(USER, conv["id"], "hi")
        self.assertEqual(c.run(conv2, up, lambda e: None)["error"], "model down")

    def test_new_artifacts_are_linked_to_the_reply(self):
        from artifacts import Artifacts
        d = Path(tempfile.mkdtemp())

        class Writes(HermesMax):
            def events(self, resp):
                (d / "brief.md").write_text("# Brief")
                yield from HermesMax.events(resp)
        backend = Writes({"url": "http://x"}, opener=FakeOpener(TURN))
        c = MaxChat({"store_dir": tempfile.mkdtemp()}, backend=backend, artifacts=Artifacts(d))
        conv = c.new()
        conv2, up = c.begin(USER, conv["id"], "make a brief")
        seen = []
        reply = c.run(conv2, up, seen.append)
        self.assertEqual(reply["artifacts"], ["brief.md"])
        self.assertIn({"type": "artifacts", "names": ["brief.md"]}, seen)

    def test_store_lists_newest_first(self):
        s = ChatStore(tempfile.mkdtemp())
        a = s.new()
        b = s.new()
        time.sleep(0.01)  # timestamps are to the millisecond
        b["messages"].append({"role": "me", "text": "x"})
        s.save(b)
        self.assertEqual(s.list()[0]["id"], b["id"])
        s.delete(a["id"])
        self.assertEqual(len(s.list()), 1)


class ChatHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache)
        d = tempfile.mkdtemp()
        backend = HermesMax({"url": "http://x"}, opener=FakeOpener(TURN))
        chat = MaxChat({"store_dir": d}, backend=backend)
        web = Path(__file__).resolve().parent.parent / "web"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, web, cache, app, chat))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def req(self, path, body=None, action=True):
        hd = {"Tailscale-User-Login": LOGIN}
        data = None
        if body is not None:
            hd["Content-Type"] = "application/json"
            if action:
                hd["X-Hermes-Action"] = "1"
            data = json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, headers=hd)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, resp.headers.get("Content-Type"), resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Content-Type"), e.read()

    def test_full_turn_over_http(self):
        code, _, body = self.req("/api/chat", {})
        cid = json.loads(body)["id"]
        code, ctype, body = self.req(f"/api/chat/{cid}/send", {"text": "hi"})
        self.assertEqual(code, 200)
        self.assertTrue(ctype.startswith("text/event-stream"))
        events = [json.loads(l[6:]) for l in body.decode().splitlines() if l.startswith("data: ")]
        self.assertEqual(events[-1]["type"], "end")
        code, _, body = self.req("/api/chat")
        self.assertEqual(json.loads(body)["chats"][0]["id"], cid)
        code, _, body = self.req(f"/api/chat/{cid}")
        self.assertEqual(len(json.loads(body)["messages"]), 2)

    def test_send_needs_the_app_header_and_good_ids(self):
        self.assertEqual(self.req("/api/chat/abc/send", {"text": "hi"}, action=False)[0], 403)
        self.assertEqual(self.req("/api/chat/a.b/send", {"text": "hi"})[0], 400)
        self.assertEqual(self.req("/api/chat/missing1/send", {"text": "hi"})[0], 404)


if __name__ == "__main__":
    unittest.main()
