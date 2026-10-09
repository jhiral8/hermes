"""The Hermes app's API: what each screen reads, and the few actions it can take.

Reads never fail as a whole: each section carries its own "error" so the app
shows "not connected" for that part instead of empty or zero values.

Actions are Craig's own decisions made through the app: create a task, decide
a Paperclip (board) approval, pause or resume an agent, cancel a run, and
pull the stop. Broker approvals (email sends) are NOT decided here; they stay
on the broker's fingerprint page. Every action is written to an audit log.
"""

import json
import threading
import time
from pathlib import Path

from paperclip import Paperclip, PaperclipError
from sources import Broker, CostFile, SourceError

ERRORS = (PaperclipError, SourceError, KeyError, OSError)


class ActionError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _section(fn):
    try:
        return {"ok": True, "data": fn()}
    except ERRORS as e:
        msg = str(e) if not isinstance(e, KeyError) else f"missing {e}"
        return {"ok": False, "error": msg or type(e).__name__}


class App:
    def __init__(self, cfg, status_cache, demo=None):
        self.cfg = cfg
        self.status = status_cache
        self.demo = demo
        pc = cfg.get("paperclip")
        br = cfg.get("broker")
        cf = cfg.get("cost_file")
        self.board = demo or (Paperclip(pc) if pc else None)
        self.broker = demo or (Broker(br) if br else None)
        self.cost_file = demo or (CostFile(cf) if cf else None)
        self.board_url = (pc or {}).get("web_url")
        self.broker_url = (br or {}).get("review_url")
        stop = cfg.get("stop") or {}
        self.stop_file = stop.get("request_file")
        self.audit_path = cfg.get("audit_log")
        self._lock = threading.Lock()
        self._cache = {}
        self.cache_seconds = float(cfg.get("board_cache_seconds", 10))

    # ------------------------------------------------------------ helpers

    def _cached(self, key, fn):
        now = time.time()
        with self._lock:
            hit = self._cache.get(key)
            if hit and now - hit[0] < self.cache_seconds:
                return hit[1]
        val = fn()
        with self._lock:
            self._cache[key] = (now, val)
        return val

    def _forget(self):
        with self._lock:
            self._cache.clear()

    def _need(self, src, name):
        if src is None:
            raise SourceError(f"{name} not connected yet")
        return src

    def _agents(self):
        return self._cached("agents", lambda: self._need(self.board, "Paperclip").agents())

    def _agent_names(self):
        try:
            return {a["id"]: a["name"] for a in self._agents()}
        except ERRORS:
            return {}

    def audit(self, user, action, detail):
        if not self.audit_path:
            return
        line = json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                           "by": user["login"], "action": action, **detail})
        try:
            with open(self.audit_path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass  # the action already happened; a lost audit line must not undo it

    def meta(self):
        return {"demo": bool(self.demo), "board_url": self.board_url,
                "broker_url": self.broker_url, "stop_ready": bool(self.stop_file or self.demo)}

    # ------------------------------------------------------------ reads

    def agents(self):
        def build():
            agents = self._agents()
            issues = self._cached("issues", lambda: self.board.issues())
            for a in agents:
                mine = [i for i in issues if i["agent_id"] == a["id"] and i["status"] not in ("done", "cancelled")]
                a["current"] = mine[0] if mine else None
                a["open_tasks"] = len(mine)
            return agents
        return {"agents": _section(build), **self.meta()}

    def work(self):
        def build():
            board = self._need(self.board, "Paperclip")
            names = self._agent_names()
            projects = self._cached("projects", board.projects)
            issues = self._cached("issues", board.issues)
            for i in issues:
                i["agent"] = names.get(i["agent_id"])
                i["project"] = (projects.get(i["project_id"]) or {}).get("name")
            return {"issues": issues,
                    "agents": [{"id": k, "name": v} for k, v in names.items()]}
        return {"work": _section(build), **self.meta()}

    def approvals(self):
        names = self._agent_names()

        def board_list():
            items = self._cached("approvals", lambda: self._need(self.board, "Paperclip").approvals())
            for a in items:
                a["agent"] = names.get(a.get("agent_id"))
            return items

        return {
            "board": _section(board_list),
            "broker_pending": _section(lambda: self._need(self.broker, "Approval broker").pending()),
            "broker_history": _section(lambda: self._need(self.broker, "Approval broker").history()),
            **self.meta(),
        }

    def routines(self):
        def build():
            names = self._agent_names()
            items = self._cached("routines", lambda: self._need(self.board, "Paperclip").routines())
            for r in items:
                r["agent"] = names.get(r.get("agent_id"))
            return items
        return {"routines": _section(build), **self.meta()}

    def spending(self):
        return {
            "board": _section(lambda: self._cached("costs", lambda: self._need(self.board, "Paperclip").costs())),
            "agents": _section(self._agents),
            "max": _section(lambda: self._need(self.cost_file, "Cost monitor").read()),
            **self.meta(),
        }

    def today(self):
        appr = self.approvals()
        work = self.work()
        board_pending = [a for a in (appr["board"].get("data") or []) if a.get("status") == "pending"]
        waiting = (appr["broker_pending"].get("data") or []) + board_pending
        issues = (work["work"].get("data") or {}).get("issues") or []
        active = [i for i in issues if i["status"] in ("in_progress", "in_review", "blocked", "todo")]
        return {
            "status": self.status.get(),
            "waiting": waiting,
            "waiting_ok": appr["board"]["ok"] and appr["broker_pending"]["ok"],
            "waiting_errors": [s["error"] for s in (appr["board"], appr["broker_pending"]) if not s["ok"]],
            "work": active[:6],
            "work_ok": work["work"]["ok"],
            "needs_you": sum(1 for i in issues if i.get("needs_you")),
            "spending": self.spending(),
            **self.meta(),
        }

    # ------------------------------------------------------------ one record

    def _detail(self, fn):
        out = _section(fn)
        if not out["ok"] and (out["error"].startswith("missing ") or out["error"] in ("Paperclip answered 404", "not a board id")):
            out["error"] = "not found"
        return {"item": out, **self.meta()}

    def issue(self, issue_id):
        def build():
            board = self._need(self.board, "Paperclip")
            names = self._agent_names()
            i = board.issue_detail(issue_id)
            i["agent"] = names.get(i.get("agent_id"))
            i["project"] = (self._cached("projects", board.projects).get(i.get("project_id")) or {}).get("name")
            for c in i["comments"]:
                c["by"] = "You" if c.get("by_user") else names.get(c.get("agent_id")) or "Agent"
            for r in i["runs"]:
                r["agent"] = names.get(r.get("agent_id"))
            return i
        return self._detail(build)

    def run(self, run_id):
        def build():
            r = self._need(self.board, "Paperclip").run_detail(run_id)
            r["agent"] = self._agent_names().get(r.get("agent_id"))
            if r.get("issue_id"):
                hit = next((i for i in self._cached("issues", self.board.issues) if i["id"] == r["issue_id"]), None)
                r["issue"] = {"id": r["issue_id"], "ref": hit and hit.get("ref"), "title": hit and hit.get("title")}
            return r
        return self._detail(build)

    def agent(self, agent_id):
        def build():
            board = self._need(self.board, "Paperclip")
            a = board.agent_detail(agent_id)
            names = self._agent_names()
            a["reports_to_name"] = names.get(a.get("reports_to"))
            issues = self._cached("issues", board.issues)
            a["work"] = [{**i, "agent": a["name"]} for i in issues if i["agent_id"] == a["id"]][:20]
            refs = {i["id"]: i.get("ref") for i in issues}
            for r in a["runs"]:
                r["issue_ref"] = refs.get(r.get("issue_id"))
            try:
                a["routines"] = [r for r in self._cached("routines", board.routines) if r.get("agent_id") == a["id"]]
            except ERRORS:
                a["routines"] = []
            return a
        return self._detail(build)

    def routine(self, routine_id):
        def build():
            board = self._need(self.board, "Paperclip")
            r = board.routine_detail(routine_id)
            r["agent"] = self._agent_names().get(r.get("agent_id"))
            refs = {i["id"]: (i.get("ref"), i.get("title")) for i in self._cached("issues", board.issues)}
            for x in r["runs"]:
                x["issue_ref"], x["issue_title"] = refs.get(x.get("issue_id"), (None, None))
            return r
        return self._detail(build)

    # ------------------------------------------------------------ actions

    def create_task(self, user, body):
        title = str(body.get("title") or "").strip()
        if not title or len(title) > 240:
            raise ActionError(400, "A task needs a title of up to 240 characters.")
        desc = str(body.get("description") or "").strip()[:8000]
        agent = body.get("agent_id") or None
        if agent and agent not in self._agent_names():
            raise ActionError(400, "That agent isn't on the board.")
        try:
            task = self._need(self.board, "Paperclip").create_task(title, desc, agent)
        except ERRORS as e:
            raise ActionError(502, f"Task not created: {e}")
        self._forget()
        self.audit(user, "create_task", {"ref": task.get("ref"), "title": title, "agent": agent})
        return task

    def decide(self, user, approval_id, body):
        decision = body.get("decision")
        if decision not in ("approve", "reject"):
            raise ActionError(400, "Decision must be approve or reject.")
        note = str(body.get("note") or "").strip()[:2000]
        try:
            self._need(self.board, "Paperclip").decide(approval_id, decision == "approve", note)
        except ERRORS as e:
            raise ActionError(502, f"Decision not recorded: {e}")
        self._forget()
        self.audit(user, "board_decision", {"approval": approval_id, "decision": decision})
        return {"ok": True}

    def pause(self, user, agent_id, paused):
        if agent_id not in self._agent_names():
            raise ActionError(404, "That agent isn't on the board.")
        try:
            self.board.set_agent_paused(agent_id, paused)
        except ERRORS as e:
            raise ActionError(502, f"Not changed: {e}")
        self._forget()
        self.audit(user, "pause_agent" if paused else "resume_agent", {"agent": agent_id})
        return {"ok": True}

    def cancel_run(self, user, run_id):
        try:
            self._need(self.board, "Paperclip").cancel_run(run_id)
        except ERRORS as e:
            raise ActionError(502, f"Run not cancelled: {e}")
        self._forget()
        self.audit(user, "cancel_run", {"run": run_id})
        return {"ok": True}

    def stop(self, user, body):
        """Ask the server's kill switch to stop all agent work.

        The app can't run the kill switch itself (it has no privileges). It
        drops a request file that a root-owned systemd path unit watches;
        that unit runs the existing kill switch. Resuming stays on the Mac.
        """
        if body.get("confirm") != "STOP":
            raise ActionError(400, "Confirm the stop first.")
        if self.demo:
            self.demo.killed = True
            self.audit(user, "stop", {"demo": True})
            return {"ok": True, "requested": True}
        if not self.stop_file:
            raise ActionError(501, "The stop button isn't connected on the server yet.")
        try:
            p = Path(self.stop_file)
            p.write_text(json.dumps({"by": user["login"], "at": int(time.time())}), encoding="utf-8")
        except OSError as e:
            raise ActionError(500, f"Stop request not written ({type(e).__name__}).")
        self.audit(user, "stop", {})
        return {"ok": True, "requested": True}
