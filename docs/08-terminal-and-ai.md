# 08 Terminal view, local AI and MCP

## Terminal view (`pc`, `printcrastinator show`)

Interactive Textual app. It is what the daemon opens in Alacritty when the daily check fires,
and what `pc` opens from any terminal (`pc` is installed to `~/.local/bin` by `install.sh`).

- Top: today's calendar. Middle: tasks, grouped like the receipt (`daily.group_by_list`), with
  lateness markers, first note line and tags.
- **Click a task** (or hover + click) to complete it in Nextcloud; click again to reopen. The row
  shows `[x]` with strikethrough while done. Changes go through the daemon API
  (`POST /api/tasks/{uid}/done`), which writes to CalDAV (`complete()`/`uncomplete()`) or the Deck
  API (`done` field; the PUT must include `owner`).
- Bottom: AI panel with the morning briefing and an input line.
- Keys: `r` refresh, `p` print today's receipt (forced), `a` focus the AI input, `q` quit.
- `printcrastinator show --plain` prints the static receipt rendering instead.

## Local AI (Ollama)

Config section `ai`: `enabled`, `url` (default `http://localhost:11434`), `model` (default
`gemma4:12B`, fits the RX 9070 XT's 16 GB), `num_ctx`, `max_tokens`, `think`, `timeout`,
`summary_on_open`.

- `POST /api/ai/summary`: short briefing from the agenda (about 8 s warm on the 12B model).
- `POST /api/ai/chat {messages}`: natural-language task management. The daemon sends the agenda
  (with uids) and the available lists as system context and offers four tools:
  `complete_task`, `reopen_task`, `edit_task`, `create_task`. Tool calls are executed by the
  daemon and the loop continues for up to four rounds. The reply and the list of performed
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
| `edit_task(uid, title?, due?, notes?)` | edit (`due=""` clears) |
| `create_task(list_id, title, due?, notes?)` | list id or `board/stack` |
| `print_today(force?, layout?)` | print receipt |
| `daemon_status` | health |

Register in Claude Code: `claude mcp add printcrastinator -- pc mcp`
