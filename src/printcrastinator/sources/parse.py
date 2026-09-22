"""Pure parsing of iCalendar data and Deck JSON into models. No network, fully testable."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from icalendar import Calendar as ICal

from ..models import CalendarEvent, TaskItem


def _local_tz():
    return datetime.now().astimezone().tzinfo


def _to_local_date(value: Any) -> date | None:
    if value is None:
        return None
    dt = getattr(value, "dt", value)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            return dt.date()
        return dt.astimezone(_local_tz()).date()
    if isinstance(dt, date):
        return dt
    return None


def _to_local_dt(value: Any, *, all_day_end: bool = False) -> tuple[datetime, bool]:
    dt = getattr(value, "dt", value)
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_local_tz())
        return dt.astimezone(_local_tz()), False
    return datetime.combine(dt, time.min, _local_tz()), True


def parse_vtodo(ics: str, list_id: str, list_name: str) -> TaskItem | None:
    """First pending VTODO in the ICS, or None if completed/cancelled."""
    cal = ICal.from_ical(ics)
    for comp in cal.walk("VTODO"):
        status = str(comp.get("STATUS", "")).upper()
        if status in ("COMPLETED", "CANCELLED") or comp.get("COMPLETED") is not None:
            return None
        created = comp.get("CREATED") or comp.get("DTSTAMP")
        created_dt = getattr(created, "dt", None)
        if isinstance(created_dt, date) and not isinstance(created_dt, datetime):
            created_dt = datetime.combine(created_dt, time.min)
        cats = comp.get("CATEGORIES")
        tags: list[str] = []
        if cats is not None:
            for c in cats if isinstance(cats, list) else [cats]:
                tags.extend(str(x) for x in getattr(c, "cats", [c]))
        return TaskItem(
            uid=str(comp.get("UID", "")),
            source="tasks",
            title=str(comp.get("SUMMARY", "")).strip() or "(untitled)",
            due=_to_local_date(comp.get("DUE")),
            list_id=list_id,
            list_name=list_name,
            created=created_dt,
            notes=str(comp.get("DESCRIPTION", "") or "").strip(),
            tags=tuple(t.strip() for t in tags if t.strip()),
        )
    return None


def parse_vevent_components(ics: str, cal_id: str, cal_name: str, day: date) -> list[CalendarEvent]:
    """All VEVENT instances in the ICS that overlap `day` (expanded data expected)."""
    out: list[CalendarEvent] = []
    tz = _local_tz()
    day_start = datetime.combine(day, time.min, tz)
    day_end = day_start + timedelta(days=1)
    cal = ICal.from_ical(ics)
    for comp in cal.walk("VEVENT"):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = comp.get("DTSTART")
        if dtstart is None:
            continue
        start, all_day = _to_local_dt(dtstart)
        dtend = comp.get("DTEND")
        if dtend is not None:
            end, _ = _to_local_dt(dtend)
        elif comp.get("DURATION") is not None:
            end = start + comp.get("DURATION").dt
        else:
            end = start + (timedelta(days=1) if all_day else timedelta(0))
        if end <= day_start or start >= day_end:
            continue
        rid = comp.get("RECURRENCE-ID")
        uid = str(comp.get("UID", ""))
        if rid is not None:
            uid = f"{uid}@{getattr(rid, 'dt', rid)}"
        out.append(
            CalendarEvent(
                uid=uid,
                title=str(comp.get("SUMMARY", "")).strip() or "(untitled)",
                start=start,
                end=end,
                all_day=all_day,
                calendar_id=cal_id,
                calendar_name=cal_name,
                location=str(comp.get("LOCATION", "") or ""),
            )
        )
    return out


def parse_deck_cards(
    board: dict[str, Any], stacks: list[dict[str, Any]], base_url: str = ""
) -> list[TaskItem]:
    """Cards of one board. Skips archived or done cards."""
    items: list[TaskItem] = []
    board_id = int(board["id"])
    board_title = str(board.get("title", ""))
    for stack in stacks:
        stack_id = int(stack["id"])
        stack_title = str(stack.get("title", ""))
        for card in stack.get("cards") or []:
            if card.get("archived") or card.get("done"):
                continue
            due_raw = card.get("duedate")
            due = None
            if due_raw:
                try:
                    due = datetime.fromisoformat(str(due_raw).replace("Z", "+00:00"))
                    due = due.astimezone(_local_tz()).date()
                except ValueError:
                    due = None
            created_raw = card.get("createdAt")
            created = None
            if isinstance(created_raw, int | float):
                created = datetime.fromtimestamp(created_raw, _local_tz())
            items.append(
                TaskItem(
                    uid=f"deck:{card['id']}",
                    source="deck",
                    title=str(card.get("title", "")).strip() or "(untitled)",
                    due=due,
                    list_id=f"{board_id}/{stack_id}",
                    list_name=f"{board_title} · {stack_title}",
                    created=created,
                    url=f"{base_url}/index.php/apps/deck/#/board/{board_id}/card/{card['id']}"
                    if base_url
                    else "",
                    board_id=board_id,
                    stack_id=stack_id,
                    notes=str(card.get("description") or "").strip(),
                    tags=tuple(
                        str(lab.get("title", ""))
                        for lab in (card.get("labels") or [])
                        if lab.get("title")
                    ),
                    card_id=int(card["id"]),
                )
            )
    return items
