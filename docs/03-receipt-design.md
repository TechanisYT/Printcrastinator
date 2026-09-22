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
- Font: bundled **JetBrains Mono** (OFL, `assets/fonts/`) in four weights: ExtraBold for the
  date and section headers (44 / 28 px), Bold for list subheaders (24 px), Medium for task
  titles (24 px), Regular for notes, tags and markers (20 px). Umlauts and € come from the font;
  no code pages involved.
- The image is sent in bands of 64 dot lines, each flushed and paced to `printer.lines_per_second`
  (default 200). The Anjet 58 resets its USB link when flooded with raster data, which drops
  bytes and blanks paper; pacing keeps the transfer within what it can print.

## Sections (top to bottom)

EVENTS → DUE TODAY (or DUE <date> for other days) → OVERDUE → DECKS & LISTS (the always-print
stacks and lists). Inside DUE and OVERDUE, tasks are grouped under their list label when
`daily.group_by_list` is on.

### Events timeline

Timed events are drawn on a vertical hour bar spanning from the first event's hour to the last
event's end hour (64 px per hour). Each event is a framed block with a thick left edge placed at
its time; the title is wrapped inside and truncated with "…" when the block is too short.
Overlapping events are laid out side by side (greedy column assignment, at most 3 columns); an
event widens into free columns to its right. Events that would need a fourth column are listed
as plain lines under the bar. All-day events are listed above the bar.

### Calendar-only receipts

`calendar_receipt(days)`: for one day the header plus the EVENTS timeline; for 2–14 days a
table drawn landscape (hour axis vertical, days as columns of 230 px, `tiny` 16 px font for
event titles, all-day events as bullet lines under the day header) and rotated 90° onto the
paper, so the days run along the paper and the tear-off is read turned sideways. The hour
range spans the earliest to the latest event across all days (minimum 6 h). No task sections.
Reachable via "Print calendar only" on the Dashboard, `/api/print/calendar`, the AI tool
`print_calendar` and MCP.

### Birthdays

A BIRTHDAYS section after EVENTS lists today's and upcoming birthdays (`daily.birthdays_lookahead`
days, default 14) from Nextcloud's auto-generated "Contact birthdays" calendar
(`daily.birthdays_calendar`, default `contact_birthdays`). Nextcloud titles them
"🎂 Name (1990)"; the year gives the age. That calendar is excluded from EVENTS.

### Saved lists and tickets

`list_receipt(title, items)`: a title block and one checkbox per item; lists live in the
`custom_lists` table and remember when they were last printed. `ticket_receipt(dict)`: tear lines
top and bottom, kind label, headline title, subtitle, when/where/seat/name/price rows, a QR code
(`qrcode`, scaled to whole modules, about 176 px) for `code`, and a note.

### Timeline range

Both timelines start at the full hour before the first event and end exactly at the last
event's end (the final tick is labelled with that time); no padding after the last event.

### Custom receipts

`custom_receipt(title, groups, day)`: a title header with the date, then the selected tasks
under their list labels. Used by the "Custom print" card, the API (`/api/print/selection`), the
AI (`print_tasks`) and MCP. Any day's daily receipt can be printed via `/api/print/day`.

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

Text mode: 32-column ruler, `Zähne Über Straße € ß` in the fallback code page, then one
image block with the same string, a headline sample and a 24 px checkbox row. With `--sweep`
(or the "Density sweep" button) one extra block per density preset (ESC 7 and DC2 # variants)
follows, each labelled; the best one becomes `printer.density`.

Feeding uses plain line feeds (about 4 mm each) because ESC J is ignored by some printers.
`printer.feed_after_mm` (default 10) leaves room to tear off by hand without cutting text.

## Iterating on the design

`printcrastinator preview out.png [--sample | --empty | --slip]` writes the image without a
printer. Snapshot tests in `tests/snapshots/` pin the PNGs for the sample agenda, the empty day
and a slip; update them deliberately with `pytest --snapshot-update`.

## Logo and layouts

- Optional logo at the top: images uploaded in Settings are stored in
  `~/.local/share/printcrastinator/logos/`. Mode `off`, `random` (one per print) or `fixed`.
  Scaled to fit 352 px wide and `logo.max_height`, thresholded (or dithered when `logo.dither`).
- Layout `list` (continuous checklist) or `cards` (each task in its own block between cut
  lines, for scissors). Dashboard has a one-off "Print as cards" button.
- Footer: only a "+N older overdue not shown" line when overdue filters apply, and the quote
  if `daily.quote` is on (default off).
