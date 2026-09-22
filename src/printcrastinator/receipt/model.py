"""Device-independent receipt model. Layout builds it; image/terminal renderers consume it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Align = Literal["left", "center", "right"]
Size = Literal["headline", "section", "body", "small"]


@dataclass(frozen=True)
class Rule:
    thickness: int = 1  # px


@dataclass(frozen=True)
class Spacer:
    height: int = 12  # px


@dataclass(frozen=True)
class Text:
    text: str
    size: Size = "body"
    align: Align = "left"
    wrap: bool = True


@dataclass(frozen=True)
class SectionHeader:
    text: str
    hint: str = ""  # optional small right-aligned text, e.g. count


@dataclass(frozen=True)
class EventLine:
    time: str  # "09:00–10:30" or "all day"
    title: str


@dataclass(frozen=True)
class CheckItem:
    title: str
    right: str = ""  # short marker right-aligned on the first line, e.g. "-3d"
    meta: str = ""  # optional small line below the title


@dataclass(frozen=True)
class TearLine:
    pass


@dataclass(frozen=True)
class Picture:
    path: str
    max_height: int = 160
    dither: bool = False


Block = Rule | Spacer | Text | SectionHeader | EventLine | CheckItem | TearLine | Picture


@dataclass
class Receipt:
    blocks: list[Block] = field(default_factory=list)

    def add(self, *blocks: Block) -> None:
        self.blocks.extend(blocks)
