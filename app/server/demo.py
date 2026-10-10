"""Sample data for trying the app without the real services (config "demo": true).

Every response built from this is marked demo, and the app shows a
"Sample data" chip, so it can never be mistaken for the real board.
"""

import copy
import time
import uuid


def _iso(mins_ago):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - mins_ago * 60))


def _iso_ahead(mins):
    return _iso(-mins)


AGENTS = [
    {"id": "a-max", "name": "Max", "title": "Personal coordinator", "status": "idle",
     "spent_cents": 41, "budget_cents": 1000},
    {"id": "a-codex", "name": "Codex", "title": "Coding in the sealed box", "status": "running",
     "spent_cents": 0, "budget_cents": 0},
    {"id": "a-claude", "name": "Claude", "title": "Reviewer in the sealed box", "status": "idle",
     "spent_cents": 212, "budget_cents": 1500},
]

PROJECTS = {
    "p-app": {"name": "Hermes app", "description": "The PWA, Android and Wear OS apps."},
    "p-research": {"name": "Research", "description": "Public research Max can share."},
}


class Demo:
    def __init__(self):
        self.agents_ = copy.deepcopy(AGENTS)
        self.issues_ = [
            {"id": "i1", "ref": "HER-14", "title": "Build the Approvals screen", "status": "in_progress",
             "project_id": "p-app", "agent_id": "a-codex", "running": True, "run_id": "run-14",
             "needs_you": False, "updated": _iso(6)},
            {"id": "i2", "ref": "HER-13", "title": "Review Phase 1 server code", "status": "in_review",
             "project_id": "p-app", "agent_id": "a-claude", "running": False, "needs_you": True,
             "updated": _iso(48)},
            {"id": "i3", "ref": "HER-12", "title": "Compare UK supermarket price sources", "status": "todo",
             "project_id": "p-research", "agent_id": "a-max", "running": False, "needs_you": False,
             "updated": _iso(200)},
            {"id": "i4", "ref": "HER-11", "title": "Summarise Wear OS tile limits", "status": "done",
             "project_id": "p-research", "agent_id": "a-max", "running": False, "needs_you": False,
             "updated": _iso(1440)},
        ]
        self.approvals_ = [
            {"id": "ap1", "source": "paperclip", "kind": "Go over budget",
             "title": "Let Claude spend $5 more this month",
             "detail": "Reviewing the Phase 2 code needs about two more runs.",
             "status": "pending", "agent_id": "a-claude", "requested": _iso(12)},
        ]
        self.broker_ = [
            {"id": "ACT-031", "source": "broker", "kind": "Send email",
             "title": "Reply: test booking confirmation", "detail": "From the test inbox",
             "recipients": "jhiral.hermes@gmail.com", "status": "pending",
             "requested": _iso(2), "expires": _iso_ahead(8)},
        ]
        self.history_ = [
            {"id": "ACT-030", "source": "broker", "kind": "Send email", "title": "Test reply",
             "recipients": "jhiral.hermes@gmail.com", "status": "sent", "requested": _iso(900),
             "decided": _iso(898)},
            {"id": "ACT-029", "source": "broker", "kind": "Send email", "title": "Trick email reply",
             "recipients": "unknown@example.com", "status": "denied", "requested": _iso(1500),
             "decided": _iso(1499)},
        ]
        self.routines_ = [
            {"id": "r1", "title": "Monday server digest", "agent_id": None, "status": "active",
             "schedule": "Mondays at 08:00", "timezone": "Europe/London",
             "next_run": _iso_ahead(60 * 24 * 4), "last_result": "succeeded", "last_run": _iso(60 * 24 * 3)},
            {"id": "r2", "title": "Weekly research queue", "agent_id": "a-max", "status": "paused",
             "schedule": "Fridays at 09:00", "timezone": "Europe/London",
             "next_run": None, "last_result": None, "last_run": None},
        ]
        self.killed = False

    # Same method names as Paperclip / Broker / CostFile.
    def agents(self):
        out = copy.deepcopy(self.agents_)
        for a in out:
            a["status_text"] = {"idle": "Available", "running": "Working", "paused": "Paused"}.get(a["status"], a["status"])
        return out

    def projects(self):
        return copy.deepcopy(PROJECTS)

    def issues(self, limit=100):
        return copy.deepcopy(self.issues_[:limit])

    def approvals(self, status=None):
        return [a for a in copy.deepcopy(self.approvals_) if not status or a["status"] == status]

    def routines(self):
        return copy.deepcopy(self.routines_)

    def costs(self):
        return {"spend_cents": sum(a["spent_cents"] for a in self.agents_),
                "budget_cents": 2500, "pricing_complete": True}

    def create_task(self, title, description, agent_id):
        ref = f"HER-{15 + len(self.issues_) - 4}"
        self.issues_.insert(0, {"id": str(uuid.uuid4()), "ref": ref, "title": title, "status": "todo",
                                "project_id": None, "agent_id": agent_id, "running": False,
                                "needs_you": False, "updated": _iso(0)})
        return {"id": self.issues_[0]["id"], "ref": ref, "title": title}

    def decide(self, approval_id, approve, note):
        for a in self.approvals_:
            if a["id"] == approval_id:
                a["status"] = "approved" if approve else "rejected"
                a["decided"] = _iso(0)
                a["note"] = note
                return
        raise KeyError(approval_id)

    def set_agent_paused(self, agent_id, paused):
        for a in self.agents_:
            if a["id"] == agent_id:
                a["status"] = "paused" if paused else "idle"
                return
        raise KeyError(agent_id)

    def set_issue_status(self, issue_id, status):
        for i in self.issues_:
            if i["id"] == issue_id:
                i["status"], i["updated"] = status, _iso(0)
                return
        raise KeyError(issue_id)

    def add_comment(self, issue_id, body):
        if not any(i["id"] == issue_id for i in self.issues_):
            raise KeyError(issue_id)

    def cancel_run(self, run_id):
        for i in self.issues_:
            if i.get("run_id") == run_id:
                i["running"] = False

    # one record
    def issue_detail(self, issue_id):
        i = next((x for x in self.issues_ if x["id"] == issue_id or x["ref"] == issue_id), None)
        if not i:
            raise KeyError(issue_id)
        i = copy.deepcopy(i)
        running = i.get("running")
        i.update({"description": "Build the screen from the mockup: pending and history, filters, and a detail pane. "
                                 "Email sends stay on the broker's fingerprint page.",
                  "status_text": {"in_progress": "In progress", "in_review": "In review", "todo": "To do", "done": "Done"}.get(i["status"], i["status"]),
                  "priority": "medium", "created": _iso(60 * 26),
                  "comments": [{"id": "c1", "body": "Picked this up. Starting with the list.", "agent_id": i["agent_id"], "by_user": False, "created": _iso(90)},
                               {"id": "c2", "body": "Keep the broker approvals read-only here.", "agent_id": None, "by_user": True, "created": _iso(60)}],
                  "runs": [{"id": i.get("run_id") or "run-" + i["ref"][-2:], "status": "running" if running else "succeeded",
                            "status_text": "Running" if running else "Finished", "agent_id": i["agent_id"], "issue_id": i["id"],
                            "source": "assignment", "trigger": None, "created": _iso(40), "started": _iso(40),
                            "finished": None if running else _iso(20), "error": None,
                            "usage": {"input_tokens": 18400, "output_tokens": 2100, "cost_usd": None if running else 0.12}}]})
        return i

    def run_detail(self, run_id):
        for i in self.issues_:
            d = self.issue_detail(i["id"])
            for r in d["runs"]:
                if r["id"] == run_id:
                    r["events"] = [{"seq": 1, "type": "lifecycle", "level": "info", "message": "Run started", "at": r["started"]},
                                   {"seq": 2, "type": "log", "level": "info", "message": "Read the task and the mockup notes", "at": _iso(38)},
                                   {"seq": 3, "type": "log", "level": "info", "message": "Wrote the list view and filters", "at": _iso(30)}]
                    if r["finished"]:
                        r["events"].append({"seq": 4, "type": "lifecycle", "level": "info", "message": "Run finished", "at": r["finished"]})
                    return r
        raise KeyError(run_id)

    def agent_detail(self, agent_id):
        a = next((x for x in self.agents() if x["id"] == agent_id), None)
        if not a:
            raise KeyError(agent_id)
        a.update({"role": "general", "adapter": {"a-max": "hermes_local", "a-codex": "codex_local"}.get(agent_id, "claude_local"),
                  "reports_to": None, "capabilities": a["title"], "last_heartbeat": _iso(5), "created": _iso(60 * 24 * 3),
                  "runs": [r for i in self.issues_ if i["agent_id"] == agent_id for r in self.issue_detail(i["id"])["runs"]]})
        return a

    def routine_detail(self, routine_id):
        r = next((x for x in self.routines_ if x["id"] == routine_id), None)
        if not r:
            raise KeyError(routine_id)
        r = copy.deepcopy(r)
        return {"id": r["id"], "title": r["title"], "description": "Summarise the week's server alerts, costs and test results.",
                "agent_id": r["agent_id"], "status": r["status"],
                "triggers": [{"kind": "schedule", "label": r["schedule"], "timezone": r["timezone"], "enabled": r["status"] == "active",
                              "next_run": r["next_run"], "last_run": r["last_run"], "last_result": r["last_result"]}],
                "runs": [{"id": "rr1", "status": "completed", "source": "schedule", "triggered": r["last_run"],
                          "completed": r["last_run"], "issue_id": "i4", "failure": None}] if r["last_run"] else []}

    # broker
    def pending(self):
        return copy.deepcopy(self.broker_)

    def history(self):
        return copy.deepcopy(self.history_)

    # cost file
    def read(self):
        return {"label": "Max on OpenRouter", "today_usd": 0.07, "month_usd": 1.84,
                "month_cap_usd": 10, "balance_usd": 14.62}
