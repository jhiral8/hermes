# Hermes app

Craig's app for managing his personal agent setup. It starts as a web app
(PWA) served from hermes-oracle over Tailscale; later the same code is wrapped
as an Android app, and a Wear OS app comes last.

Plan: https://claude.ai/code/artifact/d542bf9b-2e95-4129-bd74-2632542923fc
Design reference: the Hermes Workspace mockup (https://claude.ai/artifact/LfwAwDMUu4cpg51CQkdf83).

## Layout

- `web/`: the app itself. Plain HTML, CSS and JavaScript, no build step.
  `hermes.css`, `icons.js` and the Geist fonts are the mockup's own design
  system, carried over unchanged; `app.css` holds the few additions.
- `server/hermes_app.py`: the small server that serves `web/` and a read-only
  API. Python standard library only, so nothing is installed on the server.
- `deploy/`: the systemd unit and the deployment steps.

## Rules

- Only Craig's Tailscale login gets in (Tailscale serve adds the header).
- The server listens on loopback only. Its one key is a Paperclip board key
  for Craig's own board actions; it holds no model keys and cannot send email.
- Private data (mail, calendar, health, Personal notes) is never passed to Max
  or any model.
- A service that can't be checked shows as Unknown, never as up or down.

## Tests

    cd app/server && python3 -m unittest -v test_hermes_app test_api test_chat test_artifacts

## Status

- Phase 1: installable app, live server status. Deployed.
- Phase 2: the mockup's Today, Work, Agents, Routines, Approvals and System
  screens on the real Paperclip board, approval broker and cost monitor.
  Actions: create a task, decide a board approval, pause or resume an agent,
  stop a run, block all agent work. Email approvals stay on the broker's
  fingerprint page.
- Phase 3: chat with Max (the mockup's Max screen) through Hermes Agent's API
  server, streamed, with tool calls shown and transcripts kept on the server.
  Pages and documents Max saves to his artifacts folder open in the mockup's
  side panel (pages in a sandbox with scripts and network off).

Try it without the real services: set `"demo": true` in a local config. The
app then shows a "Sample data" chip on every screen.
