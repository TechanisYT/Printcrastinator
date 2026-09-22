"""MCP server (stdio) exposing the task tools to MCP clients such as Claude Code.

Register with:  claude mcp add printcrastinator -- printcrastinator mcp
All calls go through the running daemon's HTTP API.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import load_config


def _api(method: str, path: str, **kw: Any) -> Any:
    cfg = load_config()
    r = httpx.request(method, cfg.api_base + path, timeout=120, **kw)
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise RuntimeError(f"daemon answered {r.status_code}: {detail}")
    return r.json()


def build_server():
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer(
        "printcrastinator",
        instructions=(
            "Personal to-do list synced with Nextcloud Tasks and Deck, printed on a thermal "
            "printer. Use list_tasks to get uids before completing or editing tasks."
        ),
    )

    @srv.tool()
    def get_agenda() -> dict:
        """Today's agenda: calendar events, overdue tasks, tasks due today, always-print groups."""
        return _api("GET", "/api/agenda")

    @srv.tool()
    def list_tasks() -> dict:
        """All open tasks and Deck cards with uids, plus the lists/stacks new tasks can go to."""
        return _api("GET", "/api/tasks")

    @srv.tool()
    def complete_task(uid: str) -> dict:
        """Mark a task or Deck card as done."""
        return _api("POST", f"/api/tasks/{uid}/done", params={"done": True})

    @srv.tool()
    def reopen_task(uid: str) -> dict:
        """Undo completion of a task or Deck card."""
        return _api("POST", f"/api/tasks/{uid}/done", params={"done": False})

    @srv.tool()
    def edit_task(
        uid: str,
        title: str | None = None,
        due: str | None = None,
        start: str | None = None,
        notes: str | None = None,
        priority: int | None = None,
        tags: list[str] | None = None,
        location: str | None = None,
        assignees: list[str] | None = None,
        stack: int | None = None,
    ) -> dict:
        """Edit a task. Dates YYYY-MM-DD ("" clears, omit keeps). tags = Tasks categories or
        Deck label names; assignees/stack are Deck only; start/priority/location Tasks only."""
        body = {k: v for k, v in locals().items() if k != "uid" and v is not None}
        return _api("POST", f"/api/tasks/{uid}/edit", json=body)

    @srv.tool()
    def create_task(
        list_id: str,
        title: str,
        due: str | None = None,
        start: str | None = None,
        notes: str = "",
        priority: int | None = None,
        tags: list[str] | None = None,
        location: str | None = None,
        assignees: list[str] | None = None,
    ) -> dict:
        """Create a task. list_id is a task list id or 'board/stack' for Deck (see list_tasks)."""
        body = {k: v for k, v in locals().items() if v is not None}
        return _api("POST", "/api/tasks", json=body)

    @srv.tool()
    def list_calendars() -> dict:
        """Writable Nextcloud calendars for create_event."""
        return _api("GET", "/api/calendars")

    @srv.tool()
    def create_event(
        calendar_id: str,
        title: str,
        start: str,
        end: str | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        rrule: str = "",
    ) -> dict:
        """Create an event. start/end: YYYY-MM-DD (all-day) or YYYY-MM-DDTHH:MM (timed).
        attendees: 'Name <mail>' or 'mail'. rrule: daily|weekly|weekdays|monthly|yearly|
        'every 2 weeks'|FREQ=... for recurring events."""
        body = {k: v for k, v in locals().items() if v is not None}
        return _api("POST", "/api/events", json=body)

    @srv.tool()
    def edit_event(
        uid: str,
        title: str | None = None,
        start: str | None = None,
        end: str | None = None,
        description: str | None = None,
        location: str | None = None,
        attendees: list[str] | None = None,
        rrule: str | None = None,
    ) -> dict:
        """Edit an event; attendees replaces the whole list ([] removes everyone); rrule ""
        removes the recurrence."""
        body = {k: v for k, v in locals().items() if k != "uid" and v is not None}
        return _api("POST", f"/api/events/{uid}/edit", json=body)

    @srv.tool()
    def delete_event(uid: str) -> dict:
        """Delete an event by uid (see get_agenda for uids)."""
        return _api("DELETE", f"/api/events/{uid}")

    @srv.tool()
    def print_today(force: bool = False, layout: str = "") -> dict:
        """Print today's receipt. layout: '' (configured), 'list' or 'cards'."""
        return _api("POST", "/api/print/daily", params={"force": force, "layout_mode": layout})

    @srv.tool()
    def print_day(day: str, layout: str = "") -> dict:
        """Print the daily receipt for any day (YYYY-MM-DD): its events, tasks due that day,
        what is overdue by then, and pinned lists."""
        return _api("POST", "/api/print/day", params={"day": day, "layout_mode": layout})

    @srv.tool()
    def print_calendar(day_from: str, day_to: str | None = None) -> dict:
        """Print only calendar events: one day upright, a range (max 14 days) rotated with the
        days side by side. Dates YYYY-MM-DD."""
        params = {"day_from": day_from}
        if day_to:
            params["day_to"] = day_to
        return _api("POST", "/api/print/calendar", params=params)

    @srv.tool()
    def select_tasks(
        list_ids: list[str] | None = None,
        due_from: str | None = None,
        due_to: str | None = None,
        include_no_due: bool = True,
        overdue_only: bool = False,
        tags: list[str] | None = None,
        text: str = "",
    ) -> dict:
        """Filter open tasks (list ids or 'board/stack', due range, tags, text), no print."""
        body = {k: v for k, v in locals().items() if v is not None}
        return _api("POST", "/api/tasks/select", json=body)

    @srv.tool()
    def print_tasks(
        title: str,
        list_ids: list[str] | None = None,
        due_from: str | None = None,
        due_to: str | None = None,
        include_no_due: bool = True,
        overdue_only: bool = False,
        tags: list[str] | None = None,
        text: str = "",
    ) -> dict:
        """Print a custom receipt: a title and the tasks matching the filters, grouped by list."""
        body = {k: v for k, v in locals().items() if v is not None}
        return _api("POST", "/api/print/selection", json=body)

    @srv.tool()
    def get_settings() -> dict:
        """All Printcrastinator settings (app password masked)."""
        return _api("GET", "/api/settings")

    @srv.tool()
    def set_setting(section: str, key: str, value: str) -> dict:
        """Change one setting, e.g. section='daily', key='layout', value='cards'."""
        return _api("POST", "/api/settings", json={"section": section, "key": key, "value": value})

    @srv.tool()
    def printer_action(action: str) -> dict:
        """test_print, density_sweep, feed, test_notification, full_cycle or poll."""
        return _api("POST", "/api/printer/action", params={"action": action})

    @srv.tool()
    def birthdays(days: int = 14) -> dict:
        """Birthdays of the next N days from the contacts birthday calendar."""
        return _api("GET", "/api/birthdays", params={"days": days})

    @srv.tool()
    def print_birthdays(days: int = 14) -> dict:
        """Print the birthdays of the next N days as a receipt."""
        return _api("POST", "/api/print/birthdays", params={"days": days})

    @srv.tool()
    def custom_lists() -> dict:
        """Saved custom lists (shopping lists etc.) with items and print state."""
        return _api("GET", "/api/lists")

    @srv.tool()
    def save_custom_list(title: str, items: list[str], list_id: int | None = None) -> dict:
        """Create (or with list_id replace) a saved list."""
        return _api("POST", "/api/lists", json={"title": title, "items": items, "list_id": list_id})

    @srv.tool()
    def print_custom_list(list_id: int) -> dict:
        """Print a saved list as a checklist."""
        return _api("POST", f"/api/print/list/{list_id}")

    @srv.tool()
    def delete_custom_list(list_id: int) -> dict:
        """Delete a saved list."""
        return _api("DELETE", f"/api/lists/{list_id}")

    @srv.tool()
    def print_ticket(
        title: str,
        kind: str = "TICKET",
        subtitle: str = "",
        when: str = "",
        where: str = "",
        seat: str = "",
        holder: str = "",
        price: str = "",
        code: str = "",
        note: str = "",
    ) -> dict:
        """Print a ticket (cinema, entry, voucher); code is rendered as a QR code."""
        return _api("POST", "/api/print/ticket", json=dict(locals()))

    @srv.tool()
    def daemon_status() -> dict:
        """Daemon health, printer state, last poll, today's print status."""
        return _api("GET", "/api/status")

    return srv


def run() -> None:
    build_server().run(transport="stdio")
