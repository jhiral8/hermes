# Hermes page watcher

Watches a short list of public pages and tells Craig on Signal when one actually changes. Unchanged pages cost nothing: detection is plain fetch-and-compare, and Max is only asked to summarise a change that already happened.

Built 2026-10-08 from the stack-report review (thread "ChatGPT stack report"). Not installed yet.

## Pieces

| File | What it is |
|---|---|
| `compose.yaml` | changedetection.io in rootless Docker under its own `watch` user on an internal-only network; squid is its only way out and also exposes the UI/API on host loopback :5000 |
| `squid.conf` | Port 3128: allowlist forward proxy (only `allowed-hosts.txt`). Port 3129: reverse proxy to the UI. `squid -k parse` passes |
| `allowed-hosts.txt` | Hosts the watcher may fetch. Starts with `github.com` only |
| `watches.json` | First watch list, loaded once with `load_watches.py` |
| `load_watches.py` | Adds `watches.json` to changedetection through its local API (stdlib only) |
| `relay.py` | Polls changedetection's local API every 10 min, builds the diff itself, asks Max for a two-line summary, sends `[Watch]` to Signal, queues during quiet hours. `relay.py status` prints the Monday digest line |
| `hermes-watch-relay.service/.timer`, `hermes-watch-flush.service/.timer` | systemd units: poll every 10 min, flush quiet-hours queue at 08:00 UK |
| `relay.env.example` | Relay settings; `NOTIFY_CMD` must point at the existing Signal alert sender |
| `test_relay.py` | 9 offline tests for the relay (`python3 -m unittest test_relay`) |

## Rules it keeps

- **No new web reach for Max.** Max never fetches anything for the watcher. It gets the diff text only, under a fixed session key `watcher`, with instructions to treat it as data. If Max's reply shows any tool call, the relay discards it and sends the trimmed raw diff instead, and logs `TOOL_USE_REJECTED`.
- **Watcher egress is allowlisted.** The container has no direct internet; it goes through `squid` which allows only `allowed-hosts.txt`. Adding a site needs Craig's yes (it loosens a lock).
- **Plain fetch only.** No browser container, no logins, no page scripts.
- **Quiet.** Max 10 Signal messages a day; 22:00 to 08:00 UK changes are queued and sent at 08:00 as one message. A fetch failure becomes "monitor unavailable" (from changedetection's error notification), never "no change".
- **Fits the existing safety kit.** Kill switch stops the watcher and relay; nightly backup covers `/srv/watch/datastore`; Monday digest gets one line (watch count, changes this week, failures).

## First watch list

Release feeds that also cover the plan's "Hermes security-update watch" to-do:
- Hermes Agent releases (NousResearch/hermes-agent)
- Paperclip releases (paperclipai/paperclip)
- salt.md releases (saltmd/salt.md)

Craig can add pages later in the watcher UI (`http://127.0.0.1:5000` through an SSH tunnel, or a Tailscale port if he wants one; that needs his yes since `tailscale serve` is shared). New hosts still need adding to `allowed-hosts.txt`.

## Install outline (phase-one server session)

1. `useradd --system --create-home watch`; enable rootless Docker for it (same pattern as the `health` user). Check port 5000 on host loopback is free first.
2. Copy this folder to `/srv/watch/` (owned by `watch`). Write `.env` with `CD_VERSION=<newest changedetection.io release at least two weeks old>` and record the tag. `mkdir datastore`.
3. `docker compose up -d` as `watch`. Acceptance: `curl -s 127.0.0.1:5000` shows the UI; `docker exec watch-cd python3 -c "import urllib.request;urllib.request.urlopen('https://example.com',timeout=10)"` FAILS; the same with `https://github.com` works.
4. In the UI set a password, then copy the API key (Settings > API) into `/etc/hermes-watch/cd-api.key` (0600, owner watch). Run `python3 load_watches.py`.
5. Copy Max's API key (the one the app uses) to `/etc/hermes-watch/max-api.key` (0600 watch). Copy `relay.env.example` to `/etc/hermes-watch/relay.env` and set `NOTIFY_CMD` to the existing Signal alert sender. Create `/var/lib/hermes-watch` (owner watch).
6. Install the four systemd units; enable both timers.
7. Kill switch: stop both timers and `docker compose down` for watch. Resume: reverse. Nightly backup: add `/srv/watch/datastore`. Monday digest: add the output of `relay.py status`.
8. Tests: `python3 -m unittest test_relay` (9 pass offline). Live: temporarily add a watch on `https://github.com/NousResearch/hermes-agent/commits.atom` (changes often) with a 30-min check, wait for the next commit, confirm one `[Watch]` Signal message arrives, then delete that watch.
