"""Max's memory and skills, for the app's Library (read-only).

Max's home folder is private to the hermes user, and the app must not be
given read access to it (it holds his keys and sessions). So, like the broker
feed, a small exporter runs as the hermes user on a timer, reads only his two
memory files and his skills' SKILL.md files, and writes them to a feed file
the app's group can read. It changes nothing in Max's folder.

    python3 max_library.py --home /home/hermes/.hermes --out /var/lib/hermes-app-library/library.json

The app reads that feed with MaxLibrary. A remembered preference isn't a
permission: nothing here grants Max anything.
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

MEMORY_FILES = (("MEMORY.md", "Max's notes"), ("USER.md", "About you"))
MAX_SKILLS = 300
MAX_SKILL_BYTES = 64 * 1024
MAX_MEMORY_BYTES = 256 * 1024
STALE_AFTER = 30 * 60


def _read(p, limit):
    if p.is_symlink() or not p.is_file():
        return None
    with open(p, encoding="utf-8", errors="replace") as f:
        return f.read(limit)


def memory_entries(text):
    """Hermes keeps one entry per block, separated by a line holding only "§"."""
    parts = re.split(r"(?m)^\s*§\s*$", text) if "§" in text else re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def frontmatter(text):
    m = re.match(r"---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        k = re.match(r"([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if k and not line.startswith((" ", "\t")):
            meta[k.group(1).lower()] = k.group(2).strip().strip("'\"")
    return meta, text[m.end():]


def export(home, now=None):
    home = Path(home)
    memory = []
    for fname, label in MEMORY_FILES:
        text = _read(home / "memories" / fname, MAX_MEMORY_BYTES)
        for i, e in enumerate(memory_entries(text or "")):
            memory.append({"id": f"{fname[:-3].lower()}-{i + 1}", "store": label, "text": e})
    skills = []
    root = home / "skills"
    if root.is_dir() and not root.is_symlink():
        for dirpath, dirs, files in os.walk(root):  # doesn't follow links
            dirs[:] = sorted(d for d in dirs if not d.startswith("."))
            if "SKILL.md" not in files or len(skills) >= MAX_SKILLS:
                continue
            p = Path(dirpath) / "SKILL.md"
            text = _read(p, MAX_SKILL_BYTES)
            if text is None:
                continue
            meta, _ = frontmatter(text)
            rel = p.parent.relative_to(root).as_posix()
            skills.append({"id": rel, "name": meta.get("name") or p.parent.name,
                           "description": meta.get("description", ""), "version": meta.get("version", ""),
                           "category": rel.split("/")[0] if "/" in rel else "",
                           "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(p.stat().st_mtime)),
                           "source": text})
    skills.sort(key=lambda s: (s["category"], s["name"].lower()))
    return {"at": now if now is not None else time.time(), "memory": memory, "skills": skills}


def write(data, out):
    out = Path(out)
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".library-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o640)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class LibraryError(Exception):
    pass


class MaxLibrary:
    """Reads the exporter's feed file."""

    def __init__(self, feed, clock=time.time):
        self.feed, self.clock = Path(feed), clock

    def get(self):
        try:
            data = json.loads(self.feed.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise LibraryError("Max's memory and skills aren't being shared with the app yet")
        except (OSError, ValueError):
            raise LibraryError("Max's memory and skills feed isn't readable")
        age = self.clock() - float(data.get("at") or 0)
        data["stale"] = age > STALE_AFTER
        data["age_min"] = max(0, int(age // 60))
        return data


class SampleLibrary:
    def get(self):
        skill = "---\nname: {n}\ndescription: {d}\nversion: 1.0.0\n---\n\n# {n}\n\n{d}\n"
        return {"at": time.time(), "stale": False, "age_min": 2, "memory": [
            {"id": "memory-1", "store": "Max's notes", "text": "Craig's server is hermes-oracle (Oracle, London). Approvals need his fingerprint."},
            {"id": "memory-2", "store": "Max's notes", "text": "Weekly digest goes out Monday 8am on Signal."},
            {"id": "user-1", "store": "About you", "text": "Craig prefers short answers that lead with the result."},
            {"id": "user-2", "store": "About you", "text": "Lives in the UK; use pounds, kilograms and Europe/London times."}],
            "skills": [dict(id=i, name=n, description=d, version="1.0.0", category=c, updated="2026-10-08T09:40:00Z",
                            source=skill.format(n=n, d=d)) for i, n, d, c in [
                ("artifacts", "artifacts", "Save pages, documents and tables to the app's Files panel.", ""),
                ("research/arxiv", "arxiv", "Search and summarise papers from arXiv.", "research"),
                ("productivity/google-workspace", "google-workspace", "Read the test mailbox; sending goes through the broker.", "productivity")]]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", required=True, help="Max's Hermes folder, e.g. /home/hermes/.hermes")
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    data = export(a.home)
    write(data, a.out)
    print(f"library: {len(data['memory'])} memory entries, {len(data['skills'])} skills", file=sys.stderr)


if __name__ == "__main__":
    main()
