# 04 Nextcloud sources

All access uses HTTP Basic auth with a Nextcloud **app password**. This bypasses the Authentik
OIDC login because app passwords are validated by Nextcloud itself. App passwords cannot be
limited to specific apps; turn off "Allow filesystem access" on the token in
Settings → Security → Devices & sessions.

Credentials are entered in the web UI (Settings page, "Test connection") and stored in
`~/.config/printcrastinator/config.toml` with mode 0600.

## Tasks (CalDAV VTODO)

- Endpoint: `<url>/remote.php/dav/calendars/<user>/`. Every collection that supports VTODO is a
  task list.
- Each poll reads the collection **ctag**; objects are only downloaded when it changed
  (per-list cache in `caldav_cache`).
- A VTODO counts when `STATUS` ≠ `COMPLETED`/`CANCELLED` and `COMPLETED` is unset.
  `DUE` (date or datetime, converted to local date) drives today/overdue.
- Recurring VTODOs: the current occurrence's due date is used.
- Per-list "always print" switch → `list_rules(list_id)`.
- `TaskItem.uid` = VTODO UID; `source = "tasks"`.

## Deck (REST)

- Base: `<url>/index.php/apps/deck/api/v1.0`, header `OCS-APIRequest: true`, JSON.
- `GET /boards` → for each non-archived board `GET /boards/{id}/stacks` (includes cards).
- Cards are skipped when `archived` is true or `done` is set. `duedate` (ISO) → local date.
- Per-stack "always print" switch → `stack_rules(board_id, stack_id)`.
- `TaskItem.uid` = `deck:<card id>`; `source = "deck"`; `list_name = "<board> · <stack>"`.

## Calendar (CalDAV VEVENT)

- Same CalDAV root; every collection supporting VEVENT is a calendar.
- Each calendar can be toggled in the web UI → `calendar_prefs(calendar_id, enabled)`; new
  calendars default to enabled.
- Events are expanded server-side with a time-range REPORT for today (local midnight to
  midnight) so recurrences are handled by Nextcloud. All-day events are shown first.
- ctag caching as for tasks.

## Suppression ("hide from prints")

`suppressed(uid, due)` where `due` is the ISO date or `""`. A task is hidden only when both
match, so hiding one occurrence of a recurring task leaves later ones visible. The Tasks page
shows hidden items and allows unhiding.

## Network errors

Each source fetch is independent. A failing source is logged and the last successful result is
reused for that poll (stale-while-error), the poll loop backs off exponentially (5 s → 5 min).
