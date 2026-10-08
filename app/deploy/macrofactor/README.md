> **Superseded.** Don't follow these steps. The import needs a NutriTrace write key
> and a NutriTrace setting change, and Craig chose to read the MacroFactor export
> directly instead. The steps are in HISTORY.md.

# Import the MacroFactor export into NutriTrace

Loads Craig's MacroFactor export into NutriTrace: one daily-total food entry per
day, and one weigh-in per day. Dry run first. Nothing is written until `--apply`.
Stop and report at any step that doesn't match. Don't write to the database
directly, and don't create a key.

## Steps (run on the server, as the health user where noted)

1. **Find the export.** It is MacroFactor's own .xlsx, covering 19 Aug to 7 Oct 2026.
   Check `/home/health` and `~hermes`. If it isn't there, copy it from the Mac's
   `~/Documents/oracle-hermes-audit` or Downloads. Note the path you use.

2. **Check the key.** The importer needs a NutriTrace key with `mcp:write`.
   `hermes-read` is read-only and the LiftTrace token is `write:workouts`, so
   neither works. Look for an existing `mcp:write` key in
   `/etc/hermes-app/health/`. If there is none, **stop here and report**. A new key
   needs Craig's own yes first. Put the key in
   `/etc/hermes-app/health/nutritrace-import.key` (root:hermes-app, 0640) only
   after he says so.

3. **Check the tools.** Python 3 with openpyxl: `python3 -c "import openpyxl"`. If
   that fails, `python3 -m pip install openpyxl`. If pip can't reach PyPI, stop
   and report. PyPI has been blocked on this host before.

4. **Confirm the request bodies.** The importer sends:
   - `POST /api/v1/diary/<date>/food` with `{name, source, quantity, unit, nutrition: {calories, proteins, carbohydrates, fat}}`
   - `PUT /api/v1/diary/<date>/body-stat` with `{weight}`

   Check both against NutriTrace 1.3.1's route code (the field names, and whether
   `nutrition` is nested). If they differ, **stop and report the real names**. Don't
   run `--apply` until they are confirmed.

5. **Dry run.** Writes nothing:
   ```
   python3 app/deploy/macrofactor/import_macrofactor.py <export.xlsx> \
     --state /var/lib/hermes-app/macrofactor-import.state.json
   ```
   Expected: 44 food days and 18 weigh-ins, with 5 partly logged days skipped and
   7 Oct skipped. Any other count: stop and report.

6. **Back up NutriTrace.** An online backup, so the live database isn't stopped.
   As the health user:
   ```
   sqlite3 /home/health/health-stack/data/nutritrace/db/nutritrace.db \
     ".backup '/var/backups/nutritrace/nutritrace-$(date +%Y%m%d-%H%M).db'"
   ```
   Check the file is non-empty. The apply step refuses to run without it.

7. **Apply.** Same command, plus `--apply --key-file /etc/hermes-app/health/nutritrace-import.key --base http://127.0.0.1:3001`.
   It takes about two minutes. It is safe to re-run: anything already recorded in
   the state file is skipped.

8. **Check.** Read-only: `GET /api/v1/diary/2026-08-19/totals` with the read key
   shows the day's calories. `GET /api/me` and `GET /api/health/food` on the app
   with the login header show the Weight and Expenditure tiles now have data.
   Report the results.

## If something goes wrong

A failed write stops the run. Writes already made are recorded in the state file,
so a re-run continues where it stopped. Don't restore from the backup without
reporting first.
