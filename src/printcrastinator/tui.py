"""Interactive terminal view (Textual): click tasks to cross them off, talk to the local AI."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Header, Input, Static

from .config import Config, load_config
from .receipt import i18n


class TaskRow(Static):
    """One clickable task line. Click toggles done in Nextcloud."""

    DEFAULT_CSS = """
    TaskRow { padding: 0 1; height: auto; }
    TaskRow:hover { background: $boost; }
    TaskRow.done { color: $text-muted; }
    TaskRow.busy { color: $warning; }
    """

    def __init__(self, task: dict[str, Any], today: date, lang: str) -> None:
        super().__init__(markup=True)
        self.item = task
        self.today = today
        self.lang = lang
        self.done = False
        self.busy = False
        self.refresh_text()

    def refresh_text(self) -> None:
        t = self.item
        box = "[b green]☑[/b green]  " if self.done else "[b yellow]☐[/b yellow]  "
        title = t["title"].replace("[", "\\[")
        if self.done:
            title = f"[strike]{title}[/strike]"
        marker = ""
        if t.get("due"):
            due = date.fromisoformat(t["due"])
            late = (self.today - due).days
            if late > 0:
                marker = f"  [red]{i18n.label(self.lang, 'late', n=late)}[/red]"
            elif late == 0:
                marker = f"  [green]{i18n.label(self.lang, 'today_word')}[/green]"
            else:
                marker = f"  [dim]{due.strftime('%d.%m')}[/dim]"
        busy = "  [yellow]…[/yellow]" if self.busy else ""
        src = i18n.label(self.lang, "src_" + t["source"])
        where = f"  [dim]{t['list_name']} · {src}[/dim]".replace("[dim]", "[dim]", 1)
        lines = [f"{box}{title}{marker}{where}{busy}"]
        notes = (t.get("notes") or "").strip()
        if notes:
            first = notes.splitlines()[0].replace("[", "\\[")
            lines.append(f"     [dim]{first[:100]}[/dim]")
        if t.get("tags"):
            lines.append("     [dim]#" + " #".join(t["tags"]).replace("[", "\\[") + "[/dim]")
        self.update("\n".join(lines))
        self.set_class(self.done, "done")
        self.set_class(self.busy, "busy")

    def on_click(self) -> None:
        self.app.toggle_task(self)  # type: ignore[attr-defined]


class TaskView(App):
    TITLE = "Printcrastinator"
    CSS = """
    #events { height: auto; max-height: 8; padding: 0 1; border: round $primary; }
    #tasks { height: 1fr; border: round $secondary; border-title-color: $secondary; }
    #ai-panel { height: auto; max-height: 12; border: round $accent; padding: 0 1; }
    #ai-input { dock: bottom; border: round $accent; background: $surface; }
    #ai-input:focus { border: round $warning; }
    .section { color: $accent; text-style: bold; padding: 1 1 0 1; }
    #summary { color: $text; }
    """
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("p", "print_receipt", "Print"),
        Binding("a", "focus_ai", "Ask AI"),
        Binding("escape", "unfocus", show=False),
    ]

    def __init__(self, cfg: Config | None = None) -> None:
        super().__init__()
        self.cfg = cfg or load_config()
        self.lang = self.cfg.ui.language
        self.history: list[dict[str, str]] = []
        self.agenda: dict[str, Any] = {}
        self.ai_enabled = self.cfg.ai.enabled

    # ---- api ---------------------------------------------------------------------------------

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=self.cfg.api_base, timeout=300)

    async def _post(self, path: str, **kw: Any) -> Any:
        async with self._client() as c:
            r = await c.post(path, **kw)
            if r.status_code >= 400:
                try:
                    raise RuntimeError(r.json().get("detail", r.text))
                except ValueError:
                    raise RuntimeError(r.text) from None
            return r.json()

    # ---- layout -------------------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Static(id="events")
            tasks = VerticalScroll(id="tasks")
            tasks.border_title = "Tasks"
            yield tasks
            panel = VerticalScroll(id="ai-panel")
            panel.border_title = "AI"
            with panel:
                yield Static("", id="summary")
            yield Input(
                placeholder="Ask or tell the AI (e.g. 'I paid the electricity bill'), Enter sends",
                id="ai-input",
            )
        yield Footer()

    def on_mount(self) -> None:
        self.sub_title = date.today().strftime("%A %d %b %Y")
        inp = self.query_one("#ai-input", Input)
        if not self.ai_enabled:
            inp.placeholder = "AI disabled in settings"
            inp.disabled = True
        self.load_agenda(summary=True)

    # ---- data ------------------------------------------------------------------------------------

    @work(exclusive=True, group="agenda")
    async def load_agenda(self, summary: bool = False) -> None:
        events = self.query_one("#events", Static)
        try:
            async with self._client() as c:
                await c.post("/api/print/daily")  # daily gate (screen-only if already done)
                r = await c.get("/api/agenda")
                r.raise_for_status()
                self.agenda = r.json()
        except Exception as exc:
            events.update(f"[red]Daemon not reachable at {self.cfg.api_base}: {exc}[/red]")
            return
        self.render_agenda()
        if summary and self.ai_enabled and self.cfg.ai.summary_on_open:
            self.load_summary()

    def render_agenda(self) -> None:
        a = self.agenda
        today = date.fromisoformat(a["day"])
        ev_lines = [f"[b]{i18n.label(self.lang, 'today')}[/b]"]
        for e in a["events"]:
            when = (
                i18n.label(self.lang, "all_day")
                if e["all_day"]
                else f"{e['start'][11:16]}–{e['end'][11:16]}"
            )
            ev_lines.append(f"[cyan]{when:<12}[/cyan] {e['title']}")
        if not a["events"]:
            ev_lines.append(f"[dim]{i18n.label(self.lang, 'no_events')}[/dim]")
        self.query_one("#events", Static).update("\n".join(ev_lines))

        box = self.query_one("#tasks", VerticalScroll)
        box.remove_children()
        sections: list[tuple[str, list[dict[str, Any]]]] = []
        if a["overdue"]:
            sections.append((i18n.label(self.lang, "overdue"), a["overdue"]))
        if a["due_today"]:
            sections.append((i18n.label(self.lang, "due_today"), a["due_today"]))
        for g in a["always"]:
            sections.append((g["title"], g["items"]))
        if not sections:
            box.mount(Static(f"[dim]{i18n.label(self.lang, 'no_tasks')}[/dim]", classes="section"))
        for name, items in sections:
            box.mount(Static(f"{name}  [dim]{len(items)}[/dim]", classes="section"))
            for t in items:
                box.mount(TaskRow(t, today, self.lang))
        if a.get("overdue_hidden"):
            box.mount(
                Static(
                    f"[dim]{i18n.label(self.lang, 'older_overdue', n=a['overdue_hidden'])}[/dim]",
                    classes="section",
                )
            )

    # ---- actions ------------------------------------------------------------------------

    @work(group="toggle")
    async def toggle_task(self, row: TaskRow) -> None:
        if row.busy:
            return
        row.busy = True
        row.refresh_text()
        try:
            await self._post(f"/api/tasks/{row.item['uid']}/done", params={"done": not row.done})
            row.done = not row.done
        except Exception as exc:
            self.notify(f"failed: {exc}", severity="error", timeout=6)
        row.busy = False
        row.refresh_text()

    def action_refresh(self) -> None:
        self.load_agenda()

    def action_focus_ai(self) -> None:
        self.query_one("#ai-input", Input).focus()

    def action_unfocus(self) -> None:
        self.set_focus(None)

    @work(group="print")
    async def action_print_receipt(self) -> None:
        try:
            r = await self._post("/api/print/daily", params={"force": True})
            self.notify("printed" if r.get("printed") else f"not printed: {r.get('reason')}")
        except Exception as exc:
            self.notify(f"print failed: {exc}", severity="error")

    @work(exclusive=True, group="ai")
    async def load_summary(self) -> None:
        out = self.query_one("#summary", Static)
        out.update("[dim]AI briefing…[/dim]")
        try:
            r = await self._post("/api/ai/summary")
            out.update(r["summary"])
        except Exception as exc:
            out.update(f"[red]AI: {exc}[/red]")

    @on(Input.Submitted, "#ai-input")
    def on_ai_submit(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        if not text:
            return
        event.input.value = ""
        self.history.append({"role": "user", "content": text})
        self.ask_ai()

    @work(exclusive=True, group="ai")
    async def ask_ai(self) -> None:
        out = self.query_one("#summary", Static)
        out.update(f"[b]you:[/b] {self.history[-1]['content']}\n[dim]thinking…[/dim]")
        try:
            r = await self._post("/api/ai/chat", json={"messages": self.history})
        except Exception as exc:
            out.update(f"[red]AI: {exc}[/red]")
            return
        self.history.append({"role": "assistant", "content": r["reply"]})
        acts = "\n".join(f"[green]✓[/green] {a}" for a in r.get("actions", []))
        out.update(
            f"[b]you:[/b] {self.history[-2]['content']}\n{r['reply']}"
            + (f"\n{acts}" if acts else "")
        )
        if r.get("actions"):
            await asyncio.sleep(0.5)
            self.load_agenda()


def run(cfg: Config | None = None) -> None:
    TaskView(cfg).run()
