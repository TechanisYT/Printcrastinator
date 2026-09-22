# 02 Architecture

## Stack

| Concern | Choice |
|---|---|
| Runtime | Python ≥3.13 (3.14 on this machine), `uv` project with lockfile |
| Web UI + HTTP API + daemon | NiceGUI (FastAPI underneath), single asyncio process, `127.0.0.1:8555` |
| Printer transport | python-escpos `File` printer on `/dev/usb/lp0` (kernel `usblp`, no libusb) |
| Receipt rendering | Pillow, 384 px wide 1-bit images, bundled JetBrains Mono ExtraBold (OFL) |
| Nextcloud Tasks + Calendar | `caldav` library, called via `asyncio.to_thread` |
| Nextcloud Deck | `httpx.AsyncClient`, header `OCS-APIRequest: true` |
| Wake triggers | `dbus-next` on `org.freedesktop.login1` (PrepareForSleep) and session `Unlock` |
| State | SQLite (stdlib), config TOML at `~/.config/printcrastinator/config.toml` (mode 0600) |
| Screen output | `rich` in a terminal, `notify-send` for KDE notifications |
| Tooling | `ruff`, `pytest`, `pytest-asyncio`, PNG snapshot tests |

## Process model

```
 systemd --user                      autostart .desktop
 ┌──────────────────────────┐        ┌───────────────────────────┐
 │ printcrastinator serve   │  HTTP  │ alacritty -e              │
 │  - poll loop             │◄───────│   printcrastinator show   │
 │  - daily gate + triggers │        └───────────────────────────┘
 │  - HTTP API  /api/...    │◄─── print-today / test-print / preview / notify
 │  - NiceGUI web pages     │
 │  - the ONLY /dev/usb/lp0 │
 │    opener                │
 └──────────┬───────────────┘
            │ raster bands via ESC/POS
        Qian Anjet 58
```

If the daemon is unreachable, CLI commands run the same code in-process (direct fallback).

## Modules (`src/printcrastinator/`)

| Module | Responsibility |
|---|---|
| `config.py` | XDG paths, dataclass config, TOML load/save |
| `db.py` | SQLite schema and helpers (see 05) |
| `models.py` | `TaskItem`, `CalendarEvent`, `DailyAgenda` |
| `sources/caldav_tasks.py` | VTODO → `TaskItem`, ctag cache, list rules |
| `sources/caldav_events.py` | today's VEVENTs with recurrence, calendar toggles, ctag cache |
| `sources/deck.py` | Deck boards/stacks/cards → `TaskItem`, stack rules |
| `agenda.py` | merge sources, apply rules and suppressions, sort |
| `receipt/model.py` | device-independent receipt model (sections, items, styles) |
| `receipt/layout.py` | `DailyAgenda` / new-task list → receipt model |
| `render/image.py` | receipt model → 1-bit PIL image, 384 px wide |
| `render/terminal.py` | receipt model → rich output |
| `render/notify.py` | agenda summary → `notify-send` |
| `printer/escpos_out.py` | open device, send image in bands, minimal text path, density setup |
| `daemon.py` | poll loop, backoff, debounce, daily gate, D-Bus triggers |
| `api.py` | FastAPI routes mounted into the NiceGUI app |
| `client.py` | httpx client used by the CLI commands |
| `web/` | NiceGUI pages |
| `cli.py` | argparse entry point |

## Data flow for the daily receipt

1. Trigger (poll tick, D-Bus wake/unlock, `show`, API call) → `daemon.maybe_print_daily()`.
2. Time gate: local time ≥ `daily.earliest_hour`.
3. Fetch: tasks (CalDAV), cards (Deck), events (CalDAV) concurrently; caches are used when
   ctags have not changed.
4. `agenda.build()` applies rules and suppressions.
5. Atomic claim: `INSERT OR IGNORE INTO daily_prints(date)`. No row inserted → someone else did it.
6. Render: `layout.daily(agenda)` → `render.image` → `printer.print_image()`. On exception the
   claim row is deleted so the next trigger retries. Screen output happens regardless.
