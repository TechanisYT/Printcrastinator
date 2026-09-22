"""CLI-side client: talk to the running daemon, fall back to in-process code."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import httpx

from .config import Config
from .models import Birthday, CalendarEvent, DailyAgenda, TaskGroup, TaskItem

log = logging.getLogger(__name__)


class DaemonUnavailable(RuntimeError):
    pass


class DaemonError(RuntimeError):
    pass


def _call(cfg: Config, method: str, path: str, timeout: float = 120, **params: Any) -> Any:
    try:
        r = httpx.request(method, cfg.api_base + path, params=params, timeout=timeout)
    except httpx.ConnectError as exc:
        raise DaemonUnavailable(str(exc)) from exc
    if r.status_code >= 400:
        try:
            detail = r.json().get("detail", r.text)
        except ValueError:
            detail = r.text
        raise DaemonError(f"daemon answered {r.status_code}: {detail}")
    if r.headers.get("content-type", "").startswith("application/json"):
        return r.json()
    return r.content


def daemon_reachable(cfg: Config) -> bool:
    try:
        _call(cfg, "GET", "/api/status", timeout=3)
        return True
    except (DaemonUnavailable, httpx.HTTPError):
        return False


def _task_from_dict(x: dict[str, Any]) -> TaskItem:
    return TaskItem(
        uid=x["uid"],
        source=x["source"],
        title=x["title"],
        due=date.fromisoformat(x["due"]) if x.get("due") else None,
        list_id=x["list_id"],
        list_name=x["list_name"],
        created=datetime.fromisoformat(x["created"]) if x.get("created") else None,
        url=x.get("url", ""),
        board_id=x.get("board_id"),
        stack_id=x.get("stack_id"),
        notes=x.get("notes", ""),
        tags=tuple(x.get("tags") or ()),
        card_id=x.get("card_id"),
    )


def _event_from_dict(x: dict[str, Any]) -> CalendarEvent:
    return CalendarEvent(
        uid=x["uid"],
        title=x["title"],
        start=datetime.fromisoformat(x["start"]),
        end=datetime.fromisoformat(x["end"]),
        all_day=x["all_day"],
        calendar_id=x["calendar_id"],
        calendar_name=x["calendar_name"],
        location=x.get("location", ""),
        attendees=tuple(x.get("attendees") or ()),
        organizer=x.get("organizer", ""),
        description=x.get("description", ""),
    )


def agenda_from_dict(d: dict[str, Any]) -> DailyAgenda:
    def task(x: dict[str, Any]) -> TaskItem:
        return TaskItem(
            uid=x["uid"],
            source=x["source"],
            title=x["title"],
            due=date.fromisoformat(x["due"]) if x.get("due") else None,
            list_id=x["list_id"],
            list_name=x["list_name"],
            created=datetime.fromisoformat(x["created"]) if x.get("created") else None,
            url=x.get("url", ""),
            board_id=x.get("board_id"),
            stack_id=x.get("stack_id"),
            notes=x.get("notes", ""),
            tags=tuple(x.get("tags") or ()),
            card_id=x.get("card_id"),
        )

    def event(x: dict[str, Any]) -> CalendarEvent:
        return CalendarEvent(
            uid=x["uid"],
            title=x["title"],
            start=datetime.fromisoformat(x["start"]),
            end=datetime.fromisoformat(x["end"]),
            all_day=x["all_day"],
            calendar_id=x["calendar_id"],
            calendar_name=x["calendar_name"],
            location=x.get("location", ""),
            attendees=tuple(x.get("attendees") or ()),
            organizer=x.get("organizer", ""),
            description=x.get("description", ""),
        )

    return DailyAgenda(
        day=date.fromisoformat(d["day"]),
        events=[event(e) for e in d["events"]],
        overdue=[task(t) for t in d["overdue"]],
        due_today=[task(t) for t in d["due_today"]],
        always=[TaskGroup(g["title"], [task(t) for t in g["items"]]) for g in d["always"]],
        birthdays=[
            Birthday(b["name"], date.fromisoformat(b["day"]), b.get("age"), b.get("uid", ""))
            for b in d.get("birthdays", [])
        ],
    )


def _local_daemon(cfg: Config):
    from .daemon import Daemon

    return Daemon(cfg)


def get_agenda_or_build(cfg: Config, refresh: bool = False) -> DailyAgenda:
    try:
        return agenda_from_dict(_call(cfg, "GET", "/api/agenda", refresh=refresh))
    except DaemonUnavailable:
        import asyncio

        return asyncio.run(_local_daemon(cfg).agenda(refresh=True))


def print_daily(cfg: Config, force: bool = False, layout: str = "") -> dict[str, Any]:
    try:
        return _call(cfg, "POST", "/api/print/daily", force=force, layout_mode=layout)
    except DaemonUnavailable:
        import asyncio

        return asyncio.run(
            _local_daemon(cfg).maybe_print_daily(
                force=force, reason="cli-direct", layout_mode=layout or None
            )
        )


def print_test(cfg: Config, sweep: bool = False) -> None:
    try:
        _call(cfg, "POST", "/api/print/test", sweep=sweep)
    except DaemonUnavailable:
        _local_daemon(cfg).printer.print_calibration(sweep)


def notify(cfg: Config) -> None:
    try:
        _call(cfg, "POST", "/api/notify")
    except DaemonUnavailable:
        from .render import notify as n

        n.send(get_agenda_or_build(cfg), cfg.ui.language)
