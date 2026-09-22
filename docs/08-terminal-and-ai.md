# 08 Terminal view, local AI and MCP

## Terminal view (`pc`, `printcrastinator show`)

Interactive Textual app. It is what the daemon opens in Alacritty when the daily check fires,
and what `pc` opens from any terminal (`pc` is installed to `~/.local/bin` by `install.sh`).

- Top: today's calendar. Middle: tasks, grouped like the receipt (`daily.group_by_list`), with
  lateness markers, first note line and tags.
- **Click a task** to complete it in Nextcloud; click again to reopen. Optimistic UI: the row
  flips to `[x]` with strikethrough immediately and only reverts (with an error toast) if
  Nextcloud rejects the change. Changes go through the daemon API
  (`POST /api/tasks/{uid}/done`), which writes to CalDAV (`complete()`/`uncomplete()`) or the Deck
  API (`done` field; the PUT must include `owner`).
- Bottom: AI panel with the morning briefing and an input line.
- Keys: `r` refresh, `p` print today's receipt (forced), `t` print tomorrow's receipt, `a` focus
  the AI input, `q` quit.
- `printcrastinator show --plain` prints the static receipt rendering instead.

## Local AI (Ollama)

Config section `ai`: `enabled`, `url` (default `http://localhost:11434`), `model` (default
`gemma4:12B`, fits the RX 9070 XT's 16 GB), `num_ctx`, `max_tokens`, `think`, `timeout`,
`summary_on_open`.

- `POST /api/ai/summary`: short briefing from the agenda (about 8 s warm on the 12B model).
- `POST /api/ai/chat {messages}`: natural-language task management. The daemon sends the agenda
  (with uids), the available lists, the writable calendars and each Deck board's labels and
  users as system context. Tools: `complete_task`, `reopen_task`, `edit_task`, `create_task`
  (title, notes, due, start, priority, tags/labels, location, assignees, stack), `create_event`
  / `edit_event` / `delete_event` (calendar, times, description, location, attendees), `get_settings`, `set_setting`, `print_receipt`
  (any day), `print_calendar` (events only, one day or a rotated multi-day table),
  `lists` (show/save/add/remove/print/delete saved lists), `print_ticket`, recurring events via
  `rrule` on create/edit (daily, weekly, weekdays, monthly, yearly, "every 2 weeks", "every
  monday and thursday", or a raw RRULE), `print_tasks` / `preview_tasks` (filtered custom receipts: list or stack ids, due
  range, overdue only, tags, text), `printer_action`. The system prompt carries guidelines for
  common phrasings ("print the X list under deck Y", "tasks from list L due next week") and
  tells the model to preview before printing when a filter is unclear. Tool calls are executed by the daemon and the loop continues for up to
  four rounds. The reply and the list of performed
  actions come back; the terminal view refreshes after actions.
- `think` is off by default: gemma4 otherwise spends thousands of hidden reasoning tokens per
  answer (a briefing took over two minutes instead of eight seconds).
- Tested models on this machine: gemma4:12B (good tool use), gemma4:E4B (fast but misses
  actions), Qwen3.6:35B-A3B (works, does not fit VRAM, slow).

## MCP server (`printcrastinator mcp`, `pc mcp`)

Stdio MCP server built on the `mcp` package (v2 `MCPServer`), for Claude Code or any MCP
client, as an alternative to talking to the local model in the window. All tools call the
daemon's HTTP API:

| Tool | Effect |
|---|---|
| `get_agenda` | today's agenda |
| `list_tasks` | all open tasks with uids, and lists/stacks for creation |
| `complete_task(uid)` / `reopen_task(uid)` | done / undone |
| `edit_task(uid, …)` | title, due, start, notes, priority, tags, location, assignees, stack |
| `create_task(list_id, title, …)` | list id or `board/stack`, same fields |
| `list_calendars` / `create_event(…)` / `edit_event(uid, …)` / `delete_event(uid)` | events incl. attendees |
| `get_settings` / `set_setting(section, key, value)` | read / change configuration |
| `printer_action(action)` | test_print, density_sweep, feed, test_notification, full_cycle, poll |
| `print_today(force?, layout?)` / `print_day(day, layout?)` | print the daily receipt for today / any day |
| `print_calendar(day_from, day_to?)` | events only, single day or side-by-side range |
| `select_tasks(…)` / `print_tasks(title, …)` | filter open tasks / print them as a custom receipt |
| `custom_lists` / `save_custom_list` / `print_custom_list` / `delete_custom_list` | saved lists |
| `print_ticket(…)` | ticket with QR code |
| `daemon_status` | health |

Register in Claude Code: `claude mcp add printcrastinator -- pc mcp`
