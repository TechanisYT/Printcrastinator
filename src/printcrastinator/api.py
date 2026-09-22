"""HTTP API used by the CLI commands. Mounted into the NiceGUI/FastAPI app."""

from __future__ import annotations

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
    notes: str | None = None


class ChatBody(BaseModel):
    messages: list[dict[str, str]]


class TaskCreate(BaseModel):
    list_id: str
    title: str
    due: str | None = None
    notes: str = ""


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
        try:
            t = await daemon.update_task(uid, body.title, _parse_due(body.due), body.notes)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud update failed: {exc}") from exc
        return t.to_dict()

    @r.post("/tasks")
    async def task_create(body: TaskCreate):
        try:
            uid = await daemon.create_task(
                body.list_id,
                body.title,
                date.fromisoformat(body.due) if body.due else None,
                body.notes,
            )
        except Exception as exc:
            raise HTTPException(502, f"Nextcloud create failed: {exc}") from exc
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

    @r.post("/notify/test")
    async def notify_test():
        return {"result": daemon.test_notification()}

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
