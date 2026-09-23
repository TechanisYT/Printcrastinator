"""Local AI assistant via Ollama: daily summary and natural-language task editing (tool calls)."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
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
                "Change fields of a task. Dates YYYY-MM-DD, empty string clears. Tasks-only: "
                "start, priority (1 high .. 9 low), location. Deck-only: assignees (user ids), "
                "stack (target stack id). tags = Tasks categories or Deck label names."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "start": {"type": "string"},
                    "notes": {"type": "string"},
                    "priority": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "location": {"type": "string"},
                    "assignees": {"type": "array", "items": {"type": "string"}},
                    "stack": {"type": "integer"},
                },
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "contacts",
            "description": (
                "Nextcloud contacts. action 'find': contacts whose name contains query, with "
                "their current birthday and href. action 'set_birthday': set (or with empty "
                "birthday remove) the birthday of the contact with that href; birthday "
                "YYYY-MM-DD, or --MM-DD when the year is unknown. Always find first, and if "
                "several contacts match, ask which one."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "query": {"type": "string"},
                    "href": {"type": "string"},
                    "birthday": {"type": "string"},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "birthdays",
            "description": (
                "Birthdays of the next N days from the contacts birthday calendar. "
                "action: show (list them) or print (print them as a receipt)."
            ),
            "parameters": {
                "type": "object",
                "properties": {"action": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lists",
            "description": (
                "Saved custom lists (shopping list, packing list …). action: show | save | add | "
                "remove | print | delete. save creates or replaces the list with `items`; add "
                "appends items; remove deletes matching items; print prints it as a checklist."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "title": {"type": "string"},
                    "items": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_ticket",
            "description": (
                "Print a ticket (cinema, entry, voucher …): kind (header, e.g. ADMIT ONE), title, "
                "subtitle, when, where, seat, holder, price, code (printed as QR), note."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                    "when": {"type": "string"},
                    "where": {"type": "string"},
                    "seat": {"type": "string"},
                    "holder": {"type": "string"},
                    "price": {"type": "string"},
                    "code": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["title"],
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
            "description": (
                "Print the daily receipt (events, due, overdue, pinned) for a day. "
                "day: YYYY-MM-DD, default today. layout: list or cards (empty = configured)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "day": {"type": "string"},
                    "force": {"type": "boolean", "description": "print even if already printed"},
                    "layout": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_calendar",
            "description": (
                "Print ONLY calendar events, no tasks: one day as an upright timeline, or a "
                "range of days (max 14) as a rotated table with the days side by side. "
                "day_from/day_to: YYYY-MM-DD; omit day_to for a single day."
            ),
            "parameters": {
                "type": "object",
                "properties": {"day_from": {"type": "string"}, "day_to": {"type": "string"}},
                "required": ["day_from"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "print_tasks",
            "description": (
                "Print a custom receipt with a title and a filtered set of open tasks, grouped by "
                "list. Filters combine. list_ids: task list ids or names, Deck board ids or names "
                "(all stacks), or 'board/stack' ids or 'Board · Stack' names. Use only list_ids "
                "unless the user asked for a time frame. due_from/due_to: YYYY-MM-DD. "
                "include_no_due: "
                "keep tasks without a due date (default true; set false for 'due in the next N "
                "days'). overdue_only. tags: any-of. text: substring in title/notes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "list_ids": {"type": "array", "items": {"type": "string"}},
                    "due_from": {"type": "string"},
                    "due_to": {"type": "string"},
                    "include_no_due": {"type": "boolean"},
                    "overdue_only": {"type": "boolean"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
                },
                "required": ["title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "preview_tasks",
            "description": "Same filters as print_tasks, returns the matching tasks, no print.",
            "parameters": {
                "type": "object",
                "properties": {
                    "list_ids": {"type": "array", "items": {"type": "string"}},
                    "due_from": {"type": "string"},
                    "due_to": {"type": "string"},
                    "include_no_due": {"type": "boolean"},
                    "overdue_only": {"type": "boolean"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "text": {"type": "string"},
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
            "description": (
                "Create a task in a task list or Deck stack. Same optional fields as edit_task."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "list_id": {"type": "string", "description": "id from the available lists"},
                    "title": {"type": "string"},
                    "due": {"type": "string"},
                    "start": {"type": "string"},
                    "notes": {"type": "string"},
                    "priority": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "location": {"type": "string"},
                    "assignees": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["list_id", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_event",
            "description": (
                "Create a calendar event in one of the user's Nextcloud calendars. start/end: "
                "YYYY-MM-DD for all-day or YYYY-MM-DDTHH:MM for timed (end defaults to +1 h)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "calendar_id": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "'Name <mail@x>' or 'mail@x'; each needs a mail address",
                    },
                    "rrule": {
                        "type": "string",
                        "description": (
                            "recurrence: daily, weekly, weekdays, monthly, yearly, "
                            "'every 2 weeks', "
                            "'every monday and thursday', or an RRULE like FREQ=WEEKLY;BYDAY=TU"
                        ),
                    },
                },
                "required": ["calendar_id", "title", "start"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_event",
            "description": (
                "Change an event: title, start/end (YYYY-MM-DD or YYYY-MM-DDTHH:MM), description, "
                "location, attendees (full replacement list of 'Name <mail>'; [] removes all). "
                "Use the event uid from the calendar list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "uid": {"type": "string"},
                    "title": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "description": {"type": "string"},
                    "location": {"type": "string"},
                    "attendees": {"type": "array", "items": {"type": "string"}},
                    "rrule": {
                        "type": "string",
                        "description": "recurrence; empty string removes it",
                    },
                },
                "required": ["uid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_event",
            "description": "Delete an event by uid.",
            "parameters": {
                "type": "object",
                "properties": {"uid": {"type": "string"}},
                "required": ["uid"],
            },
        },
    },
]


def agenda_context(
    agenda: DailyAgenda,
    lists: list[dict[str, Any]],
    today: date,
    calendars: list[dict[str, Any]] | None = None,
    deck_meta: dict[int, dict[str, Any]] | None = None,
) -> str:
    lines = [f"Today is {today.isoformat()} ({today.strftime('%A')})."]
    if agenda.events:
        lines.append("Calendar today (uid | time | title | location | attendees):")
        for e in agenda.events:
            when = "all day" if e.all_day else f"{e.start:%H:%M}-{e.end:%H:%M}"
            who = ", ".join(e.attendees) if e.attendees else "-"
            lines.append(f"- {e.uid} | {when} | {e.title} | {e.location or '-'} | {who}")
    if agenda.birthdays:
        lines.append("Birthdays (from the contacts birthday calendar):")
        for b in agenda.birthdays:
            delta = (b.day - today).days
            when = "today" if delta == 0 else f"in {delta} days ({b.day.isoformat()})"
            lines.append(f"- {b.name} {when}" + (f", turns {b.age}" if b.age else ""))
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
    if calendars:
        lines.append("Calendars for new events (id | name):")
        for c in calendars:
            lines.append(f"- {c['id']} | {c['name']}")
    if deck_meta:
        lines.append("Deck boards: labels and assignable user ids:")
        for bid, meta in deck_meta.items():
            lines.append(
                f"- board {bid}: labels {list(meta['labels'])}; users {list(meta['users'])}"
            )
    return "\n".join(lines)


SYSTEM = (
    "You are Printcrastinator, a terse assistant for a personal to-do list synced with Nextcloud. "
    "You can complete, reopen, edit and create tasks (all fields), create calendar events, "
    "read and change settings, print the daily receipt for any day, print filtered task lists, "
    "keep saved custom lists (shopping lists) and print tickets, "
    "and run printer tests with the provided tools. Always use the uid "
    "exactly as listed. When the user refers to a task by a rough description, pick the best "
    "match; if it is ambiguous, ask. After tool calls, confirm in one short sentence. "
    "Never invent tasks that are not in the list. Task titles may be in German or dialect; "
    "that does not change your reply language.\n\n"
)

LANGUAGE_RULE = {
    "en": "Always reply in English, whatever language the tasks or the user use.\n\n",
    "de": "Antworte immer auf Deutsch, egal in welcher Sprache Aufgaben oder Nutzer schreiben.\n\n",
    "auto": "Reply in the language the user's message is written in.\n\n",
}

GUIDELINES = (
    "Printing guidelines:\n"
    "- 'print today / the receipt / for tomorrow / for <date>': print_receipt with day.\n"
    "- 'print the <stack> list under deck <board>': print_tasks with "
    "list_ids=['<board>/<stack>'] (look the id up in the lists), title '<board> · <stack>'.\n"
    "- 'print everything from board <X>': list_ids=['<board id>'].\n"
    "- 'tasks from list <L> due in the next week': list_ids=['<L id>'], due_from=today, "
    "due_to=today+7, include_no_due=false.\n"
    "- 'print what is overdue in <L>': list_ids, overdue_only=true.\n"
    "- 'print the calendar / events / plan / schedule (for today, tomorrow, the next 3 days, "
    "next week)': print_calendar ONLY; never print_receipt or print_tasks for that. 'next N "
    "days' = today through today+N-1; 'next week' = tomorrow through tomorrow+6.\n"
    "- After create_event or task changes the data is already refreshed; print right away.\n"
    "- Attendees need an e-mail address ('Name <mail>'). If the user only gives a name and no "
    "address is known, ask for the address instead of guessing one.\n"
    "- To change attendance, pass the complete new attendee list to edit_event.\n"
    "- print_tasks ALWAYS needs a filter. 'all decks/stacks named X' -> list_ids=['X'] (a "
    "name matches every stack with that name across boards). Never call print_tasks with "
    "only a title; that would print every open task.\n"
    "- Events: the title is only the activity ('eating pizza', 'bookstores'); place names go "
    "into location ('Pizzaiolo, Graz', 'Thalia and Morawa, Graz'), never into the title. "
    "Times never go into the title either.\n"
    "- 'print (the) tasks for/of/from <name>': print_tasks with list_ids=['<name>'] and NO other "
    "filter. Names are accepted: a task list name, a Deck board name (all its stacks) or "
    "'Board · Stack'. If a task list and a board share the name, both are included.\n"
    "- Never add due_from/due_to/overdue_only/text unless the user explicitly asks for a time "
    "frame, overdue items or a keyword. 'print the tasks for X' means ALL open tasks of X.\n"
    "- When unsure what a filter matches, call preview_tasks first, then print_tasks.\n"
    "- Report the 'count' the tool returns; do not count items yourself.\n"
    "- 'set Anna's birthday to 21 May 1990' -> contacts(find, query='Anna'), then "
    "contacts(set_birthday, href, birthday='1990-05-21'); unknown year: '--05-21'.\n"
    "- 'print/show birthdays for the next 2 weeks' -> birthdays(action, days=14). "
    "'How far ahead are birthdays shown' is the setting daily.birthdays_lookahead.\n"
    "- Custom lists: 'add milk and eggs to the shopping list' -> lists(add, title='shopping "
    "list', items=[...]); 'print the shopping list' -> lists(print). Lists are saved until "
    "deleted; create one with save when it does not exist yet.\n"
    "- Tickets: 'print a cinema ticket for Dune, Saturday 20:00, seat 12' -> print_ticket with "
    "kind 'CINEMA'; put any booking code or URL into code so it becomes a QR code.\n"
    "- Recurring events: pass rrule (e.g. 'weekly', 'every monday', FREQ=…) to create_event; "
    "'until' or 'count' can be appended to an RRULE string.\n"
    "- Compute dates from 'Today is …'; never ask the user for the date format.\n\n"
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

    async def _system(self, agenda: DailyAgenda) -> str:
        rule = LANGUAGE_RULE.get(self.cfg.language, LANGUAGE_RULE["en"])
        warn = ""
        if self.daemon.state.errors:
            warn = (
                "WARNING: the last fetch from Nextcloud failed ("
                + "; ".join(f"{k}: {v[:80]}" for k, v in self.daemon.state.errors.items())
                + "). The task list below may be stale or incomplete; say so.\n\n"
            )
        return (
            SYSTEM
            + rule
            + GUIDELINES
            + warn
            + agenda_context(
                agenda,
                self.daemon.task_lists(),
                date.today(),
                self.daemon.calendars(),
                await self.daemon.deck_meta(),
            )
        )

    def _no_data_message(self) -> str | None:
        """When the fetch failed and nothing is loaded, answer without the model."""
        st = self.daemon.state
        if st.errors and not (st.tasks or st.cards):
            reasons = "; ".join(f"{k}: {v.splitlines()[0][:100]}" for k, v in st.errors.items())
            return f"I can't see your tasks right now, the Nextcloud fetch failed: {reasons}"
        return None

    async def summary(self, agenda: DailyAgenda) -> str:
        if (msg := self._no_data_message()) is not None:
            return msg
        msg = await self._chat(
            [
                {"role": "system", "content": await self._system(agenda)},
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
                f = {k: v for k, v in args.items() if k != "uid"}
                for k in ("due", "start"):
                    if k in f:
                        f[k] = date.fromisoformat(f[k]) if f[k] else None
                t = await d.update_task(args["uid"], **f)
                return f"edited: {args.get('title') or t.title}"
            if name == "create_task":
                f = {k: v for k, v in args.items() if k not in ("list_id", "title")}
                for k in ("due", "start"):
                    if k in f:
                        f[k] = date.fromisoformat(f[k]) if f[k] else None
                uid = await d.create_task(args["list_id"], args["title"], **f)
                return f"created {uid}: {args['title']}"
            if name == "create_event":

                def when(v: str):
                    return date.fromisoformat(v) if len(v) == 10 else datetime.fromisoformat(v)

                uid = await d.create_event(
                    args["calendar_id"],
                    args["title"],
                    when(args["start"]),
                    when(args["end"]) if args.get("end") else None,
                    args.get("description", ""),
                    args.get("location", ""),
                    args.get("attendees") or None,
                    args.get("rrule") or "",
                )
                rec = f", repeats {args['rrule']}" if args.get("rrule") else ""
                return f"event created: {args['title']} ({args['start']}{rec})"
            if name == "edit_event":

                def when2(v: str):
                    return date.fromisoformat(v) if len(v) == 10 else datetime.fromisoformat(v)

                f = {k: v for k, v in args.items() if k != "uid" and v is not None}
                for k in ("start", "end"):
                    if k in f:
                        f[k] = when2(f[k])
                await d.update_event(args["uid"], **f)
                return f"event edited: {args.get('title') or args['uid'][:8]}"
            if name == "delete_event":
                await d.delete_event(args["uid"])
                return f"event deleted: {args['uid'][:8]}"
            if name == "contacts":
                if args.get("action") == "find":
                    found = await d.find_contacts(args.get("query", ""))
                    return json.dumps({"count": len(found), "contacts": found})
                if args.get("action") == "set_birthday":
                    if not args.get("href"):
                        return "error: href required (use find first)"
                    c = await d.set_contact_birthday(args["href"], args.get("birthday") or None)
                    return f"birthday of {c['name']} set to {c['birthday'] or 'none'}"
                return "unknown contacts action"
            if name == "birthdays":
                days = int(args.get("days") or d.cfg.daily.birthdays_lookahead)
                if args.get("action") == "print":
                    return json.dumps(await d.print_birthdays(days))
                bd = await d.birthdays_for(days)
                return json.dumps(
                    {
                        "count": len(bd),
                        "birthdays": [
                            f"{b.name} {b.day.isoformat()}" + (f" turns {b.age}" if b.age else "")
                            for b in bd
                        ],
                    }
                )
            if name == "lists":
                act = args.get("action", "show")
                title = (args.get("title") or "").strip()
                items = [str(x) for x in (args.get("items") or [])]
                if act == "show":
                    ls = d.db.custom_lists()
                    return json.dumps(
                        [
                            {
                                "title": x["title"],
                                "items": x["items"],
                                "printed": bool(x["printed_at"]),
                            }
                            for x in ls
                        ]
                    )
                if not title:
                    return "error: title required"
                existing = d.db.find_custom_list(title)
                if act == "save":
                    lid = d.db.save_custom_list(title, items, existing["id"] if existing else None)
                    return f"saved list '{title}' with {len(items)} items (id {lid})"
                if act == "add":
                    cur = existing["items"] if existing else []
                    lid = d.db.save_custom_list(
                        existing["title"] if existing else title,
                        cur + items,
                        existing["id"] if existing else None,
                    )
                    return f"added {len(items)} to '{existing['title'] if existing else title}'"
                if existing is None:
                    return f"error: no list named '{title}'"
                if act == "remove":
                    low = [x.lower() for x in items]
                    keep = [x for x in existing["items"] if x.lower() not in low]
                    d.db.save_custom_list(existing["title"], keep, existing["id"])
                    return (
                        f"removed {len(existing['items']) - len(keep)} from '{existing['title']}'"
                    )
                if act == "print":
                    return json.dumps(await d.print_custom_list(existing["id"]))
                if act == "delete":
                    d.db.delete_custom_list(existing["id"])
                    return f"deleted list '{existing['title']}'"
                return f"unknown list action {act}"
            if name == "print_ticket":
                return json.dumps(await d.print_ticket(dict(args)))
            if name == "get_settings":
                return json.dumps(d.settings_dict())
            if name == "set_setting":
                return d.apply_setting(args["section"], args["key"], args["value"])
            if name == "print_receipt":
                if args.get("day") and args["day"] != date.today().isoformat():
                    return json.dumps(
                        await d.print_day(
                            date.fromisoformat(args["day"]), args.get("layout") or None
                        )
                    )
                r = await d.maybe_print_daily(
                    force=bool(args.get("force", True)),
                    reason="ai",
                    layout_mode=args.get("layout") or None,
                )
                return json.dumps(r)
            if name == "print_calendar":
                d0 = date.fromisoformat(args["day_from"])
                d1 = date.fromisoformat(args["day_to"]) if args.get("day_to") else None
                return json.dumps(await d.print_calendar(d0, d1))
            if name in ("print_tasks", "preview_tasks"):
                f = {k: v for k, v in args.items() if k != "title" and v not in (None, "", [])}
                for k in ("due_from", "due_to"):
                    if k in f:
                        f[k] = date.fromisoformat(f[k])
                if name == "preview_tasks":
                    items = d.select_tasks(**f)
                    rows = [f"{t.uid} | {t.title} | {t.list_name}" for t in items][:40]
                    note = f"{len(items)} tasks match this filter"
                    if len(items) > len(rows):
                        note += f"; only the first {len(rows)} are listed here"
                    return json.dumps({"count": len(items), "note": note, "tasks": rows})
                try:
                    return json.dumps(await d.print_selection(args.get("title") or "Tasks", **f))
                except ValueError as exc:
                    return f"error: {exc}"
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
        if (msg := self._no_data_message()) is not None:
            return {"reply": msg, "actions": []}
        messages: list[dict[str, Any]] = [{"role": "system", "content": await self._system(agenda)}]
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
                log.info(
                    "ai tool %s %s", fn.get("name"), json.dumps(args, ensure_ascii=False)[:400]
                )
                result = await self._run_tool(fn.get("name", ""), args)
                log.info("ai tool result: %s", result[:200])
                if fn.get("name") not in ("get_settings", "preview_tasks"):
                    actions.append(result)
                messages.append(
                    {"role": "tool", "content": result, "tool_name": fn.get("name", "")}
                )
            # refresh context so follow-up calls see the new state
            await self.daemon.agenda(refresh=True)
        return {"reply": "Done: " + "; ".join(actions), "actions": actions}
