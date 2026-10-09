import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import hermes_app as h
from api import App
from demo import Demo
from paperclip import Paperclip

LOGIN = "craig@example.com"


class FakeBoard:
    """Answers Paperclip's read routes for one task, run, agent and routine; records each call."""

    def __init__(self):
        self.calls = []

    def __call__(self, req, timeout=None):
        path = req.full_url.split("/api", 1)[1]
        self.calls.append((req.get_method(), path))
        routes = {
            "/issues/HER-14": {"id": "i1", "identifier": "HER-14", "title": "Approvals", "description": "x" * 5000,
                               "status": "in_progress", "assigneeAgentId": "a1", "updatedAt": "2026-10-09T08:00:00Z"},
            "/issues/i1/comments": [{"id": "c2", "body": "later", "authorUserId": "u", "createdAt": "2026-10-09T09:00:00Z"},
                                    {"id": "c1", "body": "first", "authorAgentId": "a1", "createdAt": "2026-10-09T08:00:00Z"}],
            "/issues/i1/runs": [{"runId": "r1", "status": "succeeded", "agentId": "a1", "createdAt": "2026-10-09T08:10:00Z",
                                 "usageJson": {"inputTokens": 10, "outputTokens": 2, "costUsd": 0.01}}],
            "/heartbeat-runs/r1": {"id": "r1", "status": "failed", "agentId": "a1", "issueId": "i1", "error": "boom"},
            "/heartbeat-runs/r1/events?limit=200": [{"seq": 1, "eventType": "log", "message": "hi", "createdAt": "t"},
                                                   {"seq": 2, "eventType": "raw", "message": None}],
            "/agents/a1": {"id": "a1", "name": "Codex", "status": "running", "adapterType": "codex_local"},
            "/companies/co/heartbeat-runs?agentId=a1&limit=20&summary=true": [{"id": "r1", "status": "running"}],
            "/routines/rt1": {"id": "rt1", "title": "Digest", "triggers": [{"kind": "schedule", "cronExpression": "0 8 * * 1", "enabled": True}]},
            "/routines/rt1/runs?limit=20": [{"id": "x", "status": "completed", "linkedIssueId": "i1"}],
        }
        if path not in routes:
            raise urllib.error.HTTPError(req.full_url, 404, "no", {}, io.BytesIO(b""))
        return io.BytesIO(json.dumps(routes[path]).encode())


def board():
    fake = FakeBoard()
    return Paperclip({"url": "http://pc", "company_id": "co"}, opener=fake), fake


class Client(unittest.TestCase):
    def test_issue_detail_reads_task_comments_and_runs(self):
        pc, fake = board()
        i = pc.issue_detail("HER-14")
        self.assertEqual(i["ref"], "HER-14")
        self.assertTrue(i["description"].endswith("…"))
        self.assertEqual([c["body"] for c in i["comments"]], ["first", "later"])
        self.assertEqual(i["runs"][0]["id"], "r1")
        self.assertEqual(i["runs"][0]["usage"]["cost_usd"], 0.01)
        self.assertTrue(all(m == "GET" for m, _ in fake.calls))

    def test_run_agent_routine(self):
        pc, _ = board()
        r = pc.run_detail("r1")
        self.assertEqual((r["status_text"], r["error"], len(r["events"])), ("Failed", "boom", 1))
        a = pc.agent_detail("a1")
        self.assertEqual((a["adapter"], a["runs"][0]["status_text"]), ("codex_local", "Running"))
        rt = pc.routine_detail("rt1")
        self.assertEqual((rt["triggers"][0]["label"], rt["runs"][0]["issue_id"]), ("0 8 * * 1", "i1"))

    def test_bad_ids_refused_before_any_call(self):
        pc, fake = board()
        for bad in ("../x", "a/b", "", "x" * 80):
            with self.assertRaises(Exception):
                pc.issue_detail(bad)
        self.assertEqual(fake.calls, [])


class Http(unittest.TestCase):
    def serve(self):
        cfg = {"allowed_logins": [LOGIN], "services": []}
        cache = h.StatusCache([], 15)
        app = App(cfg, cache, demo=Demo())
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h.make_handler(cfg, Path("."), cache, app))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        return f"http://127.0.0.1:{srv.server_port}"

    def get(self, base, path):
        r = urllib.request.Request(base + path, headers={"Tailscale-User-Login": LOGIN})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    def test_detail_routes(self):
        base = self.serve()
        s, b = self.get(base, "/api/work/i1")
        self.assertEqual((s, b["item"]["data"]["agent"]), (200, "Codex"))
        self.assertEqual(self.get(base, "/api/runs/run-14")[1]["item"]["data"]["issue"]["ref"], "HER-14")
        self.assertEqual(self.get(base, "/api/agents/a-max")[1]["item"]["data"]["name"], "Max")
        self.assertEqual(self.get(base, "/api/routines/r1")[1]["item"]["data"]["title"], "Monday server digest")
        self.assertEqual(self.get(base, "/api/agents/nobody")[0], 404)
        self.assertEqual(self.get(base, "/api/work/a.b")[0], 404)  # not a detail route at all


if __name__ == "__main__":
    unittest.main()
