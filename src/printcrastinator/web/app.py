"""NiceGUI web UI. Runs in the same process as the daemon and talks to it directly."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import replace
from datetime import datetime

from nicegui import app, ui

from ..api import make_router
from ..config import Config, load_config, save_config
from ..daemon import Daemon
from ..printer.escpos_out import DENSITY_PRESETS
from ..sources.caldav_client import CalDavClient
from ..sources.deck import DeckClient

log = logging.getLogger(__name__)

PAGES = [
    ("/", "Dashboard", "dashboard"),
    ("/tasks", "Tasks", "checklist"),
    ("/deck", "Deck", "view_kanban"),
    ("/lists", "Task lists", "list"),
    ("/calendars", "Calendars", "calendar_month"),
    ("/printer", "Printer", "print"),
    ("/settings", "Settings", "settings"),
]


def _ago(ts: float) -> str:
    if not ts:
        return "never"
    s = int(time.time() - ts)
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    return f"{s // 3600}h ago"


def _frame(title: str):
    ui.colors(primary="#1f1f1f")
    with ui.header().classes("items-center"):
        ui.icon("receipt_long").classes("text-2xl")
        ui.label("Printcrastinator").classes("text-lg font-bold")
        ui.space()
        ui.label(title).classes("text-sm opacity-70")
    with ui.left_drawer(value=True).props("width=210 bordered"):
        for path, name, icon in PAGES:
            ui.button(name, icon=icon, on_click=lambda p=path: ui.navigate.to(p)).props(
                "flat align=left no-caps"
            ).classes("w-full")
    return ui.column().classes("w-full max-w-4xl gap-4 p-2")


def build(daemon: Daemon) -> None:
    app.include_router(make_router(daemon))

    async def _run(coro, ok_msg: str = "done"):
        try:
            result = await coro
            ui.notify(ok_msg if result is None else f"{ok_msg}: {result}", type="positive")
        except Exception as exc:
            ui.notify(f"failed: {exc}", type="negative", multi_line=True)

    # ---- Dashboard -----------------------------------------------------------------------

    @ui.page("/")
    def dashboard():
        with _frame("Dashboard"):
            if not daemon.cfg.nextcloud.configured:
                with ui.card().classes("w-full bg-yellow-1"):
                    ui.label("Nextcloud is not configured yet.").classes("font-bold")
                    ui.button("Open settings", on_click=lambda: ui.navigate.to("/settings"))
            with ui.row().classes("w-full items-start gap-6"):
                with ui.column().classes("gap-2"):
                    ui.label("Today's receipt").classes("text-lg font-bold")
                    img = (
                        ui.image(f"/api/preview.png?kind=live&t={time.time()}")
                        .classes("w-[300px] border shadow")
                        .style("image-rendering: pixelated")
                    )
                    with ui.row():
                        ui.button(
                            "Refresh",
                            icon="refresh",
                            on_click=lambda: img.set_source(
                                f"/api/preview.png?kind=live&t={time.time()}"
                            ),
                        ).props("outline")
                        ui.button(
                            "Print today now",
                            icon="print",
                            on_click=lambda: _run(
                                daemon.maybe_print_daily(force=False, reason="ui"), "daily"
                            ),
                        )
                        ui.button(
                            "Reprint (force)",
                            icon="replay",
                            on_click=lambda: _run(
                                daemon.maybe_print_daily(force=True, reason="ui-force"), "daily"
                            ),
                        ).props("outline")
                with ui.column().classes("gap-2 grow"):
                    ui.label("Status").classes("text-lg font-bold")
                    status_md = ui.markdown()

                    def refresh_status():
                        s = daemon.status()
                        d = s["daily"] or {}
                        pr = s["printer"]
                        lines = [
                            f"**Printer** `{pr['device']}`: "
                            + (
                                "ready"
                                if pr["writable"]
                                else ("present, not writable" if pr["present"] else "not found")
                            ),
                            f"**Last poll** {_ago(s['last_poll_at'])} · "
                            f"**last success** {_ago(s['last_ok_at'])} · polls {s['polls']}",
                            f"**Loaded** {s['counts']['tasks']} tasks · "
                            f"{s['counts']['cards']} cards · {s['counts']['events']} events",
                            "**Today** "
                            + (
                                (
                                    "printed on paper"
                                    if d.get("printed_on_paper")
                                    else "done on screen only"
                                )
                                + f" ({d.get('task_count', 0)} tasks)"
                                if d
                                else "not printed yet"
                            ),
                            f"**Pending slip items** {s['pending_slip_items']}",
                        ]
                        if s["last_error"]:
                            lines.append(f"**Error** {s['last_error']}")
                        status_md.set_content("\n\n".join(lines))

                    refresh_status()
                    ui.timer(5.0, refresh_status)
                    with ui.row():
                        ui.button(
                            "Poll now",
                            icon="sync",
                            on_click=lambda: _run(daemon.poll_once(), "poll"),
                        ).props("outline")
                        ui.button(
                            "Notify",
                            icon="notifications",
                            on_click=lambda: (
                                daemon.show_on_screen(daemon.build_agenda()),
                                ui.notify("sent"),
                            ),
                        ).props("outline")
            ui.label("Recent log").classes("text-lg font-bold")
            log_table = ui.table(
                columns=[
                    {"name": "at", "label": "At", "field": "at", "align": "left"},
                    {"name": "kind", "label": "Kind", "field": "kind", "align": "left"},
                    {"name": "ok", "label": "OK", "field": "ok", "align": "left"},
                    {"name": "detail", "label": "Detail", "field": "detail", "align": "left"},
                ],
                rows=[],
            ).classes("w-full")

            def refresh_log():
                log_table.rows = [
                    {**r, "ok": "✓" if r["ok"] else "✗", "at": r["at"].replace("T", " ")[:19]}
                    for r in daemon.db.recent_log(30)
                ]

            refresh_log()
            ui.timer(10.0, refresh_log)

    # ---- Tasks ----------------------------------------------------------------------------

    @ui.page("/tasks")
    def tasks_page():
        with _frame("Tasks"):
            ui.label("Candidate tasks").classes("text-lg font-bold")
            ui.label(
                "Everything currently open in Nextcloud Tasks and Deck. Hidden tasks never "
                "appear on prints. Hiding applies to this occurrence (uid + due date) only, "
                "so a recurring task comes back with its next due date."
            ).classes("text-sm opacity-70")
            container = ui.column().classes("w-full gap-1")

            def render():
                container.clear()
                suppressed = daemon.db.suppressed_keys()
                items = sorted(
                    daemon.candidates(),
                    key=lambda t: (t.due is None, t.due or datetime.max.date(), t.title.lower()),
                )
                if not items:
                    with container:
                        ui.label("Nothing loaded yet. Poll from the dashboard.").classes(
                            "opacity-70"
                        )
                for t in items:
                    key = t.suppression_key()
                    with container, ui.row().classes("w-full items-center border-b py-1"):
                        ui.switch(
                            value=key not in suppressed,
                            on_change=lambda e, t=t: (
                                daemon.db.unsuppress(*t.suppression_key())
                                if e.value
                                else daemon.db.suppress(*t.suppression_key(), t.title)
                            ),
                        ).tooltip("on = shown on prints")
                        ui.label(t.title).classes("grow")
                        ui.badge(t.source).props("outline")
                        ui.label(t.list_name).classes("text-sm opacity-70")
                        ui.label(t.due.isoformat() if t.due else "no due").classes(
                            "text-sm w-24 text-right"
                        )

            render()
            ui.button("Refresh", icon="refresh", on_click=render).props("outline")
            ui.label("Hidden").classes("text-lg font-bold mt-4")
            hidden = ui.column().classes("w-full gap-1")

            def render_hidden():
                hidden.clear()
                rows = daemon.db.suppressed_rows()
                if not rows:
                    with hidden:
                        ui.label("none").classes("opacity-70")
                for r in rows:
                    with hidden, ui.row().classes("w-full items-center"):
                        ui.button(
                            icon="visibility",
                            on_click=lambda r=r: (
                                daemon.db.unsuppress(r["uid"], r["due"]),
                                render_hidden(),
                                render(),
                            ),
                        ).props("flat dense").tooltip("unhide")
                        ui.label(r["title"] or r["uid"]).classes("grow")
                        ui.label(r["due"] or "no due").classes("text-sm opacity-70")

            render_hidden()

    # ---- Deck ---------------------------------------------------------------------------------

    @ui.page("/deck")
    def deck_page():
        with _frame("Deck"):
            ui.label("Always-print stacks").classes("text-lg font-bold")
            ui.label("Cards in these stacks are printed every day regardless of due date.").classes(
                "text-sm opacity-70"
            )
            always = daemon.db.always_print_stacks()
            boards: dict[int, list] = {}
            for s in daemon.state.stacks:
                boards.setdefault(s.board_id, []).append(s)
            if not boards:
                ui.label("No boards loaded yet. Poll from the dashboard.").classes("opacity-70")
            for stacks in boards.values():
                with ui.card().classes("w-full"):
                    ui.label(stacks[0].board_title).classes("font-bold")
                    for s in stacks:
                        ui.switch(
                            s.stack_title,
                            value=(s.board_id, s.stack_id) in always,
                            on_change=lambda e, s=s: daemon.db.set_stack_rule(
                                s.board_id, s.stack_id, bool(e.value)
                            ),
                        )

    # ---- Task lists ---------------------------------------------------------------------------

    @ui.page("/lists")
    def lists_page():
        with _frame("Task lists"):
            ui.label("Always-print task lists").classes("text-lg font-bold")
            ui.label("Tasks in these lists are printed every day regardless of due date.").classes(
                "text-sm opacity-70"
            )
            always = daemon.db.always_print_lists()
            cols = [c for c in daemon.state.collections if c.vtodo]
            if not cols:
                ui.label("No lists loaded yet. Poll from the dashboard.").classes("opacity-70")
            for c in cols:
                ui.switch(
                    c.name,
                    value=c.id in always,
                    on_change=lambda e, c=c: daemon.db.set_list_rule(c.id, bool(e.value)),
                )

    # ---- Calendars ---------------------------------------------------------------------------

    @ui.page("/calendars")
    def calendars_page():
        with _frame("Calendars"):
            ui.label("Included calendars").classes("text-lg font-bold")
            disabled = daemon.db.disabled_calendars()
            cols = [c for c in daemon.state.collections if c.vevent]
            if not cols:
                ui.label("No calendars loaded yet. Poll from the dashboard.").classes("opacity-70")
            for c in cols:
                ui.switch(
                    c.name,
                    value=c.id not in disabled,
                    on_change=lambda e, c=c: daemon.db.set_calendar_enabled(c.id, bool(e.value)),
                )

    # ---- Printer ---------------------------------------------------------------------------------

    @ui.page("/printer")
    def printer_page():
        with _frame("Printer"):
            cfg = daemon.cfg
            with ui.card().classes("w-full"):
                ui.label("Device").classes("font-bold")
                dev = ui.input("Device path", value=cfg.printer.device).classes("w-96")
                width = ui.number(
                    "Width (px)", value=cfg.printer.width_px, min=200, max=832, step=8
                )
                band = ui.number(
                    "Band lines", value=cfg.printer.band_lines, min=16, max=1024, step=1
                )
                feed = ui.number(
                    "Feed after print (mm)", value=cfg.printer.feed_after_mm, min=0, max=120
                )
                density = ui.select(
                    list(DENSITY_PRESETS),
                    value=cfg.printer.density if cfg.printer.density in DENSITY_PRESETS else "",
                    label="Density (from test print)",
                    with_input=True,
                    new_value_mode="add-unique",
                ).classes("w-64")
                codepage = ui.input(
                    "Fallback code page (text mode only)", value=cfg.printer.fallback_codepage
                ).classes("w-64")

                def save():
                    new = replace(
                        cfg.printer,
                        device=dev.value.strip(),
                        width_px=int(width.value),
                        band_lines=int(band.value),
                        feed_after_mm=int(feed.value),
                        density=density.value or "",
                        fallback_codepage=codepage.value.strip() or "CP858",
                    )
                    cfg.printer = new
                    save_config(cfg)
                    daemon.reload_config(cfg)
                    ui.notify("printer settings saved", type="positive")

                ui.button("Save", icon="save", on_click=save)
            with ui.card().classes("w-full"):
                ui.label("Actions").classes("font-bold")
                with ui.row():
                    ui.button(
                        "Test print (density sweep)",
                        icon="print",
                        on_click=lambda: _run(daemon.print_test(), "test print"),
                    )
                    ui.button(
                        "Feed", icon="arrow_downward", on_click=lambda: _run(daemon.feed(), "feed")
                    ).props("outline")
                    ui.button(
                        "Print sample receipt",
                        icon="receipt",
                        on_click=lambda: _run(_print_sample(daemon), "sample"),
                    ).props("outline")
            with ui.card().classes("w-full"):
                ui.label("Previews").classes("font-bold")
                with ui.row().classes("gap-4"):
                    for kind in ("sample", "empty", "slip"):
                        with ui.column():
                            ui.label(kind)
                            ui.image(f"/api/preview.png?kind={kind}").classes(
                                "w-[240px] border"
                            ).style("image-rendering: pixelated")

    # ---- Settings ------------------------------------------------------------------------

    @ui.page("/settings")
    def settings_page():
        with _frame("Settings"):
            cfg = daemon.cfg
            with ui.card().classes("w-full"):
                ui.label("Nextcloud").classes("font-bold")
                ui.label(
                    "Use an app password (Settings → Security → Devices & sessions). "
                    "It works with SSO logins. Untick 'Allow filesystem access' on it."
                ).classes("text-sm opacity-70")
                url = ui.input(
                    "Nextcloud URL",
                    value=cfg.nextcloud.url,
                    placeholder="https://cloud.example.org",
                ).classes("w-96")
                user = ui.input("Username", value=cfg.nextcloud.username).classes("w-96")
                pw = ui.input(
                    "App password",
                    value=cfg.nextcloud.app_password,
                    password=True,
                    password_toggle_button=True,
                ).classes("w-96")

                async def test():
                    nc = replace(
                        cfg.nextcloud,
                        url=url.value.strip(),
                        username=user.value.strip(),
                        app_password=pw.value,
                    )
                    try:
                        cal_msg = await asyncio.to_thread(CalDavClient(nc).test_connection)
                        deck_msg = await DeckClient(nc).test_connection()
                        ui.notify(
                            f"CalDAV {cal_msg} · Deck {deck_msg}", type="positive", multi_line=True
                        )
                    except Exception as exc:
                        ui.notify(f"connection failed: {exc}", type="negative", multi_line=True)

                with ui.row():
                    ui.button("Test connection", icon="link", on_click=test).props("outline")
            with ui.card().classes("w-full"):
                ui.label("Daily receipt").classes("font-bold")
                earliest = ui.number("Earliest hour", value=cfg.daily.earliest_hour, min=0, max=23)
                cal_only = ui.switch(
                    "Print days that only have calendar events",
                    value=cfg.daily.print_calendar_only_days,
                )
                lang = ui.select(
                    {"en": "English", "de": "Deutsch"},
                    value=cfg.ui.language,
                    label="Receipt language",
                ).classes("w-48")
            with ui.card().classes("w-full"):
                ui.label("New-task slips").classes("font-bold")
                s_tasks = ui.switch("Slips for Nextcloud Tasks", value=cfg.slips.enabled_tasks)
                s_deck = ui.switch("Slips for Deck cards", value=cfg.slips.enabled_deck)
                debounce = ui.number(
                    "Debounce (seconds)", value=cfg.slips.debounce_seconds, min=0, max=3600
                )
                poll = ui.number(
                    "Poll interval (seconds)", value=cfg.server.poll_interval, min=30, max=3600
                )
            with ui.card().classes("w-full"):
                ui.label("On screen").classes("font-bold")
                notify_sw = ui.switch(
                    "Desktop notification when the daily check runs", value=cfg.screen.notify
                )
                term_sw = ui.switch(
                    "Terminal window at login (autostart entry)", value=cfg.screen.terminal
                )

            def save():
                cfg.nextcloud = replace(
                    cfg.nextcloud,
                    url=url.value.strip(),
                    username=user.value.strip(),
                    app_password=pw.value,
                )
                cfg.daily = replace(
                    cfg.daily,
                    earliest_hour=int(earliest.value),
                    print_calendar_only_days=bool(cal_only.value),
                )
                cfg.ui = replace(cfg.ui, language=lang.value)
                cfg.slips = replace(
                    cfg.slips,
                    enabled_tasks=bool(s_tasks.value),
                    enabled_deck=bool(s_deck.value),
                    debounce_seconds=int(debounce.value),
                )
                cfg.server = replace(cfg.server, poll_interval=int(poll.value))
                cfg.screen = replace(
                    cfg.screen, notify=bool(notify_sw.value), terminal=bool(term_sw.value)
                )
                save_config(cfg)
                daemon.reload_config(cfg)
                ui.notify("saved; polling now", type="positive")

            ui.button("Save", icon="save", on_click=save)


async def _print_sample(daemon: Daemon) -> None:
    from ..receipt import layout
    from ..render import image as render_image

    img = render_image.render(layout.daily_receipt(layout.sample_agenda(), daemon.cfg.ui.language))
    await asyncio.to_thread(daemon.printer.print_image, img)


def run(cfg: Config | None = None) -> None:
    cfg = cfg or load_config()
    daemon = Daemon(cfg)
    build(daemon)
    app.on_startup(daemon.start)
    app.on_shutdown(daemon.stop)
    ui.run(
        host=cfg.server.host,
        port=cfg.server.port,
        title="Printcrastinator",
        reload=False,
        show=False,
        show_welcome_message=False,
        favicon="🧾",
    )
