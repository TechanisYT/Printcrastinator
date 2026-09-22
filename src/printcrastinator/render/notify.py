"""Desktop notification via notify-send (libnotify)."""

from __future__ import annotations

import shutil
import subprocess

from ..models import DailyAgenda
from ..receipt import i18n


def summary_lines(agenda: DailyAgenda, lang: str = "en") -> tuple[str, str]:
    if not agenda.has_tasks:
        title = i18n.label(lang, "no_tasks")
    else:
        parts = []
        if agenda.overdue:
            parts.append(f"{len(agenda.overdue)} {i18n.label(lang, 'summary_overdue')}")
        if agenda.due_today:
            parts.append(f"{len(agenda.due_today)} {i18n.label(lang, 'summary_today')}")
        if agenda.always_items:
            parts.append(f"{len(agenda.always_items)} {i18n.label(lang, 'summary_always')}")
        title = " · ".join(parts)
    body = []
    for ev in agenda.events[:4]:
        t = i18n.label(lang, "all_day") if ev.all_day else ev.start.strftime("%H:%M")
        body.append(f"{t}  {ev.title}")
    for t in agenda.all_tasks[:6]:
        body.append(f"☐ {t.title}")
    rest = len(agenda.all_tasks) - 6
    if rest > 0:
        body.append(f"… +{rest}")
    return title, "\n".join(body)


def send(agenda: DailyAgenda, lang: str = "en") -> bool:
    exe = shutil.which("notify-send")
    if not exe:
        return False
    title, body = summary_lines(agenda, lang)
    try:
        subprocess.run(
            [
                exe,
                "--app-name=Printcrastinator",
                "--icon=task-due",
                f"Printcrastinator: {title}",
                body,
            ],
            check=False,
            timeout=10,
        )
        return True
    except Exception:
        return False
