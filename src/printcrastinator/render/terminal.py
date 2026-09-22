"""Render a Receipt in the terminal with rich, from the same model as the image."""

from __future__ import annotations

from rich.console import Console
from rich.text import Text as RText

from ..receipt.model import (
    CheckItem,
    EventLine,
    Picture,
    Receipt,
    Rule,
    SectionHeader,
    Spacer,
    SubHeader,
    TearLine,
    Text,
)

COLS = 40


def render(receipt: Receipt, console: Console | None = None) -> None:
    console = console or Console()
    for b in receipt.blocks:
        match b:
            case Rule(thickness=t):
                console.print(("━" if t > 1 else "─") * COLS, style="bold" if t > 1 else "dim")
            case Spacer(height=h):
                if h >= 12:
                    console.print()
            case Text(text=t, size=s, align=a):
                style = {"headline": "bold", "section": "bold", "body": "", "small": "dim"}[s]
                console.print(
                    RText(t, style=style, justify="center" if a == "center" else "left"), width=COLS
                )
            case SectionHeader(text=t, hint=h):
                line = RText(t, style="bold underline")
                if h:
                    line.append(f"  {h}", style="dim")
                console.print(line)
            case SubHeader(text=t):
                console.print(RText(f"  {t}", style="dim bold"))
            case EventLine(time=t, title=title):
                console.print(RText.assemble((f"{t:<12}", "cyan"), title))
            case CheckItem(title=t, right=r, meta=m):
                line = RText.assemble(("[ ] ", "bold yellow"), t)
                if r:
                    line.append(f"  {r}", style="red")
                console.print(line)
                for line in m.split("\n") if m else []:
                    console.print(RText(f"    {line}", style="dim"))
            case TearLine():
                console.print("✂ " + "- " * ((COLS - 2) // 2), style="dim")
            case Picture(path=pth):
                console.print(
                    RText(f"[logo: {pth.rsplit('/', 1)[-1]}]", style="dim", justify="center"),
                    width=COLS,
                )
