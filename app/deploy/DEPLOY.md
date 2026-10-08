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
3. Paperclip (v2026.1005.0, container orch-board-1, 127.0.0.1:3100): create a
   board API key for the app named "Hermes app": start a challenge with
   `POST /api/cli-auth/challenges`, give Craig the approval link (one tap while
   signed in to the board), then save the key to
   `/etc/hermes-app/paperclip.key`, owner `root:hermes-app`, mode `640`. Never
   paste it in chat. The config finds the company by name (`"company_name":
   "Jhiral"`), so its id isn't needed.
4. Broker: its database is owner-only and holds full email payloads, so the app
   doesn't read it. A broker-run exporter copies only subject, recipients,
   status and times into a feed file the app can read:
   - Check the payload's key names for subject and recipients (key names only:
     `sqlite3 -readonly broker.db "select distinct j.key from requests, json_each(payload) j"`)
     and the status words (`select distinct status from requests`). Adjust
     `app/server/broker-export.example.json` to match and save it as
     `/etc/hermes-app/broker-export.json` (root, 644).
   - `sudo cp app/deploy/hermes-app-broker-export.service app/deploy/hermes-app-broker-export.timer /etc/systemd/system/`
   - `sudo systemctl daemon-reload && sudo systemctl enable --now hermes-app-broker-export.timer`
   - Check: `sudo systemctl start hermes-app-broker-export.service` prints
     "exported N pending", and `/var/lib/hermes-app-feed/broker.json` exists.
5. Cost: the app reads `/var/lib/hermes-cost/balance.csv` (provider
   `openrouter`; spend is the rise in `used` between checks). Set
   `month_cap_usd` to `monthly_budget_usd` from `/etc/hermes-cost/config.json`.
   The CSV is already 644 but its folder is root 0700. Needs Craig's OK:
   `sudo chmod 0711 /var/lib/hermes-cost` (traverse only: nobody can list the
   folder, and alerts.json stays unreadable unless it is itself world-readable).
6. Backup tile: kind `last_run` on `hermes-backup.service`. It asks
   `systemctl show` for the last run's result and time; no new permissions.
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

# Phase 3: chat with Max

The app talks to Max through Hermes Agent's own API server on
127.0.0.1:8642 (the one `/health` already answers on), using its Responses
API with one named conversation per app chat. Max keeps his sandbox, the
broker approvals and the kill switch exactly as on Signal. Transcripts are
kept in `/var/lib/hermes-app/chat` (mode 600). Max's API key lets the app start
turns as Craig, so it is readable by root and hermes-app only.

## Steps

1. Update the code (`git pull` on the branch) and restart hermes-app.
2. Check the API server: `GET /v1/capabilities` with the key must list the
   Responses API. Read the key name only, not the value: is `API_SERVER_KEY`
   set in Max's environment or `gateway.api_server.key` in his config.yaml?
   - If a key is set: copy it to `/etc/hermes-app/max-api.key`, owner
     `root:hermes-app`, mode `640`.
   - If none is set: generate one (`openssl rand -hex 32`), put it in Max's
     environment as `API_SERVER_KEY` and in `/etc/hermes-app/max-api.key`, and
     restart Max's gateway (`systemctl --user restart hermes-gateway` as the
     hermes user). Check Signal still answers.
   - Never print the key.
3. Check what Max does when a tool needs his own "dangerous command" approval
   over the API server (approval mode in his config). Report it; don't change it.
4. Add `max_chat` to the config (see `config.example.json`) and restart.
5. Test once through the app's API with the login header:
   `POST /api/chat` (with `X-Hermes-Action: 1`), then
   `POST /api/chat/<id>/send {"text": "Reply with just the word ok."}`.
   The stream should end with `{"type": "end", "status": "done"}`.

## Done when

Craig opens Max in the app, sends a message, sees the reply stream in with
any tool calls listed, and the chat is still there after a reload. With the
kill switch on, the app refuses to send and says why.
