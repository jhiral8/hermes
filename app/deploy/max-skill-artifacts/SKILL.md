---
name: artifacts
description: Save pages, documents, tables and images for Craig to open in the Hermes app. Use when he asks for a page, document, report, one-pager, table or diagram, or for something he'll want to keep or reread.
---

# Artifacts for the Hermes app

Craig's Hermes app shows the files in `/workspace/artifacts` in a side panel
next to the chat. Use it whenever a reply is better as something he can open,
reread, copy or download than as chat text.

## How

- Write ONE file per artifact straight into `/workspace/artifacts/` (no
  subfolders). Use a short lowercase name with dashes and one of these
  endings: `.html` (a page), `.md` (a document), `.csv` (a table),
  `.svg` (a diagram or image), `.json` or `.txt`.
- Give it a title: `<title>` in a page, a first line `# Title` in a document.
- To change an artifact, overwrite the same file name. The app shows the
  latest version.
- Keep each file under 2 MB.
- After saving, say in one line what you made and its file name. Don't paste
  the whole content into the chat as well.

## Pages

- Pages are shown in a locked sandbox: **scripts don't run and nothing loads
  from the network**. So write plain HTML with the styles inline in a
  `<style>` block. No JavaScript, no external fonts, images or stylesheets.
  Use inline SVG for charts.
- Keep pages readable on a phone as well as a laptop.

## Never

- Never put private data in an artifact unless Craig gave it to you in this
  chat for that purpose: no secrets, keys, passwords or tokens.
- Never write anywhere else on behalf of the app, and never create links or
  shortcuts in the folder.
