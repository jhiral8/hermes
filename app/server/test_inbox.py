import base64
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
from inbox import Gmail, SampleMail
from sources import SourceError

LOGIN = "craig@example.com"


def b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode().rstrip("=")


class FakeGoogle:
    """Answers the token endpoint and the Gmail read endpoints; records every request."""

    def __init__(self, fail=None):
        self.calls, self.fail = [], fail or {}
        self.token_calls = 0

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((req.get_method(), url, req.get_header("Authorization")))
        for key, code in self.fail.items():
            if key in url:
                raise urllib.error.HTTPError(url, code, "no", {}, io.BytesIO(b""))
        if url.startswith("https://oauth2.googleapis.com/token"):
            self.token_calls += 1
            return io.BytesIO(json.dumps({"access_token": "ya29.test", "expires_in": 3600}).encode())
        if "/messages?" in url:
            return io.BytesIO(json.dumps({"messages": [{"id": "19a2b3c4d5e6f7a8"}], "nextPageToken": "NEXT"}).encode())
        if "format=metadata" in url:
            return io.BytesIO(json.dumps({
                "id": "19a2b3c4d5e6f7a8", "snippet": "Results &amp; more", "internalDate": "1791462000000",
                "labelIds": ["INBOX", "UNREAD"],
                "payload": {"headers": [{"name": "From", "value": "Surgery <a@example.org>"},
                                        {"name": "Subject", "value": "Results"}]}}).encode())
        if "format=full" in url:
            return io.BytesIO(json.dumps({
                "id": "19a2b3c4d5e6f7a8", "internalDate": "1791462000000",
                "payload": {"mimeType": "multipart/alternative", "headers": [{"name": "Subject", "value": "Results"}],
                            "parts": [{"mimeType": "text/plain", "body": {"data": b64("Plain body\n\n\n\nBye")}},
                                      {"mimeType": "text/html", "body": {"data": b64("<p>HTML</p>")}},
                                      {"mimeType": "application/pdf", "filename": "a.pdf", "body": {"attachmentId": "x"}}]}}).encode())
        raise AssertionError("unexpected " + url)


def token_file(d=None):
    f = Path(tempfile.mkdtemp(), "gmail-real.token")
    f.write_text(json.dumps({"client_id": "cid", "client_secret": "csecret", "refresh_token": "rtok"}))
    return str(f)


class Client(unittest.TestCase):
    def test_list_uses_readonly_calls_and_refreshes_token(self):
        g = FakeGoogle()
        m = Gmail({"token_file": token_file()}, opener=g).list()
        row = m["messages"][0]
        self.assertEqual(row["subject"], "Results")
        self.assertTrue(row["unread"])
        self.assertEqual(row["snippet"], "Results & more")
        self.assertEqual(m["next"], "NEXT")
        self.assertEqual(g.token_calls, 1)
        self.assertTrue(all(method == "GET" for method, _, _ in g.calls[1:]))
        self.assertTrue(all("gmail.googleapis.com" in url for _, url, _ in g.calls[1:]))

    def test_token_is_cached_until_near_expiry(self):
        g = FakeGoogle()
        now = [1000.0]
        gm = Gmail({"token_file": token_file()}, opener=g, clock=lambda: now[0])
        gm.list()
        gm.list()
        self.assertEqual(g.token_calls, 1)
        now[0] += 3600
        gm.list()
        self.assertEqual(g.token_calls, 2)

    def test_read_takes_plain_text_and_names_attachments(self):
        r = Gmail({"token_file": token_file()}, opener=FakeGoogle()).read("19a2b3c4d5e6f7a8")
        self.assertEqual(r["text"], "Plain body\n\nBye")
        self.assertEqual(r["files"], ["a.pdf"])

    def test_html_only_mail_loses_tags(self):
        g = FakeGoogle()
        gm = Gmail({"token_file": token_file()}, opener=g)
        gm._get = lambda path, **kw: {"id": "19a2b3c4d5e6f7a8", "payload": {"mimeType": "text/html",
                                                                            "body": {"data": b64("<div>Hi<br>there &amp; you</div>")}}}
        self.assertEqual(gm.read("19a2b3c4d5e6f7a8")["text"], "Hi\nthere & you")

    def test_bad_ids_and_views_refused_before_any_call(self):
        g = FakeGoogle()
        gm = Gmail({"token_file": token_file()}, opener=g)
        for bad in ("../x", "abc/def", "", "zz"):
            with self.assertRaises(SourceError):
                gm.read(bad)
        with self.assertRaises(SourceError):
            gm.list(view="trash")
        self.assertEqual(g.calls, [])

    def test_missing_token_file_is_a_clear_error(self):
        with self.assertRaises(SourceError) as e:
            Gmail({"token_file": "/nonexistent/gmail.token"}, opener=FakeGoogle()).list()
        self.assertIn("isn't set up", str(e.exception))

    def test_errors_are_mapped_and_carry_no_tokens(self):
        g = FakeGoogle(fail={"oauth2": 401})
        with self.assertRaises(SourceError) as e:
            Gmail({"token_file": token_file()}, opener=g).list()
        self.assertIn("refused the sign-in", str(e.exception))
        self.assertNotIn("rtok", str(e.exception))
        self.assertNotIn("csecret", str(e.exception))

    def test_sample_mail_unread_filter(self):
        s = SampleMail()
        self.assertEqual({m["id"] for m in s.list(view="unread")["messages"]}, {"9f3a21c4b0d1e7aa"})
        self.assertIn("results", s.read("9f3a21c4b0d1e7aa")["text"].lower())


class Http(unittest.TestCase):
    def serve(self, inbox):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, app, None, None, None, inbox))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def get(self, base, path, login=LOGIN):
        r = urllib.request.Request(base + path, headers={"Tailscale-User-Login": login})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read()), resp.headers.get("Cache-Control")
        except urllib.error.HTTPError as e:
            try:
                body = json.loads(e.read() or b"null")
            except ValueError:
                body = None  # the server's plain-text refusal (403) isn't JSON
            return e.code, body, None

    def test_routes(self):
        base = self.serve(SampleMail())
        status, body, cache = self.get(base, "/api/inbox")
        self.assertEqual(status, 200)
        self.assertEqual(cache, "no-store")
        self.assertEqual(len(body["messages"]), 3)
        self.assertEqual(self.get(base, "/api/inbox/9f3a21c4b0d1e7aa")[1]["subject"], "Your blood test results are ready")
        self.assertEqual(self.get(base, "/api/inbox/..%2Fetc")[0], 400)
        self.assertEqual(self.get(base, "/api/inbox/9f3a21c4b0d1e7ff")[0], 503)
        self.assertEqual(self.get(base, "/api/inbox", login="x@y.z")[0], 403)

    def test_not_signed_in_says_so(self):
        base = self.serve(None)
        status, body, _ = self.get(base, "/api/inbox")
        self.assertEqual(status, 503)
        self.assertIn("isn't signed in", body["error"])


if __name__ == "__main__":
    unittest.main()
