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


def test_deck_cards_skip_archived_and_done():
    fx = json.loads((FX / "deck_board.json").read_text())
    items = parse_deck_cards(fx["board"], fx["stacks"], "https://cloud.example.org")
    uids = [i.uid for i in items]
    assert uids == ["deck:100", "deck:101", "deck:104"]
    assert items[0].list_name == "Projects · Backlog"
    assert items[0].board_id == 3 and items[0].stack_id == 10
    assert items[1].due == date(2026, 9, 25)
    assert items[0].url.endswith("/board/3/card/100")
