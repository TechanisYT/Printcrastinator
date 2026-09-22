"""Render a Receipt to a 1-bit PIL image, 384 px wide, no antialiasing, no dithering."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from importlib import resources

from PIL import Image, ImageDraw, ImageFont

from ..receipt.model import (
    Block,
    CheckItem,
    EventLine,
    Picture,
    Receipt,
    Rule,
    SectionHeader,
    Size,
    Spacer,
    TearLine,
    Text,
)

WIDTH = 384
MARGIN = 16
CHECK = 24
CHECK_STROKE = 2
CHECK_GAP = 6
TEXT_X = MARGIN + CHECK + CHECK_GAP  # 46
LINE_FACTOR = 1.3
ITEM_GAP = 6
THRESHOLD = 128

SIZES: dict[Size, int] = {"headline": 44, "section": 28, "body": 24, "small": 20}
FONTS: dict[Size, str] = {
    "headline": "JetBrainsMono-ExtraBold.ttf",
    "section": "JetBrainsMono-ExtraBold.ttf",
    "body": "JetBrainsMono-Bold.ttf",
    "small": "JetBrainsMono-Bold.ttf",
}


@cache
def font(size: Size) -> ImageFont.FreeTypeFont:
    path = resources.files("printcrastinator") / "assets" / "fonts" / FONTS[size]
    with resources.as_file(path) as p:
        return ImageFont.truetype(str(p), SIZES[size])


def _line_height(size: Size) -> int:
    return int(SIZES[size] * LINE_FACTOR)


def text_width(text: str, size: Size) -> int:
    return int(font(size).getlength(text))


def wrap(text: str, size: Size, max_width: int) -> list[str]:
    """Greedy word wrap by measured width; words longer than the line are split hard."""
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        while text_width(w, size) > max_width:
            # hard split an overlong word
            cut = max(1, len(w) * max_width // max(1, text_width(w, size)))
            while cut > 1 and text_width(w[:cut], size) > max_width:
                cut -= 1
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(w[:cut])
            w = w[cut:]
        candidate = f"{cur} {w}".strip()
        if cur and text_width(candidate, size) > max_width:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines or [""]


@dataclass
class _Canvas:
    img: Image.Image
    draw: ImageDraw.ImageDraw
    y: int = 0

    def ensure(self, extra: int) -> None:
        if self.y + extra <= self.img.height:
            return
        new_h = max(self.img.height * 2, self.y + extra + 64)
        new = Image.new("L", (WIDTH, new_h), 255)
        new.paste(self.img, (0, 0))
        self.img = new
        self.draw = ImageDraw.Draw(new)
        self.draw.fontmode = "1"


def _draw_text(
    c: _Canvas,
    x: int,
    text: str,
    size: Size,
    align: str = "left",
    right_limit: int = WIDTH - MARGIN,
) -> None:
    w = text_width(text, size)
    if align == "center":
        x = (WIDTH - w) // 2
    elif align == "right":
        x = right_limit - w
    c.draw.text((x, c.y), text, font=font(size), fill=0)


def _text_block(c: _Canvas, b: Text) -> None:
    lh = _line_height(b.size)
    avail = WIDTH - 2 * MARGIN
    lines = wrap(b.text, b.size, avail) if b.wrap else [b.text]
    for line in lines:
        c.ensure(lh)
        _draw_text(c, MARGIN, line, b.size, b.align)
        c.y += lh


def _section(c: _Canvas, b: SectionHeader) -> None:
    lh = _line_height("section")
    hint_w = text_width(b.hint, "small") + 8 if b.hint else 0
    lines = wrap(b.text, "section", WIDTH - 2 * MARGIN - hint_w)
    c.ensure(lh * len(lines) + 3 + 8)
    for i, line in enumerate(lines):
        _draw_text(c, MARGIN, line, "section")
        if i == 0 and b.hint:
            hy = c.y + (lh - _line_height("small")) // 2
            w = text_width(b.hint, "small")
            c.draw.text((WIDTH - MARGIN - w, hy), b.hint, font=font("small"), fill=0)
        c.y += lh
    c.draw.rectangle((MARGIN, c.y, WIDTH - MARGIN - 1, c.y + 2), fill=0)
    c.y += 3 + 8


def _event(c: _Canvas, b: EventLine) -> None:
    lh = _line_height("body")
    time_w = text_width("00:00–00:00", "small") + 10
    lines = wrap(b.title, "body", WIDTH - MARGIN - (MARGIN + time_w))
    c.ensure(lh * len(lines) + ITEM_GAP)
    ty = c.y + (lh - _line_height("small")) // 2
    c.draw.text((MARGIN, ty), b.time, font=font("small"), fill=0)
    for line in lines:
        c.draw.text((MARGIN + time_w, c.y), line, font=font("body"), fill=0)
        c.y += lh
    c.y += ITEM_GAP


def _check(c: _Canvas, b: CheckItem) -> None:
    lh = _line_height("body")
    right_w = text_width(b.right, "small") + 8 if b.right else 0
    first_avail = WIDTH - MARGIN - TEXT_X - right_w
    rest_avail = WIDTH - MARGIN - TEXT_X
    lines = wrap(b.title, "body", first_avail)
    # re-wrap tail lines with the full width
    if len(lines) > 1:
        lines = [lines[0]] + wrap(" ".join(lines[1:]), "body", rest_avail)
    meta_lines: list[str] = []
    for para in b.meta.split("\n") if b.meta else []:
        meta_lines.extend(wrap(para, "small", rest_avail))
    total = lh * len(lines) + _line_height("small") * len(meta_lines) + ITEM_GAP
    c.ensure(total)
    # checkbox vertically centred on the first line
    by = c.y + (lh - CHECK) // 2
    c.draw.rectangle(
        (MARGIN, by, MARGIN + CHECK - 1, by + CHECK - 1), outline=0, width=CHECK_STROKE
    )
    for i, line in enumerate(lines):
        c.draw.text((TEXT_X, c.y), line, font=font("body"), fill=0)
        if i == 0 and b.right:
            ry = c.y + (lh - _line_height("small")) // 2
            rw = text_width(b.right, "small")
            c.draw.text((WIDTH - MARGIN - rw, ry), b.right, font=font("small"), fill=0)
        c.y += lh
    for line in meta_lines:
        c.draw.text((TEXT_X, c.y), line, font=font("small"), fill=0)
        c.y += _line_height("small")
    c.y += ITEM_GAP


def _picture(c: _Canvas, b: Picture) -> None:
    try:
        src = Image.open(b.path)
    except Exception:
        return
    if src.mode in ("RGBA", "LA", "P"):
        bg = Image.new("RGBA", src.size, (255, 255, 255, 255))
        bg.alpha_composite(src.convert("RGBA"))
        src = bg
    src = src.convert("L")
    max_w = WIDTH - 2 * MARGIN
    scale = min(max_w / src.width, b.max_height / src.height, 1.0)
    if scale < 1.0:
        src = src.resize(
            (max(1, int(src.width * scale)), max(1, int(src.height * scale))), Image.LANCZOS
        )
    if b.dither:
        mono = src.convert("1")  # Floyd-Steinberg
    else:
        mono = src.point(lambda p: 255 if p > THRESHOLD else 0).convert("1")
    c.ensure(mono.height)
    c.img.paste(mono.convert("L"), ((WIDTH - mono.width) // 2, c.y))
    c.y += mono.height


def _tear(c: _Canvas) -> None:
    c.ensure(12)
    y = c.y + 4
    x = MARGIN
    while x < WIDTH - MARGIN:
        c.draw.rectangle((x, y, min(x + 7, WIDTH - MARGIN - 1), y + 1), fill=0)
        x += 16
    c.y += 12


def _render_block(c: _Canvas, b: Block) -> None:
    match b:
        case Rule(thickness=t):
            c.ensure(t)
            c.draw.rectangle((MARGIN, c.y, WIDTH - MARGIN - 1, c.y + t - 1), fill=0)
            c.y += t
        case Spacer(height=h):
            c.ensure(h)
            c.y += h
        case Text():
            _text_block(c, b)
        case SectionHeader():
            _section(c, b)
        case EventLine():
            _event(c, b)
        case CheckItem():
            _check(c, b)
        case TearLine():
            _tear(c)
        case Picture():
            _picture(c, b)


def render(receipt: Receipt, top_pad: int = 8, bottom_pad: int = 8) -> Image.Image:
    img = Image.new("L", (WIDTH, 512), 255)
    draw = ImageDraw.Draw(img)
    draw.fontmode = "1"
    c = _Canvas(img, draw, y=top_pad)
    for b in receipt.blocks:
        _render_block(c, b)
    c.y += bottom_pad
    out = c.img.crop((0, 0, WIDTH, c.y))
    return out.point(lambda p: 255 if p > THRESHOLD else 0).convert("1")
