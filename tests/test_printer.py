import pytest

from printcrastinator.config import PrinterConfig
from printcrastinator.printer.escpos_out import Printer, PrinterError, density_bytes, dummy_bytes
from printcrastinator.receipt import layout
from printcrastinator.render import image


def test_density_parsing():
    assert density_bytes("") == b""
    assert density_bytes("esc7:7,80,2") == b"\x1b7\x07\x50\x02"
    assert density_bytes("dc2:12") == b"\x12#\x0c"
    with pytest.raises(ValueError):
        density_bytes("bogus")


def test_image_is_split_into_bands():
    img = image.render(layout.daily_receipt(layout.sample_agenda()))
    cfg = PrinterConfig(band_lines=150)
    data = dummy_bytes(img, cfg)
    # GS v 0 raster header appears once per band
    bands = data.count(b"\x1dv0")
    assert bands == -(-img.height // 150)


def test_missing_device_raises(tmp_path):
    p = Printer(PrinterConfig(device=str(tmp_path / "nope")))
    with pytest.raises(PrinterError):
        p.feed(1)
