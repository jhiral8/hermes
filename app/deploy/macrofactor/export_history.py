#!/usr/bin/env python3
"""Turns MacroFactor's .xlsx export into the JSON history the app's day feed reads.

Run once after each new export (needs openpyxl). Writes:
  {"days": [{"date": "YYYY-MM-DD", "kcal": 2037, "status": "complete" | "partial"}],
   "weights": {"YYYY-MM-DD": 86.0}}
Partly logged days (MacroFactor's "Partial Logging" sheet) and the export day
itself are marked partial. Fasting days keep their totals. Days with no total
are left out. Nothing else from the export is written.

Usage: python3 export_history.py <export.xlsx> <history.json>
"""

import datetime
import json
import sys
from pathlib import Path

import openpyxl


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def export(path):
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
    days = []
    for d in sorted(totals):
        status = "partial" if d in partial or d == export_day else "complete"
        days.append({"date": d.isoformat(), "kcal": totals[d], "status": status})
    return {"days": days, "weights": dict(sorted(weights.items()))}


def main(argv=None):
    if len(argv or sys.argv[1:]) != 2:
        print(__doc__)
        return 2
    src, dst = (argv or sys.argv[1:])
    data = export(src)
    p = Path(dst)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.chmod(0o640)
    tmp.replace(p)
    print(f"{len(data['days'])} days, {len(data['weights'])} weigh-ins written to {dst}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
