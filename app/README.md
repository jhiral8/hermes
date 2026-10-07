# Hermes app

Craig's app for managing his personal agent setup. It starts as a web app
(PWA) served from hermes-oracle over Tailscale; later the same code is wrapped
as an Android app, and a Wear OS app comes last.

Plan: https://claude.ai/code/artifact/d542bf9b-2e95-4129-bd74-2632542923fc
Design reference: the Hermes Workspace mockup (https://claude.ai/artifact/LfwAwDMUu4cpg51CQkdf83).

## Layout

- `web/`: the app itself. Plain HTML, CSS and JavaScript, no build step.
- `server/hermes_app.py`: the small server that serves `web/` and a read-only
  API. Python standard library only, so nothing is installed on the server.
- `deploy/`: the systemd unit and the deployment steps.

## Rules

- Only Craig's Tailscale login gets in (Tailscale serve adds the header).
- The server listens on loopback only, holds no keys and cannot send anything.
- Private data (mail, calendar, health, Personal notes) is never passed to Max
  or any model.
- A service that can't be checked shows as Unknown, never as up or down.

## Tests

    cd app/server && python3 -m unittest -v test_hermes_app

## Phase 1 status

Today and System screens: live status of Max, Paperclip, the approval broker,
the kill switch, the health apps, salt.md and the nightly backup.
