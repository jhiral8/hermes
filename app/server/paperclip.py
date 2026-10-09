"""Paperclip board client for the Hermes app server.

Talks to Paperclip's REST API on loopback with a board API key read from a
file. Every response is normalised into the small shapes the app shows, and
written defensively: Paperclip changes fast, so a missing field becomes None
rather than an error.
"""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path


class PaperclipError(Exception):
    pass


# Paperclip status words mapped to the app's wording.
ISSUE_STATUS = {
    "backlog": "Backlog", "todo": "To do", "in_progress": "In progress",
    "in_review": "In review", "done": "Done", "blocked": "Blocked",
    "cancelled": "Cancelled",
}
AGENT_STATUS = {
    "active": "Available", "idle": "Available", "running": "Working",
    "paused": "Paused", "error": "Error", "pending_approval": "Awaiting approval",
    "terminated": "Retired",
}
APPROVAL_TYPE = {
    "hire_agent": "Add an agent",
    "approve_ceo_strategy": "Approve a plan",
    "budget_override_required": "Go over budget",
    "request_board_approval": "Board decision",
}


RUN_STATUS = {
    "queued": "Queued", "running": "Running", "succeeded": "Finished", "completed": "Finished",
    "failed": "Failed", "cancelled": "Stopped", "timed_out": "Timed out", "cancelling": "Stop requested",
}
REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
MAX_TEXT = 4000


def _ref(x):
    if not REF.fullmatch(str(x or "")):
        raise PaperclipError("not a board id")
    return x


def _clip(text, n=MAX_TEXT):
    text = text if isinstance(text, str) else ("" if text is None else str(text))
    return text if len(text) <= n else text[:n] + "…"


def _usage(u):
    u = u if isinstance(u, dict) else {}
    cost = u.get("costUsd", u.get("totalCostUsd"))
    return {"input_tokens": u.get("inputTokens"), "output_tokens": u.get("outputTokens"),
            "cost_usd": cost if isinstance(cost, (int, float)) else None}


def _run(r):
    st = r.get("status")
    return {"id": r.get("id") or r.get("runId"), "status": st, "status_text": RUN_STATUS.get(st, st or "Unknown"),
            "agent_id": r.get("agentId"), "issue_id": r.get("issueId"),
            "source": r.get("invocationSource"), "trigger": _clip(r.get("triggerDetail"), 300),
            "created": r.get("createdAt"), "started": r.get("startedAt"), "finished": r.get("finishedAt"),
            "error": _clip(r.get("error"), 1000) or None, "usage": _usage(r.get("usageJson") or r.get("usage"))}


def _as_list(body, *keys):
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        for k in keys + ("items", "data", "results"):
            if isinstance(body.get(k), list):
                return body[k]
    return []


class Paperclip:
    def __init__(self, cfg, opener=None):
        self.base = cfg["url"].rstrip("/")
        self.company = cfg.get("company_id")
        self.company_name = cfg.get("company_name")
        if not (self.company or self.company_name):
            raise KeyError("company_id or company_name")
        self.key_file = cfg.get("key_file")
        self.timeout = float(cfg.get("timeout", 5))
        self.web_url = cfg.get("web_url")  # Craig-facing board address
        self._open = opener or urllib.request.urlopen

    # ------------------------------------------------------------ transport

    def _key(self):
        if not self.key_file:
            return None
        try:
            return Path(self.key_file).read_text(encoding="utf-8").strip() or None
        except OSError:
            raise PaperclipError("board key file not readable")

    def _call(self, method, path, body=None):
        headers = {"Accept": "application/json", "User-Agent": "hermes-app"}
        key = self._key()
        if key:
            headers["Authorization"] = "Bearer " + key
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + "/api" + path, data=data,
                                     method=method, headers=headers)
        try:
            with self._open(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise PaperclipError(f"Paperclip answered {e.code}")
        except (urllib.error.URLError, OSError) as e:
            raise PaperclipError(f"Paperclip unreachable ({type(e).__name__})")
        try:
            return json.loads(raw or b"null")
        except ValueError:
            raise PaperclipError("Paperclip sent something that isn't JSON")

    def _co(self, path):
        if not self.company:
            # Look the company up by name once; the id is stable after that.
            want = self.company_name.strip().lower()
            for c in _as_list(self._call("GET", "/companies"), "companies"):
                if str(c.get("name") or "").strip().lower() == want:
                    self.company = c.get("id")
                    break
            else:
                raise PaperclipError(f"no company named {self.company_name} on the board")
        return f"/companies/{self.company}{path}"

    # ------------------------------------------------------------ reads

    def agents(self):
        out = []
        for a in _as_list(self._call("GET", self._co("/agents")), "agents"):
            if a.get("status") == "terminated":
                continue
            out.append({
                "id": a.get("id"),
                "name": a.get("name") or "?",
                "title": a.get("title") or a.get("role"),
                "status": a.get("status"),
                "status_text": AGENT_STATUS.get(a.get("status"), a.get("status") or "Unknown"),
                "pause_reason": a.get("pauseReason"),
                "spent_cents": a.get("spentMonthlyCents"),
                "budget_cents": a.get("budgetMonthlyCents"),
                "last_heartbeat": a.get("lastHeartbeatAt"),
            })
        return out

    def projects(self):
        out = {}
        for p in _as_list(self._call("GET", self._co("/projects")), "projects"):
            out[p.get("id")] = {"name": p.get("name"), "description": p.get("description")}
        return out

    def issues(self, limit=100):
        body = self._call("GET", self._co(f"/issues?limit={int(limit)}&sortField=updated&sortDir=desc"))
        out = []
        for i in _as_list(body, "issues"):
            run = i.get("activeRun") or {}
            out.append({
                "id": i.get("id"),
                "ref": i.get("identifier"),
                "title": i.get("title") or "(untitled)",
                "status": i.get("status"),
                "status_text": ISSUE_STATUS.get(i.get("status"), i.get("status") or "Unknown"),
                "priority": i.get("priority"),
                "project_id": i.get("projectId"),
                "agent_id": i.get("assigneeAgentId"),
                "needs_you": bool(i.get("assigneeUserId")) or i.get("status") == "in_review",
                "running": bool(run) and run.get("status") in ("running", "queued"),
                "run_id": run.get("id") if run else None,
                "updated": i.get("updatedAt") or i.get("lastActivityAt"),
            })
        return out

    def approvals(self, status=None):
        q = f"?status={status}" if status else ""
        out = []
        for a in _as_list(self._call("GET", self._co("/approvals" + q)), "approvals"):
            payload = a.get("payload") or {}
            title = (payload.get("title") or payload.get("summary") or payload.get("name")
                     or APPROVAL_TYPE.get(a.get("type"), "Decision"))
            out.append({
                "id": a.get("id"),
                "source": "paperclip",
                "kind": APPROVAL_TYPE.get(a.get("type"), a.get("type")),
                "title": str(title)[:200],
                "detail": payload.get("description") or payload.get("reason") or payload.get("rationale"),
                "status": a.get("status"),
                "agent_id": a.get("requestedByAgentId"),
                "requested": a.get("createdAt"),
                "decided": a.get("decidedAt"),
                "note": a.get("decisionNote"),
            })
        return out

    def routines(self):
        out = []
        for r in _as_list(self._call("GET", self._co("/routines")), "routines"):
            trig = next((t for t in (r.get("triggers") or []) if t.get("enabled")), None) or {}
            last = r.get("lastRun") or {}
            out.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "agent_id": r.get("assigneeAgentId"),
                "status": r.get("status"),
                "schedule": trig.get("label") or trig.get("cronExpression"),
                "timezone": trig.get("timezone"),
                "next_run": trig.get("nextRunAt"),
                "last_result": trig.get("lastResult") or last.get("status"),
                "last_run": trig.get("lastFiredAt") or last.get("createdAt"),
            })
        return out

    def costs(self):
        s = self._call("GET", self._co("/costs/summary"))
        if not isinstance(s, dict):
            return None
        return {
            "spend_cents": s.get("spendCents"),
            "budget_cents": s.get("budgetCents"),
            "pricing_complete": s.get("pricingComplete"),
        }

    # ------------------------------------------------------------ one record

    def issue_detail(self, issue_id):
        i = self._call("GET", f"/issues/{_ref(issue_id)}")
        if not isinstance(i, dict) or not i.get("id"):
            raise PaperclipError("task not found")
        comments = [{"id": c.get("id"), "body": _clip(c.get("body")), "agent_id": c.get("authorAgentId"),
                     "by_user": bool(c.get("authorUserId")), "created": c.get("createdAt")}
                    for c in _as_list(self._call("GET", f"/issues/{i['id']}/comments"), "comments")]
        runs = [_run(r) for r in _as_list(self._call("GET", f"/issues/{i['id']}/runs"), "runs")]
        return {"id": i.get("id"), "ref": i.get("identifier"), "title": i.get("title") or "(untitled)",
                "description": _clip(i.get("description")), "status": i.get("status"),
                "status_text": ISSUE_STATUS.get(i.get("status"), i.get("status") or "Unknown"),
                "priority": i.get("priority"), "project_id": i.get("projectId"),
                "agent_id": i.get("assigneeAgentId"), "parent_id": i.get("parentId"),
                "created": i.get("createdAt"), "updated": i.get("updatedAt"),
                "comments": sorted(comments, key=lambda c: c["created"] or ""),
                "runs": sorted(runs, key=lambda r: r["created"] or r["started"] or "", reverse=True)[:30]}

    def run_detail(self, run_id):
        r = self._call("GET", f"/heartbeat-runs/{_ref(run_id)}")
        if not isinstance(r, dict) or not r.get("id"):
            raise PaperclipError("run not found")
        out = _run(r)
        out["events"] = [{"seq": e.get("seq"), "type": e.get("eventType"), "level": e.get("level"),
                          "message": _clip(e.get("message"), 500), "at": e.get("createdAt")}
                         for e in _as_list(self._call("GET", f"/heartbeat-runs/{r['id']}/events?limit=200"), "events")
                         if e.get("message")]
        return out

    def agent_detail(self, agent_id):
        a = self._call("GET", f"/agents/{_ref(agent_id)}")
        if not isinstance(a, dict) or not a.get("id"):
            raise PaperclipError("agent not found")
        runs = self._call("GET", self._co(f"/heartbeat-runs?agentId={a['id']}&limit=20&summary=true"))
        return {"id": a.get("id"), "name": a.get("name") or "?", "title": a.get("title") or a.get("role"),
                "role": a.get("role"), "status": a.get("status"),
                "status_text": AGENT_STATUS.get(a.get("status"), a.get("status") or "Unknown"),
                "pause_reason": a.get("pauseReason"), "adapter": a.get("adapterType"),
                "reports_to": a.get("reportsTo"), "capabilities": _clip(a.get("capabilities"), 1000) or None,
                "spent_cents": a.get("spentMonthlyCents"), "budget_cents": a.get("budgetMonthlyCents"),
                "last_heartbeat": a.get("lastHeartbeatAt"), "created": a.get("createdAt"),
                "runs": [_run(r) for r in _as_list(runs, "runs")][:20]}

    def routine_detail(self, routine_id):
        r = self._call("GET", f"/routines/{_ref(routine_id)}")
        if not isinstance(r, dict) or not r.get("id"):
            raise PaperclipError("routine not found")
        runs = [{"id": x.get("id"), "status": x.get("status"), "source": x.get("source"),
                 "triggered": x.get("triggeredAt") or x.get("createdAt"), "completed": x.get("completedAt"),
                 "issue_id": x.get("linkedIssueId"), "failure": _clip(x.get("failureReason"), 500) or None}
                for x in _as_list(self._call("GET", f"/routines/{r['id']}/runs?limit=20"), "runs")][:20]
        return {"id": r.get("id"), "title": r.get("title"), "description": _clip(r.get("description")),
                "agent_id": r.get("assigneeAgentId"), "status": r.get("status"),
                "triggers": [{"kind": t.get("kind"), "label": t.get("label") or t.get("cronExpression"),
                              "timezone": t.get("timezone"), "enabled": bool(t.get("enabled")),
                              "next_run": t.get("nextRunAt"), "last_run": t.get("lastFiredAt"),
                              "last_result": t.get("lastResult")} for t in (r.get("triggers") or [])],
                "runs": runs}

    # ------------------------------------------------------------ writes

    def create_task(self, title, description, agent_id):
        body = {"title": title, "status": "todo", "priority": "medium"}
        if description:
            body["description"] = description
        if agent_id:
            body["assigneeAgentId"] = agent_id
        i = self._call("POST", self._co("/issues"), body)
        return {"id": i.get("id"), "ref": i.get("identifier"), "title": i.get("title")}

    def decide(self, approval_id, approve, note):
        action = "approve" if approve else "reject"
        body = {"decisionNote": note} if note else {}
        self._call("POST", f"/approvals/{approval_id}/{action}", body)

    def set_agent_paused(self, agent_id, paused):
        self._call("POST", f"/agents/{agent_id}/{'pause' if paused else 'resume'}", {})

    def cancel_run(self, run_id):
        self._call("POST", f"/heartbeat-runs/{run_id}/cancel", {})
