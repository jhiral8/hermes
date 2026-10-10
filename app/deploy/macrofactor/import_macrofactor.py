"""Load Craig's MacroFactor export into NutriTrace: daily totals and weigh-ins.

Runs on the server with the NutriTrace write key. It reads the .xlsx read-only and
does nothing until --apply is given, and --apply refuses to run unless a backup of
NutriTrace's database has been taken first (--backup-dir must hold a non-empty
nutritrace*.db file).

What is loaded:
  - one food entry per day from "Calories & Macros": the day's total kcal, protein,
    carbs and fat, named "MacroFactor daily total (imported)".
  - one weigh-in per day from "Scale Weight".
What is not: per-item food history, custom foods and fasting. Days MacroFactor marked
as partly logged are skipped, and so is the last day in the export, which was taken
mid-day.

Idempotent: every write is recorded in a state file keyed by date, kind and value,
and a re-run skips anything already recorded. Requests are paced to stay under
NutriTrace's 60 calls a minute.
"""

import argparse
import datetime
import glob
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import openpyxl

FOOD_NAME = "MacroFactor daily total (imported)"
MAX_CALLS_PER_MINUTE = 60


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def read_export(path):
    """Days to load from the export: (totals, weights, last day skipped, partly logged days skipped)."""
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = lambda name: list(wb[name].iter_rows(values_only=True))[1:]
    totals, last_day = {}, None
    for r in rows("Calories & Macros"):
        if not isinstance(r[0], datetime.datetime):
            continue
        d = r[0].date()
        last_day = max(last_day, d) if last_day else d
        kcal = _num(r[1])
        if kcal is None:
            continue
        totals[d] = {"calories": kcal, "fat": _num(r[2]), "carbohydrates": _num(r[3]), "proteins": _num(r[4])}
    partial = set()
    for r in rows("Partial Logging"):  # days MacroFactor marked as only partly logged
        if r[1] == "Yes" and r[0]:
            partial.add(datetime.datetime.strptime(str(r[0]), "%d/%m/%Y").date())
    for d in partial & set(totals):
        del totals[d]
    weights = {}
    for r in rows("Scale Weight"):
        if isinstance(r[0], datetime.datetime) and _num(r[1]) is not None:
            weights[r[0].date()] = _num(r[1])
    skipped = None
    if last_day in totals:
        skipped = last_day
        del totals[last_day]  # taken mid-day: the total would be short
    return totals, weights, skipped, sorted(partial)


def plan(totals, weights):
    """Every write the import would make, in order, each with a stable key."""
    out = []
    for d in sorted(totals):
        t = totals[d]
        body = {"name": FOOD_NAME, "source": "macrofactor-import", "quantity": 1, "unit": "day",
                "nutrition": {k: v for k, v in t.items() if v is not None}}
        out.append({"kind": "food", "date": d.isoformat(), "body": body,
                    "key": _key("food", d, json.dumps(body["nutrition"], sort_keys=True))})
    for d in sorted(weights):
        out.append({"kind": "weight", "date": d.isoformat(), "body": {"weight": weights[d]},
                    "key": _key("weight", d, str(weights[d]))})
    return out


def _key(kind, d, value):
    return hashlib.sha256(f"{kind}|{d.isoformat()}|{value}".encode()).hexdigest()[:20]


class Client:
    def __init__(self, base, key, opener=None, pause=1.1, sleep=time.sleep):
        self.base = base.rstrip("/") + "/api/v1"
        self.key = key
        self._open = opener or urllib.request.urlopen
        self.pause = pause
        self._sleep = sleep
        self._last = 0.0

    def write(self, item):
        """PUT for weight, POST for food. Returns the HTTP status."""
        if item["kind"] == "food":
            method, path = "POST", f"/diary/{item['date']}/food"
        else:
            method, path = "PUT", f"/diary/{item['date']}/body-stat"
        wait = self.pause - (time.time() - self._last)
        if wait > 0:
            self._sleep(wait)
        req = urllib.request.Request(self.base + path, method=method,
                                     data=json.dumps(item["body"]).encode(),
                                     headers={"Authorization": "Bearer " + self.key,
                                              "Content-Type": "application/json", "Accept": "application/json"})
        self._last = time.time()
        try:
            with self._open(req, timeout=15) as r:
                return r.status
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"NutriTrace answered {e.code} for {item['kind']} on {item['date']}")
        except (urllib.error.URLError, OSError):
            raise RuntimeError(f"NutriTrace is unreachable ({item['kind']} on {item['date']})")


def check_backup(backup_dir):
    files = glob.glob(os.path.join(backup_dir, "nutritrace*.db"))
    if not any(os.path.getsize(f) > 0 for f in files):
        raise SystemExit("Refusing to apply: no non-empty nutritrace*.db backup in " + backup_dir)
    return max(files, key=os.path.getmtime)


def load_state(path):
    try:
        return set(json.loads(Path(path).read_text(encoding="utf-8")))
    except FileNotFoundError:
        return set()


def save_state(path, done):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(sorted(done)), encoding="utf-8")
    tmp.replace(path)


def run(args, client=None, out=sys.stdout):
    totals, weights, skipped, partial = read_export(args.export)
    items = plan(totals, weights)
    done = load_state(args.state)
    todo = [i for i in items if i["key"] not in done]
    counts = {k: sum(1 for i in todo if i["kind"] == k) for k in ("food", "weight")}
    print(f"Export: {len(totals)} days of totals, {len(weights)} weigh-ins; "
          f"skipped {len(partial)} partly logged day(s)" + (f" and {skipped} (taken mid-day)" if skipped else ""), file=out)
    print(f"Already loaded: {len(items) - len(todo)}. To write now: {counts['food']} food entries, "
          f"{counts['weight']} weigh-ins.", file=out)
    if not args.apply:
        print("Dry run. Nothing was written. Add --apply after the backup to load.", file=out)
        for i in todo[:5]:
            print(f"  {i['kind']:6} {i['date']}  {json.dumps(i['body'])}", file=out)
        return 0
    backup = check_backup(args.backup_dir)
    print(f"Backup found: {backup}", file=out)
    client = client or Client(args.base, Path(args.key_file).read_text(encoding="utf-8").strip(), pause=args.pause)
    for n, i in enumerate(todo, 1):
        client.write(i)
        done.add(i["key"])
        save_state(args.state, done)  # after each write, so a stop mid-run never double-loads
        if n % 10 == 0:
            print(f"  {n}/{len(todo)} written", file=out)
    print(f"Done. {len(todo)} written, {len(items) - len(todo)} were already there.", file=out)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("export", help="path to the MacroFactor .xlsx (read only)")
    ap.add_argument("--base", default="http://127.0.0.1:3001", help="NutriTrace address")
    ap.add_argument("--key-file", default="/etc/hermes-app/health/nutritrace-import.key",
                    help="a NutriTrace key with mcp:write")
    ap.add_argument("--state", default="macrofactor-import.state.json", help="record of what was written")
    ap.add_argument("--backup-dir", default="/var/backups/nutritrace", help="where the database backup is")
    ap.add_argument("--pause", type=float, default=60.0 / MAX_CALLS_PER_MINUTE + 0.1)
    ap.add_argument("--apply", action="store_true", help="write to NutriTrace (default is a dry run)")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
