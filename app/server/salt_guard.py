#!/usr/bin/env python3
"""Delete blocker for salt.md: a small proxy the agents' MCP and REST calls go through.

salt's own tokens are only read or read-write, so "write but not delete" needs a
gate in front of it. This proxy refuses, by name, the MCP tools that remove
things and any HTTP DELETE. Everything else is passed through unchanged, and
the upstream response (including streamed responses) is returned as it is.

Modes (--mode):
  trash-counts  refuse delete_view, delete_comment and set_trashed (moving to trash)
  trash-allowed refuse delete_view and delete_comment only; set_trashed stays

A refusal is a JSON-RPC error that names the blocked tool, so the agent can tell
Craig, and it is written to the audit log. Restoring from trash is always allowed.
"""

import argparse
import http.server
import json
import socketserver
import sys
import time
import urllib.error
import urllib.request

ALWAYS_BLOCKED = {"delete_view", "delete_comment"}
TRASH_TOOL = "set_trashed"
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers",
       "transfer-encoding", "upgrade", "host", "content-length"}


def blocked_tools(mode):
    return ALWAYS_BLOCKED | ({TRASH_TOOL} if mode == "trash-counts" else set())


def refused_calls(body, mode):
    """Names of blocked tool calls in a JSON-RPC body (one message or a batch)."""
    try:
        msgs = json.loads(body or b"null")
    except ValueError:
        return []
    msgs = msgs if isinstance(msgs, list) else [msgs]
    out = []
    for m in msgs:
        if not isinstance(m, dict) or m.get("method") != "tools/call":
            continue
        name = (m.get("params") or {}).get("name")
        if name in blocked_tools(mode):
            # set_trashed with trash=false restores a page: always allowed.
            args = (m.get("params") or {}).get("arguments") or {}
            if name == TRASH_TOOL and args.get("trashed") is False:
                continue
            out.append((m.get("id"), name))
    return out


def jsonrpc_refusal(msg_id, name):
    return json.dumps({"jsonrpc": "2.0", "id": msg_id, "error": {
        "code": -32001,
        "message": f"Blocked by Craig's setting: agents can't use {name} here. Ask Craig to do it in salt."}}).encode()


def make_handler(upstream, mode, audit):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "salt-guard"
        sys_version = ""

        def log_message(self, fmt, *args):  # the audit log below is the record
            pass

        def _audit(self, outcome, detail=""):
            if audit:
                with open(audit, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"t": int(time.time()), "path": self.path, "outcome": outcome,
                                        "detail": detail}) + "\n")  # never the token

        def _refuse(self, code, body, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _forward(self, method, body):
            url = upstream.rstrip("/") + self.path
            headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
            req = urllib.request.Request(url, data=body, method=method, headers=headers)
            try:
                resp = urllib.request.urlopen(req, timeout=120)
            except urllib.error.HTTPError as e:
                resp = e
            except (urllib.error.URLError, OSError):
                self._refuse(502, b'{"error":"salt is unreachable"}')
                return
            self.send_response(resp.status if hasattr(resp, "status") else resp.code)
            for k, v in resp.headers.items():
                if k.lower() not in HOP:
                    self.send_header(k, v)
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                while True:
                    chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    self.wfile.flush()
            finally:
                resp.close()
            self.close_connection = True

        def do_DELETE(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._audit("refused", "DELETE")
            self._refuse(403, b'{"error":"Deleting is blocked for agents."}')

        def _body(self):
            n = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(n) if n else b""

        def do_POST(self):
            body = self._body()
            bad = refused_calls(body, mode)
            if bad:
                for msg_id, name in bad:
                    self._audit("refused", name)
                # Answer with the refusal rather than sending anything upstream.
                # A batch gets a batch back; a single message gets one refusal.
                if isinstance(json.loads(body), list):
                    self._refuse(200, json.dumps([json.loads(jsonrpc_refusal(i, n)) for i, n in bad]).encode())
                else:
                    self._refuse(200, jsonrpc_refusal(bad[0][0], bad[0][1]))
                return
            self._audit("allowed")
            self._forward("POST", body)

        def do_GET(self):
            self._audit("allowed", "GET")
            self._forward("GET", None)

        def do_PUT(self):
            body = self._body()
            self._audit("allowed", "PUT")
            self._forward("PUT", body)

        def do_PATCH(self):
            body = self._body()
            self._audit("allowed", "PATCH")
            self._forward("PATCH", body)

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--upstream", required=True, help="salt's own address, e.g. http://127.0.0.1:8420")
    ap.add_argument("--listen", default="127.0.0.1:11100")
    ap.add_argument("--mode", choices=["trash-counts", "trash-allowed"], required=True)
    ap.add_argument("--audit", default="", help="append-only JSON-lines audit file")
    args = ap.parse_args(argv)
    host, port = args.listen.rsplit(":", 1)
    srv = Server((host, int(port)), make_handler(args.upstream, args.mode, args.audit))
    print(f"salt-guard ({args.mode}) on {args.listen} -> {args.upstream}", file=sys.stderr, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
