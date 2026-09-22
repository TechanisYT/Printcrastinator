"""HTTP API used by the CLI commands. Mounted into the NiceGUI/FastAPI app."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Response

from .daemon import Daemon
from .receipt import layout
from .render import image as render_image


def make_router(daemon: Daemon) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/status")
    async def status():
        return daemon.status()

    @r.get("/agenda")
    async def agenda(refresh: bool = False):
        ag = await daemon.agenda(refresh=refresh)
        return ag.to_dict()

    @r.get("/preview.png")
    async def preview(kind: str = "live"):
        lang = daemon.cfg.ui.language
        if kind == "sample":
            rc = layout.daily_receipt(layout.sample_agenda(), lang)
        elif kind == "empty":
            rc = layout.daily_receipt(layout.empty_agenda(), lang)
        elif kind == "slip":
            rc = layout.slip_receipt(layout.sample_slip_items(), datetime.now(), lang)
        else:
            rc = layout.daily_receipt(await daemon.agenda(), lang)
        buf = BytesIO()
        render_image.render(rc).save(buf, format="PNG")
        return Response(buf.getvalue(), media_type="image/png")

    @r.post("/print/daily")
    async def print_daily(force: bool = False):
        return await daemon.maybe_print_daily(force=force, reason="api")

    @r.post("/print/test")
    async def print_test():
        await daemon.print_test()
        return {"ok": True}

    @r.post("/printer/feed")
    async def feed(mm: int | None = None):
        await daemon.feed(mm)
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
