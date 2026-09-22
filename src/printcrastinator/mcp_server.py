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
        uid: str, title: str | None = None, due: str | None = None, notes: str | None = None
    ) -> dict:
        """Edit title, due date (YYYY-MM-DD, "" clears, omit to keep) or notes of a task."""
        body = {
            k: v for k, v in {"title": title, "due": due, "notes": notes}.items() if v is not None
        }
        return _api("POST", f"/api/tasks/{uid}/edit", json=body)

    @srv.tool()
    def create_task(list_id: str, title: str, due: str | None = None, notes: str = "") -> dict:
        """Create a task. list_id is a task list id or 'board/stack' for Deck (see list_tasks)."""
        return _api(
            "POST",
            "/api/tasks",
            json={"list_id": list_id, "title": title, "due": due, "notes": notes},
        )

    @srv.tool()
    def print_today(force: bool = False, layout: str = "") -> dict:
        """Print today's receipt. layout: '' (configured), 'list' or 'cards'."""
        return _api("POST", "/api/print/daily", params={"force": force, "layout_mode": layout})

    @srv.tool()
    def daemon_status() -> dict:
        """Daemon health, printer state, last poll, today's print status."""
        return _api("GET", "/api/status")

    return srv


def run() -> None:
    build_server().run(transport="stdio")
