# 06 Web UI, HTTP API and CLI

## Web UI (NiceGUI, http://127.0.0.1:8555)

Single page; the left menu swaps sections in place (menu button on narrow screens). Dark mode
by default, toggle in Settings.

| Section | Content |
|---|---|
| Dashboard | receipt preview, "Print today now", "Reprint (force)", "Print as cards", print/preview for any date, custom print (lists, due range, text), status, poll, notify, log |
| Tasks | current candidate tasks with a hide switch per occurrence; hidden list with unhide |
| Deck | boards → stacks with an "always print" switch each |
| Task lists | Nextcloud Tasks lists with an "always print" switch each |
| Calendars | Nextcloud and extra calendars with an "include" switch each |
| Printer | device, width, band lines, send speed, feed, density, code page; test print, density sweep, feed, sample; previews |
| Custom lists | saved lists: edit title and items, save, print (marks printed), delete |
| Tickets | ticket form with live preview and print |
| Settings | Nextcloud credentials + test; extra calendars; daily receipt options (earliest hour, calendar-only days, language, layout, overdue filters, grouping, notes); quotes editor; logo upload; slips; AI; on-screen options with test buttons |

## HTTP API (same port, JSON)

| Route | Effect |
|---|---|
| `GET /api/status` | daemon health, printer state, counts, today's print |
| `GET /api/agenda?refresh=&day=` | today's (or any day's) agenda |
| `GET /api/preview.png?kind=live\|sample\|empty\|slip\|calendar&layout_mode=&day=&day_to=` | rendered receipt |
| `POST /api/print/daily?force=&layout_mode=` | daily gate / forced print (silent) |
| `POST /api/print/day?day=&layout_mode=` | the daily receipt for any date, no gate |
| `POST /api/print/sample` | the sample receipt |
| `POST /api/print/calendar?day_from=&day_to=` | events only; one day upright, a range rotated side by side |
| `POST /api/tasks/select` · `POST /api/print/selection` | filter open tasks (list ids, due range, tags, text) / print them under a title |
| `POST /api/print/test?sweep=` · `POST /api/printer/feed` · `POST /api/printer/action?action=` | printer actions |
| `POST /api/notify` · `POST /api/notify/test` · `POST /api/test/cycle` | screen output tests, full morning cycle |
| `POST /api/poll` | fetch now |
| `GET /api/tasks` | open tasks with uids, suppression keys, lists/stacks |
| `POST /api/tasks/{uid}/done?done=` | complete / reopen |
| `POST /api/tasks/{uid}/edit` · `POST /api/tasks` | edit / create with all fields |
| `GET/POST /api/lists` · `DELETE /api/lists/{id}` · `POST /api/print/list/{id}` | saved lists |
| `POST /api/print/ticket` · `POST /api/preview/ticket.png` | tickets |
| `GET /api/calendars` · `POST /api/events` · `POST /api/events/{uid}/edit` · `DELETE /api/events/{uid}` | calendars / create, edit (incl. attendees), delete events |
| `GET /api/settings` · `POST /api/settings` | read (password masked) / change one setting |
| `GET /api/ai/status` · `POST /api/ai/summary` · `POST /api/ai/chat` | AI |

## CLI (`printcrastinator …`, or `pc …`)

| Command | Behaviour |
|---|---|
| `serve` | daemon + web UI (systemd) |
| `show` (default for `pc`) | interactive terminal view; `--plain` static output, `--no-wait` |
| `print-today [--force] [--layout list\|cards] [--day YYYY-MM-DD]` | via API; direct fallback if daemon down |
| `test-print [--sweep]` | calibration receipt |
| `preview PATH [--kind …] [--layout …] [--lang …] [--open]` | write receipt PNG, no printer needed |
| `notify` | desktop notification |
| `mcp` | MCP server on stdio |
