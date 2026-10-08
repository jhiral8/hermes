import datetime
import io
import json
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
import urllib.error
import urllib.parse
from pathlib import Path

import hermes_app as h
from api import App
from planner import Calendar, SampleCalendar, group_by_day
from sources import SourceError

D = datetime.date


class FakeGoogle:
    """Answers the token endpoint and the Calendar events list; records every request."""

    def __init__(self, items=None, fail=None):
        self.calls, self.items, self.fail = [], items or [], fail

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.calls.append((req.get_method(), url))
        if url.startswith("https://oauth2.googleapis.com/token"):
            return io.BytesIO(json.dumps({"access_token": "ya29.test", "expires_in": 3600}).encode())
        if self.fail:
            raise urllib.error.HTTPError(url, self.fail, "no", {}, io.BytesIO(b""))
        if "/events?" in url:
            return io.BytesIO(json.dumps({"items": self.items}).encode())
        raise AssertionError("unexpected " + url)


def token_file():
    f = Path(tempfile.mkdtemp(), "calendar.token")
    f.write_text(json.dumps({"client_id": "cid", "client_secret": "cs", "refresh_token": "rt"}))
    return str(f)


def cal(google, **cfg):
    return Calendar({"token_file": token_file(), **cfg}, opener=google, today=lambda: D(2026, 10, 8))


ITEMS = [
    {"id": "a", "summary": "Dentist", "start": {"dateTime": "2026-10-09T14:30:00+01:00"},
     "end": {"dateTime": "2026-10-09T15:00:00+01:00"}, "location": "High St"},
    {"id": "b", "summary": "Away", "start": {"date": "2026-10-10"}, "end": {"date": "2026-10-12"}},
    {"id": "c", "summary": "Gone", "status": "cancelled", "start": {"dateTime": "2026-10-08T09:00:00+01:00"}},
    {"id": "d", "start": {"dateTime": "2026-10-08T07:00:00+01:00"}, "end": {"dateTime": "2026-10-08T08:00:00+01:00"}},
]


class Week(unittest.TestCase):
    def test_lists_a_week_read_only_and_groups_by_day(self):
        g = FakeGoogle(ITEMS)
        out = cal(g).week()
        self.assertEqual(out["start"], "2026-10-08")
        days = {d["date"]: [e["title"] for e in d["events"]] for d in out["days"]}
        self.assertEqual(len(days), 7)
        self.assertEqual(days["2026-10-08"], ["(no title)"])  # cancelled one left out
        self.assertEqual(days["2026-10-09"], ["Dentist"])
        self.assertEqual(days["2026-10-10"], ["Away"])
        self.assertEqual(days["2026-10-11"], ["Away"])  # all-day spans its days
        self.assertEqual(days["2026-10-12"], [])  # end date is exclusive
        self.assertTrue(all(m == "GET" for m, _ in g.calls[1:]))
        q = urllib.parse.parse_qs(g.calls[1][1].split("?", 1)[1])
        self.assertEqual(q["singleEvents"], ["true"])
        self.assertEqual(q["timeMin"], ["2026-10-08T00:00:00+01:00"])
        self.assertEqual(q["timeMax"], ["2026-10-15T00:00:00+01:00"])
        self.assertIn("/calendars/primary/events", g.calls[1][1])

    def test_each_configured_calendar_is_read_and_ids_are_quoted(self):
        g = FakeGoogle([])
        cal(g, calendars=["primary", "family#x@group.calendar.google.com", "bad id/../x"]).week()
        urls = [u for _, u in g.calls[1:]]
        self.assertEqual(len(urls), 2)  # the unsafe id is dropped
        self.assertIn("/calendars/family%23x%40group.calendar.google.com/events", urls[1])

    def test_days_are_bounded_and_bad_input_refused(self):
        g = FakeGoogle([])
        self.assertEqual(len(cal(g).week(days=90)["days"]), 31)
        self.assertEqual(len(cal(g).week(start="2026-10-12", days=1)["days"]), 1)
        with self.assertRaises(ValueError):
            cal(g).week(start="soon")
        with self.assertRaises(ValueError):
            cal(g).week(days="lots")

    def test_errors_name_the_calendar(self):
        with self.assertRaises(SourceError) as e:
            cal(FakeGoogle(fail=403)).week()
        self.assertEqual(str(e.exception), "Google Calendar refused access (scope or quota)")
        with self.assertRaises(SourceError) as e:
            Calendar({"token_file": "/nonexistent"}, opener=FakeGoogle()).week()
        self.assertEqual(str(e.exception), "Google Calendar sign-in isn't set up yet")


class Grouping(unittest.TestCase):
    def test_all_day_first_then_by_time(self):
        ev = [{"title": "late", "all_day": False, "start": "2026-10-08T18:00:00+01:00", "end": None},
              {"title": "early", "all_day": False, "start": "2026-10-08T07:00:00+01:00", "end": None},
              {"title": "allday", "all_day": True, "start": "2026-10-08", "end": "2026-10-09"}]
        day = group_by_day(ev, D(2026, 10, 8), 1)[0]
        self.assertEqual([e["title"] for e in day["events"]], ["allday", "early", "late"])

    def test_sample_week_has_events(self):
        out = SampleCalendar(today=lambda: D(2026, 10, 8)).week()
        self.assertTrue(any(d["events"] for d in out["days"]))


class Http(unittest.TestCase):
    LOGIN = "craig@example.com"

    def serve(self, planner):
        cfg = {"allowed_logins": [self.LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, App(cfg, cache),
                                                                   None, None, None, None, None, planner))
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
                return e.code, json.loads(e.read() or b"null"), None
            except ValueError:
                return e.code, None, None

    def test_routes(self):
        base = self.serve(SampleCalendar(today=lambda: D(2026, 10, 8)))
        status, body, cache = self.get(base, "/api/planner?days=14")
        self.assertEqual(status, 200)
        self.assertEqual(cache, "no-store")
        self.assertEqual(len(body["days"]), 14)
        self.assertEqual(self.get(base, "/api/planner?start=nope")[0], 400)
        self.assertEqual(self.get(base, "/api/planner", login="x@y.z")[0], 403)

    def test_not_signed_in_says_so(self):
        status, body, _ = self.get(self.serve(None), "/api/planner")
        self.assertEqual(status, 503)
        self.assertIn("isn't signed in", body["error"])


if __name__ == "__main__":
    unittest.main()
