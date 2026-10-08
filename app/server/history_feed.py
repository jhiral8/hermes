#!/usr/bin/env python3
"""Builds the expenditure estimator's day feed from MacroFactor history and NutriTrace.

The MacroFactor history is read straight from MacroFactor's .xlsx export on the
server (openpyxl). Partly logged days, and the export's own last day, are marked
partial. For each day from the first
MacroFactor day up to today, the feed takes:
  - NutriTrace's totals, when NutriTrace has logged anything that day (NutriTrace wins);
  - otherwise MacroFactor's total for the day, marked complete or partial as exported;
  - otherwise "missing".
Weigh-ins come from MacroFactor, and from NutriTrace on any day it has one. NutriTrace
can be read two ways: through its API with the read-only key (food totals only, since
the key can't read weight history), or from its database file, opened read-only
(food totals and weigh-ins, with no key). The database way is the one the nightly
timer uses.

Then the estimator runs on that feed and writes the same small estimate file the
app already reads. Health data stays on this server: nothing here goes to Max or a model.

Usage (nightly):
  python3 history_feed.py --history /home/health/imports/macrofactor/MacroFactor-20261007134323.xlsx \
      --nutritrace-url http://127.0.0.1:3001 --key-file /etc/hermes-app/health/nutritrace.key \
      --days-out /var/lib/hermes-history/health-days.json \
      --estimator-out /var/lib/hermes-history/estimator.json
"""

import argparse
import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path

import openpyxl

import estimate_feed
from health import TraceApp, nutrients
from sources import SourceError


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_export(path):
    """MacroFactor's own history from the .xlsx: ({date: {kcal, status}}, {date: kg}).

    Days marked "Partial Logging", and the export's last day, are partial. Days
    with no total are left out. Read-only.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = lambda name: list(wb[name].iter_rows(values_only=True))[1:]
    totals = {}
    for r in rows("Calories & Macros"):
        if isinstance(r[0], datetime.datetime) and _num(r[1]) is not None:
            totals[r[0].date()] = _num(r[1])
    partial = set()
    for r in rows("Partial Logging"):
        if r[1] == "Yes" and r[0]:
            partial.add(datetime.datetime.strptime(str(r[0]), "%d/%m/%Y").date())
    weights = {}
    for r in rows("Scale Weight"):
        if isinstance(r[0], datetime.datetime) and _num(r[1]) is not None:
            weights[r[0].date().isoformat()] = _num(r[1])
    export_day = max(totals) if totals else None
    days = {d.isoformat(): {"kcal": totals[d],
                            "status": "partial" if d in partial or d == export_day else "complete"}
            for d in totals}
    return days, weights


DEFAULT_MODULE = str(Path(__file__).resolve().parent / "estimator")


def london_today():
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("Europe/London")).date()


def load_history(path):
    """MacroFactor history from the export: ({date: {kcal, status}}, {date: kg})."""
    return read_export(path)


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


def _kcal_of(items):
    """Total kcal of one day's diary items. The item layout isn't confirmed on the live
    database yet, so this reads a per-item nutrition object and skips anything else."""
    total, seen = 0.0, False
    for it in items:
        nut = it.get("nutrition") if isinstance(it, dict) else None
        if not isinstance(nut, dict):
            continue
        for k in ("calories", "kcal", "energy"):
            v = nut.get(k)
            if isinstance(v, (int, float)):
                total += float(v)
                seen = True
                break
    return round(total, 1) if seen else None


LB_TO_KG = 0.45359237


def _weight_of(body_stats):
    """Weight in kg from one day's body_stats. NutriTrace writes "weight" with a
    "weight_unit" of kg (the default) or lb."""
    try:
        stats = json.loads(body_stats) if isinstance(body_stats, str) else (body_stats or {})
    except ValueError:
        return None
    if not isinstance(stats, dict):
        return None
    v = stats.get("weight")
    if not isinstance(v, (int, float)):
        return None
    return float(v) * LB_TO_KG if stats.get("weight_unit") == "lb" else float(v)


def read_nutritrace_db(path, start, end):
    """NutriTrace's own daily totals and weigh-ins, read from its database file.

    Opened read-only (SQLite mode=ro), so nothing is written and no key is needed.
    Returns ({date: {"kcal", "items"}}, {date: kg}).
    """
    uri = Path(path).resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=5)
    try:
        days, weights = {}, {}
        bounds = (start.isoformat(), end.isoformat())
        for date, items, body_stats in con.execute(
                "SELECT date, items, body_stats FROM diary WHERE date BETWEEN ? AND ? AND deleted_at IS NULL",
                bounds):
            try:
                rows = json.loads(items) if isinstance(items, str) else (items or [])
            except ValueError:
                rows = []
            rows = rows if isinstance(rows, list) else []
            days[date] = {"kcal": _kcal_of(rows), "items": len(rows)}
            w = _weight_of(body_stats)
            if w is not None:
                weights[date] = w
        for date, value in con.execute(
                "SELECT date, value FROM wellness_data WHERE metric_type = 'weight_kg' "
                "AND date BETWEEN ? AND ? ORDER BY synced_at", bounds):
            if isinstance(value, (int, float)):
                weights[date] = float(value)  # the later sync wins
        return days, weights
    finally:
        con.close()


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
        fetch=fetch_nutritrace, nutritrace_db=None):
    mf_days, mf_weights = load_history(history_path)
    if not mf_days:
        raise SystemExit("The MacroFactor export has no days.")
    today = today or london_today()
    start = min(datetime.date.fromisoformat(d) for d in mf_days)
    if nutritrace_db:
        nt_days, nt_weights = read_nutritrace_db(nutritrace_db, start, today)
    else:
        nt_days = fetch(nutritrace_url, key_file, start, today) if nutritrace_url else {}
        nt_weights = {}
    # NutriTrace wins on any day it has logged, for both food and weight.
    days = build_days((mf_days, {**mf_weights, **nt_weights}), nt_days, today)
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
    ap.add_argument("--history", required=True, help="MacroFactor's .xlsx export (read only)")
    ap.add_argument("--nutritrace-url", default="http://127.0.0.1:3001", help="NutriTrace address; empty to skip")
    ap.add_argument("--key-file", default="/etc/hermes-app/health/nutritrace.key", help="the read-only key")
    ap.add_argument("--days-out", required=True, help="where to write the day feed")
    ap.add_argument("--estimator-out", required=True, help="where to write the estimate the app reads")
    ap.add_argument("--module-dir", default=None, help="folder holding estimator.py (default: the app's copy)")
    ap.add_argument("--nutritrace-db", default=None,
                    help="NutriTrace's database file, opened read-only; used instead of the API when given")
    args = ap.parse_args(argv)
    res = run(args.history, args.nutritrace_url, args.key_file, args.days_out, args.estimator_out,
              module_dir=args.module_dir, nutritrace_db=args.nutritrace_db)
    print(f"Day feed: {res['days']} days, {res['nutritrace_days']} from NutriTrace. "
          f"Estimate held: {res['estimate'].get('held')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
