"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path


def _cmd_preview(args: argparse.Namespace) -> int:
    from .config import load_config
    from .receipt import layout
    from .render import image

    cfg = load_config()
    lang = args.lang or cfg.ui.language
    day = date.fromisoformat(args.day) if args.day else None
    if args.kind == "slip":
        receipt = layout.slip_receipt(layout.sample_slip_items(day), datetime.now(), lang)
    elif args.kind == "empty":
        receipt = layout.daily_receipt(layout.empty_agenda(day), lang)
    elif args.kind == "live":
        from .client import get_agenda_or_build

        receipt = layout.daily_receipt(get_agenda_or_build(cfg), lang)
    else:
        receipt = layout.daily_receipt(layout.sample_agenda(day), lang)
    img = image.render(receipt)
    out = Path(args.path)
    img.save(out)
    print(f"wrote {out} ({img.width}x{img.height})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="printcrastinator")
    sub = p.add_subparsers(dest="cmd", required=True)

    pv = sub.add_parser("preview", help="render a receipt PNG without a printer")
    pv.add_argument("path")
    pv.add_argument("--kind", choices=["sample", "empty", "slip", "live"], default="sample")
    pv.add_argument("--lang", choices=["en", "de"])
    pv.add_argument("--day", help="ISO date for the sample")
    pv.set_defaults(func=_cmd_preview)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
