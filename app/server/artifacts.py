"""Artifacts: documents and pages Max makes, shown in the app's side panel.

Max saves each one as a single file in his artifacts folder (on the host, a
folder his sandbox can write and the app can read). The app only reads that
folder: plain file names, a short list of text types, no subfolders or links,
and a size limit. Pages are previewed in the browser in a sandbox with scripts
and network turned off, so a page Max made can't act on the app.
"""

import html
import re
import time
from pathlib import Path

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}")
TYPES = {
    ".html": ("html", "HTML page"), ".htm": ("html", "HTML page"),
    ".md": ("md", "Document"), ".txt": ("text", "Text"),
    ".svg": ("svg", "SVG image"), ".csv": ("csv", "Table"), ".json": ("json", "Data"),
}
MAX_BYTES = 2 * 1024 * 1024


class ArtifactError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _title(kind, text, fallback):
    if kind == "html":
        m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S) or re.search(r"<h1[^>]*>(.*?)</h1>", text, re.I | re.S)
        if m:
            t = html.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            if t:
                return t[:120]
    if kind == "md":
        for line in text.splitlines():
            if line.startswith("# "):
                return line[2:].strip()[:120]
    return fallback


class Artifacts:
    def __init__(self, folder):
        self.dir = Path(folder)

    def _path(self, name):
        if not NAME.fullmatch(name or "") or Path(name).suffix.lower() not in TYPES:
            raise ArtifactError(400, "Not an artifact name.")
        p = self.dir / name
        if p.is_symlink() or not p.is_file():
            raise ArtifactError(404, "That artifact isn't there any more.")
        return p

    def _files(self):
        try:
            entries = list(self.dir.iterdir())
        except OSError:
            raise ArtifactError(503, "The artifacts folder isn't readable.")
        for p in entries:
            if NAME.fullmatch(p.name) and p.suffix.lower() in TYPES and not p.is_symlink() and p.is_file():
                yield p

    def list(self):
        out = []
        for p in self._files():
            st = p.stat()
            kind, label = TYPES[p.suffix.lower()]
            head = ""
            if kind in ("html", "md"):
                try:
                    with open(p, encoding="utf-8", errors="replace") as f:
                        head = f.read(4096)
                except OSError:
                    continue
            out.append({"name": p.name, "title": _title(kind, head, p.stem.replace("-", " ").replace("_", " ")),
                        "kind": kind, "label": label, "size": st.st_size,
                        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime)),
                        "mtime": st.st_mtime})
        out.sort(key=lambda a: a["mtime"], reverse=True)
        return out

    def get(self, name):
        p = self._path(name)
        st = p.stat()
        if st.st_size > MAX_BYTES:
            raise ArtifactError(413, "That artifact is too big to show here.")
        kind, label = TYPES[p.suffix.lower()]
        text = p.read_text(encoding="utf-8", errors="replace")
        return {"name": p.name, "title": _title(kind, text, p.stem), "kind": kind, "label": label,
                "size": st.st_size, "text": text,
                "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}

    def since(self, t):
        """Names changed at or after time t (a turn's start), newest first."""
        try:
            return [a["name"] for a in self.list() if a["mtime"] >= t - 1][:8]
        except ArtifactError:
            return []
