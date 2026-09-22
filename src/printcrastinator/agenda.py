"""Build the DailyAgenda from fetched items and the rules stored in the database."""

from __future__ import annotations

from datetime import date

from .models import CalendarEvent, DailyAgenda, TaskGroup, TaskItem


def build(
    day: date,
    tasks: list[TaskItem],
    cards: list[TaskItem],
    events: list[CalendarEvent],
    *,
    suppressed: set[tuple[str, str]],
    always_lists: set[str],
    always_stacks: set[tuple[int, int]],
) -> DailyAgenda:
    overdue: list[TaskItem] = []
    due_today: list[TaskItem] = []
    groups: dict[str, TaskGroup] = {}
    seen: set[str] = set()

    for t in tasks + cards:
        if t.completed or t.uid in seen or t.suppression_key() in suppressed:
            continue
        seen.add(t.uid)
        always = (
            t.source == "deck"
            and t.board_id is not None
            and (t.board_id, t.stack_id) in always_stacks
        ) or (t.source == "tasks" and t.list_id in always_lists)
        if t.due is not None and t.due < day:
            overdue.append(t)
        elif t.due == day:
            due_today.append(t)
        elif always:
            groups.setdefault(t.list_name, TaskGroup(t.list_name)).items.append(t)

    overdue.sort(key=lambda t: (t.due, t.title.lower()))  # type: ignore[arg-type]
    due_today.sort(key=lambda t: t.title.lower())
    for g in groups.values():
        g.items.sort(key=lambda t: (t.due is None, t.due or day, t.title.lower()))
    ev = sorted(events, key=lambda e: (not e.all_day, e.start, e.title.lower()))
    return DailyAgenda(
        day=day,
        events=ev,
        overdue=overdue,
        due_today=due_today,
        always=[groups[k] for k in sorted(groups)],
    )


def candidates(tasks: list[TaskItem], cards: list[TaskItem]) -> list[TaskItem]:
    """Everything that could appear, for the Tasks page (unsorted, incl. suppressed)."""
    seen: set[str] = set()
    out = []
    for t in tasks + cards:
        if t.completed or t.uid in seen:
            continue
        seen.add(t.uid)
        out.append(t)
    return out
