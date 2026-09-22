from datetime import date
from pathlib import Path

from printcrastinator.config import Config, ExtraCalendar, load_config, save_config
from printcrastinator.sources.extra import ics_events_for_day

FX = Path(__file__).parent / "fixtures"


def test_ics_recurrence_expansion_for_day():
    evs = ics_events_for_day((FX / "uni.ics").read_text(), date(2026, 9, 22), "extra:uni", "Uni")
    titles = sorted(e.title for e in evs)
    assert titles == ["Exam", "Signals and Systems"]
    lecture = next(e for e in evs if e.title == "Signals and Systems")
    assert lecture.start.strftime("%H:%M") == "10:00" and lecture.location == "HS 3"
    assert lecture.calendar_name == "Uni" and not lecture.all_day


def test_extra_calendars_roundtrip(tmp_path):
    cfg = Config()
    cfg.extra_calendars.append(ExtraCalendar("University", "ics", "webcal://x/y.ics"))
    cfg.extra_calendars.append(ExtraCalendar("Work", "caldav", "https://c/dav", "u", "p"))
    p = save_config(cfg, tmp_path / "c.toml")
    back = load_config(p)
    assert [c.name for c in back.extra_calendars] == ["University", "Work"]
    assert back.extra_calendars[1].password == "p"
    assert back.extra_calendars[0].id == "extra:university"
