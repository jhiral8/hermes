# Install the MacroFactor history feed

The Expenditure and Weight tiles read Craig's MacroFactor export directly on the
server, merged with NutriTrace's read-only totals. NutriTrace's totals win on any
day it has logged. Nothing is written to NutriTrace, and no key is created. Health
data never goes to Max or a model.

Export: `/home/health/imports/macrofactor/MacroFactor-20261007134323.xlsx`. It's
owned by the `health` user. Newer exports replace the file path in the timer.

## Steps (on the server, as root)

1. **Pull the code.** `cd /opt/hermes-app && sudo git fetch && sudo git checkout app-phase-1 && sudo git pull`,
   at or after commit e033713. The file is `app/server/history_feed.py`, beside
   `estimate_feed.py`. The estimator folder is already there. openpyxl is already
   installed from apt.

2. **Output folder.** The unit's `StateDirectory=hermes-history` creates
   `/var/lib/hermes-history/` on first run, owned `health:hermes-app`, mode 0750.
   Nothing else is needed. The app reads the estimate through the `hermes-app`
   group. `/var/lib/hermes-app/` is not touched, so `health` can't reach the stop
   file or Max's chat.

3. **Check the database is readable by the service.** The timer reads NutriTrace's
   database file directly, opened read-only, as the `health` user, who owns it. No
   key is used. Check that `health` can read the file. If it can't, **stop and
   report**. Don't change its owner or mode without saying so first.
   The service runs as `health` with group `hermes-app`, so it also reads the
   export. Neither file is written to.

4. **Install the timer.** Copy `hermes-history.service` and `hermes-history.timer`
   from this folder to `/etc/systemd/system/`, then `systemctl daemon-reload` and
   `systemctl enable --now hermes-history.timer`.

5. **Run once and check.** `systemctl start hermes-history.service`, then
   `journalctl -u hermes-history.service -n 20`. Expected output: a day count from
   19 Aug 2026 to today, with 18 weigh-ins. Against the export alone (no NutriTrace
   days yet), the last run gave an expenditure of 2729 kcal and a trend of 82.41 kg.

6. **Read-only check.** `GET /api/me` and `GET /api/health/food` on the app, with the
   login header. The Expenditure and Weight tiles should show those values.

## Notes

- The timer runs nightly at 04:10 London time. It doesn't run more than once an
  hour, because NutriTrace allows 60 calls a minute.
- The app's config `health.estimator_file` must be `/var/lib/hermes-history/estimator.json`.
  `config.example.json` already has that.
- Weight comes from two places. MacroFactor's Scale Weight sheet, and NutriTrace's
  own weigh-ins read from its database file (`wellness_data` weight rows, and the
  weight in `diary.body_stats`). NutriTrace wins on a day it has a weigh-in. The
  NutriTrace read key can't see weight history, which is why the timer reads the
  database and not the API.

## Tighten (one-time, on the live server, as root)

This moves the feed out of `/var/lib/hermes-app/`. That folder was made
group-writable by a drop-in that changed `StateDirectoryMode` for `hermes-app`,
which let `health` create files beside Max's chat and the stop file. It is undone here.

1. **Remove the drop-in.** Find it with `systemctl cat hermes-app`, delete the
   `StateDirectoryMode` override file, then `systemctl daemon-reload`. The unit in
   the repo already says `StateDirectoryMode=0750`.
2. **Restore the app folder.** `chmod 0750 /var/lib/hermes-app`, then
   `systemctl restart hermes-app`, so systemd applies the mode.
3. **Install the updated unit and config.** Copy `hermes-history.service` from
   this folder to `/etc/systemd/system/`, `systemctl daemon-reload`, then set
   `health.estimator_file` in the live config to
   `/var/lib/hermes-history/estimator.json`.
4. **Run once.** `systemctl start hermes-history.service`, then
   `journalctl -u hermes-history.service -n 20`. It should report the same 51 days
   and 18 weigh-ins as before. Check that `/var/lib/hermes-history/` holds
   `health-days.json` and `estimator.json`, both 0640.
5. **Read the app.** The Expenditure tile should still show the estimate.
6. **Remove the old outputs** only after step 5 works:
   `/var/lib/hermes-app/health-days.json` and `/var/lib/hermes-app/estimator.json`.
- If the export is replaced, change the path in the service file and run step 5 again.
