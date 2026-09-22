"""Configuration: XDG paths, dataclass config, TOML load/save."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import tomli_w

APP_NAME = "printcrastinator"


def config_dir() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_NAME


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
    return base / APP_NAME


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
class PrinterConfig:
    device: str = "/dev/usb/lp0"
    width_px: int = 384
    # Raster data is sent in bands of this many dot lines, paced to lines_per_second so the
    # printer's small buffer never overflows (cheap printers reset on USB when flooded).
    band_lines: int = 64
    lines_per_second: int = 200
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


@dataclass
class SlipConfig:
    enabled_tasks: bool = True
    enabled_deck: bool = True
    debounce_seconds: int = 60


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


@dataclass
class Config:
    nextcloud: NextcloudConfig = field(default_factory=NextcloudConfig)
    printer: PrinterConfig = field(default_factory=PrinterConfig)
    daily: DailyConfig = field(default_factory=DailyConfig)
    slips: SlipConfig = field(default_factory=SlipConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    screen: ScreenConfig = field(default_factory=ScreenConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def to_dict(self) -> dict[str, Any]:
        return _asdict(self)

    @property
    def api_base(self) -> str:
        return f"http://{self.server.host}:{self.server.port}"


def _asdict(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _asdict(getattr(obj, f.name)) for f in fields(obj)}
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
        elif isinstance(current, bool):
            kwargs[f.name] = bool(value)
        elif isinstance(current, int) and not isinstance(value, bool):
            kwargs[f.name] = int(value)
        else:
            kwargs[f.name] = value
    return cls(**kwargs)


def load_config(path: Path | None = None) -> Config:
    path = path or config_path()
    if not path.exists():
        return Config()
    with path.open("rb") as fh:
        raw = tomllib.load(fh)
    return _from_dict(Config, raw)


def save_config(cfg: Config, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        tomli_w.dump(cfg.to_dict(), fh)
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    return path
