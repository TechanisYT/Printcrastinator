from datetime import date, timedelta

from printcrastinator.config import Config
from printcrastinator.daemon import Daemon
from printcrastinator.db import Database
from printcrastinator.models import TaskItem
from printcrastinator.printer.escpos_out import PrinterError

DAY = date.today()


class FakePrinter:
    def __init__(self, fail=False):
        self.images = []
        self.fail = fail

    def print_image(self, img):
        if self.fail:
            raise PrinterError("no paper")
        self.images.append(img)

    def print_calibration(self):
        pass

    def feed(self, mm=None):
        pass


def make(tmp_path, tasks, cards=(), fail=False, hour=0):
    cfg = Config()
    cfg.nextcloud.url, cfg.nextcloud.username, cfg.nextcloud.app_password = "https://x", "u", "p"
    cfg.daily.earliest_hour = hour
    cfg.slips.debounce_seconds = 0
    cfg.screen.notify = False
    d = Daemon(cfg, Database(tmp_path / "s.sqlite3"))
    d.printer = FakePrinter(fail)

    async def fake_refresh(today):
        d.state.tasks = list(tasks)
        d.state.cards = list(cards)
        d.state.errors = {}
        return True

    d._refresh_sources = fake_refresh
    return d


def t(uid, title, due=DAY):
    return TaskItem(uid, "tasks", title, due, "personal", "Personal")


async def test_daily_prints_once(tmp_path):
    d = make(tmp_path, [t("a", "Do it")])
    r1 = await d.maybe_print_daily()
    r2 = await d.maybe_print_daily()
    assert r1["printed"] is True and r2["printed"] is False
    assert len(d.printer.images) == 1
    assert d.db.daily_status(DAY)["printed_on_paper"] == 1


async def test_empty_day_stays_off_paper(tmp_path):
    d = make(tmp_path, [])
    r = await d.maybe_print_daily()
    assert r["printed"] is False and r["reason"] == "no tasks"
    assert d.printer.images == []
    assert d.db.daily_status(DAY)["printed_on_paper"] == 0
    # and it does not retry the paper print later that day
    assert (await d.maybe_print_daily())["reason"] == "already done today"


async def test_printer_failure_releases_claim(tmp_path):
    d = make(tmp_path, [t("a", "Do it")], fail=True)
    r = await d.maybe_print_daily()
    assert r["printed"] is False and "no paper" in r["reason"]
    assert d.db.daily_status(DAY) is None
    d.printer.fail = False
    assert (await d.maybe_print_daily())["printed"] is True


async def test_earliest_hour_gate(tmp_path):
    d = make(tmp_path, [t("a", "Do it")], hour=24)
    assert (await d.maybe_print_daily())["reason"] == "before earliest hour"
    assert (await d.maybe_print_daily(force=True))["printed"] is True


async def test_slips_seed_then_print_new_items_once(tmp_path):
    d = make(tmp_path, [t("a", "Existing")])
    await d.poll_once()  # seeds
    assert d.printer.images == [] or len(d.printer.images) == 1  # daily may print
    daily_count = len(d.printer.images)

    async def refresh2(today):
        d.state.tasks = [t("a", "Existing"), t("b", "Brand new", DAY + timedelta(days=2))]
        d.state.errors = {}
        return True

    d._refresh_sources = refresh2
    await d.poll_once()
    assert len(d.printer.images) == daily_count + 1  # one slip
    await d.poll_once()
    assert len(d.printer.images) == daily_count + 1  # not again
    assert d.db.is_seen("b", "tasks")


async def test_suppressed_task_is_not_printed(tmp_path):
    d = make(tmp_path, [t("a", "Hidden")])
    d.db.suppress("a", DAY.isoformat(), "Hidden")
    r = await d.maybe_print_daily()
    assert r["reason"] == "no tasks"


async def test_print_selection_refuses_without_filter(tmp_path):
    import pytest

    d = make(tmp_path, [t("a", "Do it")])
    with pytest.raises(ValueError):
        await d.print_selection("Everything")
    assert d.printer.images == []
    r = await d.print_selection("Personal", list_ids=["personal"])
    assert r["printed"] and len(d.printer.images) == 1


async def test_new_event_today_reprints_calendar(tmp_path):
    from datetime import datetime

    from printcrastinator.models import CalendarEvent

    d = make(tmp_path, [t("a", "Existing")])
    tz = datetime.now().astimezone().tzinfo
    ev = CalendarEvent(
        "e-new",
        "Pizza",
        datetime.now(tz).replace(hour=18, minute=0),
        datetime.now(tz).replace(hour=19, minute=0),
        False,
        "jj",
        "JJ",
    )
    await d.poll_once()  # seeds
    n0 = len(d.printer.images)

    async def refresh2(today):
        d.state.tasks = [t("a", "Existing")]
        d.state.events = [ev]
        d.state.errors = {}
        return True

    d._refresh_sources = refresh2
    await d.poll_once()
    assert len(d.printer.images) == n0 + 1  # calendar reprinted once
    await d.poll_once()
    assert len(d.printer.images) == n0 + 1  # not again
    assert d.db.is_seen("e-new", "event")
