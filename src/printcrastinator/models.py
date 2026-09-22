"""Domain models shared by sources, agenda, rendering and the API."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

Source = Literal["tasks", "deck"]


@dataclass(frozen=True)
class TaskItem:
    uid: str
    source: Source
    title: str
    due: date | None
    list_id: str
    list_name: str
    created: datetime | None = None
    url: str = ""
    # Deck only
    board_id: int | None = None
    stack_id: int | None = None
    completed: bool = False

    def days_late(self, today: date) -> int:
        """Positive when overdue, 0 when due today, negative when in the future."""
        if self.due is None:
            return 0
        return (today - self.due).days

    def suppression_key(self) -> tuple[str, str]:
        return (self.uid, self.due.isoformat() if self.due else "")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["due"] = self.due.isoformat() if self.due else None
        d["created"] = self.created.isoformat() if self.created else None
        return d


@dataclass(frozen=True)
class CalendarEvent:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool
    calendar_id: str
    calendar_name: str
    location: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["start"] = self.start.isoformat()
        d["end"] = self.end.isoformat()
        return d


@dataclass
class TaskGroup:
    """A named group of tasks on the receipt, e.g. 'Projects · Backlog'."""

    title: str
    items: list[TaskItem] = field(default_factory=list)


@dataclass
class DailyAgenda:
    day: date
    events: list[CalendarEvent] = field(default_factory=list)
    overdue: list[TaskItem] = field(default_factory=list)
    due_today: list[TaskItem] = field(default_factory=list)
    always: list[TaskGroup] = field(default_factory=list)
    # overdue tasks left off by the overdue filters (still open in Nextcloud)
    overdue_hidden: int = 0

    @property
    def always_items(self) -> list[TaskItem]:
        return [t for g in self.always for t in g.items]

    @property
    def all_tasks(self) -> list[TaskItem]:
        return self.overdue + self.due_today + self.always_items

    @property
    def has_tasks(self) -> bool:
        return bool(self.all_tasks)

    @property
    def has_events(self) -> bool:
        return bool(self.events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "events": [e.to_dict() for e in self.events],
            "overdue": [t.to_dict() for t in self.overdue],
            "due_today": [t.to_dict() for t in self.due_today],
            "always": [
                {"title": g.title, "items": [t.to_dict() for t in g.items]} for g in self.always
            ],
            "overdue_hidden": self.overdue_hidden,
            "has_tasks": self.has_tasks,
            "has_events": self.has_events,
        }
