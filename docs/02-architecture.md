# 02 Architecture

## Stack

| Concern | Choice |
|---|---|
| Runtime | Python ≥3.13, `uv` project with lockfile |
| Web UI + HTTP API + daemon | NiceGUI (FastAPI underneath), single asyncio process, `127.0.0.1:8555` |
| Terminal view | Textual |
| Printer transport | python-escpos `File` printer on `/dev/usb/lp0` (kernel `usblp`, no libusb) |
| Receipt rendering | Pillow, 384 px wide 1-bit images, bundled JetBrains Mono (OFL) |
| Nextcloud Tasks + Calendar | `caldav` library, called via `asyncio.to_thread`, sequential |
| Nextcloud Deck | `httpx.AsyncClient`, header `OCS-APIRequest: true` |
| Extra calendars | `httpx` + `icalendar` + `recurring_ical_events` (ICS), `caldav` (other servers) |
| Local AI | Ollama HTTP API with tool calling |
| MCP | `mcp` package v2 (`MCPServer`), stdio transport |
| Secrets | `keyring` (Secret Service / KWallet), `cryptography` Fernet vault fallback |
| Wake triggers | `dbus-next` on `org.freedesktop.login1` (PrepareForSleep, session Unlock) |
| State | SQLite (stdlib); config TOML at `~/.config/printcrastinator/config.toml` |
| Screen output | `notify-send`, Alacritty running the terminal view |
| Tooling | `ruff`, `pytest`, `pytest-asyncio`, PNG snapshot tests |

## Process model

```
 systemd --user
 ┌────────────────────────────────┐
 │ printcrastinator serve         │   HTTP   ┌─────────────────────┐
 │  - poll loop, daily gate       │◄─────────│ pc  (Textual view)  │  opened by the daemon
 │  - login1 D-Bus triggers       │◄─────────│ pc mcp (MCP server) │◄── Claude Code etc.
 │  - HTTP API  /api/...          │◄─────────│ CLI: print-today …  │
 │  - NiceGUI web pages           │          └─────────────────────┘
 │  - AI assistant (→ Ollama)     │
 │  - the ONLY /dev/usb/lp0 opener│──── paced raster bands ────► Qian Anjet 58
 └────────────────────────────────┘
```

Only `serve` talks to Nextcloud and the printer. The terminal view, MCP server and CLI call
the daemon's API; CLI commands fall back to in-process code when the daemon is not running.

## Modules (`src/printcrastinator/`)

| Module | Responsibility |
|---|---|
| `config.py` | XDG paths, dataclass config, TOML load/save, secret markers |
| `secrets.py` | keyring / encrypted vault storage for passwords |
| `db.py` | SQLite schema and helpers (see 05) |
| `models.py` | `TaskItem`, `CalendarEvent`, `DailyAgenda` |
| `sources/caldav_client.py` | CalDAV: collections, ctag cache, VTODO/VEVENT fetch, write-back, events |
| `sources/deck.py` | Deck REST: boards/stacks/cards, labels, users, write-back, card moves |
| `sources/extra.py` | ICS feeds and other CalDAV servers |
| `sources/parse.py` | pure iCalendar / Deck JSON → models |
| `agenda.py` | merge sources, rules, suppression, overdue filters, sort |
| `receipt/model.py` `receipt/layout.py` `receipt/i18n.py` | device-independent receipt model, layouts, labels |
| `render/image.py` `render/terminal.py` | receipt → 1-bit image / rich output |
| `render/notify.py` `render/screen.py` | desktop notification / open the terminal window |
| `printer/escpos_out.py` | device access, paced raster bands, feed, calibration |
| `logos.py` | uploaded logo images, receipt options from config |
| `daemon.py` | poll loop, backoff, slips, daily gate, D-Bus, task/event write-back, settings |
| `ai.py` | Ollama assistant: briefing and tool-calling chat |
| `api.py` | FastAPI routes mounted into the NiceGUI app |
| `client.py` | httpx client used by the CLI commands |
| `tui.py` | Textual terminal view |
| `mcp_server.py` | MCP server on stdio |
| `web/app.py` | NiceGUI single-page UI |
| `cli.py` | argparse entry point |

## Data flow for the daily receipt

1. Trigger (poll tick, D-Bus wake/unlock, `show`, API call) → `daemon.maybe_print_daily()`.
2. Time gate: local time ≥ `daily.earliest_hour`.
3. Fetch: Nextcloud tasks, events and extra calendars sequentially on one thread; Deck
   concurrently. Caches are used when ctags/ETags have not changed.
4. `agenda.build()` applies rules, suppressions and overdue filters.
5. Atomic claim: `INSERT OR IGNORE INTO daily_prints(date)`.
6. Render: `layout.daily_receipt(agenda, lang, layout, options)` → `render.image` →
   `printer.print_image()`. On failure the claim is released so the next trigger retries.
7. Only automatic triggers (poll, wake, full-cycle test) also notify and open the terminal
   view; manual prints from UI, CLI, TUI or AI stay silent.
