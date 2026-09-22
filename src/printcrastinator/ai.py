"""Local AI assistant via Ollama: daily summary and natural-language task editing (tool calls)."""

from __future__ import annotations

import json
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

import httpx

from .config import AiConfig
from .models import DailyAgenda

if TYPE_CHECKING:
    from .daemon import Daemon

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "complete_task",
            "description": "Mark a task as done (crossed off).",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string", "description": "task uid from the list"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reopen_task",
            "description": "Undo completion of a task.",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string"}},
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_task",
            "description": (
                "Change a task's title, due date (YYYY-MM-DD, empty string clears) or notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "notes": {"type": "string"},
                },
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_settings",
            "description": "Read the current Printcrastinator settings (all sections).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_setting",
            "description": (
                "Change one setting: section (daily, printer, slips, screen, ui, logo, ai, "
                "server, nextcloud) and key as shown by get_settings. Value as string."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "section": {"type": "string"},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["section", "key", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_receipt",
            "description": "Print today's receipt now. layout: list or cards (empty = configured).",
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {"type": "boolean", "description": "print even if already printed"},
                    "layout": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "printer_action",
            "description": (
                "Printer/test actions: test_print, density_sweep, feed, test_notification, "
                "full_cycle (fetch+print+notify+window), poll (fetch from Nextcloud now)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string"}},
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": "Create a new task in a task list or Deck stack.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "id from the available lists"},
                    "title": {"type": "string"},
                    "due": {"type": "string", "description": "YYYY-MM-DD, optional"},
                    "notes": {"type": "string"},
                },
                "required": ["list_id", "title"],
            },
        },
    },
]


def agenda_context(agenda: DailyAgenda, lists: list[dict[str, Any]], today: date) -> str:
    lines = [f"Today is {today.isoformat()} ({today.strftime('%A')})."]
    if agenda.events:
        lines.append("Calendar today:")
        for e in agenda.events:
            when = "all day" if e.all_day else f"{e.start:%H:%M}-{e.end:%H:%M}"
            lines.append(f"- {when}: {e.title}")
    lines.append("Open tasks (uid | title | list | due | notes):")
    for t in agenda.all_tasks:
        due = t.due.isoformat() if t.due else "-"
        late = t.days_late(today) if t.due else 0
        status = f"overdue {late}d" if late > 0 else ("due today" if t.due == today else "")
        notes = t.notes.replace("\n", " ")[:120]
        lines.append(
            f"- {t.uid} | {t.title} | {t.list_name} ({t.source}) | {due} {status} | {notes}"
        )
    if not agenda.all_tasks:
        lines.append("- (none)")
    lines.append(
        f"Counts: {len(agenda.all_tasks)} open tasks shown, {len(agenda.overdue)} overdue, "
        f"{len(agenda.due_today)} due today, {len(agenda.always_items)} from always-print lists"
        + (
            f", {agenda.overdue_hidden} older overdue hidden by filters"
            if agenda.overdue_hidden
            else ""
        )
        + ". Use these numbers, do not count the list yourself."
    )
    lines.append("Available lists for new tasks (id | name):")
    for ls in lists:
        lines.append(f"- {ls['id']} | {ls['name']} ({ls['source']})")
    return "\n".join(lines)


SYSTEM = (
    "You are Printcrastinator, a terse assistant for a personal to-do list synced with Nextcloud. "
    "You can complete, reopen, edit and create tasks, read and change settings, print the receipt "
    "and run printer tests with the provided tools. Always use the uid "
    "exactly as listed. When the user refers to a task by a rough description, pick the best "
    "match; if it is ambiguous, ask. After tool calls, confirm in one short sentence. Answer in "
    "the language the user writes in. Never invent tasks that are not in the list.\n\n"
)

SUMMARY_PROMPT = (
    "Write a short morning briefing (max 6 lines, plain text, no markdown headings): what is on "
    "the calendar, what is most urgent, and one encouraging sentence. Mention counts, not every "
    "task."
)


class Assistant:
    def __init__(self, cfg: AiConfig, daemon: Daemon) -> None:
        self.cfg = cfg
        self.daemon = daemon

    async def _chat(self, messages: list[dict[str, Any]], tools: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.cfg.model,
            "messages": messages,
            "stream": False,
            # thinking models otherwise spend thousands of hidden tokens per answer
            "think": self.cfg.think,
            "options": {"num_ctx": self.cfg.num_ctx, "num_predict": self.cfg.max_tokens},
        }
        if tools:
            body["tools"] = TOOLS
        async with httpx.AsyncClient(timeout=self.cfg.timeout) as c:
            r = await c.post(f"{self.cfg.url.rstrip('/')}/api/chat", json=body)
            r.raise_for_status()
            return r.json()["message"]

    async def available(self) -> str:
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(f"{self.cfg.url.rstrip('/')}/api/tags")
                r.raise_for_status()
                names = [m["name"] for m in r.json().get("models", [])]
            if self.cfg.model not in names:
                return f"model {self.cfg.model} not installed (have: {', '.join(names) or 'none'})"
            return "ok"
        except Exception as exc:
            return f"ollama unreachable at {self.cfg.url}: {exc}"

    def _system(self, agenda: DailyAgenda) -> str:
        return SYSTEM + agenda_context(agenda, self.daemon.task_lists(), date.today())

    async def summary(self, agenda: DailyAgenda) -> str:
        msg = await self._chat(
            [
                {"role": "system", "content": self._system(agenda)},
                {"role": "user", "content": SUMMARY_PROMPT},
            ],
            tools=False,
        )
        return (msg.get("content") or "").strip()

    async def _run_tool(self, name: str, args: dict[str, Any]) -> str:
        d = self.daemon
        try:
            if name == "complete_task":
                t = await d.set_task_done(args["uid"], True)
                return f"completed: {t.title}"
            if name == "reopen_task":
                t = await d.set_task_done(args["uid"], False)
                return f"reopened: {t.title}"
            if name == "edit_task":
                due: Any = "keep"
                if "due" in args and args["due"] is not None:
                    due = date.fromisoformat(args["due"]) if args["due"] else None
                t = await d.update_task(args["uid"], args.get("title"), due, args.get("notes"))
                return f"edited: {args.get('title') or t.title}"
            if name == "create_task":
                due = date.fromisoformat(args["due"]) if args.get("due") else None
                uid = await d.create_task(
                    args["list_id"], args["title"], due, args.get("notes", "")
                )
                return f"created {uid}: {args['title']}"
            if name == "get_settings":
                return json.dumps(d.settings_dict())
            if name == "set_setting":
                return d.apply_setting(args["section"], args["key"], args["value"])
            if name == "print_receipt":
                r = await d.maybe_print_daily(
                    force=bool(args.get("force", True)),
                    reason="ai",
                    layout_mode=args.get("layout") or None,
                )
                return json.dumps(r)
            if name == "printer_action":
                act = args.get("action", "")
                if act == "test_print":
                    await d.print_test()
                    return "test print sent"
                if act == "density_sweep":
                    await d.print_test(sweep=True)
                    return "density sweep sent"
                if act == "feed":
                    await d.feed()
                    return "paper fed"
                if act == "test_notification":
                    return d.test_notification()
                if act == "full_cycle":
                    return json.dumps(await d.test_full_cycle())
                if act == "poll":
                    ok = await d.poll_once()
                    return f"polled, ok={ok}, {d.status()['counts']}"
                return f"unknown action {act}"
            return f"unknown tool {name}"
        except Exception as exc:
            return f"error: {exc}"

    async def chat(self, history: list[dict[str, str]], agenda: DailyAgenda) -> dict[str, Any]:
        """history: [{role, content}] from the client. Returns reply + actions performed."""
        messages: list[dict[str, Any]] = [{"role": "system", "content": self._system(agenda)}]
        messages.extend(history[-12:])
        actions: list[str] = []
        for _ in range(4):
            msg = await self._chat(messages, tools=True)
            calls = msg.get("tool_calls") or []
            if not calls:
                return {"reply": (msg.get("content") or "").strip(), "actions": actions}
            messages.append(msg)
            for call in calls:
                fn = call.get("function", {})
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = await self._run_tool(fn.get("name", ""), args)
                if fn.get("name") not in ("get_settings",):
                    actions.append(result)
                messages.append(
                    {"role": "tool", "content": result, "tool_name": fn.get("name", "")}
                )
            # refresh context so follow-up calls see the new state
            await self.daemon.agenda(refresh=True)
        return {"reply": "Done: " + "; ".join(actions), "actions": actions}
