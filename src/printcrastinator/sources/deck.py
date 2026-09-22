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

    # ---- write-back -------------------------------------------------------------------------

    async def _card(self, c: httpx.AsyncClient, board_id: int, stack_id: int, card_id: int) -> dict:
        r = await c.get(f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}")
        r.raise_for_status()
        return r.json()

    async def _put_card(self, board_id: int, stack_id: int, card_id: int, **changes: Any) -> None:
        async with self._client() as c:
            card = await self._card(c, board_id, stack_id, card_id)
            owner = card.get("owner")
            body = {
                "owner": owner.get("uid") if isinstance(owner, dict) else owner,
                "title": card.get("title", ""),
                "type": card.get("type", "plain"),
                "order": card.get("order", 0),
                "description": card.get("description") or "",
                "duedate": card.get("duedate"),
                "done": card.get("done"),
            }
            body.update(changes)
            r = await c.put(f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}", json=body)
            r.raise_for_status()

    async def complete_card(self, board_id: int, stack_id: int, card_id: int) -> None:
        from datetime import UTC, datetime

        await self._put_card(
            board_id, stack_id, card_id, done=datetime.now(UTC).isoformat(timespec="seconds")
        )

    async def uncomplete_card(self, board_id: int, stack_id: int, card_id: int) -> None:
        await self._put_card(board_id, stack_id, card_id, done=None)

    async def update_card(
        self,
        board_id: int,
        stack_id: int,
        card_id: int,
        title: str | None = None,
        due: str | None = "keep",
        notes: str | None = None,
    ) -> None:
        changes: dict[str, Any] = {}
        if title is not None:
            changes["title"] = title
        if notes is not None:
            changes["description"] = notes
        if due != "keep":
            changes["duedate"] = due
        await self._put_card(board_id, stack_id, card_id, **changes)

    async def create_card(
        self, board_id: int, stack_id: int, title: str, due: str | None = None, notes: str = ""
    ) -> int:
        async with self._client() as c:
            body: dict[str, Any] = {"title": title, "type": "plain", "order": 999}
            if due:
                body["duedate"] = due
            if notes:
                body["description"] = notes
            r = await c.post(f"/boards/{board_id}/stacks/{stack_id}/cards", json=body)
            r.raise_for_status()
            return int(r.json()["id"])
