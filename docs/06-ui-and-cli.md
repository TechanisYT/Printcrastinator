# 06 Web UI, HTTP API and CLI

## Web UI (NiceGUI, http://127.0.0.1:8555)

| Page | Content |
|---|---|
| Dashboard | today's receipt preview (PNG), "Print today now", "Reprint", last prints, log tail, daemon status |
| Tasks | current candidate tasks (source, list, due, status) with a "hide" switch per row; hidden list with unhide |
| Deck | boards → stacks with an "always print" switch each |
| Task lists | Nextcloud Tasks lists with an "always print" switch each |
| Calendars | calendars with an "include" switch each |
| Printer | test print, feed, density selector, feed-after-mm, device path |
| Settings | Nextcloud URL / user / app password, test connection, poll interval, earliest hour, calendar-only days, slip toggles, screen output toggles, language |

`ui.run(host="127.0.0.1", port=8555, reload=False, show=False)`.

## HTTP API (same port, JSON)

| Route | Effect |
|---|---|
| `GET /api/status` | daemon health, printer reachable (best effort), last poll |
| `GET /api/agenda` | today's agenda |
| `GET /api/preview.png?kind=daily|empty|slip` | rendered receipt image |
| `POST /api/print/daily?force=false` | run the daily gate (force ignores gate) |
| `POST /api/print/test` | calibration receipt |
| `POST /api/printer/feed` | feed paper |
| `POST /api/notify` | send desktop notification for today's agenda |
| `POST /api/poll` | poll now |

## CLI (`printcrastinator ...`)

| Command | Behaviour |
|---|---|
| `serve` | daemon + web UI (used by the systemd unit) |
| `show` | trigger daily check via API, render agenda in the terminal with rich, wait for a key |
| `print-today [--force]` | via API; direct fallback if daemon down |
| `test-print` | calibration receipt |
| `preview PATH [--sample\|--empty\|--slip]` | write receipt PNG, no printer needed |
| `notify` | desktop notification |

All commands except `serve` and `preview` try the API first and fall back to running the same
code in-process when the daemon is not reachable.
