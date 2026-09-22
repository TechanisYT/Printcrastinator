"""Labels and date names. Deliberately not using locale() so output is deterministic."""

from __future__ import annotations

from datetime import date

LABELS: dict[str, dict[str, str]] = {
    "en": {
        "today": "TODAY",
        "all_day": "all day",
        "overdue": "OVERDUE",
        "due_today": "DUE TODAY",
        "no_tasks": "No tasks for today.",
        "no_events": "No events today.",
        "new": "NEW",
        "summary_overdue": "overdue",
        "summary_today": "today",
        "summary_always": "listed",
        "tear": "tear here",
        "late": "-{n}d",
        "due_prefix": "due",
        "today_word": "today",
        "older_overdue": "+{n} older overdue not shown",
        "cut": "cut",
        "src_tasks": "Tasks",
        "src_deck": "Deck",
    },
    "de": {
        "today": "HEUTE",
        "all_day": "ganztägig",
        "overdue": "ÜBERFÄLLIG",
        "due_today": "HEUTE FÄLLIG",
        "no_tasks": "Keine Aufgaben für heute.",
        "no_events": "Keine Termine heute.",
        "new": "NEU",
        "summary_overdue": "überfällig",
        "summary_today": "heute",
        "summary_always": "gelistet",
        "tear": "hier abreißen",
        "late": "-{n}T",
        "due_prefix": "fällig",
        "today_word": "heute",
        "older_overdue": "+{n} ältere überfällige nicht gezeigt",
        "cut": "schneiden",
        "src_tasks": "Aufgaben",
        "src_deck": "Deck",
    },
}

WEEKDAYS = {
    "en": ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"],
    "de": ["MONTAG", "DIENSTAG", "MITTWOCH", "DONNERSTAG", "FREITAG", "SAMSTAG", "SONNTAG"],
}
MONTHS = {
    "en": ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"],
    "de": ["JAN", "FEB", "MÄR", "APR", "MAI", "JUN", "JUL", "AUG", "SEP", "OKT", "NOV", "DEZ"],
}

QUOTES = [
    "Done is better than perfect.",
    "Start where you are.",
    "One box at a time.",
    "Paper doesn't procrastinate.",
    "Small steps, real ink.",
    "Today counts. Cross it off.",
    "Momentum beats motivation.",
    "Finish something.",
    "Nothing here is impossible.",
    "Make the pen useful.",
]


def label(lang: str, key: str, **kw: object) -> str:
    table = LABELS.get(lang, LABELS["en"])
    return table.get(key, LABELS["en"][key]).format(**kw)


def weekday_name(lang: str, d: date) -> str:
    return WEEKDAYS.get(lang, WEEKDAYS["en"])[d.weekday()]


def date_line(lang: str, d: date) -> str:
    months = MONTHS.get(lang, MONTHS["en"])
    if lang == "de":
        return f"{d.day}. {months[d.month - 1]} {d.year}"
    return f"{d.day} {months[d.month - 1]} {d.year}"


def quote_for(d: date, quotes: list[str] | None = None) -> str:
    pool = quotes if quotes else QUOTES
    return pool[d.toordinal() % len(pool)]
