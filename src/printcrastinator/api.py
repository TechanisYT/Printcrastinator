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
            )
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud event create failed: {exc}") from exc
        return {"uid": uid}

    @r.get("/status")
    async def status():
        return daemon.status()

    @r.get("/agenda")
    async def agenda(refresh: bool = False):
        ag = await daemon.agenda(refresh=refresh)
        return ag.to_dict()

    @r.get("/preview.png")
    async def preview(kind: str = "live", layout_mode: str = ""):
        lang = daemon.cfg.ui.language
        mode = layout_mode or daemon.cfg.daily.layout
        opt = daemon.layout_options()
        if kind == "sample":
            rc = layout.daily_receipt(layout.sample_agenda(), lang, mode, opt)
        elif kind == "empty":
            rc = layout.daily_receipt(layout.empty_agenda(), lang, "list", opt)
        elif kind == "slip":
            rc = layout.slip_receipt(layout.sample_slip_items(), datetime.now(), lang)
        else:
            rc = layout.daily_receipt(await daemon.agenda(), lang, mode, opt)
        buf = BytesIO()
        render_image.render(rc).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @r.post("/print/daily")
    async def print_daily(force: bool = False, layout_mode: str = ""):
        return await daemon.maybe_print_daily(
            force=force, reason="api", layout_mode=layout_mode or None
        )

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
