"""Open the terminal window that shows today's agenda (alacritty running `show`)."""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys

log = logging.getLogger(__name__)


def open_terminal() -> str:
    term = shutil.which("alacritty")
    if not term:
        return "alacritty not found"
    cmd = [
        term,
        "--title",
        "Printcrastinator",
        "-e",
        sys.executable,
        "-m",
        "printcrastinator",
        "show",
    ]
    try:
        subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        log.warning("could not open terminal: %s", exc)
        return f"terminal failed: {exc}"
    return "terminal opened"
