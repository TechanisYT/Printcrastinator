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
    RawImage,
    Receipt,
    Rule,
    SectionHeader,
    Size,
    Spacer,
    SubHeader,
    TearLine,
    Text,
    Timeline,
    TimelineEvent,
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

SIZES: dict[Size, int] = {
    "headline": 44,
    "section": 28,
    "subheader": 24,
    "body": 24,
    "small": 20,
    "tiny": 16,
}
# Weights: headers heavy, list subheaders bold, task titles medium, notes/meta regular.
FONTS: dict[Size, str] = {
    "headline": "JetBrainsMono-ExtraBold.ttf",
    "section": "JetBrainsMono-ExtraBold.ttf",
    "subheader": "JetBrainsMono-Bold.ttf",
    "body": "JetBrainsMono-Medium.ttf",
    "small": "JetBrainsMono-Regular.ttf",
    "tiny": "JetBrainsMono-Regular.ttf",
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


def _subheader(c: _Canvas, b: SubHeader) -> None:
    """List/board label inside a section: bold body text with a 2 px rule underneath."""
    lh = _line_height("subheader")
    lines = wrap(b.text, "subheader", WIDTH - 2 * MARGIN)
    c.ensure(10 + lh * len(lines) + 2 + 8)
    c.y += 10
    for line in lines:
        _draw_text(c, MARGIN, line, "subheader")
        c.y += lh
    c.draw.rectangle((MARGIN, c.y, WIDTH - MARGIN - 1, c.y + 1), fill=0)
    c.y += 2 + 8


def _event(c: _Canvas, b: EventLine) -> None:
    lh = _line_height("body")
    time_w = text_width("00:00–00:00", "small") + 10
    lines = wrap(b.title, "body", WIDTH - MARGIN - (MARGIN + time_w))
    meta_lines = wrap(b.meta, "small", WIDTH - MARGIN - (MARGIN + time_w)) if b.meta else []
    c.ensure(lh * len(lines) + _line_height("small") * len(meta_lines) + ITEM_GAP)
    ty = c.y + (lh - _line_height("small")) // 2
    c.draw.text((MARGIN, ty), b.time, font=font("small"), fill=0)
    for line in lines:
        c.draw.text((MARGIN + time_w, c.y), line, font=font("body"), fill=0)
        c.y += lh
    for line in meta_lines:
        c.draw.text((MARGIN + time_w, c.y), line, font=font("small"), fill=0)
        c.y += _line_height("small")
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


def _assign_columns(events: list[TimelineEvent], max_cols: int):
    """Greedy interval colouring. Returns ([(event, col)], overflow_events)."""
    placed: list[tuple[TimelineEvent, int]] = []
    overflow: list[TimelineEvent] = []
    col_end = [0] * max_cols
    for e in sorted(events, key=lambda x: (x.start_min, x.end_min)):
        for col in range(max_cols):
            if col_end[col] <= e.start_min:
                placed.append((e, col))
                col_end[col] = e.end_min
                break
        else:
            overflow.append(e)
    return placed, overflow


def _hhmm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _timeline(c: _Canvas, b: Timeline) -> None:
    placed, overflow = _assign_columns(list(b.events), b.max_columns)
    if not placed:
        return
    px_per_hour = 64
    lh_small = _line_height("small")
    label_w = text_width("00:00", "small") + 6
    bar_x = MARGIN + label_w
    first = min(e.start_min for e, _ in placed) // 60  # full hour before the first event
    end_min = max(e.end_min for e, _ in placed)  # bar ends exactly with the last event
    height = max(lh_small + 8, int((end_min - first * 60) * px_per_hour / 60))
    ncols = max(col for _, col in placed) + 1
    area_x = bar_x + 6
    gap = 4
    col_w = (WIDTH - MARGIN - area_x - gap * (ncols - 1)) // ncols
    c.ensure(height + lh_small + 16)
    top = c.y + lh_small // 2

    def y_of(minutes: int) -> int:
        return top + int((minutes - first * 60) * px_per_hour / 60)

    c.draw.rectangle((bar_x, top, bar_x + 1, top + height), fill=0)
    ticks = [h * 60 for h in range(first, end_min // 60 + 1) if h * 60 <= end_min - 40]
    ticks.append(end_min)
    for m in ticks:
        y = y_of(m)
        c.draw.rectangle((bar_x - 4, y, bar_x + 1, y), fill=0)
        c.draw.text((MARGIN, y - lh_small // 2), _hhmm(m), font=font("small"), fill=0)

    def overlaps(a: TimelineEvent, b: TimelineEvent) -> bool:
        return a.start_min < b.end_min and b.start_min < a.end_min

    for e, col in placed:
        # widen into columns to the right that hold nothing overlapping this event
        span = 1
        while col + span < ncols and not any(
            c2 == col + span and overlaps(e, o) for o, c2 in placed
        ):
            span += 1
        x0 = area_x + col * (col_w + gap)
        x1 = x0 + span * col_w + (span - 1) * gap - 1
        y0 = y_of(e.start_min)
        y1 = max(y0 + lh_small + 6, y_of(e.end_min) - 2)
        c.draw.rectangle((x0, y0, x1, y1), outline=0, width=2)
        c.draw.rectangle((x0, y0, x0 + 5, y1), fill=0)
        lines = wrap(e.title, "small", x1 - x0 - 14)
        max_lines = max(1, (y1 - y0 - 4) // lh_small)
        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1][:-1] + "…" if len(lines[-1]) > 1 else lines[-1]
        ty = y0 + 3
        for line in lines:
            c.draw.text((x0 + 9, ty), line, font=font("small"), fill=0)
            ty += lh_small
        if e.sub:
            lh_tiny = _line_height("tiny")
            room = (y1 - ty - 2) // lh_tiny
            sub_lines = wrap(e.sub, "tiny", x1 - x0 - 14)
            if len(sub_lines) > room > 0:
                sub_lines = sub_lines[:room]
                sub_lines[-1] = sub_lines[-1].rstrip(", ") + "…"
            for line in sub_lines[: max(0, room)]:
                c.draw.text((x0 + 9, ty), line, font=font("tiny"), fill=0)
                ty += lh_tiny
    c.y = top + height + lh_small // 2 + 8
    for e in overflow:
        _event(c, EventLine(f"{_hhmm(e.start_min)}–{_hhmm(e.end_min)}", e.title, meta=e.sub))


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
        case SubHeader():
            _subheader(c, b)
        case EventLine():
            _event(c, b)
        case CheckItem():
            _check(c, b)
        case TearLine():
            _tear(c)
        case Picture():
            _picture(c, b)
        case Timeline():
            _timeline(c, b)
        case RawImage(image=im):
            c.ensure(im.height)
            c.img.paste(im.convert("L"), ((WIDTH - im.width) // 2, c.y))
            c.y += im.height


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


# ---- multi-day calendar, drawn landscape and rotated onto the 384 px paper -------------------


def multi_day_calendar(days: list[tuple[str, list[TimelineEvent], list[str]]]) -> Image.Image:
    """days: [(label, timed events, all-day titles)]. Returns a 384 px wide 1-bit image whose
    content is rotated 90°, days side by side along the paper."""
    from PIL import ImageOps

    H = WIDTH  # landscape height = paper width
    header_h = _line_height("subheader") + 6
    lh_tiny = _line_height("tiny")
    allday_rows = min(2, max((len(a) for _, _, a in days), default=0))
    allday_h = allday_rows * lh_tiny + (4 if allday_rows else 0)
    axis_w = text_width("00:00", "tiny") + 8
    day_w = 230
    gap = 6
    W = MARGIN + axis_w + len(days) * (day_w + gap) + MARGIN
    timed_all = [e for _, evs, _ in days for e in evs]
    first = min((e.start_min // 60 for e in timed_all), default=8)
    end_min = max((e.end_min for e in timed_all), default=18 * 60)
    span_min = max(60, end_min - first * 60)
    top = 8 + header_h + allday_h
    grid_h = H - top - 10
    px_per_hour = grid_h / (span_min / 60)

    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    d.fontmode = "1"

    def y_of(minutes: int) -> int:
        return int(top + (minutes - first * 60) * px_per_hour / 60)

    axis_x = MARGIN + axis_w
    # hour grid lines + labels
    ticks = [h * 60 for h in range(first, end_min // 60 + 1) if h * 60 <= end_min - 40]
    ticks.append(end_min)
    for m in ticks:
        y = y_of(m)
        d.line((axis_x, y, W - MARGIN, y), fill=0 if m in (first * 60, end_min) else 160)
        d.text((MARGIN, y - lh_tiny // 2), _hhmm(m), font=font("tiny"), fill=0)
    for i, (label, evs, allday) in enumerate(days):
        x0 = axis_x + i * (day_w + gap)
        x1 = x0 + day_w
        d.text((x0 + 4, 8 + 2), label, font=font("subheader"), fill=0)
        d.rectangle((x0, 8 + header_h - 3, x1 - 1, 8 + header_h - 1), fill=0)
        ay = 8 + header_h + 2
        for title in allday[:allday_rows]:
            line = wrap(title, "tiny", day_w - 8)[0]
            d.text((x0 + 4, ay), "▪ " + line if len(line) < 30 else line, font=font("tiny"), fill=0)
            ay += lh_tiny
        d.line((x0, top, x0, H - 10), fill=0, width=1)
        placed, overflow = _assign_columns(list(evs) + [], 3)
        # overflow events still get drawn, squeezed into the last column
        placed += [(e, 2) for e in overflow]
        ncols = max((c for _, c in placed), default=0) + 1
        col_w = (day_w - 4 - 3 * (ncols - 1)) // max(1, ncols)

        def _ov(a: TimelineEvent, b: TimelineEvent) -> bool:
            return a.start_min < b.end_min and b.start_min < a.end_min

        for e, col in placed:
            span = 1
            while col + span < ncols and not any(
                c2 == col + span and _ov(e, o) for o, c2 in placed
            ):
                span += 1
            ex0 = x0 + 2 + col * (col_w + 3)
            ex1 = ex0 + span * col_w + (span - 1) * 3 - 1
            ey0 = y_of(e.start_min)
            ey1 = max(ey0 + lh_tiny + 4, y_of(e.end_min) - 1)
            d.rectangle((ex0, ey0, ex1, ey1), fill=255, outline=0, width=2)
            d.rectangle((ex0, ey0, ex0 + 4, ey1), fill=0)
            lines = wrap(e.title, "tiny", ex1 - ex0 - 12)
            max_lines = max(1, (ey1 - ey0 - 2) // lh_tiny)
            if len(lines) > max_lines:
                lines = lines[:max_lines]
                if len(lines[-1]) > 1:
                    lines[-1] = lines[-1][:-1] + "…"
            ty = ey0 + 2
            for line in lines:
                d.text((ex0 + 8, ty), line, font=font("tiny"), fill=0)
                ty += lh_tiny
            if e.sub and len(lines) < max_lines:
                for line in wrap(e.sub, "tiny", ex1 - ex0 - 12)[: max_lines - len(lines)]:
                    d.text((ex0 + 8, ty), line, font=font("tiny"), fill=0)
                    ty += lh_tiny
    d.line(
        (
            axis_x + len(days) * (day_w + gap) - gap,
            top,
            axis_x + len(days) * (day_w + gap) - gap,
            H - 10,
        ),
        fill=0,
    )
    mono = img.point(lambda p: 255 if p > THRESHOLD else 0).convert("1")
    rotated = mono.rotate(-90, expand=True)  # paper start = left edge of the table
    return (
        ImageOps.pad(rotated, (WIDTH, rotated.height), color=255)
        if rotated.width < WIDTH
        else rotated
    )
