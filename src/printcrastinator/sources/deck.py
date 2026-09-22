"""Nextcloud Deck REST API (async)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

from ..config import NextcloudConfig
from ..models import TaskItem
from .parse import parse_deck_cards

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class DeckStack:
    board_id: int
    board_title: str
    stack_id: int
    stack_title: str


class DeckClient:
    def __init__(self, nc: NextcloudConfig) -> None:
        self.nc = nc

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=f"{self.nc.base_url}/index.php/apps/deck/api/v1.0",
            auth=(self.nc.username, self.nc.app_password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=30,
        )

    async def boards_and_stacks(self) -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
        async with self._client() as c:
            r = await c.get("/boards")
            r.raise_for_status()
            boards = [b for b in r.json() if not b.get("archived") and not b.get("deletedAt")]
            out = []
            for board in boards:
                rs = await c.get(f"/boards/{board['id']}/stacks")
                rs.raise_for_status()
                out.append((board, rs.json()))
            return out

    async def fetch(self) -> tuple[list[DeckStack], list[TaskItem]]:
        stacks: list[DeckStack] = []
        items: list[TaskItem] = []
        for board, board_stacks in await self.boards_and_stacks():
            for s in board_stacks:
                stacks.append(
                    DeckStack(
                        int(board["id"]),
                        str(board.get("title", "")),
                        int(s["id"]),
                        str(s.get("title", "")),
                    )
                )
            items.extend(parse_deck_cards(board, board_stacks, self.nc.base_url))
        return stacks, items

    async def test_connection(self) -> str:
        async with self._client() as c:
            r = await c.get("/boards")
            r.raise_for_status()
            return f"OK: {len(r.json())} boards"
