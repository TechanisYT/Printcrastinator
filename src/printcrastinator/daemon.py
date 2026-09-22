"""Poll loop, daily gate, new-task slips, D-Bus wake triggers. Owns the printer."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from . import agenda as agenda_mod
from . import logos
from .config import Config, load_config
from .db import Database
from .models import CalendarEvent, DailyAgenda, TaskItem
from .printer.escpos_out import Printer, PrinterError
from .receipt import i18n, layout
from .render import image as render_image
from .render import notify, screen
from .sources.caldav_client import CalDavClient, Collection
from .sources.deck import DeckClient, DeckStack

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
        await self.maybe_print_daily()
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
            return r_t, r_e

        results = await asyncio.gather(
            asyncio.to_thread(caldav_both), deck.fetch(), return_exceptions=True
        )
        ok = True
        errors: dict[str, str] = {}
        r_both, r_deck = results
        if isinstance(r_both, BaseException):
            r_tasks = r_events = r_both
        else:
            r_tasks, r_events = r_both
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
            cols, self.state.events = r_events
            self._merge_collections(cols)
        if isinstance(r_deck, BaseException):
            ok, errors["deck"] = False, str(r_deck)
            log.warning("deck fetch failed: %s", r_deck)
        else:
            self.state.stacks, self.state.cards = r_deck
        self.state.errors = errors
        self.state.last_error = "; ".join(f"{k}: {v}" for k, v in errors.items())
        if ok:
            self.state.last_ok = time.time()
        return ok

    def _merge_collections(self, cols: list[Collection]) -> None:
        by_id = {c.id: c for c in self.state.collections}
        for c in cols:
            by_id[c.id] = c
        self.state.collections = sorted(by_id.values(), key=lambda c: c.name.lower())

    # ---- agenda ------------------------------------------------------------------------------

    def build_agenda(self, day: date | None = None) -> DailyAgenda:
        day = day or date.today()
        return agenda_mod.build(
            day,
            self.state.tasks,
            self.state.cards,
            self.state.events,
            suppressed=self.db.suppressed_keys(),
            always_lists=self.db.always_print_lists(),
            always_stacks=self.db.always_print_stacks(),
            overdue_max_days=self.cfg.daily.overdue_max_days,
            overdue_max_count=self.cfg.daily.overdue_max_count,
        )

    async def agenda(self, refresh: bool = False) -> DailyAgenda:
        if refresh or (self.last_poll_at == 0 and self.cfg.nextcloud.configured):
            async with self._lock:
                await self._refresh_sources(date.today())
                self.last_poll_at = time.time()
        return self.build_agenda()

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
        return await self.maybe_print_daily(force=True, reason="test-cycle")

    async def maybe_print_daily(
        self, force: bool = False, reason: str = "poll", layout_mode: str | None = None
    ) -> dict[str, Any]:
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
        self._wake.set()
        return t

    async def update_task(
        self,
        uid: str,
        title: str | None = None,
        due: date | None | str = "keep",
        notes: str | None = None,
    ) -> TaskItem:
        t = self.find_task(uid)
        if t is None:
            raise LookupError(f"unknown task {uid}")
        nc = self.cfg.nextcloud
        if t.source == "deck":
            assert t.board_id is not None and t.stack_id is not None and t.card_id is not None
            due_s: str | None = "keep"
            if due != "keep":
                due_s = f"{due.isoformat()}T12:00:00+00:00" if isinstance(due, date) else None
            await DeckClient(nc).update_card(t.board_id, t.stack_id, t.card_id, title, due_s, notes)
        else:
            await asyncio.to_thread(
                CalDavClient(nc, self.db).update_task, t.uid, t.list_id, title, due, notes
            )
        self.db.log("task", True, f"edited: {title or t.title}")
        self._wake.set()
        return t

    async def create_task(
        self, list_id: str, title: str, due: date | None = None, notes: str = ""
    ) -> str:
        """list_id is a CalDAV list id or 'board/stack' for Deck."""
        nc = self.cfg.nextcloud
        if "/" in list_id:
            b, st = (int(x) for x in list_id.split("/", 1))
            due_s = f"{due.isoformat()}T12:00:00+00:00" if due else None
            cid = await DeckClient(nc).create_card(b, st, title, due_s, notes)
            uid = f"deck:{cid}"
        else:
            uid = await asyncio.to_thread(
                CalDavClient(nc, self.db).create_task, list_id, title, due, notes
            )
        self.db.mark_seen(
            [(uid, "deck" if "/" in list_id else "tasks")]
        )  # no slip for own creations
        self.db.log("task", True, f"created: {title}")
        self._wake.set()
        return uid

    def task_lists(self) -> list[dict[str, Any]]:
        out = [
            {"id": c.id, "name": c.name, "source": "tasks"}
            for c in self.state.collections
            if c.vtodo
        ]
        for st in self.state.stacks:
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
