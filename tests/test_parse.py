import json
from datetime import date
from pathlib import Path

from printcrastinator.sources.parse import parse_deck_cards, parse_vevent_components, parse_vtodo

FX = Path(__file__).parent / "fixtures"


def test_parse_pending_vtodo():
    t = parse_vtodo((FX / "todo_pending.ics").read_text(), "personal", "Personal")
    assert t is not None
    assert t.uid == "todo-1" and t.title == "Stromrechnung zahlen"
    assert t.due == date(2026, 9, 19)
    assert t.days_late(date(2026, 9, 22)) == 3
    assert t.suppression_key() == ("todo-1", "2026-09-19")


def test_completed_vtodo_is_skipped():
    assert parse_vtodo((FX / "todo_done.ics").read_text(), "p", "P") is None


def test_datetime_due_is_local_date():
    t = parse_vtodo((FX / "todo_datetime_due.ics").read_text(), "p", "P")
    assert t is not None and t.due is not None
    assert t.due in (date(2026, 9, 22), date(2026, 9, 23))  # depends on local tz


def test_events_for_day():
    evs = parse_vevent_components((FX / "events.ics").read_text(), "w", "Work", date(2026, 9, 22))
    titles = {e.title for e in evs}
    assert titles == {"Zahnarzt", "Team sync"}
    allday = next(e for e in evs if e.title == "Zahnarzt")
    assert allday.all_day is True
    timed = next(e for e in evs if e.title == "Team sync")
    assert timed.all_day is False and timed.calendar_name == "Work"
    assert timed.attendees == ("Anna Example", "bob@example.com")
    assert timed.organizer == "Me" and timed.location == "Room 3"


def test_deck_cards_skip_archived_and_done():
    fx = json.loads((FX / "deck_board.json").read_text())
    items = parse_deck_cards(fx["board"], fx["stacks"], "https://cloud.example.org")
    uids = [i.uid for i in items]
    assert uids == ["deck:100", "deck:101", "deck:104"]
    assert items[0].list_name == "Projects · Backlog"
    assert items[0].board_id == 3 and items[0].stack_id == 10
    assert items[1].due == date(2026, 9, 25)
    assert items[0].url.endswith("/board/3/card/100")


def test_event_window_is_padded_around_day(monkeypatch):
    """Nextcloud returns nothing for a window ending exactly at the next midnight, so the
    client must query day-1 .. day+2 and let the parser filter (regression)."""
    from datetime import datetime

    from printcrastinator.config import NextcloudConfig
    from printcrastinator.sources.caldav_client import CalDavClient, Collection

    captured = {}

    class FakeCal:
        def search(self, **kw):
            captured.update(kw)
            return []

    c = CalDavClient(NextcloudConfig("https://x", "u", "p"))
    monkeypatch.setattr(c, "_calendar", lambda col: FakeCal())
    monkeypatch.setattr(c, "ctag", lambda col: "")
    c.events_raw(Collection("p", "P", "https://x/p/", False, True), date(2026, 9, 22))
    assert isinstance(captured["start"], datetime)
    assert captured["start"].date() == date(2026, 9, 21)
    assert captured["end"].date() == date(2026, 9, 24)
    assert captured["expand"] is True


def test_birthdays_parsed_with_age_and_range():
    from printcrastinator.sources.parse import parse_birthdays

    bd = parse_birthdays((FX / "birthdays.ics").read_text(), date(2026, 9, 22), date(2026, 10, 6))
    assert [(b.name, b.day.isoformat(), b.age) for b in bd] == [
        ("Anna Example", "2026-09-22", 30),
        ("Bob", "2026-09-25", None),
    ]
