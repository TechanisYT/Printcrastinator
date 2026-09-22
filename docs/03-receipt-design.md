# 03 Receipt design

## Physical constraints

- Qian Anjet 58 mini: 58 mm paper, 48 mm printable, **384 dots wide**, 203 dpi (8 dots/mm).
- No cutter. After the last line the paper is fed so the tear line reaches the tear bar
  (`printer.feed_after_mm`, config value, default measured on the device).
- Thermal heads dislike large solid black areas (banding, slow, faded). No inverse blocks;
  section headers use the heavy font plus a 3 px rule.

## Rendering

- The receipt is a **raster image**: 384 px wide, arbitrary height, mode `1`.
- Drawn with Pillow at 1:1 (one image pixel = one printer dot). Body text is drawn with
  antialiasing off and converted with a fixed threshold, no dithering, so strokes stay crisp.
- Font: bundled **JetBrains Mono ExtraBold** (OFL, `assets/fonts/`). Sizes: headline ~44 px,
  section headers ~28 px, body ~24 px, small meta ~20 px. Umlauts and € come from the font;
  no code pages involved.
- The image is sent to the printer in bands of about 150 lines with python-escpos `image()`.

## Layout (top to bottom)

```
┌────────────────────────────────┐ 384 px
│ ════════════════════════════   │ 3 px rule
│        MONTAG                  │ headline 44 px, centred
│      22. SEP 2026              │ headline 44 px, centred
│ ════════════════════════════   │ 3 px rule
│ HEUTE / TODAY                  │ section header 28 px + 3 px rule
│ 09:00–10:30  Team sync         │ body 24 px, time column fixed width
│ ganztägig    Zahnarzt          │
│                                │
│ ÜBERFÄLLIG / OVERDUE           │ section header
│ ☐  Stromrechnung zahlen   -3d  │ checkbox 24×24, 2 px stroke
│ ☐  Anna antworten          -1d │
│ HEUTE FÄLLIG / DUE TODAY       │
│ ☐  Kapitel 3 fertig            │
│    schreiben                   │ wrapped line, hang indent = text start
│ PROJEKTE · Backlog             │ always-print group, board · stack
│ ☐  PCB rev 2 designen          │
│ ────────────────────────────   │ 1 px rule
│ 2 overdue · 1 today · 1 project│ small meta, centred
│ "Done is better than perfect." │ rotating quote, small
│ ✂ - - - - - - - - - - - - - -  │ tear line
└────────────────────────────────┘
```

Geometry: 16 px left margin, checkbox 24×24 px with 2 px stroke, 6 px gap to text, so text
starts at x = 46. Right margin 16 px. Line height = font size × 1.3. Items are separated by
6 px, sections by 18 px. The "-3d" lateness marker is right-aligned in the small font.

## Language

Labels are English by default; German labels are a config switch (`ui.language = "de"`).
The date line always uses the configured locale for the weekday and month.

## New-task slip

```
┌────────────────────────────────┐
│ ════════════════════════════   │
│ NEW  ·  14:32                  │ section header
│ ════════════════════════════   │
│ ☐  Order screws                │
│    Projects · Backlog · 25 Sep │ meta line: list/board, due
│ ☐  Call landlord               │
│    Personal · today            │
│ ────────────────────────────   │
└────────────────────────────────┘
```
One slip per debounce window listing all new items.

## Empty day

Nothing is printed on paper (unless `daily.print_calendar_only_days` is true and there are
events). Screen shows "No tasks for today" and the calendar.

## Calibration receipt (`test-print`)

Text mode: 32-column ruler, `Zähne Über Straße € ß` in the fallback code page, then the
same string rendered as an image, a density sweep (ESC 7 heating settings and GS ( E where
supported) with labelled blocks, and a 24 px checkbox row. The working density becomes
`printer.density`.

## Iterating on the design

`printcrastinator preview out.png [--sample | --empty | --slip]` writes the image without a
printer. Snapshot tests in `tests/snapshots/` pin the PNGs for the sample agenda, the empty day
and a slip; update them deliberately with `pytest --snapshot-update`.
