"""Open the terminal window that shows today's agenda (alacritty running `show`)."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time

log = logging.getLogger(__name__)


def session_env() -> dict[str, str]:
    """The current systemd user environment (DISPLAY/WAYLAND_DISPLAY appear only after the
    desktop session is up, which can be after this daemon started at login)."""
    env = dict(os.environ)
    try:
        out = subprocess.run(
            ["systemctl", "--user", "show-environment"], capture_output=True, text=True, timeout=5
        ).stdout
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                env[k] = v
    except Exception as exc:  # noqa: BLE001
        log.debug("show-environment failed: %s", exc)
    return env


def _display_ready(env: dict[str, str]) -> bool:
    runtime = env.get("XDG_RUNTIME_DIR", "")
    wl = env.get("WAYLAND_DISPLAY", "")
    if wl and runtime and os.path.exists(os.path.join(runtime, wl)):
        return True
    disp = env.get("DISPLAY", "")
    return bool(disp) and os.path.exists("/tmp/.X11-unix/X" + disp.lstrip(":").split(".")[0])


def open_terminal(wait_seconds: int = 90) -> str:
    """Open the task view in a terminal. Waits for the graphical session if it is still
    coming up (daily check right after login)."""
    term = shutil.which("alacritty")
    if not term:
        return "alacritty not found"
    deadline = time.monotonic() + wait_seconds
    env = session_env()
    while not _display_ready(env):
        if time.monotonic() > deadline:
            log.warning("no display available after %ss, not opening the window", wait_seconds)
            return "terminal failed: no display"
        time.sleep(2)
        env = session_env()
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
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("could not open terminal: %s", exc)
        return f"terminal failed: {exc}"
    time.sleep(1.5)
    if proc.poll() is not None and proc.returncode != 0:
        err = (proc.stderr.read().decode(errors="replace") if proc.stderr else "").strip()[-200:]
        log.warning("terminal exited immediately (%s): %s", proc.returncode, err)
        return f"terminal failed: {err or proc.returncode}"
    log.info("terminal window opened")
    return "terminal opened"
