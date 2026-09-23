"""Printer output. The daemon is the only process that should open the device.

Raster path: Receipt -> PIL image -> ESC/POS raster bands. Text path: calibration and fallback.
"""

from __future__ import annotations

import errno
import logging
import os
import threading
import time

from escpos.printer import Dummy, File
from PIL import Image

from ..config import PrinterConfig
from ..receipt import layout
from ..render import image as render_image

log = logging.getLogger(__name__)

ESC = b"\x1b"
GS = b"\x1d"
DC2 = b"\x12"
DOTS_PER_MM = 8

# Density presets tried in test-print. Key = config value for printer.density.
DENSITY_PRESETS: dict[str, bytes] = {
    "": b"",
    "esc7:7,80,2": ESC + b"7" + bytes([7, 80, 2]),
    "esc7:9,100,2": ESC + b"7" + bytes([9, 100, 2]),
    "esc7:11,120,4": ESC + b"7" + bytes([11, 120, 4]),
    "esc7:15,150,4": ESC + b"7" + bytes([15, 150, 4]),
    "dc2:8": DC2 + b"#" + bytes([8]),
    "dc2:12": DC2 + b"#" + bytes([12]),
    "dc2:15": DC2 + b"#" + bytes([15]),
}


def density_bytes(value: str) -> bytes:
    """Parse 'esc7:n1,n2,n3' or 'dc2:n' or a preset key into raw bytes."""
    if not value:
        return b""
    if value in DENSITY_PRESETS:
        return DENSITY_PRESETS[value]
    kind, _, rest = value.partition(":")
    nums = [int(x) for x in rest.split(",") if x.strip()]
    if kind == "esc7" and len(nums) == 3:
        return ESC + b"7" + bytes(nums)
    if kind == "dc2" and len(nums) == 1:
        return DC2 + b"#" + bytes(nums)
    raise ValueError(f"unknown density setting: {value!r}")


class PrinterError(RuntimeError):
    pass


class Printer:
    """One printer job at a time; the device is always closed, even on failure."""

    _lock = threading.Lock()

    def __init__(self, cfg: PrinterConfig) -> None:
        self.cfg = cfg

    # ---- low level ------------------------------------------------------------------

    def _open(self) -> File:
        if not os.path.exists(self.cfg.device):
            raise PrinterError(f"printer device {self.cfg.device} not found")
        if not os.access(self.cfg.device, os.W_OK):
            raise PrinterError(f"no write access to {self.cfg.device} (udev rule / lp group?)")
        last: Exception | None = None
        for _ in range(5):
            try:
                p = File(self.cfg.device, auto_flush=False)
                break
            except Exception as exc:  # pragma: no cover - hardware
                last = exc
                if getattr(getattr(exc, "__cause__", None), "errno", None) == errno.EBUSY or (
                    "busy" in str(exc).lower()
                ):
                    time.sleep(1.0)
                    continue
                raise PrinterError(f"cannot open {self.cfg.device}: {exc}") from exc
        else:
            raise PrinterError(f"{self.cfg.device} stays busy: {last}") from last
        p._raw(ESC + b"@")  # initialise
        d = density_bytes(self.cfg.density)
        if d:
            p._raw(d)
        return p

    @staticmethod
    def _close_quietly(p: File) -> None:
        try:
            p.close()
        except Exception as exc:  # device may have vanished mid-job
            log.warning("closing printer failed: %s", exc)

    def _feed_mm(self, p: File, mm: int) -> None:
        """Feed with plain line feeds (ESC J is ignored by some cheap printers).

        Default line spacing is 1/6 inch, about 4.2 mm per line.
        """
        lines = -(-mm // 4)
        if lines > 0:
            p._raw(b"\n" * lines)

    def _send_image(self, p: File, img: Image.Image) -> None:
        """Send the image in bands, keeping the printer's buffer a fixed lead ahead of the
        head. The first `lead_lines` go out immediately so the motor never waits for data;
        after that bands are released at `lines_per_second` (just under the head speed), so
        the buffer never grows beyond the lead and the printer is never overrun."""
        if img.width != self.cfg.width_px:
            img = img.resize((self.cfg.width_px, int(img.height * self.cfg.width_px / img.width)))
        band = max(8, self.cfg.band_lines)
        rate = max(1, self.cfg.lines_per_second)
        lead = max(0, self.cfg.lead_lines)
        t0 = time.monotonic()
        sent = 0
        for y in range(0, img.height, band):
            allowed = lead + (time.monotonic() - t0) * rate
            if sent + band > allowed:
                time.sleep((sent + band - allowed) / rate)
            part = img.crop((0, y, img.width, min(y + band, img.height)))
            p.image(part, impl="bitImageRaster", fragment_height=band, center=False)
            p.flush()
            sent += part.height

    def _finish(self, p: File) -> None:
        self._feed_mm(p, self.cfg.feed_after_mm)
        p.flush()

    # ---- public --------------------------------------------------------------------------

    def _job(self, fn) -> None:
        """Run fn(p) with the device open, serialised, always closed."""
        with self._lock:
            p = self._open()
            try:
                fn(p)
            except PrinterError:
                raise
            except Exception as exc:
                raise PrinterError(str(exc)) from exc
            finally:
                self._close_quietly(p)

    def print_image(self, img: Image.Image) -> None:
        """Raise PrinterError on failure. Success = open and write did not raise."""

        def go(p: File) -> None:
            # The tear-off feed is appended as blank raster rows, so it is part of the same
            # continuous band stream instead of a separate line-feed motion at the end.
            pad = self.cfg.feed_after_mm * DOTS_PER_MM
            if pad > 0:
                padded = Image.new("1", (img.width, img.height + pad), 1)
                padded.paste(img, (0, 0))
                self._send_image(p, padded)
            else:
                self._send_image(p, img)
            p.flush()

        self._job(go)

    def feed(self, mm: int | None = None) -> None:
        def go(p: File) -> None:
            self._feed_mm(p, mm if mm is not None else self.cfg.feed_after_mm)
            p.flush()

        self._job(go)

    def print_text_fallback(self, lines: list[str]) -> None:
        """Plain text path with explicit code page. Used only when rendering fails."""

        def go(p: File) -> None:
            p.charcode(self.cfg.fallback_codepage)
            for line in lines:
                p.text(line + "\n")
            self._finish(p)

        self._job(go)

    def print_calibration(self, sweep: bool = False) -> None:
        """Calibration receipt: text ruler + umlauts and one image block.

        With sweep=True, one extra block per density preset follows.
        """

        def go(p: File) -> None:
            p.charcode(self.cfg.fallback_codepage)
            p.set(align="left", bold=True)
            p.text("PRINTCRASTINATOR CALIBRATION\n")
            p.set(bold=False)
            p.text("text mode, " + self.cfg.fallback_codepage + "\n")
            p.text("12345678901234567890123456789012\n")
            p.text("Zähne Über Straße € ß\n\n")
            p.flush()
            label = f"image mode, density {self.cfg.density or 'default'}"
            self._send_image(p, calibration_image(label))
            if sweep:
                for key in list(DENSITY_PRESETS)[1:]:
                    p._raw(DENSITY_PRESETS[key])
                    self._send_image(p, calibration_image(f"density {key}"))
            self._finish(p)

        self._job(go)


def calibration_image(label: str) -> Image.Image:
    from ..receipt.model import CheckItem, Receipt, Rule, Spacer, Text

    r = Receipt()
    r.add(
        Rule(3),
        Text(label, "small"),
        Text("Zähne Über Straße € ß", "body"),
        Text("HEADLINE 44", "headline", "center", wrap=False),
        CheckItem("Checkbox 24 px, 2 px stroke", right="-3d"),
        Rule(1),
        Spacer(4),
    )
    return render_image.render(r, top_pad=4, bottom_pad=4)


def dummy_bytes(img: Image.Image, cfg: PrinterConfig) -> bytes:
    """Bytes that would be sent, for tests."""
    d = Dummy()
    d.image(img, impl="bitImageRaster", fragment_height=cfg.band_lines, center=False)
    return d.output


def daily_image(agenda, lang: str = "en") -> Image.Image:  # noqa: ANN001
    return render_image.render(layout.daily_receipt(agenda, lang))
