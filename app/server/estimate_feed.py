#!/usr/bin/env python3
"""Runs the expenditure estimator on the day feed and writes the app's file.

Input (written by the NutriTrace reader, see health.py): a JSON list of day
records, one per calendar day, in the estimator's own format:
    {"date": "2026-10-07", "intake": 2150 or null, "status": "complete",
     "weight": 80.4 or null}

Output: the small JSON that health.py's estimate() reads. Only the estimate
and the trend leave this script: no day records, no food names.

The estimator is Craig's expenditure code, copied into estimator/ in this app
so the server runs the same tested file.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path


def load_estimator(module_dir):
    spec = importlib.util.spec_from_file_location("estimator", Path(module_dir) / "estimator.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["estimator"] = mod  # dataclasses look the module up by name
    spec.loader.exec_module(mod)
    return mod


def run(days, module_dir, today=None):
    est = load_estimator(module_dir)
    if not days:
        return {"ok": False, "reason": "no days in the feed yet"}
    last = max(datetime.date.fromisoformat(d["date"]) for d in days)
    today = today or last
    e = est.estimate_on(days, today)
    out = {"as_of": today.isoformat(), "held": bool(e.held), "reason": e.reason,
           "intake_days": e.intake_days, "weigh_ins": e.weigh_ins,
           "expenditure": None if e.expenditure is None else round(e.expenditure),
           "trend_kg": None if e.trend_kg is None else round(e.trend_kg, 2),
           "weekly_change_kg": None if e.trend_kg_per_week is None else round(e.trend_kg_per_week, 2)}
    return out


def write_atomic(path, obj):
    p = Path(path)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".estimator-")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o640)
    os.replace(tmp, p)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", required=True, help="the day feed (JSON list)")
    ap.add_argument("--module-dir", default=str(Path(__file__).resolve().parent / "estimator"),
                    help="folder holding estimator.py (defaults to the copy in this app)")
    ap.add_argument("--out", required=True, help="where to write the estimate JSON")
    args = ap.parse_args(argv)
    days = json.loads(Path(args.days).read_text(encoding="utf-8"))
    write_atomic(args.out, run(days, args.module_dir))


if __name__ == "__main__":
    sys.exit(main())
