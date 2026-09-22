"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

from .config import load_config


def _cmd_preview(args: argparse.Namespace) -> int:
    from .receipt import layout
    from .render import image

    cfg = load_config()
    lang = args.lang or cfg.ui.language
    mode = args.layout or cfg.daily.layout
    day = date.fromisoformat(args.day) if args.day else None
    if args.kind == "slip":
        receipt = layout.slip_receipt(layout.sample_slip_items(day), datetime.now(), lang)
    elif args.kind == "empty":
        receipt = layout.daily_receipt(layout.empty_agenda(day), lang, mode)
    elif args.kind == "live":
        from .client import get_agenda_or_build

        receipt = layout.daily_receipt(get_agenda_or_build(cfg), lang, mode)
    else:
        receipt = layout.daily_receipt(layout.sample_agenda(day), lang, mode)
    img = image.render(receipt)
    out = Path(args.path)
    img.save(out)
    print(f"wrote {out} ({img.width}x{img.height})")
    if args.open:
        import subprocess

        subprocess.Popen(["xdg-open", str(out)])
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    from .web.app import run

    run(load_config())
    return 0


def _cmd_show(args: argparse.Namespace) -> int:
    from rich.console import Console

    from . import client
    from .receipt import layout
    from .render import terminal

    cfg = load_config()
    console = Console()
    if not cfg.nextcloud.configured:
        console.print(f"[yellow]Nextcloud not configured. Open {cfg.api_base} to set it up.[/]")
        return 1
    result = client.print_daily(cfg, force=False)
    ag = client.get_agenda_or_build(cfg)
    terminal.render(layout.daily_receipt(ag, cfg.ui.language), console)
    if result.get("printed"):
        console.print("[green]Printed today's receipt.[/]")
    else:
        console.print(f"[dim]Paper: {result.get('reason', '')}[/]")
    if not args.no_wait and sys.stdin.isatty():
        console.print("[dim]press Enter to close[/]")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
    return 0


def _cmd_print_today(args: argparse.Namespace) -> int:
    from . import client

    result = client.print_daily(load_config(), force=args.force, layout=args.layout or "")
    print(result)
    return 0 if result.get("printed") or result.get("on_screen") else 1


def _cmd_test_print(args: argparse.Namespace) -> int:
    from . import client

    client.print_test(load_config(), sweep=args.sweep)
    print("calibration receipt sent")
    return 0


def _cmd_notify(args: argparse.Namespace) -> int:
    from . import client

    client.notify(load_config())
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="printcrastinator")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run daemon, HTTP API and web UI")
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser("show", help="show today's agenda in the terminal (triggers daily print)")
    s.add_argument("--no-wait", action="store_true")
    s.set_defaults(func=_cmd_show)

    s = sub.add_parser("print-today", help="print today's receipt")
    s.add_argument("--force", action="store_true", help="ignore the once-per-day gate")
    s.add_argument("--layout", choices=["list", "cards"], help="override the configured layout")
    s.set_defaults(func=_cmd_print_today)

    s = sub.add_parser("test-print", help="calibration receipt")
    s.add_argument("--sweep", action="store_true", help="also print one block per density preset")
    s.set_defaults(func=_cmd_test_print)

    s = sub.add_parser("notify", help="desktop notification for today")
    s.set_defaults(func=_cmd_notify)

    pv = sub.add_parser("preview", help="render a receipt PNG without a printer")
    pv.add_argument("path")
    pv.add_argument("--kind", choices=["sample", "empty", "slip", "live"], default="sample")
    pv.add_argument("--lang", choices=["en", "de"])
    pv.add_argument("--layout", choices=["list", "cards"], default=None)
    pv.add_argument("--day", help="ISO date for the sample")
    pv.add_argument("--open", action="store_true", help="open the PNG with xdg-open")
    pv.set_defaults(func=_cmd_preview)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.cmd == "serve":
        logging.getLogger().setLevel(logging.DEBUG if args.verbose else logging.INFO)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        if args.verbose:
            raise
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
