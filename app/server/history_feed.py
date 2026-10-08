#!/usr/bin/env python3
"""Builds the expenditure estimator's day feed from MacroFactor history and NutriTrace.

The export history comes from export_history.py (MacroFactor's .xlsx, saved as JSON
once, so the app never needs the spreadsheet library). For each day from the first
MacroFactor day up to today, the feed takes:
  - NutriTrace's totals, when NutriTrace has logged anything that day (NutriTrace wins);
  - otherwise MacroFactor's total for the day, marked complete or partial as exported;
  - otherwise "missing".
Weigh-ins come from MacroFactor. NutriTrace's read-only tokens can't read weight
history, so new weigh-ins won't show until they can.

Then the estimator runs on that feed and writes the same small estimate file the
app already reads. Health data stays on this server: nothing here goes to Max or a model.

Usage (nightly, after the MacroFactor export is refreshed):
  python3 history_feed.py --history /var/lib/hermes-app/macrofactor-history.json \
      --nutritrace-url http://127.0.0.1:3001 --key-file /etc/hermes-app/health/nutritrace.key \
      --days-out /var/lib/hermes-app-feed/health-days.json \
      --estimator-out /var/lib/hermes-app-feed/estimator.json
"""

import argparse
import datetime
import json
import sys
import time
from pathlib import Path

import estimate_feed
from health import TraceApp, nutrients
from sources import SourceError


DEFAULT_MODULE = str(Path(__file__).resolve().parent / "estimator")


def london_today():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("Europe/London")).date()


def load_history(path):
    """MacroFactor history: {"days": [{"date", "kcal", "status"}], "weights": {"date": kg}}."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    days = {d["date"]: {"kcal": d["kcal"], "status": d["status"]} for d in data.get("days", [])}
    weights = {k: float(v) for k, v in (data.get("weights") or {}).items()}
    return days, weights


def build_days(history, nt_days, today):
    """The estimator's day records, from the first MacroFactor day to today.

    nt_days: {"YYYY-MM-DD": {"kcal": float or None, "items": int}} from NutriTrace.
    """
    mf_days, weights = history
    if not mf_days:
        return []
    start = min(datetime.date.fromisoformat(d) for d in mf_days)
    out, d = [], start
    while d <= today:
        key = d.isoformat()
        nt = nt_days.get(key)
        mf = mf_days.get(key)
        if nt and nt.get("items"):
            status = "partial" if d == today else "complete"
            intake = nt.get("kcal")
        elif mf:
            status = "partial" if d == today else mf["status"]
            intake = mf["kcal"]
        else:
            status, intake = "missing", None
        out.append({"date": key, "intake": intake, "status": status, "weight": weights.get(key)})
        d += datetime.timedelta(days=1)
    return out


def fetch_nutritrace(url, key_file, start, end, pause=1.1, sleep=time.sleep, timeout=6):
    """NutriTrace totals for each day in the range. A day that can't be read is left out."""
    app = TraceApp("NutriTrace", {"url": url, "key_file": key_file, "timeout": timeout})
    out, d, first = {}, start, True
    while d <= end:
        if not first:
            sleep(pause)  # NutriTrace allows 60 calls a minute
        first = False
        try:
            t = app.get(f"/diary/{d.isoformat()}/totals", ttl=0)
            out[d.isoformat()] = {"kcal": nutrients((t or {}).get("totals") or {})["kcal"],
                                  "items": (t or {}).get("item_count") or 0}
        except SourceError:
            pass
        d += datetime.timedelta(days=1)
    return out


def run(history_path, nutritrace_url, key_file, days_out, estimator_out, today=None, module_dir=None,
        fetch=fetch_nutritrace):
    history = load_history(history_path)
    mf_days, _ = history
    if not mf_days:
        raise SystemExit("The MacroFactor history file has no days.")
    today = today or london_today()
    start = min(datetime.date.fromisoformat(d) for d in mf_days)
    nt_days = fetch(nutritrace_url, key_file, start, today) if nutritrace_url else {}
    days = build_days(history, nt_days, today)
    p = Path(days_out)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(days), encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(p)
    result = estimate_feed.run(days, module_dir or DEFAULT_MODULE, today=today)
    estimate_feed.write_atomic(estimator_out, result)
    return {"days": len(days), "nutritrace_days": sum(1 for v in nt_days.values() if v.get("items")),
            "estimate": result}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--history", required=True, help="MacroFactor history JSON from export_history.py")
    ap.add_argument("--nutritrace-url", default="http://127.0.0.1:3001", help="NutriTrace address; empty to skip")
    ap.add_argument("--key-file", default="/etc/hermes-app/health/nutritrace.key", help="the read-only key")
    ap.add_argument("--days-out", required=True, help="where to write the day feed")
    ap.add_argument("--estimator-out", required=True, help="where to write the estimate the app reads")
    ap.add_argument("--module-dir", default=None, help="folder holding estimator.py (default: the app's copy)")
    args = ap.parse_args(argv)
    res = run(args.history, args.nutritrace_url, args.key_file, args.days_out, args.estimator_out,
              module_dir=args.module_dir)
    print(f"Day feed: {res['days']} days, {res['nutritrace_days']} from NutriTrace. "
          f"Estimate held: {res['estimate'].get('held')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
