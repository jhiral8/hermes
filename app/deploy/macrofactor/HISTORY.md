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

2. **Make the output folder writable by the service.** `/var/lib/hermes-app/` already
   exists for the app's stop file. Keep its owner, set its group to `hermes-app`,
   and set mode 0770. The timer runs as `health` in group `hermes-app`, so it can
   write the two outputs, and the app reads them through the same group.

3. **Check the key is readable by the service.** `/etc/hermes-app/health/nutritrace.key`
   must be readable by group `hermes-app`, because the service runs as `health`
   with group `hermes-app`. If it isn't, **stop and report**. Don't change its owner
   or mode without saying so first.

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
- The app's config `health.estimator_file` must be `/var/lib/hermes-app/estimator.json`.
  `config.example.json` already has that.
- Weight comes from MacroFactor's weigh-ins only. NutriTrace's read token can't read
  its weight history, so new weigh-ins won't reach the tiles until it can.
- If the export is replaced, change the path in the service file and run step 5 again.
