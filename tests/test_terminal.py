from rich.console import Console

from printcrastinator.receipt import layout
from printcrastinator.render import terminal


def test_terminal_render_contains_items():
    console = Console(record=True, width=60, force_terminal=False)
    terminal.render(layout.daily_receipt(layout.sample_agenda(), "en"), console)
    out = console.export_text()
    assert "TUESDAY" in out and "[ ] Buy filament" in out and "-3d" in out
