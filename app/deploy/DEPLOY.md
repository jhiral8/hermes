# Deploying the Hermes app server (Phase 1)

For the session that works on hermes-oracle. Craig does none of this.

The app server is read-only: it serves the web app and reports whether the
existing services are up. It holds no keys, no send power and no tokens.

## Before you start

- Craig must have said yes to the new Tailscale port (see step 5). Tailscale
  serve is shared; don't change existing ports.
- Ports already in use on the tailnet name: 443 broker, 8443 NutriTrace,
  8444 Paperclip, 9443 LiftTrace, 10000 CookTrace. Proposed: **7443**.
- Loopback port proposed: **3010**. Check it's free: `ss -ltn | grep :3010`.

## Steps

1. Create a system user with no login and no home:
   `sudo useradd --system --no-create-home --shell /usr/sbin/nologin hermes-app`
2. Put the code at `/opt/hermes-app` (root-owned, world-readable):
   `sudo git clone --depth 1 -b <branch-or-main> https://github.com/jhiral8/hermes /opt/hermes-app`
   If the host can't reach GitHub, copy the `app/` folder over from the Mac instead.
3. Write `/etc/hermes-app/config.json` from `app/server/config.example.json`
   (root-owned, mode 644; it holds no secrets):
   - `allowed_logins`: Craig's Tailscale login (`tailscale status --json`, `User` entries).
   - Fill each `REPLACE` with what's really on the box: Max's systemd unit,
     the loopback ports for Paperclip, the broker and salt.md, the kill switch
     flag file (whatever KILL-MAX.sh leaves behind), and the folder the nightly
     backup writes its log or marker to. If something can't be checked
     without new permissions, set its `kind` to `"todo"`; it then shows as
     Unknown, never as up.
4. Install and start the service:
   `sudo cp /opt/hermes-app/app/deploy/hermes-app.service /etc/systemd/system/`
   `sudo systemctl daemon-reload && sudo systemctl enable --now hermes-app`
   Check: `curl -s -H 'Tailscale-User-Login: <craig login>' http://127.0.0.1:3010/api/status`
5. Publish it on the tailnet only (no Funnel):
   `sudo tailscale serve --bg --https=7443 http://127.0.0.1:3010`
   Then confirm `tailscale serve status` still shows the other five ports unchanged.
6. Tell Craig to open `https://hermes-oracle.tail4abc29.ts.net:7443` on his
   phone and use Chrome's "Add to Home screen" / "Install app".

## Done when

Craig opens the address on his phone, installs it to the home screen, and the
System screen shows live status with no sign-in form.

## Notes

- The kill switch does not need to stop this service in Phase 1 (it can't
  change anything, and it shows that the agents are stopped). From Phase 2,
  when it gains a Stop button and writes, the kill switch freezes its writes.
- Updating: `cd /opt/hermes-app && sudo git pull && sudo systemctl restart hermes-app`.
- Not part of the nightly backup on purpose: the code is in git and the config
  is rebuilt from this file.

# Phase 2: the control centre

Phase 2 connects the app to the Paperclip board, the approval broker and the
cost monitor, and adds a few of Craig's own actions (create a task, decide a
board approval, pause an agent, stop a run, block all agent work). Every
action is logged to `/var/lib/hermes-app/actions.log`.

## Steps

1. Update the code: `cd /opt/hermes-app && sudo git fetch && sudo git checkout <branch> && sudo git pull`.
2. Reinstall the unit (it now has a state folder for the audit log):
   `sudo cp app/deploy/hermes-app.service /etc/systemd/system/ && sudo systemctl daemon-reload`.
3. Paperclip: create a board API key for the app (Craig's board, named
   "Hermes app") and save it to `/etc/hermes-app/paperclip.key`, owner
   `root:hermes-app`, mode `640`. Never paste it in chat. Fill `paperclip` in the
   config: the loopback URL, the company id, and `web_url` (the board address
   Craig opens).
4. Broker: fill `broker` with the path to its SQLite file and two SELECT
   queries whose columns are aliased to `id, title, detail, recipients,
   status, requested, expires, decided`. The app opens the file read-only. If
   hermes-app can't read the file, add hermes-app to the file's group rather
   than widening its permissions. `review_url` is the page Craig approves on.
5. Cost monitor: point `cost_file` at the JSON file the hourly script writes,
   and map `today_usd`, `month_usd`, `month_cap_usd` and `balance_usd` to its
   keys (dotted paths work). Leave out any it doesn't have.
6. Backup tile: point the `backup` service at the folder holding the nightly
   backup's last-success file, readable by hermes-app.
7. `sudo systemctl restart hermes-app` and check `/api/today` with the login
   header: each section should say `"ok": true`.

## Stop button (needs Craig's yes first: it adds root units)

The app can't run the kill switch itself. It writes
`/var/lib/hermes-app/stop-request`; a root-owned path unit sees it and runs
the existing kill switch. Resuming stays on the Mac.

    sudo cp app/deploy/hermes-app-stop.path app/deploy/hermes-app-stop.service /etc/systemd/system/
    # check ExecStart in hermes-app-stop.service matches the real kill command
    sudo systemctl daemon-reload && sudo systemctl enable --now hermes-app-stop.path

Then set `"stop": {"request_file": "/var/lib/hermes-app/stop-request"}` in the
config and restart. Until then the button shows as not connected.

## Done when

A task created in the app appears on the board and is picked up; a board
approval decided in the app shows as decided in Paperclip; a broker email
waiting for approval shows in the app with a link to the fingerprint page;
and (once Craig agrees) Block all agent work stops the agents and the System
screen shows the kill switch On.
