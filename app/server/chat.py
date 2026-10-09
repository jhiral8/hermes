"""Chat with Max from the Hermes app.

The app server talks to Max through Hermes Agent's own API server (the
OpenAI-compatible endpoint on loopback, the same one /health is served from),
using its Responses API with a named conversation per app chat, so Max keeps
the thread's context on his side. Replies stream back to the browser as
simple events: text deltas, tool calls, and the end of the turn.

Each chat's transcript is also kept here (in the app's state folder), so the
app can show it again after a reload. Nothing here sends email or grants Max
anything: he runs with the same sandbox, approvals and kill switch as on
Signal. When the kill switch is on, the app refuses to send.
"""

import json
import os
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

MAX_INPUT = 8000
MAX_MESSAGES = 400  # per chat; older ones drop off the stored transcript
MAX_CHATS = 200


class ChatError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _now_iso():
    t = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + f".{int(t * 1000) % 1000:03d}Z"


class ChatStore:
    """One JSON file per chat, readable only by the app's user."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self._lock = threading.Lock()

    def _path(self, cid):
        return self.dir / f"{cid}.json"

    def list(self):
        out = []
        try:
            files = list(self.dir.glob("*.json"))
        except OSError:
            return out
        for p in files:
            try:
                c = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            last = c["messages"][-1] if c.get("messages") else None
            out.append({"id": c["id"], "title": c.get("title") or "New chat",
                        "updated": c.get("updated"), "count": len(c.get("messages") or []),
                        "preview": (last or {}).get("text", "")[:120]})
        out.sort(key=lambda c: c.get("updated") or "", reverse=True)
        return out

    def get(self, cid):
        try:
            return json.loads(self._path(cid).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ChatError(404, "That chat isn't here any more.")
        except (OSError, ValueError):
            raise ChatError(500, "That chat couldn't be read.")

    def save(self, conv):
        conv["messages"] = conv["messages"][-MAX_MESSAGES:]
        conv["updated"] = _now_iso()
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".chat-")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(conv, f)
                os.chmod(tmp, 0o600)
                os.replace(tmp, self._path(conv["id"]))
            except BaseException:
                Path(tmp).unlink(missing_ok=True)
                raise

    def new(self):
        if len(self.list()) >= MAX_CHATS:
            raise ChatError(409, f"There are {MAX_CHATS} chats already; delete some first.")
        conv = {"id": uuid.uuid4().hex[:16], "title": None, "created": _now_iso(), "messages": []}
        self.save(conv)
        return conv

    def delete(self, cid):
        try:
            self._path(cid).unlink()
        except FileNotFoundError:
            pass


def _sse_events(resp):
    """Yield (event, data) pairs from a Server-Sent Events response."""
    event, data = None, []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith(":"):
            yield None, None  # keepalive: a chance to notice a stop
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())
    if data:
        yield event, "\n".join(data)


class HermesMax:
    """Max through Hermes Agent's API server (Responses API, streamed)."""

    def __init__(self, cfg, opener=None):
        self.url = cfg.get("url", "http://127.0.0.1:8642").rstrip("/")
        self.key_file = cfg.get("key_file")
        self.prefix = cfg.get("conversation_prefix", "hermes-app-")
        self.timeout = float(cfg.get("read_timeout", 180))
        self._open = opener or urllib.request.urlopen

    def _key(self):
        if not self.key_file:
            return None
        try:
            return Path(self.key_file).read_text(encoding="utf-8").strip() or None
        except OSError:
            raise ChatError(503, "Max's API key file isn't readable.")

    def open(self, cid, text):
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream",
                   "User-Agent": "hermes-app"}
        key = self._key()
        if key:
            headers["Authorization"] = "Bearer " + key
        body = {"input": text, "conversation": self.prefix + cid, "stream": True, "store": True}
        req = urllib.request.Request(self.url + "/v1/responses", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            return self._open(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            msg = {401: "Max refused the app's key.", 403: "Max refused the app's key.",
                   404: "This Hermes version has no chat endpoint."}.get(e.code, f"Max answered {e.code}.")
            raise ChatError(502, msg)
        except (urllib.error.URLError, OSError) as e:
            raise ChatError(502, f"Max isn't reachable ({type(e).__name__}).")

    @staticmethod
    def events(resp):
        """Turn Hermes's Responses stream into the app's small event shapes."""
        for event, data in _sse_events(resp):
            if event is None and data is None:
                yield {"type": "tick"}
                continue
            if data == "[DONE]":
                break
            try:
                d = json.loads(data)
            except ValueError:
                continue
            kind = event or d.get("type")
            if kind == "response.output_text.delta":
                yield {"type": "text", "delta": d.get("delta") or ""}
            elif kind in ("response.output_item.added", "response.output_item.done"):
                item = d.get("item") or {}
                if item.get("type") == "function_call" and kind.endswith("added"):
                    yield {"type": "tool", "id": item.get("call_id") or item.get("id"),
                           "name": item.get("name") or "tool", "args": item.get("arguments")}
                elif item.get("type") == "function_call_output":
                    out = item.get("output")
                    if not isinstance(out, str):
                        out = json.dumps(out)
                    yield {"type": "tool_done", "id": item.get("call_id"), "result": (out or "")[:400]}
            elif kind == "response.completed":
                yield {"type": "done"}
                return
            elif kind in ("response.failed", "error", "response.error"):
                err = (d.get("response") or {}).get("error") or d.get("error") or {}
                msg = err.get("message") if isinstance(err, dict) else str(err)
                yield {"type": "error", "message": msg or "Max hit an error."}
                return
        yield {"type": "done"}


class DemoMax:
    """Scripted replies for sample-data mode."""

    sample = True

    def __init__(self, artifact_dir=None):
        self.artifact_dir = artifact_dir

    def open(self, cid, text):
        return text

    def events(self, text):
        if self.artifact_dir and any(w in text.lower() for w in ("page", "doc", "artifact", "summary")):
            Path(self.artifact_dir, "sample-summary.md").write_text(
                "# Sample summary\n\nWritten by the sample-data Max for: *" + text[:80].replace("*", "") +
                "*\n\n- On the server, Max saves real pages and documents here.\n- They open in this panel.\n",
                encoding="utf-8")
        yield {"type": "tool", "id": "t1", "name": "search_notes", "args": json.dumps({"query": text[:40]})}
        time.sleep(0.4)
        yield {"type": "tool_done", "id": "t1", "result": "2 notes found"}
        reply = ("This is sample data, so I'm not really here. On the server, this chat "
                 "goes to me through Hermes, with the same sandbox and approvals as Signal.\n\n"
                 "- Your message was **" + str(len(text)) + "** characters.\n- Try `Stop` while I type.")
        for i in range(0, len(reply), 12):
            time.sleep(0.05)
            yield {"type": "text", "delta": reply[i:i + 12]}
        yield {"type": "done"}


class MaxChat:
    def __init__(self, cfg, backend=None, store=None, audit=None, artifacts=None):
        cfg = cfg or {}
        self.artifacts = artifacts
        self.store = store or ChatStore(cfg.get("store_dir", "/var/lib/hermes-app/chat"))
        self.backend = backend or HermesMax(cfg)
        self.kill_flag = cfg.get("kill_flag")
        self.audit = audit or (lambda *a, **k: None)
        self._stops = {}
        self._busy = set()
        self._lock = threading.Lock()

    def _check_kill(self):
        if self.kill_flag and Path(self.kill_flag).exists():
            raise ChatError(423, "Agents are stopped (kill switch on). Resume from your Mac first.")

    def list(self):
        return {"chats": self.store.list(), "busy": sorted(self._busy)}

    def get(self, cid):
        c = self.store.get(cid)
        c["busy"] = cid in self._busy
        return c

    def new(self):
        return self.store.new()

    def delete(self, user, cid):
        if cid in self._busy:
            raise ChatError(409, "Max is still answering in that chat.")
        self.store.delete(cid)
        self.audit(user, "chat_delete", {"chat": cid})
        return {"ok": True}

    def stop(self, user, cid):
        ev = self._stops.get(cid)
        if ev:
            ev.set()
            self.audit(user, "chat_stop", {"chat": cid})
        return {"ok": True, "stopping": bool(ev)}

    def begin(self, user, cid, text):
        """Validate and record the user's message; returns the open turn.

        Errors here are raised before any streaming starts, so the browser
        gets a normal JSON error.
        """
        text = str(text or "").strip()
        if not text:
            raise ChatError(400, "Type a message first.")
        if len(text) > MAX_INPUT:
            raise ChatError(400, f"Messages can be up to {MAX_INPUT} characters.")
        self._check_kill()
        conv = self.store.get(cid)
        with self._lock:
            if cid in self._busy:
                raise ChatError(409, "Max is still answering in this chat.")
            self._busy.add(cid)
            self._stops[cid] = threading.Event()
        try:
            conv["messages"].append({"role": "me", "text": text, "at": _now_iso()})
            if not conv.get("title"):
                conv["title"] = text.splitlines()[0][:60]
            self.store.save(conv)
            upstream = self.backend.open(cid, text)
        except BaseException:
            self._finish(cid)
            raise
        self.audit(user, "chat_send", {"chat": cid, "chars": len(text)})
        return conv, upstream

    def _finish(self, cid):
        with self._lock:
            self._busy.discard(cid)
            self._stops.pop(cid, None)

    def run(self, conv, upstream, emit):
        """Stream Max's reply. `emit` may raise when the browser has gone;
        the turn still runs to the end and is saved, so a phone that slept
        sees the whole answer when it comes back."""
        cid = conv["id"]
        started = time.time()
        stop = self._stops.get(cid) or threading.Event()
        reply = {"role": "max", "text": "", "at": _now_iso(), "tools": [], "status": "done"}
        tools = {}
        listening = True

        def send(ev):
            nonlocal listening
            if not listening:
                return
            try:
                emit(ev)
            except OSError:
                listening = False

        try:
            for ev in self.backend.events(upstream):
                if stop.is_set():
                    reply["status"] = "stopped"
                    break
                t = ev["type"]
                if t == "tick":
                    continue
                if t == "text":
                    reply["text"] += ev["delta"]
                elif t == "tool":
                    tool = {"name": ev["name"], "args": (ev.get("args") or "")[:1000], "result": None}
                    tools[ev.get("id")] = tool
                    reply["tools"].append(tool)
                elif t == "tool_done" and ev.get("id") in tools:
                    tools[ev["id"]]["result"] = ev["result"]
                elif t == "error":
                    reply["status"] = "error"
                    reply["error"] = ev["message"]
                elif t == "done":
                    break
                send(ev)
        except (socket.timeout, TimeoutError):
            reply["status"] = "error"
            reply["error"] = "Max went quiet for too long."
        except (OSError, urllib.error.URLError) as e:
            reply["status"] = "error"
            reply["error"] = f"The connection to Max dropped ({type(e).__name__})."
        finally:
            close = getattr(upstream, "close", None)
            if close:
                try:
                    close()
                except OSError:
                    pass
            if self.artifacts is not None:
                made = self.artifacts.since(started)
                if made:
                    reply["artifacts"] = made
                    send({"type": "artifacts", "names": made})
            try:
                latest = self.store.get(cid)
            except ChatError:
                latest = None  # deleted mid-turn: nothing to keep
            if latest is not None:
                latest["messages"].append(reply)
                self.store.save(latest)
            self._finish(cid)
        final = {"type": "end", "status": reply["status"]}
        if reply.get("error"):
            final["message"] = reply["error"]
        send(final)
        return reply
