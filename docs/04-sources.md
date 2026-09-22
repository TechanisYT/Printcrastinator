# 04 Nextcloud sources

All access uses HTTP Basic auth with a Nextcloud **app password**. This bypasses the Authentik
OIDC login because app passwords are validated by Nextcloud itself. App passwords cannot be
limited to specific apps; turn off "Allow filesystem access" on the token in
Settings → Security → Devices & sessions.

Credentials are entered in the web UI (Settings page, "Test connection"). The password is
stored in the **system keyring** (KWallet on KDE, via the Secret Service API) under service
`printcrastinator`; `config.toml` only contains the marker `@keyring:nextcloud`. Extra
calendar passwords use `@keyring:extra:<name>`. A plaintext password found in the file
(older configs, hand edits) is migrated into the keyring on the next load and the file is
rewritten.

Fallback when no keyring is reachable (`PRINTCRASTINATOR_SECRETS=vault` forces it): a
Fernet-encrypted `vault.json` in `~/.local/state/printcrastinator/` with its key in
`vault.key` (0600) next to it. That protects against reading or accidentally sharing the
config file, not against another process running as the same user.

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

## Extra calendars (outside Nextcloud)

`[[extra_calendars]]` entries in the config (managed in Settings → Extra calendars), each with
`name`, `kind`, `url`, optional `username`/`password`:

- `kind = "ics"`: a public or `webcal://` subscription link (typical for universities). Fetched
  with `If-None-Match`/`If-Modified-Since`, cached in `caldav_cache` under `ics:<id>`, and
  expanded for the day with `recurring_ical_events` (RRULE, EXDATE, RECURRENCE-ID handled).
- `kind = "caldav"`: another CalDAV server. The URL may be an account root (principal
  discovery) or a single calendar collection; collection ids are prefixed with the calendar id
  so the Calendars page toggles work per collection.

Errors are reported per calendar in the daemon status and never block the Nextcloud sources.
