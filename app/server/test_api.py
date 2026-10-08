import io
import json
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import hermes_app as h
from api import ActionError, App
from demo import Demo
from paperclip import Paperclip, PaperclipError
from sources import Broker, CostFile, SourceError

LOGIN = "craig@example.com"
USER = {"login": LOGIN, "name": "Craig"}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeOpener:
    """Records requests and answers from a {(method, path): body} table."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split("/api", 1)[1]
        self.calls.append((req.get_method(), path, req.data, dict(req.header_items())))
        key = (req.get_method(), path.split("?")[0])
        if key not in self.table:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return FakeResp(json.dumps(self.table[key]).encode())


class PaperclipClient(unittest.TestCase):
    def make(self, table, key_text="pcp_board_x"):
        d = tempfile.mkdtemp()
        kf = Path(d, "key")
        kf.write_text(key_text + "\n")
        op = FakeOpener(table)
        return Paperclip({"url": "http://127.0.0.1:3100/", "company_id": "co1", "key_file": str(kf)}, opener=op), op

    def test_reads_are_normalised_and_authenticated(self):
        pc, op = self.make({
            ("GET", "/companies/co1/agents"): [
                {"id": "a1", "name": "Max", "status": "idle", "spentMonthlyCents": 40, "budgetMonthlyCents": 200},
                {"id": "a2", "name": "Old", "status": "terminated"}],
            ("GET", "/companies/co1/issues"): {"items": [
                {"id": "i1", "identifier": "HER-1", "title": "T", "status": "in_review",
                 "assigneeAgentId": "a1", "activeRun": {"id": "r1", "status": "running"}}]},
        })
        agents = pc.agents()
        self.assertEqual([a["name"] for a in agents], ["Max"])
        self.assertEqual(agents[0]["status_text"], "Available")
        issues = pc.issues()
        self.assertEqual(issues[0]["status_text"], "In review")
        self.assertTrue(issues[0]["needs_you"])
        self.assertTrue(issues[0]["running"])
        self.assertEqual(op.calls[0][3]["Authorization"], "Bearer pcp_board_x")
        self.assertIn("sortField=updated&", op.calls[1][1])  # this Paperclip refuses updatedAt

    def test_writes_send_the_right_calls(self):
        pc, op = self.make({
            ("POST", "/companies/co1/issues"): {"id": "i9", "identifier": "HER-9", "title": "New"},
            ("POST", "/approvals/ap1/approve"): {},
            ("POST", "/agents/a1/pause"): {},
            ("POST", "/heartbeat-runs/r1/cancel"): {},
        })
        self.assertEqual(pc.create_task("New", "", "a1")["ref"], "HER-9")
        body = json.loads(op.calls[-1][2])
        self.assertEqual(body["assigneeAgentId"], "a1")
        pc.decide("ap1", True, "ok")
        pc.set_agent_paused("a1", True)
        pc.cancel_run("r1")
        self.assertEqual([c[1] for c in op.calls[1:]], ["/approvals/ap1/approve", "/agents/a1/pause", "/heartbeat-runs/r1/cancel"])

    def test_errors_become_paperclip_errors(self):
        pc, _ = self.make({})
        with self.assertRaises(PaperclipError):
            pc.agents()


class Sources(unittest.TestCase):
    def test_broker_reads_sqlite_read_only(self):
        d = tempfile.mkdtemp()
        db = Path(d, "broker.db")
        con = sqlite3.connect(db)
        con.execute("create table req (rid text, subject text, rcpt text, state text, created text)")
        con.execute("insert into req values ('ACT-1','Hello','a@b.c','pending','2026-10-08T08:00:00Z')")
        con.commit()
        con.close()
        b = Broker({"sqlite_path": str(db), "pending_sql":
                    "select rid as id, subject as title, rcpt as recipients, state as status, created as requested from req where state='pending'"})
        rows = b.pending()
        self.assertEqual(rows[0]["id"], "ACT-1")
        self.assertEqual(rows[0]["source"], "broker")
        self.assertIsNone(rows[0]["expires"])
        bad = Broker({"sqlite_path": str(db), "pending_sql": "delete from req"})
        with self.assertRaises(SourceError):
            bad.pending()  # read-only connection refuses writes
        self.assertEqual(len(b.pending()), 1)

    def test_broker_export_and_feed(self):
        import broker_export, time as _t
        d = tempfile.mkdtemp()
        db = Path(d, "broker.db")
        con = sqlite3.connect(db)
        con.execute("create table requests (id text primary key, created real, payload text, status text, updated real)")
        con.execute("insert into requests values ('r1', 1760000000, ?, 'pending', 1760000000)",
                    (json.dumps({"subject": "Hi", "to": ["a@b.c", "d@e.f"], "body": "secret"}),))
        con.execute("insert into requests values ('r0', 1759990000, '{}', 'sent', 1759990100)")
        con.commit()
        con.close()
        cfg = json.loads(Path(__file__).with_name("broker-export.example.json").read_text())
        cfg.update(sqlite_path=str(db), out=str(Path(d, "feed.json")))
        broker_export.export(cfg)
        raw = Path(d, "feed.json").read_text()
        self.assertNotIn("secret", raw)  # only the shown columns leave the broker
        b = Broker({"feed_path": cfg["out"]})
        p = b.pending()
        self.assertEqual((p[0]["id"], p[0]["title"], p[0]["recipients"]), ("r1", "Hi", "a@b.c, d@e.f"))
        self.assertEqual(p[0]["expires"], "2025-10-09T09:03:20Z")  # created + 10 min
        h = b.history()
        self.assertEqual((h[0]["title"], h[0]["status"]), ("Email send", "sent"))
        with self.assertRaises(SourceError):
            b._feed("pending", now=_t.time() + 3600)  # stale feed isn't trusted

    def test_cost_csv(self):
        import datetime as dt
        d = tempfile.mkdtemp()
        p = Path(d, "balance.csv")
        now = dt.datetime(2026, 10, 8, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        rows = [("epoch", "provider", "remaining", "used"),
                (now - 40 * 86400, "openrouter", 20, 1.0),   # last month
                (now - 3 * 86400, "openrouter", 18, 2.0),    # this month: +1.0
                (now - 3 * 86400, "deepseek", 5, 9.0),       # other provider ignored
                (now - 3600 * 2, "openrouter", 17.5, 2.5),   # today: +0.5
                (now - 3600, "openrouter", 19.9, 0.1),       # new key: no negative spend
                (now - 60, "openrouter", 19.8, 0.2)]         # today: +0.1
        p.write_text("\n".join(",".join(map(str, r)) for r in rows))
        c = CostFile({"csv_path": str(p), "provider": "openrouter", "month_cap_usd": 10})
        out = c.read_csv(now=now)
        self.assertAlmostEqual(out["today_usd"], 0.6)
        self.assertAlmostEqual(out["month_usd"], 1.6)
        self.assertEqual((out["balance_usd"], out["month_cap_usd"]), (19.8, 10))
        with self.assertRaises(SourceError):
            CostFile({"csv_path": d + "/none.csv"}).read()

    def test_company_found_by_name(self):
        d = tempfile.mkdtemp()
        op = FakeOpener({("GET", "/companies"): [{"id": "x", "name": "Other"}, {"id": "co9", "name": "Jhiral"}],
                         ("GET", "/companies/co9/agents"): []})
        pc = Paperclip({"url": "http://127.0.0.1:3100", "company_name": "jhiral"}, opener=op)
        self.assertEqual(pc.agents(), [])
        pc.agents()
        self.assertEqual([c[1] for c in op.calls], ["/companies", "/companies/co9/agents", "/companies/co9/agents"])

    def test_cost_file_mapping(self):
        d = tempfile.mkdtemp()
        p = Path(d, "cost.json")
        p.write_text(json.dumps({"day": {"usd": 0.1}, "month_usd": 2}))
        c = CostFile({"path": str(p), "fields": {"today_usd": "day.usd", "month_usd": "month_usd", "balance_usd": "nope"}})
        out = c.read()
        self.assertEqual((out["today_usd"], out["month_usd"], out["balance_usd"]), (0.1, 2, None))
        with self.assertRaises(SourceError):
            CostFile({"path": d + "/missing.json"}).read()


class AppLogic(unittest.TestCase):
    def setUp(self):
        self.cache = h.StatusCache([], 15)

    def test_unconnected_sections_say_so(self):
        app = App({"allowed_logins": [LOGIN]}, self.cache)
        a = app.approvals()
        self.assertFalse(a["board"]["ok"])
        self.assertIn("not connected", a["board"]["error"])
        t = app.today()
        self.assertFalse(t["waiting_ok"])
        self.assertFalse(t["work_ok"])

    def test_demo_actions_and_audit(self):
        d = tempfile.mkdtemp()
        log = Path(d, "audit.log")
        app = App({"allowed_logins": [LOGIN], "audit_log": str(log)}, self.cache, demo=Demo())
        task = app.create_task(USER, {"title": "Write tests", "agent_id": "a-codex"})
        self.assertTrue(task["ref"].startswith("HER-"))
        with self.assertRaises(ActionError):
            app.create_task(USER, {"title": ""})
        with self.assertRaises(ActionError):
            app.create_task(USER, {"title": "x", "agent_id": "nobody"})
        app.decide(USER, "ap1", {"decision": "approve"})
        with self.assertRaises(ActionError):
            app.decide(USER, "ap1", {"decision": "maybe"})
        with self.assertRaises(ActionError):
            app.stop(USER, {})
        app.stop(USER, {"confirm": "STOP"})
        actions = [json.loads(x)["action"] for x in log.read_text().splitlines()]
        self.assertEqual(actions, ["create_task", "board_decision", "stop"])

    def test_stop_writes_request_file_or_says_not_set_up(self):
        app = App({"allowed_logins": [LOGIN]}, self.cache)
        with self.assertRaises(ActionError) as e:
            app.stop(USER, {"confirm": "STOP"})
        self.assertEqual(e.exception.code, 501)
        d = tempfile.mkdtemp()
        req = Path(d, "stop-request")
        app = App({"allowed_logins": [LOGIN], "stop": {"request_file": str(req)}}, self.cache)
        app.stop(USER, {"confirm": "STOP"})
        self.assertEqual(json.loads(req.read_text())["by"], LOGIN)


class HttpActions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache, demo=Demo())
        web = Path(__file__).resolve().parent.parent / "web"
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, web, cache, app))
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.srv.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def post(self, path, body, headers=None):
        hd = {"Tailscale-User-Login": LOGIN, "Content-Type": "application/json", "X-Hermes-Action": "1"}
        hd.update(headers or {})
        hd = {k: v for k, v in hd.items() if v is not None}
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode(), headers=hd, method="POST")
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            raw = e.read() or b"{}"
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"text": raw.decode()}

    def test_create_task_over_http(self):
        code, body = self.post("/api/work/tasks", {"title": "From the app"})
        self.assertEqual(code, 200)
        self.assertEqual(body["title"], "From the app")

    def test_cross_site_requests_refused(self):
        self.assertEqual(self.post("/api/stop", {"confirm": "STOP"}, {"X-Hermes-Action": None})[0], 403)
        self.assertEqual(self.post("/api/stop", {"confirm": "STOP"}, {"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.post("/api/stop", {"confirm": "STOP"}, {"Tailscale-User-Login": "x@y.z"})[0], 403)

    def test_bad_input(self):
        self.assertEqual(self.post("/api/nope", {})[0], 404)
        self.assertEqual(self.post("/api/approvals/board/ap1", {"decision": "x"})[0], 400)
        self.assertEqual(self.post("/api/approvals/board/..", {"decision": "approve"})[0], 400)
        self.assertEqual(self.post("/api/agents/a%2e%2e/pause", {})[0], 400)

    def test_reads(self):
        req = urllib.request.Request(self.base + "/api/today", headers={"Tailscale-User-Login": LOGIN})
        with urllib.request.urlopen(req) as r:
            d = json.loads(r.read())
        self.assertTrue(d["demo"])
        self.assertTrue(d["waiting_ok"])


if __name__ == "__main__":
    unittest.main()
