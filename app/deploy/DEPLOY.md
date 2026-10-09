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
4. Artifacts (pages and documents Max makes, shown in the app's side panel):
   - Create `/var/lib/hermes-artifacts`, writable by whatever user Max's
     sandbox container writes files as, and readable (folder and new files)
     by hermes-app, e.g. owner `hermes`, group `hermes-app`, mode `2750` with
     the container's umask leaving files group-readable. Check by writing a
     test file from inside Max's sandbox and reading it as hermes-app, then
     delete it.
   - Mount it into Max's sandbox: add
     `"/var/lib/hermes-artifacts:/workspace/artifacts"` to the existing
     `terminal.docker_volumes` list in Max's config.yaml (merge into the one
     list; a second `docker_volumes:` key silently replaces the first).
   - Install the skill: copy `app/deploy/max-skill-artifacts/SKILL.md` to
     `~hermes/.hermes/skills/artifacts/SKILL.md` (owned by hermes).
   - Restart Max's gateway and check Signal still answers.
5. Add `max_chat` to the config (see `config.example.json`, including
   `artifacts_dir`) and restart hermes-app.
6. Test once through the app's API with the login header:
   `POST /api/chat` (with `X-Hermes-Action: 1`), then
   `POST /api/chat/<id>/send {"text": "Reply with just the word ok."}`.
   The stream should end with `{"type": "end", "status": "done"}`.

## Done when

Craig opens Max in the app, sends a message, sees the reply stream in with
any tool calls listed, and the chat is still there after a reload. Asking
Max for "a one-page summary as a page" puts a file card under his reply that
opens in the side panel. With the
kill switch on, the app refuses to send and says why.

## Phase 4, step 1: Health screens (read-only)

Food, Train, Meals & Shop and Progress read NutriTrace, LiftTrace and
CookTrace over their public API on loopback. Nothing health-related goes to
Max or any model, and nothing is logged from the app yet.

1. In each app's settings, create a personal token named `hermes-read`:
   NutriTrace `mcp:read`, LiftTrace `mcp:read`, CookTrace `mcp:read` and
   `read:recipes`. Save each one as a file, one per app (never in chat):
   `/etc/hermes-app/health/nutritrace.key`, `lifttrace.key`, `cooktrace.key`.
   Owner `root`, group `hermes-app`, mode `0640`.
2. Check the scopes: `GET /api/v1/me` on each app with its token. It must
   list the scopes above and nothing more.
3. Each app needs `PUBLIC_API_ENABLED=1`, otherwise `/api/v1/*` returns 404.
   Set it in the app's environment and restart that app only.
4. Config: the `health` block in `config.example.json` (ports 3001 to 3003,
   the key files above, the estimator output path). Add it to the live
   config and restart hermes-app.
5. The expenditure estimator isn't running on the server yet. Until it writes
   `/var/lib/hermes-app-feed/estimator.json`, the Expenditure tile says so.
   Weight history isn't readable by the tokens, so the weight tiles say that
   too. Both are expected, not faults.

Test: open Health in the app. Food shows today's diary from NutriTrace; a
bad token shows "refused the token" on the screen that needs it, and the
rest still loads.

## Health logging (food, water, gym sets)

The app adds entries to NutriTrace and LiftTrace with the `hermes-write`
tokens. It never edits or deletes. Nothing goes to Max or any model.

1. Health writes must be on: `health-writes/install-health-writes.sh`
   (switch plus tokens) and, for adding foods,
   `health-writes/install-nutritrace-food-api.sh`.
2. Config: add `"write_key_file": "/etc/hermes-app/health/<app>-write.key"`
   to the nutritrace, lifttrace and cooktrace entries in the live config's
   `health` block (see `config.example.json`), then restart hermes-app.
3. Test: open Health > Food. Water +250 ml changes the water total; Log food
   lists NutriTrace foods; Train > Log a set lists LiftTrace exercises.
   Without the write key the screens say the write token isn't set up.

## Strategy and weekly check-in

Calorie and macro targets are kept by the app in `strategy.json`, next to the
saved foods (`/var/lib/hermes-app/`), or at `health.strategy_file` if set.
NutriTrace's goals are left as they are; once a strategy exists, Food, Today
and Progress use the app's targets instead. Targets change only when Craig
confirms the strategy steps, accepts a check-in, or switches program phase.
Nothing is sent to NutriTrace or to Max.

Install: `git pull`, then restart hermes-app. No config change is needed.

## Phase 5: Inbox (real Gmail, read-only)

Craig's own mail, shown only to him in the app. Read-only: the app calls
Gmail's read endpoints and nothing else. Nothing here can send, label, move or
delete mail, and nothing goes to Max or any model. The approval broker keeps
sending from the test mailbox only.

1. Sign in once with the `gmail.readonly` scope only, using the existing
   hermes-broker Google project. Save a JSON file at
   `/etc/hermes-app/inbox/gmail-real.token` with keys `client_id`,
   `client_secret` and `refresh_token`. Owner root, group hermes-app, mode 0640.
2. Max's sandbox must not be able to read `/etc/hermes-app/inbox`. Check from
   inside the sandbox, then remove the test file.
3. Add the `inbox` block from `config.example.json` to the live config, restart
   hermes-app only.
4. Test: `GET /api/inbox` with the login header returns the newest messages.
   With no token file, it says "Gmail sign-in isn't set up yet".

Mail is never written to disk or cached; the browser keeps it in memory only,
and the browser never stores it in localStorage.

## Phase 5: Planner (Google Calendar, read-only)

Craig's calendar, shown only to him in the app. The app only lists events:
nothing here can create, change or delete them, and nothing goes to Max or any
model. Events are never written to disk.

1. In the hermes-broker Google project, turn on the Google Calendar API.
2. Sign in once with the `calendar.readonly` scope only (a separate sign-in from
   Gmail, so the Inbox token is untouched). Save a JSON file at
   `/etc/hermes-app/planner/calendar.token` with keys `client_id`,
   `client_secret` and `refresh_token`. Owner root, group hermes-app, mode 0640.
3. Max's sandbox must not be able to read `/etc/hermes-app/planner`. Check from
   inside the sandbox, then remove the test file.
4. The app's outbound rule allows Google only. Check it reaches
   `www.googleapis.com` (the Calendar API host) as well as `gmail.googleapis.com`.
5. Add the `planner` block from `config.example.json` to the live config.
   `calendars` lists the calendar ids to show; `primary` is Craig's main one.
   Restart hermes-app only.
6. Test: `GET /api/planner` with the login header returns this week's days.
   With no token file, it says "Google Calendar sign-in isn't set up yet".

If the hermes-broker consent screen is still in Testing mode, Google expires its
refresh tokens after 7 days, for Gmail and Calendar alike. Check the mode before
relying on either.

## Phase 4 tiles: Expenditure and weight (day feed from the MacroFactor export)

The steps are in `app/deploy/macrofactor/HISTORY.md`. The tiles read
`/var/lib/hermes-app/estimator.json`, which `history_feed.py` writes nightly from
the MacroFactor export and NutriTrace's read-only totals. The old import steps in
`app/deploy/macrofactor/README.md` are superseded and must not be followed.

## Salt: delete blocker and Team notebook (agents write, nobody deletes)

salt.md's tokens are only read or read-write, so the "no delete" rule runs in
front of salt, in `app/server/salt_guard.py`. It refuses, by name, the MCP tools
`delete_view` and `delete_comment`, any HTTP DELETE, and, in `trash-counts`
mode, `set_trashed` (moving to trash). Restoring from trash is always allowed.
Refusals go to an audit file that never records tokens.

1. Run it as the hermes user, loopback only, pointing at salt's own address:
   `python3 salt_guard.py --upstream http://127.0.0.1:8420 --listen 127.0.0.1:11100 --mode <trash-counts|trash-allowed> --audit /var/lib/hermes-app-feed/salt-guard.jsonl`
   Confirm 8420 is salt's loopback port before starting it.
2. Point the agents' salt MCP address at `http://127.0.0.1:11100/mcp` (Max, Codex, Claude).
   Their tokens stay the same.
3. Check with `whoami` through the guard: read-write on all notebooks, and a
   `delete_comment` call comes back refused.
4. Team notebook: one shared workspace where Max, Codex and Claude write
   handoffs, plans and shared findings, beside each agent's own memory notebook.
5. Test: restoring a trashed page still works; a page moved to trash in
   trash-counts mode is refused.

## Barcode scan and saved foods (Food screen)

The camera reads the barcode in the browser (the phone's own barcode reader, or
the bundled ZXing build in `app/web/vendor/zxing`). The number goes to the server.
Nothing here goes to Max or any model.

1. Open Food Facts lookup is off by default (`foods.off_enabled` in the config).
   It is switched on only after Craig's explicit yes. When on, the server calls
   `world.openfoodfacts.org` with no key, caches answers for a day, and allows
   one call a second. That is the only new outside access.
2. Saved foods are kept in `/var/lib/hermes-app/saved-foods.json` (mode 0640),
   written by the app's own user. They are not in NutriTrace: this NutriTrace
   version (1.3.1) has no key route that can create a food, and the current
   read key can't list them either. Nothing needs a new key.
3. Test: `GET /api/health/barcode/<code>` with the login header returns the
   product (with lookup on), or says "Barcode lookup is off" (with lookup off).
   Saving a food with `POST /api/health/foods` adds it to the file; a second
   `GET /api/health/foods` lists it. `POST` without the X-Hermes-Action header is refused.

## Library: Max's memory and skills (read-only)

The Library shows Max's files (the artifacts folder, already set up), a link to
salt, and what Max remembers and which skills he has. The app must not read
Max's home folder (it holds his keys and sessions), so a small exporter runs as
the hermes user every 5 minutes and copies only `~/.hermes/memories/MEMORY.md`,
`USER.md` and each `skills/**/SKILL.md` into a feed the app's group can read.
It writes nothing in Max's folder, has no network, and skips links and hidden
folders. Nothing new reaches Max or any model.

1. Update the code, then install `hermes-app-library.service` and `.timer` from
   this folder and enable the timer. If Max's Hermes folder isn't
   `/home/hermes/.hermes`, change `--home` in the unit.
2. Check the memory files use Hermes's usual layout: one entry per block,
   blocks separated by a line holding only `§`. If not, report the layout
   (not the contents).
3. Run the service once. `/var/lib/hermes-app-library/library.json` should be
   owner hermes, group hermes-app, mode 0640, folder 0750.
4. Add the `library` block from `config.example.json` to the live config and
   restart hermes-app only.
5. Test: `GET /api/library` with the login header lists memory entries and
   skills (no skill text); `GET /api/library/skill/<id>` returns one SKILL.md.
   Max's sandbox must not be able to read `/var/lib/hermes-app-library`.
6. Add the timer to the kill switch list (the unit already won't run while
   `/etc/hermes-paused` exists).
