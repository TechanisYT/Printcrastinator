# Printcrastinator

**Your Nextcloud to-do list, printed on a thermal receipt every morning.**

Printcrastinator pulls today's tasks from Nextcloud Tasks and Deck plus today's calendar
events, renders them as a receipt with big checkboxes you can tick with a pen, and prints it
on a cheap 58 mm USB thermal printer. New tasks show up as a small slip a minute after you
create them. A local web UI lets you hide tasks, pick calendars and mark Deck stacks or task
lists as "always print".

<p align="center"><img src="docs/img/sample.png" width="300" alt="Sample receipt"></p>

## Features

- Daily receipt: date header, today's events, overdue tasks (with days late), tasks due today,
  and "always print" groups from Deck stacks or task lists. Printed once per day, only if
  there is something to do.
- New-task slips: the daemon polls Nextcloud and prints one slip listing everything new.
- On-screen agenda at login (terminal window and/or desktop notification), so you see the
  list even when the printer is out of paper.
- Raster rendering with a heavy monospace font, 384 px wide, 1-bit, no dithering. Umlauts and
  symbols come from the font, no code page fiddling.
- Web UI on `127.0.0.1:8555`: credentials, task hiding (per occurrence for recurring tasks),
  Deck stack and task list rules, calendar selection, printer test and density tuning, receipt
  preview.
- Interactive terminal view (`pc`): click tasks to cross them off or reopen them in Nextcloud,
  see notes and tags, print, and talk to a local AI.
- Local AI via Ollama: a morning briefing and natural-language task editing ("I paid the
  electricity bill", "add 'order screws' to Projects for Friday") with tool calls, fully offline.
- MCP server (`pc mcp`) exposing the same task tools to Claude Code or any MCP client.
- Extra calendars beyond Nextcloud: ICS/webcal subscription links (e.g. a university timetable) or other CalDAV servers, with their own credentials.
- Wake-aware: the daily check also runs after resume from sleep and after screen unlock.
- No credentials in the repo. Config lives in `~/.config/printcrastinator/config.toml`
  (mode 0600), state in `~/.local/state/printcrastinator/`.

## Hardware

Developed with a **Qian Anjet 58 mini** (USB ID `0456:0808`, generic ESC/POS, 384 dots,
no cutter). Any ESC/POS printer that the Linux `usblp` driver exposes as `/dev/usb/lp0` and
that accepts `GS v 0` raster images should work; adjust `printer.width_px` for 80 mm models.

## Requirements

- Arch-based Linux (EndeavourOS, Manjaro, Arch) with systemd user sessions
- Python 3.13+ (installed by `uv` if missing)
- A Nextcloud account with the Tasks, Deck and Calendar apps
- Optional: Alacritty for the terminal window at login, libnotify for notifications
- Optional: [Ollama](https://ollama.com) with a tool-capable model (default `gemma4:12B`) for the AI features

## Install

```sh
git clone <this repo> && cd printcrastinator
./install.sh          # or ./install.sh --dev to symlink this checkout
```

The script installs `uv` and `libnotify` via pacman if missing, copies the sources to
`~/.local/share/printcrastinator`, installs a udev rule for the printer, enables the
`printcrastinator` systemd user service and adds an autostart entry for the login window.

Then open <http://127.0.0.1:8555>, enter your Nextcloud URL, username and an **app password**
(Nextcloud → Settings → Security → Devices & sessions; untick "Allow filesystem access").
App passwords work even when your Nextcloud login goes through an SSO provider.

## Usage

```sh
printcrastinator serve                 # daemon + web UI (run by systemd)
printcrastinator show                  # interactive task view (also: pc), triggers the daily print
printcrastinator show --plain          # static receipt-style output
printcrastinator mcp                   # MCP server on stdio
printcrastinator print-today [--force] # print now
printcrastinator test-print            # calibration receipt with a density sweep
printcrastinator preview out.png       # render the receipt to PNG (--kind sample|empty|slip|live)
printcrastinator notify                # desktop notification
```

Run them with `uv run --project ~/.local/share/printcrastinator/src printcrastinator ...`
or from a development checkout with `uv run printcrastinator ...`.

## Configuration

Everything is editable in the web UI. The TOML file has these sections:

| Section | Keys |
|---|---|
| `nextcloud` | `url`, `username`, `app_password` |
| `printer` | `device`, `width_px`, `band_lines`, `feed_after_mm`, `density`, `fallback_codepage` |
| `daily` | `earliest_hour` (default 6), `print_calendar_only_days` |
| `slips` | `enabled_tasks`, `enabled_deck`, `debounce_seconds` |
| `server` | `host`, `port`, `poll_interval` |
| `screen` | `notify`, `terminal` |
| `ui` | `language` (`en` or `de`), `dark` |
| `logo` | `mode` (off/random/fixed), `file`, `max_height`, `dither` |
| `ai` | `enabled`, `url`, `model`, `num_ctx`, `max_tokens`, `think`, `summary_on_open` |
| `[[extra_calendars]]` | `name`, `kind` (ics/caldav), `url`, `username`, `password` |

## Development

```sh
uv sync
uv run pytest                      # includes PNG snapshot tests
uv run pytest --snapshot-update    # after intentional design changes
uv run ruff check . && uv run ruff format .
uv run printcrastinator preview /tmp/r.png --kind sample
```

Project documentation is in [`docs/`](docs/): overview, architecture, receipt design, Nextcloud
sources, daemon behaviour, UI/API/CLI, system integration, terminal view / AI / MCP.

## License

MIT. Bundled font: JetBrains Mono (SIL Open Font License), see `src/printcrastinator/assets/fonts/`.
