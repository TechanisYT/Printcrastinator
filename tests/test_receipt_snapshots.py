"""PNG snapshot tests. Update deliberately with: uv run pytest --snapshot-update"""

from datetime import date, datetime
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from printcrastinator.receipt import layout
from printcrastinator.render import image

SNAP = Path(__file__).parent / "snapshots"
DAY = date(2026, 9, 22)
NOW = datetime(2026, 9, 22, 14, 32)


def _cases():
    return {
        "sample_en": layout.daily_receipt(layout.sample_agenda(DAY), "en"),
        "sample_de": layout.daily_receipt(layout.sample_agenda(DAY), "de"),
        "empty": layout.daily_receipt(layout.empty_agenda(DAY), "en"),
        "slip": layout.slip_receipt(layout.sample_slip_items(DAY), NOW, "en"),
        "sample_cards": layout.daily_receipt(layout.sample_agenda(DAY), "en", "cards"),
        "calendar_1day": layout.calendar_receipt([(DAY, layout.sample_agenda(DAY).events)], "en"),
        "calendar_3days": layout.calendar_receipt(layout.sample_calendar_days(DAY), "en"),
    }


@pytest.mark.parametrize("name", list(_cases()))
def test_snapshot(name, snapshot_update):
    img = image.render(_cases()[name])
    assert img.mode == "1"
    assert img.width == 384
    path = SNAP / f"{name}.png"
    if snapshot_update or not path.exists():
        img.save(path)
        pytest.skip(f"snapshot written: {path.name}")
    expected = Image.open(path).convert("1")
    assert expected.size == img.size, "size changed; run with --snapshot-update if intended"
    diff = ImageChops.difference(expected.convert("L"), img.convert("L")).getbbox()
    assert diff is None, f"pixels differ in {diff}; run with --snapshot-update if intended"


def test_wrap_hang_indent_and_width():
    text = "Supercalifragilisticexpialidociousword " * 3
    lines = image.wrap(text, "body", 384 - 46 - 16)
    assert all(image.text_width(line, "body") <= 384 - 46 - 16 for line in lines)
    assert len(lines) >= 3


def test_png_roundtrip_is_bilevel():
    img = image.render(_cases()["sample_en"])
    buf = BytesIO()
    img.save(buf, format="PNG")
    assert set(Image.open(BytesIO(buf.getvalue())).convert("L").tobytes()) <= {0, 255}
