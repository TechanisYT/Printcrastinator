"""HTTP API used by the CLI commands. Mounted into the NiceGUI/FastAPI app."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from io import BytesIO

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .daemon import Daemon
from .printer.escpos_out import PrinterError
from .receipt import layout
from .render import image as render_image


class TaskEdit(BaseModel):
    title: str | None = None
    due: str | None = None  # ISO date, "" to clear, omitted to keep
    start: str | None = None  # ISO date, "" to clear (Tasks only)
    notes: str | None = None
    priority: int | None = None  # 0-9, Tasks only
    tags: list[str] | None = None  # Tasks categories / Deck labels
    location: str | None = None  # Tasks only
    assignees: list[str] | None = None  # Deck user ids
    stack: int | None = None  # Deck: move to this stack id (same board)


class EventCreate(BaseModel):
    calendar_id: str
    title: str
    start: str  # ISO date (all-day) or ISO datetime
    end: str | None = None
    description: str = ""
    location: str = ""
    attendees: list[str] | None = None  # "Name <mail>" or "mail"
    rrule: str = ""  # daily | weekly | weekdays | monthly | yearly | every 2 weeks | FREQ=...


class EventEdit(BaseModel):
    title: str | None = None
    start: str | None = None
    end: str | None = None
    description: str | None = None
    location: str | None = None
    attendees: list[str] | None = None  # replaces the whole set; [] removes all
    rrule: str | None = None  # "" removes the recurrence


class Selection(BaseModel):
    title: str = "Tasks"
    list_ids: list[str] | None = None
    due_from: str | None = None
    due_to: str | None = None
    include_no_due: bool = True
    overdue_only: bool = False
    tags: list[str] | None = None
    text: str = ""
    sources: list[str] | None = None


def _selection_filters(b: Selection) -> dict:
    return {
        "list_ids": b.list_ids,
        "due_from": date.fromisoformat(b.due_from) if b.due_from else None,
        "due_to": date.fromisoformat(b.due_to) if b.due_to else None,
        "include_no_due": b.include_no_due,
        "overdue_only": b.overdue_only,
        "tags": b.tags,
        "text": b.text,
        "sources": b.sources,
    }


class ListBody(BaseModel):
    title: str
    items: list[str]
    list_id: int | None = None


class Ticket(BaseModel):
    title: str
    kind: str = "TICKET"  # e.g. ADMIT ONE, CINEMA, ENTRY
    subtitle: str = ""
    when: str = ""
    where: str = ""
    seat: str = ""
    holder: str = ""
    price: str = ""
    code: str = ""  # rendered as QR code
    note: str = ""


class ChatBody(BaseModel):
    messages: list[dict[str, str]]


class SettingBody(BaseModel):
    section: str
    key: str
    value: str | int | bool


class TaskCreate(BaseModel):
    list_id: str
    title: str
    due: str | None = None
    start: str | None = None
    notes: str = ""
    priority: int | None = None
    tags: list[str] | None = None
    location: str | None = None
    assignees: list[str] | None = None


def _parse_when(v: str) -> datetime | date:
    return date.fromisoformat(v) if len(v) == 10 else datetime.fromisoformat(v)


def _parse_due(v: str | None) -> date | None | str:
    if v is None:
        return "keep"
    if v == "":
        return None
    return date.fromisoformat(v)


def make_router(daemon: Daemon) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/tasks")
    async def tasks():
        return {
            "tasks": [t.to_dict() for t in daemon.candidates()],
            "suppressed": [list(k) for k in daemon.db.suppressed_keys()],
            "lists": daemon.task_lists(),
        }

    @r.post("/tasks/{uid}/done")
    async def task_done(uid: str, done: bool = True):
        try:
            t = await daemon.set_task_done(uid, done)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud update failed: {exc}") from exc
        return t.to_dict()

    @r.post("/tasks/{uid}/edit")
    async def task_edit(uid: str, body: TaskEdit):
        fields: dict = body.model_dump(exclude_none=True)
        if "due" in fields:
            fields["due"] = _parse_due(body.due)
        if "start" in fields:
            fields["start"] = _parse_due(body.start)
        try:
            t = await daemon.update_task(uid, **fields)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud update failed: {exc}") from exc
        return t.to_dict()

    @r.post("/tasks")
    async def task_create(body: TaskCreate):
        fields: dict = body.model_dump(exclude_none=True)
        list_id, title = fields.pop("list_id"), fields.pop("title")
        for k in ("due", "start"):
            if fields.get(k):
                fields[k] = date.fromisoformat(fields[k])
            elif k in fields:
                fields.pop(k)
        try:
            uid = await daemon.create_task(list_id, title, **fields)
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud create failed: {exc}") from exc
        return {"uid": uid}

    @r.get("/calendars")
    async def calendars():
        return {"calendars": daemon.calendars()}

    @r.post("/events")
    async def event_create(body: EventCreate):
        try:
            uid = await daemon.create_event(
                body.calendar_id,
                body.title,
                _parse_when(body.start),
                _parse_when(body.end) if body.end else None,
                body.description,
                body.location,
                body.attendees,
                body.rrule,
            )
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud event create failed: {exc}") from exc
        return {"uid": uid}

    @r.post("/events/{uid}/edit")
    async def event_edit(uid: str, body: EventEdit):
        fields: dict = body.model_dump(exclude_none=True)
        for k in ("start", "end"):
            if k in fields:
                fields[k] = _parse_when(fields[k])
        try:
            await daemon.update_event(uid, **fields)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud event update failed: {exc}") from exc
        return {"ok": True}

    @r.delete("/events/{uid}")
    async def event_delete(uid: str):
        try:
            await daemon.delete_event(uid)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud event delete failed: {exc}") from exc
        return {"ok": True}

    @r.get("/status")
    async def status():
        return daemon.status()

    @r.get("/agenda")
    async def agenda(refresh: bool = False, day: str = ""):
        if day:
            return (await daemon.agenda_for(date.fromisoformat(day))).to_dict()
        ag = await daemon.agenda(refresh=refresh)
        return ag.to_dict()

    @r.post("/print/day")
    async def print_day(day: str, layout_mode: str = ""):
        try:
            return await daemon.print_day(date.fromisoformat(day), layout_mode or None)
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc

    @r.post("/print/calendar")
    async def print_calendar(day_from: str, day_to: str = ""):
        try:
            return await daemon.print_calendar(
                date.fromisoformat(day_from), date.fromisoformat(day_to) if day_to else None
            )
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc

    @r.post("/tasks/select")
    async def tasks_select(body: Selection):
        items = daemon.select_tasks(**_selection_filters(body))
        return {"tasks": [t.to_dict() for t in items], "count": len(items)}

    @r.post("/print/selection")
    async def print_selection(body: Selection):
        try:
            return await daemon.print_selection(body.title, **_selection_filters(body))
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc

    @r.get("/preview.png")
    async def preview(kind: str = "live", layout_mode: str = "", day: str = "", day_to: str = ""):
        lang = daemon.cfg.ui.language
        mode = layout_mode or daemon.cfg.daily.layout
        opt = daemon.layout_options()
        if kind == "list":
            lst = daemon.db.custom_list(int(day or 0)) or {"title": "List", "items": []}
            rc = layout.list_receipt(lst["title"], lst["items"], date.today(), lang, opt)
        elif kind == "calendar":
            d0 = date.fromisoformat(day) if day else date.today()
            d1 = date.fromisoformat(day_to) if day_to else d0
            rc = layout.calendar_receipt(await daemon.calendar_days(d0, d1), lang, opt)
        elif kind == "sample":
            rc = layout.daily_receipt(layout.sample_agenda(), lang, mode, opt)
        elif kind == "empty":
            rc = layout.daily_receipt(layout.empty_agenda(), lang, "list", opt)
        elif kind == "slip":
            rc = layout.slip_receipt(layout.sample_slip_items(), datetime.now(), lang)
        else:
            ag = await (daemon.agenda_for(date.fromisoformat(day)) if day else daemon.agenda())
            rc = layout.daily_receipt(ag, lang, mode, opt)
        buf = BytesIO()
        render_image.render(rc).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @r.post("/print/daily")
    async def print_daily(force: bool = False, layout_mode: str = ""):
        return await daemon.maybe_print_daily(
            force=force, reason="api", layout_mode=layout_mode or None
        )

    @r.get("/lists")
    async def lists_get():
        return {"lists": daemon.db.custom_lists()}

    @r.post("/lists")
    async def lists_save(body: ListBody):
        lid = daemon.db.save_custom_list(body.title, body.items, body.list_id)
        return {"list": daemon.db.custom_list(lid)}

    @r.delete("/lists/{list_id}")
    async def lists_delete(list_id: int):
        daemon.db.delete_custom_list(list_id)
        return {"ok": True}

    @r.post("/print/list/{list_id}")
    async def print_list(list_id: int):
        try:
            return await daemon.print_custom_list(list_id)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc

    @r.post("/print/ticket")
    async def print_ticket(body: Ticket):
        try:
            return await daemon.print_ticket(body.model_dump())
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc

    @r.post("/preview/ticket.png")
    async def preview_ticket(body: Ticket):
        buf = BytesIO()
        render_image.render(layout.ticket_receipt(body.model_dump(), daemon.cfg.ui.language)).save(
            buf, format="PNG"
        )
        return Response(buf.getvalue(), media_type="image/png")

    @r.post("/print/sample")
    async def print_sample(layout_mode: str = ""):
        rc = layout.daily_receipt(
            layout.sample_agenda(),
            daemon.cfg.ui.language,
            layout_mode or daemon.cfg.daily.layout,
            daemon.layout_options(),
        )
        try:
            await asyncio.to_thread(daemon.printer.print_image, render_image.render(rc))
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True}

    @r.get("/settings")
    async def get_settings():
        return daemon.settings_dict()

    @r.post("/settings")
    async def set_setting(body: SettingBody):
        try:
            return {"result": daemon.apply_setting(body.section, body.key, body.value)}
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @r.post("/printer/action")
    async def printer_action(action: str):
        from .ai import Assistant

        return {
            "result": await Assistant(daemon.cfg.ai, daemon)._run_tool(
                "printer_action", {"action": action}
            )
        }

    @r.get("/ai/status")
    async def ai_status():
        from .ai import Assistant

        if not daemon.cfg.ai.enabled:
            return {"enabled": False, "status": "disabled"}
        return {
            "enabled": True,
            "model": daemon.cfg.ai.model,
            "status": await Assistant(daemon.cfg.ai, daemon).available(),
        }

    @r.post("/ai/summary")
    async def ai_summary():
        from .ai import Assistant

        if not daemon.cfg.ai.enabled:
            raise HTTPException(400, "AI disabled")
        try:
            return {
                "summary": await Assistant(daemon.cfg.ai, daemon).summary(await daemon.agenda())
            }
        except Exception as exc:
            raise HTTPException(502, f"AI failed: {exc}") from exc

    @r.post("/ai/chat")
    async def ai_chat(body: ChatBody):
        from .ai import Assistant

        if not daemon.cfg.ai.enabled:
            raise HTTPException(400, "AI disabled")
        try:
            return await Assistant(daemon.cfg.ai, daemon).chat(body.messages, await daemon.agenda())
        except Exception as exc:
            raise HTTPException(502, f"AI failed: {exc}") from exc

    @r.post("/test/cycle")
    async def test_cycle():
        return await daemon.test_full_cycle()

    @r.post("/print/test")
    async def print_test(sweep: bool = False):
        try:
            await daemon.print_test(sweep)
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True}

    @r.post("/printer/feed")
    async def feed(mm: int | None = None):
        try:
            await daemon.feed(mm)
        except PrinterError as exc:
            raise HTTPException(503, str(exc)) from exc
        return {"ok": True}

    @r.post("/notify")
    async def notify_now():
        ag = await daemon.agenda()
        daemon.show_on_screen(ag)
        return {"ok": True}

    @r.post("/poll")
    async def poll():
        ok = await daemon.poll_once()
        return {"ok": ok, "status": daemon.status()}

    return r
