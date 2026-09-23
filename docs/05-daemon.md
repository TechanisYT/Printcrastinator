# 05 Daemon

`printcrastinator serve` runs the poll loop, the daily gate, the HTTP API and the web UI in one
asyncio process. It is the only process that opens the printer device.

## SQLite schema (`~/.local/state/printcrastinator/state.sqlite3`)

| Table | Columns | Purpose |
|---|---|---|
| `daily_prints` | `date TEXT PRIMARY KEY, claimed_at, printed_on_paper INTEGER, task_count` | atomic once-per-day gate |
| `seen_items` | `uid TEXT, source TEXT, first_seen, PRIMARY KEY(uid, source)` | new-task detection |
| `suppressed` | `uid, due, hidden_at, title, PRIMARY KEY(uid, due)` | hide per occurrence |
| `stack_rules` | `board_id, stack_id, always_print, PRIMARY KEY(board_id, stack_id)` | Deck always-print |
| `list_rules` | `list_id, always_print` | Tasks list always-print |
| `calendar_prefs` | `calendar_id, enabled` | calendar toggles |
| `caldav_cache` | `collection_id, ctag, payload_json, fetched_at` | skip unchanged collections |
| `print_log` | `id, kind, at, ok, detail` | history for the dashboard |
| `kv` | `key, value` | misc (e.g. density chosen, last quote index) |

## Daily gate

```
def maybe_print_daily(force=False):
    if not force and now.hour < cfg.daily.earliest_hour: return
    agenda = build_agenda()
    if not force and not db.claim_daily(today): return      # INSERT OR IGNORE, rowcount == 1
    show_on_screen(agenda)                                    # notify-send if enabled
    if agenda.has_tasks or (cfg.daily.print_calendar_only_days and agenda.has_events):
        try: printer.print_image(render(agenda))
        except Exception: db.release_daily(today); raise
        db.mark_daily_printed(today, on_paper=True)
    else:
        db.mark_daily_printed(today, on_paper=False)
```
Success means `open()` and `write()` did not raise. Printer status queries are never on this
path.

## Triggers for the daily check

- daemon start
- every poll tick
- D-Bus `org.freedesktop.login1.Manager.PrepareForSleep(false)` (resume)
- D-Bus session `Unlock` on `org.freedesktop.login1.Session`
- `printcrastinator show` / the terminal view (via API, silent)
- `POST /api/print/daily` (silent)

Only the poll loop, wake/unlock triggers and the "full morning cycle" test pass `screen=True`,
which sends the notification and opens the terminal window.

## Polling and new-task slips

- Interval `server.poll_interval` (default 180 s). Errors → exponential backoff, capped at 300 s.
- On the very first poll `seen_items` is seeded silently.
- Unseen, uncompleted, unsuppressed items are queued. 60 s (`slips.debounce_seconds`) after the
  first queued item a single slip listing all queued items is printed, then they are marked seen.
  If printing fails they stay queued and are retried next tick.
- Per-source toggles `slips.enabled_tasks` / `slips.enabled_deck`.
- New events for today (`slips.enabled_events`): instead of a slip, today's calendar-only
  receipt is reprinted after the same debounce, so the new event is seen in context. Event
  uids are tracked in `seen_items` with source `event`.
