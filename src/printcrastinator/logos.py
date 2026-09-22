"""Uploaded logo images: stored in the data dir, picked per print."""

from __future__ import annotations

import random
from pathlib import Path

from .config import LogoConfig, logo_dir
from .receipt.layout import Options

ALLOWED = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


def list_logos() -> list[Path]:
    d = logo_dir()
    if not d.exists():
        return []
    return sorted(p for p in d.iterdir() if p.suffix.lower() in ALLOWED and p.is_file())


def save_logo(name: str, data: bytes) -> Path:
    safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._-") or "logo.png"
    if Path(safe).suffix.lower() not in ALLOWED:
        raise ValueError(f"unsupported image type: {safe}")
    d = logo_dir()
    d.mkdir(parents=True, exist_ok=True)
    target = d / safe
    target.write_bytes(data)
    return target


def delete_logo(name: str) -> None:
    p = logo_dir() / Path(name).name
    if p.exists():
        p.unlink()


def pick_logo(cfg: LogoConfig) -> str:
    """Path of the logo to use for this print, or empty string."""
    logos = list_logos()
    if cfg.mode == "off" or not logos:
        return ""
    if cfg.mode == "fixed":
        for p in logos:
            if p.name == cfg.file:
                return str(p)
        return ""
    return str(random.choice(logos))


def options(cfg_logo: LogoConfig, quote: bool) -> Options:
    return Options(
        logo=pick_logo(cfg_logo),
        logo_max_height=cfg_logo.max_height,
        logo_dither=cfg_logo.dither,
        quote=quote,
    )
