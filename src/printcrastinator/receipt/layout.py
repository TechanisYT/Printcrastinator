"""Turn agendas and new-item lists into a Receipt (device independent)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from ..models import CalendarEvent, DailyAgenda, TaskGroup, TaskItem
from . import i18n
from .model import (
    CheckItem,
    EventLine,
    Picture,
    Receipt,
    Rule,
    SectionHeader,
    Spacer,
    TearLine,
    Text,
)


@dataclass
class Options:
    logo: str = ""  # path to an image file, empty = none
    logo_max_height: int = 160
    logo_dither: bool = False
    quote: bool = False


def _event_time(ev: CalendarEvent, lang: str) -> str:
    if ev.all_day:
        return i18n.label(lang, "all_day")
    start = ev.start.strftime("%H:%M")
    end = ev.end.strftime("%H:%M")
    return f"{start}–{end}" if end != start else start


def _header(r: Receipt, day: date, lang: str, opt: Options) -> None:
    if opt.logo:
        r.add(Picture(opt.logo, opt.logo_max_height, opt.logo_dither), Spacer(10))
    r.add(
        Rule(3),
        Spacer(10),
        Text(i18n.weekday_name(lang, day), "headline", "center", wrap=False),
        Text(i18n.date_line(lang, day), "headline", "center", wrap=False),
        Spacer(10),
        Rule(3),
        Spacer(14),
    )


def _events(r: Receipt, agenda: DailyAgenda, lang: str) -> None:
    r.add(SectionHeader(i18n.label(lang, "today")))
    if not agenda.events:
        r.add(Text(i18n.label(lang, "no_events"), "small"))
    for ev in agenda.events:
        r.add(EventLine(_event_time(ev, lang), ev.title))
    r.add(Spacer(18))


def _task_marker(t: TaskItem, day: date, lang: str) -> str:
    late = t.days_late(day)
    if t.due is None:
        return ""
    if late > 0:
        return i18n.label(lang, "late", n=late)
    if late < 0:
        return t.due.strftime("%d.%m")
    return ""


def _footer(r: Receipt, agenda: DailyAgenda, lang: str, opt: Options) -> None:
    r.add(Spacer(6), Rule(1), Spacer(8))
    if agenda.overdue_hidden:
        r.add(Text(i18n.label(lang, "older_overdue", n=agenda.overdue_hidden), "small", "center"))
    if opt.quote:
        r.add(Text(f"“{i18n.quote_for(agenda.day)}”", "small", "center"))
    r.add(Spacer(12), TearLine())


def daily_receipt(
    agenda: DailyAgenda, lang: str = "en", layout: str = "list", opt: Options | None = None
) -> Receipt:
    opt = opt or Options()
    if layout == "cards":
        return cards_receipt(agenda, lang, opt)
    r = Receipt()
    _header(r, agenda.day, lang, opt)
    _events(r, agenda, lang)
    if not agenda.has_tasks:
        r.add(Text(i18n.label(lang, "no_tasks"), "body", "center"), Spacer(8))
        _footer(r, agenda, lang, opt)
        return r
    if agenda.overdue:
        r.add(SectionHeader(i18n.label(lang, "overdue"), hint=str(len(agenda.overdue))))
        for t in agenda.overdue:
            r.add(CheckItem(t.title, right=_task_marker(t, agenda.day, lang)))
        r.add(Spacer(14))
    if agenda.due_today:
        r.add(SectionHeader(i18n.label(lang, "due_today"), hint=str(len(agenda.due_today))))
        for t in agenda.due_today:
            r.add(CheckItem(t.title))
        r.add(Spacer(14))
    for group in agenda.always:
        if not group.items:
            continue
        r.add(SectionHeader(group.title, hint=str(len(group.items))))
        for t in group.items:
            r.add(CheckItem(t.title, right=_task_marker(t, agenda.day, lang)))
        r.add(Spacer(14))
    _footer(r, agenda, lang, opt)
    return r


def cards_receipt(agenda: DailyAgenda, lang: str = "en", opt: Options | None = None) -> Receipt:
    """Every task in its own block with cut lines between, for scissors."""
    opt = opt or Options()
    r = Receipt()
    _header(r, agenda.day, lang, opt)
    _events(r, agenda, lang)
    if not agenda.has_tasks:
        r.add(Text(i18n.label(lang, "no_tasks"), "body", "center"), Spacer(8))
        _footer(r, agenda, lang, opt)
        return r
    day = agenda.day

    def card(t: TaskItem, section: str) -> None:
        r.add(Spacer(10), TearLine(), Spacer(14))
        marker = _task_marker(t, day, lang)
        meta = " · ".join(
            p for p in (section, t.list_name if t.source == "tasks" else "", marker) if p
        )
        r.add(CheckItem(t.title, meta=meta), Spacer(10))

    for t in agenda.overdue:
        card(t, i18n.label(lang, "overdue"))
    for t in agenda.due_today:
        card(t, i18n.label(lang, "due_today"))
    for g in agenda.always:
        for t in g.items:
            card(t, g.title)
    r.add(Spacer(10), TearLine(), Spacer(6))
    _footer(r, agenda, lang, opt)
    return r


def slip_receipt(items: list[TaskItem], now: datetime, lang: str = "en") -> Receipt:
    r = Receipt()
    r.add(
        Rule(3),
        Spacer(6),
        SectionHeader(f"{i18n.label(lang, 'new')}  ·  {now.strftime('%H:%M')}"),
    )
    today = now.date()
    for t in items:
        if t.due is None:
            due = ""
        elif t.due == today:
            due = i18n.label(lang, "today_word")
        else:
            due = f"{i18n.label(lang, 'due_prefix')} {t.due.strftime('%d.%m')}"
        meta = " · ".join(p for p in (t.list_name, due) if p)
        r.add(CheckItem(t.title, meta=meta))
    r.add(Spacer(6), Rule(1), Spacer(4), TearLine())
    return r


# ---- sample data for preview and snapshot tests ------------------------------------


def sample_agenda(day: date | None = None) -> DailyAgenda:
    day = day or date(2026, 9, 22)
    tz = datetime.now().astimezone().tzinfo
    ev = [
        CalendarEvent(
            "e1",
            "Zahnarzt",
            datetime.combine(day, datetime.min.time(), tz),
            datetime.combine(day + timedelta(days=1), datetime.min.time(), tz),
            True,
            "p",
            "Personal",
        ),
        CalendarEvent(
            "e2",
            "Team sync",
            datetime.combine(day, datetime.min.time(), tz).replace(hour=9),
            datetime.combine(day, datetime.min.time(), tz).replace(hour=10, minute=30),
            False,
            "w",
            "Work",
        ),
        CalendarEvent(
            "e3",
            "Lunch with Anna at the new Vietnamese place",
            datetime.combine(day, datetime.min.time(), tz).replace(hour=12, minute=30),
            datetime.combine(day, datetime.min.time(), tz).replace(hour=13, minute=30),
            False,
            "p",
            "Personal",
        ),
    ]

    def t(
        uid: str, title: str, due: date | None, src: str = "tasks", ln: str = "Personal"
    ) -> TaskItem:
        return TaskItem(uid, src, title, due, ln.lower(), ln)  # type: ignore[arg-type]

    return DailyAgenda(
        day=day,
        events=ev,
        overdue=[
            t("o1", "Stromrechnung zahlen", day - timedelta(days=3)),
            t("o2", "Reply to Anna about the weekend trip plans", day - timedelta(days=1)),
        ],
        due_today=[
            t("d1", "Finish thesis chapter 3 introduction and send it to Müller", day),
            t("d2", "Buy filament", day),
            t("d3", "Straße & Zähne: Übung €5", day),
        ],
        always=[
            TaskGroup(
                "Projects · Backlog",
                [
                    t("a1", "Design PCB rev 2", None, "deck", "Projects · Backlog"),
                    t(
                        "a2",
                        "Order M3 screws",
                        day + timedelta(days=3),
                        "deck",
                        "Projects · Backlog",
                    ),
                ],
            )
        ],
    )


def empty_agenda(day: date | None = None) -> DailyAgenda:
    day = day or date(2026, 9, 22)
    return DailyAgenda(day=day, events=sample_agenda(day).events[:2])


def sample_slip_items(day: date | None = None) -> list[TaskItem]:
    day = day or date(2026, 9, 22)
    return [
        TaskItem("n1", "deck", "Order screws", day + timedelta(days=3), "5", "Projects · Backlog"),
        TaskItem("n2", "tasks", "Call landlord about the heating", day, "personal", "Personal"),
        TaskItem("n3", "tasks", "Read the Pillow docs", None, "personal", "Personal"),
    ]
