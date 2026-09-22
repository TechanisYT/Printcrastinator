from PIL import Image, ImageDraw

from printcrastinator import logos
from printcrastinator.config import LogoConfig
from printcrastinator.receipt import layout
from printcrastinator.render import image


def test_logo_pick_and_render(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    img = Image.new("RGB", (800, 300), "white")
    ImageDraw.Draw(img).ellipse((100, 50, 700, 250), fill="black")
    p = tmp_path / "logo.png"
    img.save(p)
    saved = logos.save_logo("My Logo.png", p.read_bytes())
    assert saved.name == "MyLogo.png"
    assert logos.pick_logo(LogoConfig(mode="off")) == ""
    assert logos.pick_logo(LogoConfig(mode="random")) == str(saved)
    assert logos.pick_logo(LogoConfig(mode="fixed", file="MyLogo.png")) == str(saved)
    assert logos.pick_logo(LogoConfig(mode="fixed", file="nope.png")) == ""

    opt = logos.options(LogoConfig(mode="random", max_height=100), quote=True)
    with_logo = image.render(layout.daily_receipt(layout.sample_agenda(), "en", "list", opt))
    without = image.render(layout.daily_receipt(layout.sample_agenda(), "en"))
    # scaled to 352 px wide -> 132 px tall, capped at max_height 100, plus 10 px spacer
    assert with_logo.height - without.height > 100
    logos.delete_logo("MyLogo.png")
    assert logos.list_logos() == []
