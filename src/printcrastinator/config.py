"""Configuration: XDG paths, dataclass config, TOML load/save."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import tomli_w

log = logging.getLogger(__name__)

APP_NAME = "printcrastinator"


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / APP_NAME


def data_dir() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME


def logo_dir() -> Path:
    return data_dir() / "logos"


def config_path() -> Path:
    return Path(os.environ.get("PRINTCRASTINATOR_CONFIG", config_dir() / "config.toml"))


def db_path() -> Path:
    return Path(os.environ.get("PRINTCRASTINATOR_DB", state_dir() / "state.sqlite3"))


@dataclass
class NextcloudConfig:
    url: str = ""
    username: str = ""
    app_password: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.url and self.username and self.app_password)

    @property
    def base_url(self) -> str:
        return self.url.rstrip("/")


@dataclass
class ExtraCalendar:
    """An additional calendar outside Nextcloud: a CalDAV account/collection or an ICS feed."""

    name: str = ""
    kind: str = "ics"  # "ics" (public/webcal link) or "caldav" (account or collection URL)
    url: str = ""
    username: str = ""
    password: str = ""

    @property
    def id(self) -> str:
        return "extra:" + "".join(ch if ch.isalnum() else "-" for ch in self.name.lower())


@dataclass
class PrinterConfig:
    device: str = "/dev/usb/lp0"
    width_px: int = 384
    # Raster data is sent in bands of this many dot lines, paced to lines_per_second so the
    # printer's small buffer never overflows (cheap printers reset on USB when flooded).
    # The Anjet 58 crashes its USB link when raster data arrives faster than it prints
    # (~270 lines/s), and its flow control cannot be trusted. Small bands sent at just under
    # the head speed keep it fed continuously without ever overrunning its buffer.
    # Measured on the Anjet 58: 128-line bands released at ~550 lines/s fill the printer's
    # buffer, after which the USB write blocks at the head's own speed (~270 lines/s) and the
    # paper moves continuously. Smaller bands or lower rates make the motor stop between
    # blocks; flooding without any pacing crashes the printer's USB link.
    band_lines: int = 128
    lines_per_second: int = 550
    # Extra lines sent before the pacing starts. Keep at 0: a burst of two bands at the
    # start crashed the printer's USB link once.
    lead_lines: int = 0
    # Paper feed after the last printed line so the tear line reaches the tear bar.
    feed_after_mm: int = 10
    # Heating density chosen with test-print. None = printer default.
    # "esc7:<n1>,<n2>,<n3>" or "gse:<level>" (see printer/escpos_out.py).
    density: str = ""
    # Only used by the plain-text fallback / calibration path.
    fallback_codepage: str = "CP858"


@dataclass
class DailyConfig:
    earliest_hour: int = 6
    print_calendar_only_days: bool = False
    # "list": one continuous checklist. "cards": every task in its own block separated by
    # cut lines, for cutting up with scissors.
    layout: str = "list"
    # Overdue filters, 0 = unlimited. Both can be combined.
    overdue_max_days: int = 0
    overdue_max_count: int = 0
    # Rotating motivational quote (edit the list in Settings). "top" or "bottom".
    quote: bool = True
    quote_position: str = "top"
    # Print task notes/description and tags under the title.
    show_notes: bool = True
    notes_max_lines: int = 3
    # Birthdays from Nextcloud's auto-generated contact birthday calendar.
    show_birthdays: bool = True
    birthdays_calendar: str = "contact_birthdays"
    birthdays_lookahead: int = 14
    # Group tasks by task list / Deck stack instead of overdue / due today sections.
    group_by_list: bool = True
    # In the flat layout, show the list or board name next to each task.
    show_list: bool = True


@dataclass
class SlipConfig:
    enabled_tasks: bool = True
    enabled_deck: bool = True
    debounce_seconds: int = 60


@dataclass
class LogoConfig:
    # "off", "random" (one of the uploaded images per print) or "fixed" (logo.file)
    mode: str = "off"
    file: str = ""
    max_height: int = 160
    # Floyd-Steinberg dithering for photos/greyscale logos; off = plain threshold
    dither: bool = False


@dataclass
class AiConfig:
    enabled: bool = True
    url: str = "http://localhost:11434"
    model: str = "gemma4:12B"
    num_ctx: int = 16384
    max_tokens: int = 600
    # Let reasoning models think before answering (slow: thousands of hidden tokens).
    think: bool = False
    timeout: int = 180
    # Show an AI morning briefing when the terminal view opens.
    summary_on_open: bool = True
    # Reply language: "en", "de", or "auto" (follow the user's message).
    language: str = "en"


@dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8555
    poll_interval: int = 180


@dataclass
class ScreenConfig:
    notify: bool = True
    terminal: bool = True


@dataclass
class UiConfig:
    language: str = "en"
    dark: bool = True
    # Terminal view: put the cursor into the AI input on start; enable the single-key
    # shortcuts (r refresh, p print, t print tomorrow, a focus AI). q always quits.
    tui_focus_ai: bool = True
    tui_shortcuts: bool = False


@dataclass
class Config:
    nextcloud: NextcloudConfig = field(default_factory=NextcloudConfig)
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    slips: SlipConfig = field(default_factory=SlipConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    ui: UiConfig = field(default_factory=UiConfig)
    logo: LogoConfig = field(default_factory=LogoConfig)
    ai: AiConfig = field(default_factory=AiConfig)
    extra_calendars: list[ExtraCalendar] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _asdict(self)

    @property
    def api_base(self) -> str:
        return f"http://{self.server.host}:{self.server.port}"


def _asdict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _asdict(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, list):
        return [_asdict(x) for x in obj]
    return obj


def _from_dict(cls: type, data: dict[str, Any]) -> Any:
    kwargs: dict[str, Any] = {}
    defaults = cls()
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        current = getattr(defaults, f.name)
        if is_dataclass(current) and isinstance(value, dict):
            kwargs[f.name] = _from_dict(type(current), value)
        elif f.name == "extra_calendars" and isinstance(value, list):
            kwargs[f.name] = [_from_dict(ExtraCalendar, v) for v in value if isinstance(v, dict)]
        elif isinstance(current, bool):
            kwargs[f.name] = bool(value)
        elif isinstance(current, int) and not isinstance(value, bool):
            kwargs[f.name] = int(value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def _secret_slots(cfg: Config) -> list[tuple[Any, str, str]]:
    """(object, attribute, secret key) for every password field."""
    slots = [(cfg.nextcloud, "app_password", "nextcloud")]
    for ec in cfg.extra_calendars:
        slots.append((ec, "password", ec.id))
    return slots


def load_config(path: Path | None = None) -> Config:
    """Load the config and resolve secret markers. Plaintext passwords found in the file are
    moved into the secret store and the file is rewritten without them."""
    from . import secrets

    path = path or config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    cfg = _from_dict(Config, raw)
    migrate = False
    for obj, attr, _key in _secret_slots(cfg):
        value = getattr(obj, attr)
        if not value:
            continue
        if secrets.is_marker(value):
            try:
                setattr(obj, attr, secrets.load(value))
            except Exception as exc:
                log.error("cannot read secret %s: %s", value, exc)
                setattr(obj, attr, "")
        else:
            migrate = True
    if migrate:
        log.info("moving plaintext passwords from %s into the secret store", path)
        save_config(cfg, path)
    return cfg


def save_config(cfg: Config, path: Path | None = None) -> Path:
    """Write the config; passwords go to the secret store and only markers hit the disk."""
    from . import secrets

    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = cfg.to_dict()
    # replace secrets in the serialisable dict, never in the live config object
    if cfg.nextcloud.app_password:
        data["nextcloud"]["app_password"] = secrets.store("nextcloud", cfg.nextcloud.app_password)
    for ec, entry in zip(cfg.extra_calendars, data["extra_calendars"], strict=True):
        if ec.password:
            entry["password"] = secrets.store(ec.id, ec.password)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        tomli_w.dump(data, fh)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path
