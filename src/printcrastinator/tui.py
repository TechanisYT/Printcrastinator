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

    def __init__(
        self, task: dict[str, Any], today: date, lang: str, show_list: bool = True
    ) -> None:
        super().__init__(markup=True)
        self.item = task
        self.today = today
        self.lang = lang
        self.show_list = show_list
        self.done = False
        self.busy = False
        self.refresh_text()

    def refresh_text(self) -> None:
        t = self.item
        box = "[b green]\\[x][/b green] " if self.done else "[b yellow]\\[ ][/b yellow] "
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
        busy = ""  # optimistic UI: no spinner, the row already shows the new state
        src = i18n.label(self.lang, "src_" + t["source"])
        where = f"  [dim]{t['list_name']} · {src}[/dim]" if self.show_list else ""
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
    #tasks { height: 1fr; }
    .group { height: auto; border: round $primary; border-title-color: $primary;
             padding: 0 1; margin-bottom: 1; }
    .group.overdue { border: round $error; border-title-color: $error; }
    .group.always { border: round $secondary; border-title-color: $secondary; }
    .subsection { margin-top: 1; color: $text-muted; text-style: bold; }
    .event { padding: 0 1; }
    .empty { color: $text-muted; }
    #ai-panel { height: auto; max-height: 12; border: round $accent; padding: 0 1; }
    #ai-input { dock: bottom; border: round $accent; background: $surface; }
    #ai-input:focus { border: round $warning; }
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
            yield VerticalScroll(id="tasks")
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
        try:
            async with self._client() as c:
                await c.post("/api/print/daily")  # daily gate (screen-only if already done)
                r = await c.get("/api/agenda")
                r.raise_for_status()
                self.agenda = r.json()
        except Exception as exc:
            box = self.query_one("#tasks", VerticalScroll)
            box.remove_children()
            box.mount(Static(f"[red]Daemon not reachable at {self.cfg.api_base}: {exc}[/red]"))
            return
        self.render_agenda()
        if summary and self.ai_enabled and self.cfg.ai.summary_on_open:
            self.load_summary()

    def _list_rows(self, items: list[dict[str, Any]], today: date) -> list[Static]:
        """Task rows under list subheadings, or flat rows with the list name."""
        out: list[Static] = []
        if self.cfg.daily.group_by_list:
            by: dict[str, list[dict[str, Any]]] = {}
            for t in items:
                key = f"{t['list_name']} · {i18n.label(self.lang, 'src_' + t['source'])}"
                by.setdefault(key, []).append(t)
            for key in sorted(by, key=str.lower):
                out.append(Static(key, classes="subsection"))
                out.extend(TaskRow(t, today, self.lang, show_list=False) for t in by[key])
        else:
            out.extend(TaskRow(t, today, self.lang) for t in items)
        return out

    @staticmethod
    def _group(title: str, count: int, children: list[Static], extra_class: str = "") -> Vertical:
        g = Vertical(*children, classes=f"group {extra_class}".strip())
        g.border_title = f"{title}  {count}"
        return g

    def render_agenda(self) -> None:
        a = self.agenda
        today = date.fromisoformat(a["day"])
        box = self.query_one("#tasks", VerticalScroll)
        box.remove_children()
        groups: list[Vertical] = []

        # TODAY: events + tasks due today
        kids: list[Static] = []
        for e in a["events"]:
            when = (
                i18n.label(self.lang, "all_day")
                if e["all_day"]
                else f"{e['start'][11:16]}–{e['end'][11:16]}"
            )
            kids.append(Static(f"[cyan]{when:<12}[/cyan] {e['title']}", classes="event"))
        if not a["events"] and not a["due_today"]:
            kids.append(Static(i18n.label(self.lang, "no_events"), classes="empty"))
        kids.extend(self._list_rows(a["due_today"], today))
        groups.append(
            self._group(
                i18n.label(self.lang, "today"), len(a["events"]) + len(a["due_today"]), kids
            )
        )

        # OVERDUE
        if a["overdue"]:
            kids = self._list_rows(a["overdue"], today)
            if a.get("overdue_hidden"):
                kids.append(
                    Static(
                        i18n.label(self.lang, "older_overdue", n=a["overdue_hidden"]),
                        classes="empty",
                    )
                )
            groups.append(
                self._group(i18n.label(self.lang, "overdue"), len(a["overdue"]), kids, "overdue")
            )

        # always-print groups
        for grp in a["always"]:
            if grp["items"]:
                rows = [TaskRow(t, today, self.lang, show_list=False) for t in grp["items"]]
                groups.append(self._group(grp["title"], len(grp["items"]), rows, "always"))

        box.mount_all(groups)
        if not a["overdue"] and not a["due_today"] and not a["always"]:
            box.mount(Static(i18n.label(self.lang, "no_tasks"), classes="empty"))

    # ---- actions ------------------------------------------------------------------------

    @work(group="toggle")
    async def toggle_task(self, row: TaskRow) -> None:
        """Optimistic: flip immediately, revert with an error only if Nextcloud refuses."""
        if row.busy:
            return
        target = not row.done
        row.done = target
        row.busy = True
        row.refresh_text()
        try:
            await self._post(f"/api/tasks/{row.item['uid']}/done", params={"done": target})
        except Exception as exc:
            row.done = not target
            self.notify(f"{row.item['title'][:40]}: {exc}", severity="error", timeout=8)
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
