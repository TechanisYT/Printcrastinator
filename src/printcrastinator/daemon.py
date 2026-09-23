"""Poll loop, daily gate, new-task slips, D-Bus wake triggers. Owns the printer."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from . import agenda as agenda_mod
from . import logos
from .config import Config, load_config
from .db import Database
from .models import Birthday, CalendarEvent, DailyAgenda, TaskGroup, TaskItem
from .printer.escpos_out import Printer, PrinterError
from .receipt import i18n, layout
from .render import image as render_image
from .render import notify, screen
from .sources.caldav_client import CalDavClient, Collection
from .sources.deck import DeckClient, DeckStack
from .sources.extra import fetch_extra

log = logging.getLogger(__name__)

BACKOFF_MIN = 5
BACKOFF_MAX = 300


def _retry(fn, attempts: int = 2, delay: float = 2.0):
    """Call fn, retrying once on failure; returns the result or the last exception."""
    last: BaseException | None = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            if i + 1 < attempts:
                time.sleep(delay)
    return last


@dataclass
class SourceState:
    tasks: list[TaskItem] = field(default_factory=list)
    cards: list[TaskItem] = field(default_factory=list)
    events: list[CalendarEvent] = field(default_factory=list)
    collections: list[Collection] = field(default_factory=list)
    stacks: list[DeckStack] = field(default_factory=list)
    birthdays: list[Birthday] = field(default_factory=list)
    last_ok: float = 0.0
    last_error: str = ""
    errors: dict[str, str] = field(default_factory=dict)


class Daemon:
    def __init__(self, cfg: Config | None = None, db: Database | None = None) -> None:
        self.cfg = cfg or load_config()
        self.db = db or Database()
        self.printer = Printer(self.cfg.printer)
        self.state = SourceState()
        self.started_at = time.time()
        self.last_poll_at: float = 0.0
        self.polls = 0
        self._pending: dict[tuple[str, str], TaskItem] = {}
        self._pending_since: float = 0.0
        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._tasks: list[asyncio.Task[Any]] = []
        self.seeded = self.db.seen_count() > 0
        self.db.seed_quotes(i18n.QUOTES)
        self._restore_snapshot()

    # ---- snapshot: last successful fetch survives restarts and failed polls ------------------

    def _save_snapshot(self) -> None:
        from .client import agenda_from_dict  # noqa: F401  (same JSON shape helpers)

        payload = {
            "tasks": [t.to_dict() for t in self.state.tasks],
            "cards": [t.to_dict() for t in self.state.cards],
            "events": [e.to_dict() for e in self.state.events],
            "collections": [c.__dict__ for c in self.state.collections],
            "stacks": [st.__dict__ for st in self.state.stacks],
            "birthdays": [b.to_dict() for b in self.state.birthdays],
            "at": time.time(),
        }
        self.db.kv_set("snapshot", json.dumps(payload))

    def _restore_snapshot(self) -> None:
        raw = self.db.kv_get("snapshot")
        if not raw:
            return
        try:
            from .client import _event_from_dict, _task_from_dict

            d = json.loads(raw)
            self.state.tasks = [_task_from_dict(x) for x in d["tasks"]]
            self.state.cards = [_task_from_dict(x) for x in d["cards"]]
            self.state.events = [_event_from_dict(x) for x in d["events"]]
            self.state.collections = [Collection(**c) for c in d["collections"]]
            self.state.stacks = [
                DeckStack(**{k: v for k, v in st.items() if k in DeckStack.__dataclass_fields__})
                for st in d["stacks"]
            ]
            self.state.birthdays = [
                Birthday(b["name"], date.fromisoformat(b["day"]), b.get("age"), b.get("uid", ""))
                for b in d.get("birthdays", [])
            ]
            self.state.last_ok = float(d.get("at", 0))
            self.state.last_error = "using data from last successful fetch"
            log.info("restored snapshot from %s", time.ctime(self.state.last_ok))
        except Exception as exc:  # noqa: BLE001
            log.warning("snapshot restore failed: %s", exc)

    # ---- lifecycle -----------------------------------------------------------------------

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._poll_loop(), name="poll"))
        self._tasks.append(asyncio.create_task(self._dbus_loop(), name="dbus"))
        log.info("daemon started (device %s, api %s)", self.cfg.printer.device, self.cfg.api_base)

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def reload_config(self, cfg: Config) -> None:
        self.cfg = cfg
        self.printer = Printer(cfg.printer)
        self._wake.set()

    def request_poll(self) -> None:
        self._wake.set()

    # ---- polling ---------------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        backoff = BACKOFF_MIN
        while True:
            try:
                ok = await self.poll_once()
                backoff = BACKOFF_MIN if ok else min(backoff * 2, BACKOFF_MAX)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("poll failed")
                backoff = min(backoff * 2, BACKOFF_MAX)
                ok = False
            wait = self.cfg.server.poll_interval if ok else backoff
            if self._pending:
                wait = min(wait, max(1, self._slip_due_in()))
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=wait)
            except TimeoutError:
                pass
            self._wake.clear()

    async def poll_once(self) -> bool:
        """Fetch everything, update state, run gate and slips. True if all sources succeeded."""
        if not self.cfg.nextcloud.configured:
            self.state.last_error = "Nextcloud not configured"
            return False
        async with self._lock:
            today = date.today()
            ok = await self._refresh_sources(today)
            self.last_poll_at = time.time()
            self.polls += 1
            if ok or self.state.tasks or self.state.cards:
                self._queue_new_items()
        await self.maybe_print_daily(screen=True)
        await self.maybe_print_slip()
        return ok

    async def _refresh_sources(self, today: date) -> bool:
        nc = self.cfg.nextcloud
        cal = CalDavClient(nc, self.db)
        deck = DeckClient(nc)
        disabled = self.db.disabled_calendars()

        def caldav_both():
            # One client, sequential: parallel CalDAV sessions trip Nextcloud's auth throttling.
            r_t: Any = _retry(lambda: cal.fetch_tasks(today))
            r_e: Any = _retry(lambda: cal.fetch_events(today, disabled))
            r_x = fetch_extra(self.cfg.extra_calendars, nc, today, disabled, self.db)
            r_b: Any = []
            if self.cfg.daily.show_birthdays:
                r_b = _retry(
                    lambda: cal.fetch_birthdays(
                        self.cfg.daily.birthdays_calendar,
                        today,
                        today + timedelta(days=self.cfg.daily.birthdays_lookahead),
                    )
                )
            return r_t, r_e, r_x, r_b

        results = await asyncio.gather(
            asyncio.to_thread(caldav_both), deck.fetch(), return_exceptions=True
        )
        ok = True
        errors: dict[str, str] = {}
        r_both, r_deck = results
        r_extra: Any = None
        r_bdays: Any = []
        if isinstance(r_both, BaseException):
            r_tasks = r_events = r_both
        else:
            r_tasks, r_events, r_extra, r_bdays = r_both
        if isinstance(r_bdays, BaseException):
            errors["birthdays"] = str(r_bdays)
        else:
            self.state.birthdays = list(r_bdays)
        if isinstance(r_tasks, BaseException):
            ok, errors["tasks"] = False, str(r_tasks)
            log.warning("tasks fetch failed: %s", r_tasks)
        else:
            cols, self.state.tasks = r_tasks
            self._merge_collections(cols)
        if isinstance(r_events, BaseException):
            ok, errors["events"] = False, str(r_events)
            log.warning("events fetch failed: %s", r_events)
        else:
            cols, evs = r_events
            if self.cfg.daily.show_birthdays:  # birthdays get their own section
                evs = [e for e in evs if e.calendar_id != self.cfg.daily.birthdays_calendar]
            self.state.events = evs
            self._merge_collections(cols)
        if r_extra is not None:
            x_cols, x_events, x_errors = r_extra
            self._merge_collections(x_cols)
            self.state.events = self.state.events + x_events
            for name, err in x_errors.items():
                errors[f"calendar {name}"] = err
        if isinstance(r_deck, BaseException):
            ok, errors["deck"] = False, str(r_deck)
            log.warning("deck fetch failed: %s", r_deck)
        else:
            self.state.stacks, self.state.cards = r_deck
        self.state.errors = errors
        self.state.last_error = "; ".join(f"{k}: {v}" for k, v in errors.items())
        if ok:
            self.state.last_ok = time.time()
            self._save_snapshot()
        return ok

    def _merge_collections(self, cols: list[Collection]) -> None:
        by_id = {c.id: c for c in self.state.collections}
        for c in cols:
            by_id[c.id] = c
        self.state.collections = sorted(by_id.values(), key=lambda c: c.name.lower())

    # ---- agenda ------------------------------------------------------------------------------

    def build_agenda(self, day: date | None = None) -> DailyAgenda:
        day = day or date.today()
        ag = agenda_mod.build(
            day,
            self.state.tasks,
            self.state.cards,
            self.state.events,
            suppressed=self.db.suppressed_keys(),
            always_lists=self.db.always_print_lists(),
            always_stacks=self.db.always_print_stacks(),
            overdue_max_days=self.cfg.daily.overdue_max_days,
            overdue_max_count=self.cfg.daily.overdue_max_count,
            group_rank=self.group_rank(),
        )
        ag.birthdays = list(self.state.birthdays)
        return ag

    async def agenda(self, refresh: bool = False) -> DailyAgenda:
        if refresh or (self.last_poll_at == 0 and self.cfg.nextcloud.configured):
            async with self._lock:
                await self._refresh_sources(date.today())
                self.last_poll_at = time.time()
        return self.build_agenda()

    async def events_for(self, day: date) -> list[CalendarEvent]:
        """Events of an arbitrary day (today comes from state)."""
        if day == date.today():
            return list(self.state.events)
        nc = self.cfg.nextcloud
        disabled = self.db.disabled_calendars()

        def fetch():
            cal = CalDavClient(nc, self.db)
            _cols, evs = cal.fetch_events(day, disabled)
            if self.cfg.daily.show_birthdays:
                evs = [e for e in evs if e.calendar_id != self.cfg.daily.birthdays_calendar]
            _xc, xevs, _xe = fetch_extra(self.cfg.extra_calendars, nc, day, disabled, self.db)
            return evs + xevs

        return await asyncio.to_thread(fetch)

    async def agenda_for(self, day: date) -> DailyAgenda:
        if day == date.today():
            return await self.agenda()
        if self.last_poll_at == 0 and self.cfg.nextcloud.configured:
            await self.agenda(refresh=True)
        return agenda_mod.build(
            day,
            self.state.tasks,
            self.state.cards,
            await self.events_for(day),
            suppressed=self.db.suppressed_keys(),
            always_lists=self.db.always_print_lists(),
            always_stacks=self.db.always_print_stacks(),
            overdue_max_days=self.cfg.daily.overdue_max_days,
            overdue_max_count=self.cfg.daily.overdue_max_count,
            group_rank=self.group_rank(),
        )

    async def calendar_days(self, day_from: date, day_to: date) -> list[tuple[date, list]]:
        days = []
        d = day_from
        while d <= day_to and len(days) < 14:
            days.append((d, await self.events_for(d)))
            d += timedelta(days=1)
        return days

    async def print_calendar(self, day_from: date, day_to: date | None = None) -> dict[str, Any]:
        """Events only: a single day upright, several days as a rotated side-by-side table."""
        day_to = day_to or day_from
        days = await self.calendar_days(day_from, day_to)
        img = render_image.render(
            layout.calendar_receipt(days, self.cfg.ui.language, self.layout_options())
        )
        await asyncio.to_thread(self.printer.print_image, img)
        n = sum(len(e) for _, e in days)
        self.db.log("calendar", True, f"{day_from}..{day_to}: {n} events")
        return {
            "printed": True,
            "from": day_from.isoformat(),
            "to": day_to.isoformat(),
            "events": n,
        }

    async def print_day(self, day: date, layout_mode: str | None = None) -> dict[str, Any]:
        """Print the receipt for any day, no once-per-day gate, no screen output."""
        ag = await self.agenda_for(day)
        img = render_image.render(
            layout.daily_receipt(
                ag,
                self.cfg.ui.language,
                layout_mode or self.cfg.daily.layout,
                self.layout_options(),
            )
        )
        await asyncio.to_thread(self.printer.print_image, img)
        self.db.log("day", True, f"{day.isoformat()}: {len(ag.all_tasks)} tasks")
        return {"printed": True, "day": day.isoformat(), "tasks": len(ag.all_tasks)}

    def select_tasks(
        self,
        list_ids: list[str] | None = None,
        due_from: date | None = None,
        due_to: date | None = None,
        include_no_due: bool = True,
        overdue_only: bool = False,
        tags: list[str] | None = None,
        text: str = "",
        sources: list[str] | None = None,
    ) -> list[TaskItem]:
        """Filter open tasks. list_ids: task list ids or 'board/stack' (a bare board id like
        '4' matches every stack of that board)."""
        suppressed = self.db.suppressed_keys()
        today = date.today()
        resolved = [self._resolve_list_ref(x) for x in list_ids or []]
        out = []
        for t in self.candidates():
            if t.suppression_key() in suppressed:
                continue
            if sources and t.source not in sources:
                continue
            if resolved and not any(m(t) for m in resolved):
                continue
            if overdue_only and not (t.due and t.due < today):
                continue
            if t.due is None:
                if not include_no_due or overdue_only:
                    continue
            else:
                if due_from and t.due < due_from:
                    continue
                if due_to and t.due > due_to:
                    continue
            if tags and not any(tag.lower() in (x.lower() for x in t.tags) for tag in tags):
                continue
            if text and text.lower() not in (t.title + " " + t.notes).lower():
                continue
            out.append(t)
        return out

    def _resolve_list_ref(self, ref: str):
        """A list reference may be an id ('personal', '4/12', '4') or a name ('Eurocert',
        'Escort · Einkaufsliste', 'Escort'). A name that is both a task list and a Deck board
        matches both. Returns a predicate over TaskItem."""
        ref_l = ref.strip().lower()
        ids: set[str] = set()
        boards: set[int] = set()
        for c in self.state.collections:
            if c.vtodo and (c.id.lower() == ref_l or c.name.lower() == ref_l):
                ids.add(c.id)
        for st in self.state.stacks:
            full = f"{st.board_title} · {st.stack_title}".lower()
            if (
                f"{st.board_id}/{st.stack_id}" == ref_l
                or full == ref_l
                or full.replace(" · ", " ") == ref_l
            ):
                ids.add(f"{st.board_id}/{st.stack_id}")
            if str(st.board_id) == ref_l or st.board_title.lower() == ref_l:
                boards.add(st.board_id)
        if not ids and not boards:  # fall back to substring match on names
            for c in self.state.collections:
                if c.vtodo and ref_l in c.name.lower():
                    ids.add(c.id)
            for st in self.state.stacks:
                if ref_l in st.board_title.lower():
                    boards.add(st.board_id)
                elif ref_l in st.stack_title.lower():
                    ids.add(f"{st.board_id}/{st.stack_id}")

        def match(t: TaskItem) -> bool:
            return t.list_id in ids or (t.source == "deck" and t.board_id in boards)

        return match

    def group_by_list(self, items: list[TaskItem], lang: str | None = None) -> list[TaskGroup]:
        lang = lang or self.cfg.ui.language
        groups: dict[str, TaskGroup] = {}
        for t in sorted(items, key=lambda x: (x.due is None, x.due or date.max, x.title.lower())):
            name = f"{t.list_name} ({i18n.label(lang, 'src_' + t.source)})"
            groups.setdefault(name, TaskGroup(name)).items.append(t)
        return [groups[k] for k in sorted(groups, key=str.lower)]

    async def print_selection(self, title: str, **filters: Any) -> dict[str, Any]:
        items = self.select_tasks(**filters)
        groups = self.group_by_list(items)
        img = render_image.render(
            layout.custom_receipt(
                title, groups, date.today(), self.cfg.ui.language, self.layout_options()
            )
        )
        await asyncio.to_thread(self.printer.print_image, img)
        self.db.log("custom", True, f"{title}: {len(items)} tasks")
        return {"printed": True, "title": title, "tasks": len(items)}

    def candidates(self) -> list[TaskItem]:
        return agenda_mod.candidates(self.state.tasks, self.state.cards)

    # ---- daily print -------------------------------------------------------------------------

    def show_on_screen(self, ag: DailyAgenda) -> None:
        if self.cfg.screen.notify:
            notify.send(ag, self.cfg.ui.language)
        if self.cfg.screen.terminal:
            screen.open_terminal()

    def test_notification(self) -> str:
        parts = [notify.send_test()]
        if self.cfg.screen.terminal:
            parts.append(screen.open_terminal())
        return " · ".join(parts)

    def layout_options(self) -> layout.Options:
        return logos.options(self.cfg, self.db)

    async def test_full_cycle(self) -> dict[str, Any]:
        """Simulate a fresh morning: forget today's print, fetch, print, notify, open window."""
        self.db.release_daily(date.today())
        async with self._lock:
            await self._refresh_sources(date.today())
            self.last_poll_at = time.time()
        return await self.maybe_print_daily(force=True, reason="test-cycle", screen=True)

    async def maybe_print_daily(
        self,
        force: bool = False,
        reason: str = "poll",
        layout_mode: str | None = None,
        screen: bool = False,
    ) -> dict[str, Any]:
        """screen=True (automatic morning trigger / full-cycle test) also notifies and opens the
        terminal window. Manual prints from UI, CLI, TUI or AI stay silent."""
        today = date.today()
        now = datetime.now()
        if not force and now.hour < self.cfg.daily.earliest_hour:
            return {"printed": False, "reason": "before earliest hour"}
        if not self.cfg.nextcloud.configured:
            return {"printed": False, "reason": "not configured"}
        if not force and self.db.daily_status(today):
            return {"printed": False, "reason": "already done today"}
        ag = await self.agenda(refresh=self.last_poll_at == 0)
        if self.state.errors and not force and not (self.state.tasks or self.state.cards):
            # Nothing fetched successfully: do not claim the day with an empty list.
            return {"printed": False, "reason": f"sources failed: {self.state.last_error}"}
        if not force and not self.db.claim_daily(today):
            return {"printed": False, "reason": "already done today"}
        if force and not self.db.daily_status(today):
            self.db.claim_daily(today)
        if screen:
            self.show_on_screen(ag)
        on_paper = ag.has_tasks or (self.cfg.daily.print_calendar_only_days and ag.has_events)
        if not on_paper:
            self.db.mark_daily_printed(today, on_paper=False, task_count=0)
            self.db.log("daily", True, f"{reason}: nothing to print on paper")
            return {"printed": False, "reason": "no tasks", "on_screen": True}
        try:
            img = render_image.render(
                layout.daily_receipt(ag, self.cfg.ui.language, layout_mode or self.cfg.daily.layout)
            )
            await asyncio.to_thread(self.printer.print_image, img)
        except PrinterError as exc:
            if not force:
                self.db.release_daily(today)
            self.db.log("daily", False, f"{reason}: {exc}")
            log.error("daily print failed: %s", exc)
            return {"printed": False, "reason": str(exc)}
        self.db.mark_daily_printed(today, on_paper=True, task_count=len(ag.all_tasks))
        self.db.log("daily", True, f"{reason}: {len(ag.all_tasks)} tasks")
        return {"printed": True, "tasks": len(ag.all_tasks)}

    # ---- new-task slips -------------------------------------------------------------------------

    def _queue_new_items(self) -> None:
        items = [t for t in self.state.tasks if self.cfg.slips.enabled_tasks] + [
            t for t in self.state.cards if self.cfg.slips.enabled_deck
        ]
        keys = [(t.uid, t.source) for t in items]
        if not self.seeded:
            self.db.mark_seen(keys)
            self.seeded = True
            log.info("seeded %d seen items, no slips for existing tasks", len(keys))
            return
        suppressed = self.db.suppressed_keys()
        new = self.db.unseen(keys)
        for t in items:
            k = (t.uid, t.source)
            if k in new and k not in self._pending and t.suppression_key() not in suppressed:
                self._pending[k] = t
                if not self._pending_since:
                    self._pending_since = time.time()
        # items that vanished (completed/deleted) before printing are still marked seen
        gone = [k for k in new if k not in self._pending]
        if gone:
            self.db.mark_seen(gone)

    def _slip_due_in(self) -> float:
        if not self._pending:
            return 1e9
        return self._pending_since + self.cfg.slips.debounce_seconds - time.time()

    async def maybe_print_slip(self, force: bool = False) -> bool:
        if not self._pending or (not force and self._slip_due_in() > 0):
            return False
        items = list(self._pending.values())
        try:
            img = render_image.render(
                layout.slip_receipt(items, datetime.now(), self.cfg.ui.language)
            )
            await asyncio.to_thread(self.printer.print_image, img)
        except PrinterError as exc:
            self.db.log("slip", False, str(exc))
            log.error("slip print failed: %s", exc)
            return False
        self.db.mark_seen(list(self._pending))
        self.db.log("slip", True, ", ".join(t.title for t in items)[:500])
        self._pending.clear()
        self._pending_since = 0.0
        return True

    # ---- printer helpers -------------------------------------------------------------------------

    async def print_test(self, sweep: bool = False) -> None:
        await asyncio.to_thread(self.printer.print_calibration, sweep)
        self.db.log("test", True, "calibration" + (" sweep" if sweep else ""))

    async def feed(self, mm: int | None = None) -> None:
        await asyncio.to_thread(self.printer.feed, mm)

    def printer_status(self) -> dict[str, Any]:
        dev = self.cfg.printer.device
        return {
            "device": dev,
            "present": os.path.exists(dev),
            "writable": os.access(dev, os.W_OK) if os.path.exists(dev) else False,
        }

    def status(self) -> dict[str, Any]:
        return {
            "uptime": int(time.time() - self.started_at),
            "configured": self.cfg.nextcloud.configured,
            "last_poll_at": self.last_poll_at,
            "last_ok_at": self.state.last_ok,
            "polls": self.polls,
            "last_error": self.state.last_error,
            "errors": self.state.errors,
            "pending_slip_items": len(self._pending),
            "daily": self.db.daily_status(date.today()),
            "printer": self.printer_status(),
            "counts": {
                "tasks": len(self.state.tasks),
                "cards": len(self.state.cards),
                "events": len(self.state.events),
            },
        }

    # ---- D-Bus wake triggers ------------------------------------------------------------------

    async def _dbus_loop(self) -> None:
        try:
            from dbus_next import BusType, Message, MessageType
            from dbus_next.aio import MessageBus
        except Exception:  # pragma: no cover
            log.info("dbus-next unavailable, wake triggers disabled")
            return
        try:
            bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as exc:
            log.info("system bus unavailable (%s), wake triggers disabled", exc)
            return
        rules = [
            "type='signal',interface='org.freedesktop.login1.Manager',member='PrepareForSleep'",
            "type='signal',interface='org.freedesktop.login1.Session',member='Unlock'",
        ]
        for rule in rules:
            await bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="AddMatch",
                    signature="s",
                    body=[rule],
                )
            )
        loop = asyncio.get_running_loop()

        def handler(msg: Message) -> None:
            if msg.message_type != MessageType.SIGNAL:
                return
            if msg.member == "PrepareForSleep" and msg.body and msg.body[0] is False:
                log.info("resume detected, scheduling daily check")
                loop.call_later(3, self._wake.set)
            elif msg.member == "Unlock":
                log.info("session unlock detected, scheduling daily check")
                loop.call_later(1, self._wake.set)

        bus.add_message_handler(handler)
        log.info("listening for login1 resume/unlock signals")
        await bus.wait_for_disconnect()

    # ---- task write-back (used by TUI, web UI and the AI assistant) -----------------------------

    def find_task(self, uid: str) -> TaskItem | None:
        for t in self.state.tasks + self.state.cards:
            if t.uid == uid:
                return t
        return None

    async def set_task_done(self, uid: str, done: bool) -> TaskItem:
        t = self.find_task(uid)
        if t is None:
            raise LookupError(f"unknown task {uid}")
        nc = self.cfg.nextcloud
        if t.source == "deck":
            deck = DeckClient(nc)
            assert t.board_id is not None and t.stack_id is not None and t.card_id is not None
            if done:
                await deck.complete_card(t.board_id, t.stack_id, t.card_id)
            else:
                await deck.uncomplete_card(t.board_id, t.stack_id, t.card_id)
        else:
            cal = CalDavClient(nc, self.db)
            fn = cal.complete_task if done else cal.uncomplete_task
            await asyncio.to_thread(fn, t.uid, t.list_id)
        self.db.log("task", True, f"{'done' if done else 'reopened'}: {t.title}")
        await self._refresh_after_write()
        return t

    @staticmethod
    def _deck_due(due: Any) -> Any:
        if due == "keep" or due is None:
            return due
        if isinstance(due, date):
            return f"{due.isoformat()}T12:00:00+00:00"
        return str(due)

    async def update_task(self, uid: str, **fields: Any) -> TaskItem:
        """fields (all optional): title, notes, due (date|None|'keep'), start, priority, tags,
        location, labels, assignees, stack. Irrelevant fields are ignored per source."""
        t = self.find_task(uid)
        if t is None:
            raise LookupError(f"unknown task {uid}")
        nc = self.cfg.nextcloud
        fields = {k: v for k, v in fields.items() if v is not None or k in ("due", "start")}
        if t.source == "deck":
            assert t.board_id is not None and t.stack_id is not None and t.card_id is not None
            f = dict(fields)
            if "due" in f:
                f["due"] = self._deck_due(f["due"])
            if "tags" in f and "labels" not in f:
                f["labels"] = f["tags"]  # tags == Deck labels
            await DeckClient(nc).update_card(t.board_id, t.stack_id, t.card_id, **f)
        else:
            f = {k: v for k, v in fields.items() if k not in ("labels", "assignees", "stack")}
            await asyncio.to_thread(CalDavClient(nc, self.db).update_task, t.uid, t.list_id, **f)
        self.db.log("task", True, f"edited: {fields.get('title') or t.title}")
        await self._refresh_after_write()
        return t

    async def create_task(self, list_id: str, title: str, **fields: Any) -> str:
        """list_id is a CalDAV list id or 'board/stack' for Deck. fields as in update_task."""
        nc = self.cfg.nextcloud
        fields = {k: v for k, v in fields.items() if v is not None}
        if "/" in list_id:
            b, st = (int(x) for x in list_id.split("/", 1))
            f = dict(fields)
            if "due" in f:
                f["due"] = self._deck_due(f["due"])
            if "tags" in f and "labels" not in f:
                f["labels"] = f["tags"]
            cid = await DeckClient(nc).create_card(b, st, title, **f)
            uid = f"deck:{cid}"
        else:
            f = {k: v for k, v in fields.items() if k not in ("labels", "assignees", "stack")}
            uid = await asyncio.to_thread(
                CalDavClient(nc, self.db).create_task, list_id, title, **f
            )
        self.db.mark_seen([(uid, "deck" if "/" in list_id else "tasks")])  # no slip for own
        self.db.log("task", True, f"created: {title}")
        await self._refresh_after_write()
        return uid

    async def create_event(
        self,
        calendar_id: str,
        title: str,
        start: datetime | date,
        end: datetime | date | None = None,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
        rrule: str = "",
    ) -> str:
        uid = await asyncio.to_thread(
            CalDavClient(self.cfg.nextcloud, self.db).create_event,
            calendar_id,
            title,
            start,
            end,
            description=description,
            location=location,
            attendees=attendees,
            rrule=rrule,
        )
        self.db.log("event", True, f"created: {title}")
        await self._refresh_after_write()
        return uid

    async def update_event(self, uid: str, **fields: Any) -> None:
        cal_id = ""
        for e in self.state.events:
            if e.uid == uid or e.uid.split("@", 1)[0] == uid.split("@", 1)[0]:
                cal_id = e.calendar_id
                break
        await asyncio.to_thread(
            CalDavClient(self.cfg.nextcloud, self.db).update_event, uid, cal_id, **fields
        )
        self.db.log("event", True, f"edited: {fields.get('title') or uid[:8]}")
        await self._refresh_after_write()

    async def delete_event(self, uid: str) -> None:
        await asyncio.to_thread(CalDavClient(self.cfg.nextcloud, self.db).delete_event, uid)
        self.db.log("event", True, f"deleted: {uid[:8]}")
        await self._refresh_after_write()

    async def _refresh_after_write(self) -> None:
        """Re-fetch right away so anything printed in the same breath sees the change."""
        async with self._lock:
            await self._refresh_sources(date.today())
            self.last_poll_at = time.time()

    async def find_contacts(self, query: str) -> list[dict[str, Any]]:
        from .sources.contacts import ContactsClient

        found = await asyncio.to_thread(ContactsClient(self.cfg.nextcloud).find, query)
        return [c.to_dict() for c in found]

    async def set_contact_birthday(self, href: str, birthday: str | None) -> dict[str, Any]:
        from .sources.contacts import ContactsClient

        c = await asyncio.to_thread(ContactsClient(self.cfg.nextcloud).set_birthday, href, birthday)
        self.db.log("contact", True, f"birthday {birthday or 'removed'}: {c.name}")
        # Nextcloud regenerates the birthday calendar on card changes; re-fetch shortly after.
        await asyncio.sleep(2)
        await self._refresh_after_write()
        return c.to_dict()

    async def birthdays_for(self, days: int) -> list[Birthday]:
        today = date.today()
        cal = CalDavClient(self.cfg.nextcloud, self.db)
        return await asyncio.to_thread(
            cal.fetch_birthdays,
            self.cfg.daily.birthdays_calendar,
            today,
            today + timedelta(days=max(0, days)),
        )

    async def print_birthdays(self, days: int) -> dict[str, Any]:
        bdays = await self.birthdays_for(days)
        img = render_image.render(
            layout.birthdays_receipt(
                bdays, date.today(), days, self.cfg.ui.language, self.layout_options()
            )
        )
        await asyncio.to_thread(self.printer.print_image, img)
        self.db.log("birthdays", True, f"next {days} days: {len(bdays)}")
        return {"printed": True, "days": days, "birthdays": len(bdays)}

    # ---- custom lists and tickets --------------------------------------------------------

    async def print_custom_list(self, list_id: int) -> dict[str, Any]:
        lst = self.db.custom_list(list_id)
        if lst is None:
            raise LookupError(f"list {list_id} not found")
        img = render_image.render(
            layout.list_receipt(
                lst["title"],
                lst["items"],
                date.today(),
                self.cfg.ui.language,
                self.layout_options(),
            )
        )
        await asyncio.to_thread(self.printer.print_image, img)
        self.db.mark_list_printed(list_id)
        self.db.log("list", True, f"{lst['title']}: {len(lst['items'])} items")
        return {"printed": True, "title": lst["title"], "items": len(lst["items"])}

    async def print_ticket(self, ticket: dict[str, Any]) -> dict[str, Any]:
        img = render_image.render(layout.ticket_receipt(ticket, self.cfg.ui.language))
        await asyncio.to_thread(self.printer.print_image, img)
        self.db.log("ticket", True, ticket.get("title", "")[:80])
        return {"printed": True, "title": ticket.get("title", "")}

    def calendars(self) -> list[dict[str, Any]]:
        """Writable Nextcloud calendars (extras are read-only)."""
        return [
            {"id": c.id, "name": c.name}
            for c in self.state.collections
            if c.vevent and not c.id.startswith("extra:")
        ]

    async def deck_meta(self) -> dict[int, dict[str, Any]]:
        """Labels and users per board, for the AI context."""
        out: dict[int, dict[str, Any]] = {}
        deck = DeckClient(self.cfg.nextcloud)
        for bid in sorted({st.board_id for st in self.state.stacks}):
            try:
                out[bid] = await deck.board_meta(bid)
            except Exception as exc:  # noqa: BLE001
                log.debug("board meta %s: %s", bid, exc)
        return out

    def ordered_stacks(self) -> list[DeckStack]:
        """Stacks in the user's custom order per board, else Nextcloud's order. Boards keep
        Nextcloud's order. Polls only refresh the stacks, never this order."""
        pos = self.db.stack_positions()
        board_seq: dict[int, int] = {}
        for st in self.state.stacks:
            board_seq.setdefault(st.board_id, len(board_seq))
        return sorted(
            self.state.stacks,
            key=lambda st: (
                board_seq[st.board_id],
                pos.get((st.board_id, st.stack_id), 10**6),
                st.order,
                st.stack_title.lower(),
            ),
        )

    def group_rank(self) -> dict[str, int]:
        """list_id -> position for always-print groups: task lists first (Nextcloud order),
        then Deck stacks in the ordered_stacks() order."""
        rank: dict[str, int] = {}
        for c in self.state.collections:
            if c.vtodo:
                rank[c.id] = len(rank)
        for st in self.ordered_stacks():
            rank[f"{st.board_id}/{st.stack_id}"] = len(rank)
        return rank

    def task_lists(self) -> list[dict[str, Any]]:
        out = [
            {"id": c.id, "name": c.name, "source": "tasks"}
            for c in self.state.collections
            if c.vtodo
        ]
        for st in self.ordered_stacks():
            out.append(
                {
                    "id": f"{st.board_id}/{st.stack_id}",
                    "name": f"{st.board_title} · {st.stack_title}",
                    "source": "deck",
                }
            )
        return out

    # ---- settings access (AI assistant, MCP) ---------------------------------------------------

    def settings_dict(self) -> dict[str, Any]:
        d = self.cfg.to_dict()
        if d["nextcloud"].get("app_password"):
            d["nextcloud"]["app_password"] = "********"
        return d

    def apply_setting(self, section: str, key: str, value: Any) -> str:
        """Set cfg.<section>.<key> with type coercion, save and reload. Returns a message."""
        from dataclasses import fields, is_dataclass

        from .config import save_config

        sec = getattr(self.cfg, section, None)
        if sec is None or not is_dataclass(sec):
            raise ValueError(f"unknown section {section!r}; sections: {list(self.cfg.to_dict())}")
        names = {f.name for f in fields(sec)}
        if key not in names:
            raise ValueError(f"unknown key {key!r} in {section}; keys: {sorted(names)}")
        current = getattr(sec, key)
        if isinstance(current, bool):
            if isinstance(value, str):
                value = value.strip().lower() in ("1", "true", "yes", "on")
            else:
                value = bool(value)
        elif isinstance(current, int):
            value = int(value)
        else:
            value = str(value)
        setattr(sec, key, value)
        save_config(self.cfg)
        self.reload_config(self.cfg)
        return f"{section}.{key} = {value!r}"
