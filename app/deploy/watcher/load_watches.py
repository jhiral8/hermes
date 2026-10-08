#!/usr/bin/env python3
"""Add watches.json to changedetection through its local API. Skips URLs
already present. Standard library only.
Usage: python3 load_watches.py [--key-file /etc/hermes-watch/cd-api.key] [--url http://127.0.0.1:5000]
"""
import argparse, json, urllib.request
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://127.0.0.1:5000")
ap.add_argument("--key-file", default="/etc/hermes-watch/cd-api.key")
ap.add_argument("--file", default=str(Path(__file__).with_name("watches.json")))
a = ap.parse_args()
key = Path(a.key_file).read_text().strip()

def call(method, path, body=None):
    req = urllib.request.Request(a.url.rstrip("/") + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"x-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        t = r.read().decode()
        return json.loads(t) if t.strip().startswith(("{", "[")) else t

have = {w.get("url") for w in (call("GET", "/api/v1/watch") or {}).values()}
for w in json.loads(Path(a.file).read_text()):
    if w["url"] in have:
        print("exists ", w["title"]); continue
    call("POST", "/api/v1/watch", {"url": w["url"], "title": w["title"], "tag": w.get("tag", ""),
                                   "time_between_check": {"hours": w.get("check_hours", 12)}})
    print("added  ", w["title"])
