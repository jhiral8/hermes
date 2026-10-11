"""Write the approval broker's request list to a small feed file for the app.

The broker's database is private to the broker user, and the app must not be
given read access to it (it holds full email payloads). So this runs as the
broker user on a one-minute timer, reads only the columns the app shows
(through a read-only connection and the queries in its config), and writes
them to a feed file the app's group can read. It sends nothing and changes
nothing in the broker.

    python3 broker_export.py --config /etc/hermes-app/broker-export.json
"""

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

from sources import Broker, SourceError


def export(cfg, now=None):
    b = Broker({"sqlite_path": cfg["sqlite_path"], "pending_sql": cfg["pending_sql"],
                "history_sql": cfg.get("history_sql")})
    data = {"at": now if now is not None else time.time(),
            "pending": b.query(b.pending_sql),
            "history": b.query(b.history_sql) if b.history_sql else []}
    out = Path(cfg["out"])
    fd, tmp = tempfile.mkstemp(dir=out.parent, prefix=".broker-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.chmod(tmp, 0o640)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return data


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    args = ap.parse_args(argv)
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    try:
        data = export(cfg)
    except SourceError as e:
        print(f"broker export failed: {e}", file=sys.stderr)
        return 1
    print(f"exported {len(data['pending'])} pending, {len(data['history'])} decided")
    return 0


if __name__ == "__main__":
    sys.exit(main())
