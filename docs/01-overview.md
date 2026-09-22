# 01 Overview

## Goal

A small always-running program on the EndeavourOS desktop that turns Nextcloud tasks into
paper. Once a day it prints a receipt with today's calendar and a checklist of tasks; whenever
new tasks show up it prints a short slip. Crossing things off on paper should feel like an
accomplishment, so the receipt is designed for a pen: drawn checkboxes, heavy type, a tear line.

## Behaviour in one page

- **On login** a terminal window (and/or a desktop notification) shows today's agenda.
  If today's receipt has not been printed yet and it is past the configured earliest hour,
  it is printed on paper. With zero tasks nothing goes on paper; the screen says
  "No tasks for today". Today's calendar events are always part of the agenda.
- **In the background** a daemon polls Nextcloud every few minutes. New tasks or Deck cards
  get collected for 60 s and printed together on one slip.
- **A local web UI** (http://127.0.0.1:8555) holds the Nextcloud credentials, lets you hide
  individual tasks from the prints, mark Deck stacks and Tasks lists as "always print",
  choose calendars, add extra calendars, upload logos, edit quotes, tune the printer, preview
  the receipt and trigger tests.
- **An interactive terminal view** (`pc`) shows the same agenda; clicking a task completes it
  in Nextcloud. A local AI (Ollama) gives a briefing and executes natural-language commands;
  an MCP server exposes the same tools to external agents.

## What counts as "today"

A task is on the receipt if it is not completed and
- it is due today, or
- it is due in the past (overdue, marked with how many days late), or
- it lives in a Deck stack or Tasks list marked "always print" (any or no due date).

Hidden tasks are excluded. Hiding is per occurrence (uid + due date) so hiding one instance of
a recurring task does not hide future ones.

## Decisions taken (2026-09-22)

- Terminal view with Textual; optimistic completion (flip first, revert on error).
- Local AI through Ollama with tool calling; model thinking disabled by default for speed.
- Manual prints are silent; only the automatic morning trigger opens the window and notifies.

- Python 3.13+/3.14 managed with `uv`; NiceGUI for the web UI; python-escpos for the printer.
- Receipts are **rendered as raster images** (Pillow, 384 px wide, 1-bit, no dithering) and
  sent with ESC/POS raster image commands. Text mode is only used for the calibration print and
  as a fallback. Reasons: drawn checkboxes a pen fits into, real typography, no code page or
  inverse-mode quirks, design iteration via PNG snapshots.
- One process (`serve`) owns the printer device. CLI commands talk to it over HTTP and fall
  back to direct printing only when the daemon is not running.
- The daily print is gated atomically in SQLite so two triggers can never print twice.
- Nextcloud access uses an app password (works with the Authentik SSO login, cannot be
  scoped to single apps; turn off "Allow filesystem access" on the token).
- Passwords are never written to `config.toml`. They go to the system keyring (KWallet /
  Secret Service) via `keyring`; the config holds `@keyring:<key>` markers. Without a keyring
  an encrypted vault file in the state dir is used (see 04).
