"""NiceGUI web UI: one page, sections swapped in place. Talks to the daemon directly."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime

from nicegui import app, ui

from .. import logos
from ..api import make_router
from ..config import Config, ExtraCalendar, load_config, save_config
from ..daemon import Daemon
from ..printer.escpos_out import DENSITY_PRESETS
from ..sources.caldav_client import CalDavClient
from ..sources.deck import DeckClient

log = logging.getLogger(__name__)

SECTIONS = [
    ("dashboard", "Dashboard", "dashboard"),
    ("tasks", "Tasks", "checklist"),
    ("deck", "Deck", "view_kanban"),
    ("lists", "Task lists", "list"),
    ("calendars", "Calendars", "calendar_month"),
    ("printer", "Printer", "print"),
    ("custom_lists", "Custom lists", "checklist_rtl"),
    ("tickets", "Tickets", "confirmation_number"),
    ("settings", "Settings", "settings"),
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


async def _run(coro, ok_msg: str = "done"):
    try:
        result = await coro
        ui.notify(ok_msg if result is None else f"{ok_msg}: {result}", type="positive")
    except Exception as exc:
        ui.notify(f"failed: {exc}", type="negative", multi_line=True)


# ---- sections ------------------------------------------------------------------------------


def sec_dashboard(daemon: Daemon) -> None:
    if not daemon.cfg.nextcloud.configured:
        with ui.card().classes("w-full"):
            ui.label("Nextcloud is not configured yet. Go to Settings.").classes("font-bold")
    with ui.row().classes("w-full items-start gap-6"):
        with ui.column().classes("gap-2"):
            ui.label("Today's receipt").classes("text-lg font-bold")
            img = (
                ui.image(f"/api/preview.png?kind=live&t={time.time()}")
                .classes("w-[300px] border shadow bg-white")
                .style("image-rendering: pixelated")
            )
            with ui.row():
                ui.button(
                    "Refresh",
                    icon="refresh",
                    on_click=lambda: img.set_source(f"/api/preview.png?kind=live&t={time.time()}"),
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
                ui.button(
                    "Print as cards",
                    icon="content_cut",
                    on_click=lambda: _run(
                        daemon.maybe_print_daily(
                            force=True, reason="ui-cards", layout_mode="cards"
                        ),
                        "cards",
                    ),
                ).props("outline").tooltip("Each task in its own block with cut lines")
            with ui.row().classes("items-end gap-2"):
                from datetime import date as _date
                from datetime import timedelta as _td

                day_in = (
                    ui.input("Receipt for date", value=(_date.today() + _td(days=1)).isoformat())
                    .props("type=date")
                    .classes("w-44")
                )
                day_layout = ui.select(
                    {"": "configured layout", "list": "list", "cards": "cards"}, value=""
                ).classes("w-40")
                ui.button(
                    "Print for date",
                    icon="event",
                    on_click=lambda: _run(
                        daemon.print_day(
                            _date.fromisoformat(day_in.value), day_layout.value or None
                        ),
                        "day",
                    ),
                ).props("outline")
                ui.button(
                    "Preview",
                    icon="visibility",
                    on_click=lambda: img.set_source(
                        f"/api/preview.png?kind=live&day={day_in.value}"
                        f"&layout_mode={day_layout.value}&t={time.time()}"
                    ),
                ).props("flat")
        with ui.column().classes("gap-2 grow"):
            ui.label("Status").classes("text-lg font-bold")
            status_md = ui.markdown()

            def refresh_status():
                s = daemon.status()
                d = s["daily"] or {}
                pr = s["printer"]
                printer = (
                    "ready"
                    if pr["writable"]
                    else ("present, not writable" if pr["present"] else "not found")
                )
                if d:
                    today = (
                        "printed on paper" if d.get("printed_on_paper") else "done on screen only"
                    ) + f" ({d.get('task_count') or 0} tasks)"
                else:
                    today = "not printed yet"
                lines = [
                    f"**Printer** `{pr['device']}`: {printer}",
                    f"**Last poll** {_ago(s['last_poll_at'])} · "
                    f"**last success** {_ago(s['last_ok_at'])} · polls {s['polls']}",
                    f"**Loaded** {s['counts']['tasks']} tasks · "
                    f"{s['counts']['cards']} cards · {s['counts']['events']} events",
                    f"**Today** {today}",
                    f"**Pending slip items** {s['pending_slip_items']}",
                ]
                if s["last_error"]:
                    lines.append(f"**Error** {s['last_error']}")
                status_md.set_content("\n\n".join(lines))

            refresh_status()
            ui.timer(5.0, refresh_status)
            with ui.row():
                ui.button(
                    "Poll now", icon="sync", on_click=lambda: _run(daemon.poll_once(), "poll")
                ).props("outline")
                ui.button(
                    "Notify",
                    icon="notifications",
                    on_click=lambda: (
                        daemon.show_on_screen(daemon.build_agenda()),
                        ui.notify("sent"),
                    ),
                ).props("outline")
    with ui.row().classes("items-end gap-2"):
        from datetime import date as _date
        from datetime import timedelta as _td

        cal_from = (
            ui.input("Calendar from", value=_date.today().isoformat())
            .props("type=date")
            .classes("w-44")
        )
        cal_to = (
            ui.input("to", value=(_date.today() + _td(days=2)).isoformat())
            .props("type=date")
            .classes("w-44")
        )
        ui.button(
            "Print calendar only",
            icon="calendar_month",
            on_click=lambda: _run(
                daemon.print_calendar(
                    _date.fromisoformat(cal_from.value),
                    _date.fromisoformat(cal_to.value) if cal_to.value else None,
                ),
                "calendar",
            ),
        ).props("outline").tooltip("One day upright; several days rotated, side by side")
        ui.button(
            "Preview",
            icon="visibility",
            on_click=lambda: img.set_source(
                f"/api/preview.png?kind=calendar&day={cal_from.value}"
                f"&day_to={cal_to.value}&t={time.time()}"
            ),
        ).props("flat")
    with ui.card().classes("w-full"):
        ui.label("Custom print").classes("text-lg font-bold")
        ui.label("Pick lists or stacks and/or a due range, then print just those tasks.").classes(
            "text-sm opacity-70"
        )
        lists = daemon.task_lists()
        with ui.row().classes("items-end gap-2 flex-wrap"):
            c_title = ui.input("Title", value="Tasks").classes("w-48")
            c_lists = (
                ui.select(
                    {ls["id"]: ls["name"] for ls in lists},
                    multiple=True,
                    label="Lists / stacks (empty = all)",
                )
                .classes("w-96")
                .props("use-chips")
            )
            c_from = ui.input("Due from").props("type=date").classes("w-40")
            c_to = ui.input("Due to").props("type=date").classes("w-40")
            c_nodue = ui.switch("Include tasks without due date", value=True)
            c_overdue = ui.switch("Overdue only", value=False)
            c_text = ui.input("Text contains").classes("w-48")
            c_count = ui.label("").classes("text-sm opacity-70")

            def filters():
                from datetime import date as _d

                return dict(
                    list_ids=list(c_lists.value or []) or None,
                    due_from=_d.fromisoformat(c_from.value) if c_from.value else None,
                    due_to=_d.fromisoformat(c_to.value) if c_to.value else None,
                    include_no_due=bool(c_nodue.value),
                    overdue_only=bool(c_overdue.value),
                    text=c_text.value or "",
                )

            def count():
                c_count.set_text(f"{len(daemon.select_tasks(**filters()))} tasks match")

            ui.button("Count", icon="filter_alt", on_click=count).props("outline")
            ui.button(
                "Print selection",
                icon="print",
                on_click=lambda: _run(
                    daemon.print_selection(c_title.value or "Tasks", **filters()), "custom"
                ),
            )
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


def sec_tasks(daemon: Daemon) -> None:
    ui.label("Candidate tasks").classes("text-lg font-bold")
    ui.label(
        "Everything currently open in Nextcloud Tasks and Deck. Hidden tasks never "
        "appear on prints. Hiding applies to this occurrence (uid + due date) only, "
        "so a recurring task comes back with its next due date."
    ).classes("text-sm opacity-70")
    container = ui.column().classes("w-full gap-1")
    hidden = ui.column().classes("w-full gap-1")

    def render():
        container.clear()
        suppressed = daemon.db.suppressed_keys()
        items = sorted(
            daemon.candidates(),
            key=lambda t: (t.due is None, t.due or datetime.max.date(), t.title.lower()),
        )
        with container:
            if not items:
                ui.label("Nothing loaded yet. Poll from the dashboard.").classes("opacity-70")
            for t in items:
                key = t.suppression_key()
                with ui.row().classes("w-full items-center border-b py-1"):
                    ui.switch(
                        value=key not in suppressed,
                        on_change=lambda e, t=t: _toggle(t, e.value),
                    ).tooltip("on = shown on prints")
                    ui.label(t.title).classes("grow")
                    ui.badge(t.source).props("outline")
                    ui.label(t.list_name).classes("text-sm opacity-70")
                    ui.label(t.due.isoformat() if t.due else "no due").classes(
                        "text-sm w-24 text-right"
                    )

    def _toggle(t, shown):
        if shown:
            daemon.db.unsuppress(*t.suppression_key())
        else:
            daemon.db.suppress(*t.suppression_key(), t.title)
        render_hidden()

    def render_hidden():
        hidden.clear()
        rows = daemon.db.suppressed_rows()
        with hidden:
            ui.label("Hidden").classes("text-lg font-bold mt-4")
            if not rows:
                ui.label("none").classes("opacity-70")
            for r in rows:
                with ui.row().classes("w-full items-center"):
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

    render()
    render_hidden()
    ui.button("Refresh", icon="refresh", on_click=render).props("outline")


def sec_deck(daemon: Daemon) -> None:
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


def sec_lists(daemon: Daemon) -> None:
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


def sec_calendars(daemon: Daemon) -> None:
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


def sec_printer(daemon: Daemon) -> None:
    cfg = daemon.cfg
    with ui.card().classes("w-full"):
        ui.label("Device").classes("font-bold")
        dev = ui.input("Device path", value=cfg.printer.device).classes("w-96")
        width = ui.number("Width (px)", value=cfg.printer.width_px, min=200, max=832, step=8)
        band = ui.number("Band lines", value=cfg.printer.band_lines, min=8, max=1024, step=8)
        lps = ui.number(
            "Send speed (dot lines per second)",
            value=cfg.printer.lines_per_second,
            min=20,
            max=2000,
        ).tooltip("Lower this if the printer resets or drops data on long receipts")
        feed = ui.number(
            "Feed after print (mm)", value=cfg.printer.feed_after_mm, min=0, max=150
        ).tooltip("Space below the last line so you can tear off without cutting into text")
        density = ui.select(
            list(DENSITY_PRESETS),
            value=cfg.printer.density if cfg.printer.density in DENSITY_PRESETS else "",
            label="Density (pick from the sweep test print)",
            with_input=True,
            new_value_mode="add-unique",
        ).classes("w-64")
        codepage = ui.input(
            "Fallback code page (text mode only)", value=cfg.printer.fallback_codepage
        ).classes("w-64")

        def save():
            cfg.printer = replace(
                cfg.printer,
                device=dev.value.strip(),
                width_px=int(width.value),
                band_lines=int(band.value),
                lines_per_second=int(lps.value),
                feed_after_mm=int(feed.value),
                density=density.value or "",
                fallback_codepage=codepage.value.strip() or "CP858",
            )
            save_config(cfg)
            daemon.reload_config(cfg)
            ui.notify("printer settings saved", type="positive")

        ui.button("Save", icon="save", on_click=save)
    with ui.card().classes("w-full"):
        ui.label("Actions").classes("font-bold")
        with ui.row():
            ui.button(
                "Test print", icon="print", on_click=lambda: _run(daemon.print_test(), "test")
            )
            ui.button(
                "Density sweep",
                icon="tune",
                on_click=lambda: _run(daemon.print_test(sweep=True), "sweep"),
            ).props("outline")
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
            for kind, mode in (
                ("sample", "list"),
                ("sample", "cards"),
                ("empty", "list"),
                ("slip", ""),
            ):
                with ui.column():
                    ui.label(f"{kind} {mode}".strip())
                    ui.image(f"/api/preview.png?kind={kind}&layout_mode={mode}").classes(
                        "w-[240px] border bg-white"
                    ).style("image-rendering: pixelated")


def sec_settings(daemon: Daemon, dark: ui.dark_mode) -> None:
    cfg = daemon.cfg
    with ui.card().classes("w-full"):
        ui.label("Nextcloud").classes("font-bold")
        ui.label(
            "Use an app password (Settings → Security → Devices & sessions). "
            "It works with SSO logins. Untick 'Allow filesystem access' on it."
        ).classes("text-sm opacity-70")
        from .. import secrets as _secrets

        ui.label(
            f"Passwords are stored in: {_secrets.backend_name()}. "
            "The config file only holds references."
        ).classes("text-xs opacity-70")
        url = ui.input(
            "Nextcloud URL", value=cfg.nextcloud.url, placeholder="https://cloud.example.org"
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
                ui.notify(f"CalDAV {cal_msg} · Deck {deck_msg}", type="positive", multi_line=True)
            except Exception as exc:
                ui.notify(f"connection failed: {exc}", type="negative", multi_line=True)

        ui.button("Test connection", icon="link", on_click=test).props("outline")
    with ui.card().classes("w-full"):
        ui.label("Extra calendars").classes("font-bold")
        ui.label(
            "Calendars outside Nextcloud, e.g. your university. ICS: a public or webcal:// "
            "subscription link. CalDAV: an account URL (principal discovery) or a direct "
            "calendar collection URL, with its own credentials. Enable/disable them on the "
            "Calendars page after the next poll."
        ).classes("text-sm opacity-70")
        extra_box = ui.column().classes("w-full gap-1")

        def refresh_extra():
            extra_box.clear()
            with extra_box:
                for i, ec in enumerate(cfg.extra_calendars):
                    with ui.row().classes("w-full items-center"):
                        ui.badge(ec.kind.upper()).props("outline")
                        ui.label(ec.name).classes("font-bold")
                        ui.label(ec.url).classes("text-xs opacity-70 grow break-all")
                        if ec.username:
                            ui.label(f"user {ec.username}").classes("text-xs opacity-70")

                        def remove(i=i):
                            from .. import secrets as _s

                            _s.delete(_s.MARK_KEYRING + cfg.extra_calendars[i].id)
                            _s.delete(_s.MARK_VAULT + cfg.extra_calendars[i].id)
                            del cfg.extra_calendars[i]
                            save_config(cfg)
                            daemon.reload_config(cfg)
                            refresh_extra()

                        ui.button(icon="delete", on_click=remove).props("flat dense")
                if not cfg.extra_calendars:
                    ui.label("none").classes("opacity-70")
                with ui.row().classes("w-full items-end gap-2 flex-wrap"):
                    n_name = ui.input("Name", placeholder="University").classes("w-40")
                    n_kind = ui.select(
                        {"ics": "ICS / webcal link", "caldav": "CalDAV"}, value="ics"
                    ).classes("w-40")
                    n_url = ui.input("URL", placeholder="https://… or webcal://…").classes("w-96")
                    n_user = ui.input("Username (optional)").classes("w-40")
                    n_pw = ui.input(
                        "Password (optional)", password=True, password_toggle_button=True
                    ).classes("w-40")

                    def add():
                        if not n_name.value.strip() or not n_url.value.strip():
                            ui.notify("name and URL are required", type="warning")
                            return
                        cfg.extra_calendars.append(
                            ExtraCalendar(
                                name=n_name.value.strip(),
                                kind=n_kind.value,
                                url=n_url.value.strip(),
                                username=n_user.value.strip(),
                                password=n_pw.value,
                            )
                        )
                        save_config(cfg)
                        daemon.reload_config(cfg)
                        ui.notify("calendar added; polling", type="positive")
                        refresh_extra()

                    async def test_new():
                        from datetime import date as _date

                        from ..sources.extra import fetch_extra

                        ec = ExtraCalendar(
                            name=n_name.value.strip() or "test",
                            kind=n_kind.value,
                            url=n_url.value.strip(),
                            username=n_user.value.strip(),
                            password=n_pw.value,
                        )
                        cols, evs, errs = await asyncio.to_thread(
                            fetch_extra, [ec], cfg.nextcloud, _date.today(), set(), None
                        )
                        if errs:
                            ui.notify(
                                f"failed: {list(errs.values())[0]}",
                                type="negative",
                                multi_line=True,
                            )
                        else:
                            ui.notify(
                                f"OK: {len(cols)} calendar(s), {len(evs)} event(s) today",
                                type="positive",
                            )

                    ui.button("Test", icon="link", on_click=test_new).props("outline")
                    ui.button("Add", icon="add", on_click=add)

        refresh_extra()
    with ui.card().classes("w-full"):
        ui.label("Daily receipt").classes("font-bold")
        earliest = ui.number("Earliest hour", value=cfg.daily.earliest_hour, min=0, max=23)
        cal_only = ui.switch(
            "Print days that only have calendar events", value=cfg.daily.print_calendar_only_days
        )
        lang = ui.select(
            {"en": "English", "de": "Deutsch"}, value=cfg.ui.language, label="Receipt language"
        ).classes("w-48")
        layout_sel = ui.select(
            {"list": "Checklist (one continuous list)", "cards": "Cards (cut lines between tasks)"},
            value=cfg.daily.layout,
            label="Layout",
        ).classes("w-80")
        ui.label("Overdue filters, 0 = unlimited. Both can be combined.").classes(
            "text-sm opacity-70 mt-2"
        )
        od_days = ui.number(
            "Max days overdue", value=cfg.daily.overdue_max_days, min=0, max=3650
        ).tooltip("Tasks overdue longer than this are left off the receipt")
        od_count = ui.number(
            "Max overdue tasks", value=cfg.daily.overdue_max_count, min=0, max=500
        ).tooltip("Keeps the most recently due ones; a '+N older' line shows the rest")
        group_sw = ui.switch(
            "Group tasks by task list / Deck stack (instead of overdue / due today)",
            value=cfg.daily.group_by_list,
        )
        showlist_sw = ui.switch(
            "Flat layout: show list or board name under each task", value=cfg.daily.show_list
        )
        notes_sw = ui.switch("Print notes, descriptions and tags", value=cfg.daily.show_notes)
        bday_sw = ui.switch(
            "Birthdays from the contacts birthday calendar", value=cfg.daily.show_birthdays
        )
        bday_days = ui.number(
            "Birthday lookahead (days)", value=cfg.daily.birthdays_lookahead, min=0, max=90
        )
        notes_lines = ui.number(
            "Max note lines (0 = all)", value=cfg.daily.notes_max_lines, min=0, max=50
        )
    with ui.card().classes("w-full"):
        ui.label("Quotes").classes("font-bold")
        quote_sw = ui.switch("Print a rotating quote", value=cfg.daily.quote)
        quote_pos = ui.select(
            {"top": "Top, under the date", "bottom": "Bottom, above the tear line"},
            value=cfg.daily.quote_position,
            label="Position",
        ).classes("w-72")
        quotes_box = ui.column().classes("w-full gap-1")

        def refresh_quotes():
            quotes_box.clear()
            with quotes_box:
                for q in daemon.db.quotes():
                    with ui.row().classes("w-full items-center"):
                        inp = ui.input(value=q["text"]).classes("grow")
                        ui.button(
                            icon="save",
                            on_click=lambda q=q, inp=inp: (
                                daemon.db.update_quote(q["id"], inp.value),
                                ui.notify("quote saved"),
                            ),
                        ).props("flat dense")
                        ui.button(
                            icon="delete",
                            on_click=lambda q=q: (
                                daemon.db.delete_quote(q["id"]),
                                refresh_quotes(),
                            ),
                        ).props("flat dense")
                with ui.row().classes("w-full items-center"):
                    new_q = ui.input(placeholder="New quote").classes("grow")

                    def add():
                        if new_q.value.strip():
                            daemon.db.add_quote(new_q.value)
                            refresh_quotes()

                    ui.button(icon="add", on_click=add).props("flat dense")

        refresh_quotes()
    with ui.card().classes("w-full"):
        ui.label("Logo").classes("font-bold")
        ui.label(
            "Upload one or more images. They are printed centred at the top of the receipt, "
            "converted to black and white."
        ).classes("text-sm opacity-70")
        logo_mode = ui.select(
            {"off": "No logo", "random": "Random image per print", "fixed": "Always this image"},
            value=cfg.logo.mode,
            label="Mode",
        ).classes("w-72")
        logo_file = ui.select(
            [p.name for p in logos.list_logos()], value=cfg.logo.file or None, label="Image"
        ).classes("w-72")
        logo_h = ui.number(
            "Max height (px, 8 px = 1 mm)", value=cfg.logo.max_height, min=16, max=600
        )
        logo_dither = ui.switch("Dither (for photos / greyscale)", value=cfg.logo.dither)
        gallery = ui.row().classes("gap-3 flex-wrap")

        def refresh_gallery():
            gallery.clear()
            names = [p.name for p in logos.list_logos()]
            logo_file.set_options(
                names, value=logo_file.value if logo_file.value in names else None
            )
            with gallery:
                for p in logos.list_logos():
                    with ui.column().classes("items-center gap-1"):
                        ui.image(str(p)).classes("w-24 h-24 object-contain bg-white border")
                        ui.label(p.name).classes("text-xs")
                        ui.button(
                            icon="delete",
                            on_click=lambda p=p: (logos.delete_logo(p.name), refresh_gallery()),
                        ).props("flat dense")

        async def on_upload(e):
            content = await e.file.read()
            try:
                logos.save_logo(e.file.name, content)
                ui.notify(f"saved {e.file.name}", type="positive")
            except ValueError as exc:
                ui.notify(str(exc), type="negative")
            refresh_gallery()

        ui.upload(on_upload=on_upload, auto_upload=True, multiple=True, label="Add images").props(
            "accept=image/*"
        ).classes("w-72")
        refresh_gallery()
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
        ui.label("Local AI (Ollama)").classes("font-bold")
        ui.label(
            "Morning briefing and natural-language task editing in the terminal view. "
            "Needs a running Ollama with the chosen model pulled."
        ).classes("text-sm opacity-70")
        ai_sw = ui.switch("Enable AI assistant", value=cfg.ai.enabled)
        ai_url = ui.input("Ollama URL", value=cfg.ai.url).classes("w-96")
        ai_model = ui.input("Model", value=cfg.ai.model).classes("w-96")
        ai_summary = ui.switch(
            "Briefing when the terminal view opens", value=cfg.ai.summary_on_open
        )
        ai_think = ui.switch("Allow model thinking (slow)", value=cfg.ai.think)
        ai_lang = ui.select(
            {"en": "English", "de": "Deutsch", "auto": "Follow my message"},
            value=cfg.ai.language,
            label="Reply language",
        ).classes("w-48")

        async def ai_test():
            from ..ai import Assistant

            probe = replace(cfg.ai, url=ai_url.value.strip(), model=ai_model.value.strip())
            ui.notify(await Assistant(probe, daemon).available(), type="info")

        ui.button("Test AI", icon="smart_toy", on_click=ai_test).props("outline")
    with ui.card().classes("w-full"):
        ui.label("On screen").classes("font-bold")
        notify_sw = ui.switch(
            "Desktop notification when the daily check runs", value=cfg.screen.notify
        )
        term_sw = ui.switch("Terminal window at login (autostart entry)", value=cfg.screen.terminal)
        focus_sw = ui.switch(
            "Terminal view: start with the cursor in the AI field", value=cfg.ui.tui_focus_ai
        )
        keys_sw = ui.switch(
            "Terminal view: single-key shortcuts (r refresh, p print, t tomorrow, a AI field)",
            value=cfg.ui.tui_shortcuts,
        )
        dark_sw = ui.switch(
            "Dark mode", value=cfg.ui.dark, on_change=lambda e: dark.set_value(bool(e.value))
        )
        with ui.row():
            ui.button(
                "Test notification + window",
                icon="notifications_active",
                on_click=lambda: ui.notify(daemon.test_notification(), type="info"),
            ).props("outline")
            ui.button(
                "Test full morning cycle",
                icon="wb_sunny",
                on_click=lambda: _run(daemon.test_full_cycle(), "cycle"),
            ).props("outline").tooltip(
                "Forgets today's print, fetches from Nextcloud, prints, notifies, opens the window"
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
            layout=layout_sel.value,
            overdue_max_days=int(od_days.value),
            overdue_max_count=int(od_count.value),
            quote=bool(quote_sw.value),
            quote_position=quote_pos.value,
            show_notes=bool(notes_sw.value),
            show_birthdays=bool(bday_sw.value),
            birthdays_lookahead=int(bday_days.value),
            notes_max_lines=int(notes_lines.value),
            group_by_list=bool(group_sw.value),
            show_list=bool(showlist_sw.value),
        )
        cfg.logo = replace(
            cfg.logo,
            mode=logo_mode.value,
            file=logo_file.value or "",
            max_height=int(logo_h.value),
            dither=bool(logo_dither.value),
        )
        cfg.ui = replace(
            cfg.ui,
            language=lang.value,
            dark=bool(dark_sw.value),
            tui_focus_ai=bool(focus_sw.value),
            tui_shortcuts=bool(keys_sw.value),
        )
        cfg.slips = replace(
            cfg.slips,
            enabled_tasks=bool(s_tasks.value),
            enabled_deck=bool(s_deck.value),
            debounce_seconds=int(debounce.value),
        )
        cfg.server = replace(cfg.server, poll_interval=int(poll.value))
        cfg.ai = replace(
            cfg.ai,
            enabled=bool(ai_sw.value),
            url=ai_url.value.strip() or "http://localhost:11434",
            model=ai_model.value.strip() or "gemma4:12B",
            summary_on_open=bool(ai_summary.value),
            think=bool(ai_think.value),
            language=ai_lang.value,
        )
        cfg.screen = replace(cfg.screen, notify=bool(notify_sw.value), terminal=bool(term_sw.value))
        save_config(cfg)
        daemon.reload_config(cfg)
        ui.notify("saved; polling now", type="positive")

    ui.button("Save", icon="save", on_click=save)


def sec_custom_lists(daemon: Daemon) -> None:
    ui.label("Custom lists").classes("text-lg font-bold")
    ui.label(
        "Shopping lists, packing lists … Saved until you delete them; print as a checklist "
        "whenever you need it."
    ).classes("text-sm opacity-70")
    editor = ui.card().classes("w-full")
    table = ui.column().classes("w-full gap-2")
    state = {"id": None}

    with editor:
        e_title = ui.input("Title", placeholder="Shopping list").classes("w-80")
        e_items = ui.textarea("Items, one per line").classes("w-full").props("rows=8")
        with ui.row():

            def save():
                items = [ln for ln in (e_items.value or "").splitlines() if ln.strip()]
                if not (e_title.value or "").strip():
                    ui.notify("title required", type="warning")
                    return
                state["id"] = daemon.db.save_custom_list(e_title.value, items, state["id"])
                ui.notify("saved", type="positive")
                render()

            def new():
                state["id"] = None
                e_title.value, e_items.value = "", ""

            ui.button("Save", icon="save", on_click=save)
            ui.button("New", icon="add", on_click=new).props("outline")
            ui.button(
                "Save & print",
                icon="print",
                on_click=lambda: (save(), _run(daemon.print_custom_list(state["id"]), "list")),
            ).props("outline")

    def load(lst):
        state["id"] = lst["id"]
        e_title.value = lst["title"]
        e_items.value = "\n".join(lst["items"])

    def render():
        table.clear()
        with table:
            for lst in daemon.db.custom_lists():
                with ui.row().classes("w-full items-center border-b py-1"):
                    ui.label(lst["title"]).classes("font-bold grow")
                    ui.label(f"{len(lst['items'])} items").classes("text-sm opacity-70")
                    ui.label(
                        "printed " + lst["printed_at"][:16].replace("T", " ")
                        if lst["printed_at"]
                        else "not printed yet"
                    ).classes("text-xs opacity-70 w-40")
                    ui.button(icon="edit", on_click=lambda lst=lst: load(lst)).props("flat dense")
                    ui.button(
                        icon="print",
                        on_click=lambda lst=lst: _run(daemon.print_custom_list(lst["id"]), "list"),
                    ).props("flat dense")
                    ui.button(
                        icon="delete",
                        on_click=lambda lst=lst: (
                            daemon.db.delete_custom_list(lst["id"]),
                            render(),
                        ),
                    ).props("flat dense")
            if not daemon.db.custom_lists():
                ui.label("no lists yet").classes("opacity-70")

    render()


def sec_tickets(daemon: Daemon) -> None:
    ui.label("Tickets").classes("text-lg font-bold")
    ui.label(
        "Cinema ticket, entry ticket, voucher: fill in what you need, preview, print."
    ).classes("text-sm opacity-70")
    with ui.row().classes("w-full items-start gap-6"):
        with ui.column().classes("gap-1"):
            f_kind = ui.input("Header", value="ADMIT ONE").classes("w-64")
            f_title = ui.input("Title", placeholder="Dune Part Three").classes("w-64")
            f_sub = ui.input("Subtitle", placeholder="Cineplexx Graz").classes("w-64")
            f_when = ui.input("When", placeholder="Sat 27.09. 20:00").classes("w-64")
            f_where = ui.input("Where", placeholder="Hall 4").classes("w-64")
            f_seat = ui.input("Seat", placeholder="Row 7 · Seat 12").classes("w-64")
            f_holder = ui.input("Name").classes("w-64")
            f_price = ui.input("Price").classes("w-64")
            f_code = ui.input("Code / URL (QR)").classes("w-64")
            f_note = ui.input("Note").classes("w-64")

            def ticket():
                return {
                    "kind": f_kind.value or "TICKET",
                    "title": f_title.value or "",
                    "subtitle": f_sub.value or "",
                    "when": f_when.value or "",
                    "where": f_where.value or "",
                    "seat": f_seat.value or "",
                    "holder": f_holder.value or "",
                    "price": f_price.value or "",
                    "code": f_code.value or "",
                    "note": f_note.value or "",
                }

            with ui.row():
                ui.button(
                    "Print ticket",
                    icon="print",
                    on_click=lambda: _run(daemon.print_ticket(ticket()), "ticket"),
                )
                ui.button("Preview", icon="visibility", on_click=lambda: preview()).props("outline")
        with ui.column():
            from ..receipt import layout as _layout
            from ..render import image as _image

            prev = (
                ui.image().classes("w-[300px] border bg-white").style("image-rendering: pixelated")
            )

            def preview():
                import base64
                from io import BytesIO

                buf = BytesIO()
                _image.render(_layout.ticket_receipt(ticket(), daemon.cfg.ui.language)).save(
                    buf, format="PNG"
                )
                prev.set_source(
                    "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
                )

            preview()


async def _print_sample(daemon: Daemon) -> None:
    from ..receipt import layout
    from ..render import image as render_image

    img = render_image.render(layout.daily_receipt(layout.sample_agenda(), daemon.cfg.ui.language))
    await asyncio.to_thread(daemon.printer.print_image, img)


# ---- page ----------------------------------------------------------------------------------


def build(daemon: Daemon) -> None:
    app.include_router(make_router(daemon))

    @ui.page("/")
    def index():
        dark = ui.dark_mode(daemon.cfg.ui.dark)
        ui.colors(primary="#d97706")
        builders: dict[str, Callable[[], None]] = {
            "dashboard": lambda: sec_dashboard(daemon),
            "tasks": lambda: sec_tasks(daemon),
            "deck": lambda: sec_deck(daemon),
            "lists": lambda: sec_lists(daemon),
            "calendars": lambda: sec_calendars(daemon),
            "printer": lambda: sec_printer(daemon),
            "custom_lists": lambda: sec_custom_lists(daemon),
            "tickets": lambda: sec_tickets(daemon),
            "settings": lambda: sec_settings(daemon, dark),
        }
        current = {"key": "dashboard"}
        title = None
        nav_buttons: dict[str, ui.button] = {}

        drawer = ui.left_drawer(value=None).props("width=210 bordered breakpoint=800")
        with ui.header().classes("items-center"):
            ui.button(icon="menu", on_click=drawer.toggle).props("flat round dense color=white")
            ui.icon("receipt_long").classes("text-2xl")
            ui.label("Printcrastinator").classes("text-lg font-bold")
            ui.space()
            title = ui.label("").classes("text-sm opacity-70")
        content = ui.column().classes("w-full max-w-4xl gap-4 p-2")

        def show(key: str) -> None:
            current["key"] = key
            name = next(n for k, n, _ in SECTIONS if k == key)
            title.set_text(name)
            for k, b in nav_buttons.items():
                b.props("color=primary" if k == key else "color=grey-6")
            content.clear()
            with content:
                builders[key]()

        async def navigate(key: str) -> None:
            show(key)
            # on narrow screens the drawer overlays the content: close it after choosing
            if await ui.run_javascript("window.innerWidth", timeout=2) < 800:
                drawer.hide()

        with drawer:
            for key, name, icon in SECTIONS:
                nav_buttons[key] = (
                    ui.button(name, icon=icon, on_click=lambda k=key: navigate(k))
                    .props("flat align=left no-caps")
                    .classes("w-full")
                )
        show("dashboard")


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
        dark=cfg.ui.dark,
    )
