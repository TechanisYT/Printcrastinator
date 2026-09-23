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
    order: int = 0  # Nextcloud's own stack order within the board


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
            for s in sorted(board_stacks, key=lambda x: int(x.get("order") or 0)):
                stacks.append(
                    DeckStack(
                        int(board["id"]),
                        str(board.get("title", "")),
                        int(s["id"]),
                        str(s.get("title", "")),
                        int(s.get("order") or 0),
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

    async def board_meta(self, board_id: int) -> dict[str, Any]:
        """Labels and users of a board (for assigning by name)."""
        async with self._client() as c:
            r = await c.get(f"/boards/{board_id}")
            r.raise_for_status()
            b = r.json()
        return {
            "labels": {lab["title"]: int(lab["id"]) for lab in b.get("labels", [])},
            "users": {u["uid"]: u.get("displayname", u["uid"]) for u in b.get("users", [])},
        }

    async def _sync_labels(
        self, c: httpx.AsyncClient, board_id: int, stack_id: int, card_id: int, wanted: list[str]
    ) -> None:
        meta = await self.board_meta(board_id)
        card = await self._card(c, board_id, stack_id, card_id)
        have = {lab["title"]: int(lab["id"]) for lab in card.get("labels") or []}
        base = f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}"
        for title, lid in have.items():
            if title not in wanted:
                (await c.put(f"{base}/removeLabel", json={"labelId": lid})).raise_for_status()
        for title in wanted:
            if title not in have:
                lid = meta["labels"].get(title)
                if lid is None:
                    raise LookupError(f"label {title!r} not on board; have {list(meta['labels'])}")
                (await c.put(f"{base}/assignLabel", json={"labelId": lid})).raise_for_status()

    async def _sync_assignees(
        self, c: httpx.AsyncClient, board_id: int, stack_id: int, card_id: int, wanted: list[str]
    ) -> None:
        card = await self._card(c, board_id, stack_id, card_id)
        have = {
            (a.get("participant") or {}).get("uid", "") for a in card.get("assignedUsers") or []
        }
        base = f"/boards/{board_id}/stacks/{stack_id}/cards/{card_id}"
        for uid in have - set(wanted):
            (await c.put(f"{base}/unassignUser", json={"userId": uid})).raise_for_status()
        for uid in set(wanted) - have:
            (await c.put(f"{base}/assignUser", json={"userId": uid})).raise_for_status()

    async def update_card(self, board_id: int, stack_id: int, card_id: int, **fields: Any) -> None:
        """fields: title, notes, due (ISO str | None | 'keep'), labels (list of titles),
        assignees (list of user ids), stack (target stack id in the same board)."""
        changes: dict[str, Any] = {}
        if fields.get("title") is not None:
            changes["title"] = fields["title"]
        if fields.get("notes") is not None:
            changes["description"] = fields["notes"]
        if "due" in fields and fields["due"] != "keep":
            changes["duedate"] = fields["due"]
        if changes:
            await self._put_card(board_id, stack_id, card_id, **changes)
        async with self._client() as c:
            if fields.get("labels") is not None:
                await self._sync_labels(c, board_id, stack_id, card_id, list(fields["labels"]))
            if fields.get("assignees") is not None:
                await self._sync_assignees(
                    c, board_id, stack_id, card_id, list(fields["assignees"])
                )
        if fields.get("stack") is not None and int(fields["stack"]) != stack_id:
            await self.move_card(card_id, int(fields["stack"]))

    async def move_card(self, card_id: int, stack_id: int) -> None:
        """The public API's reorder endpoint answers 200 but does not move cards between
        stacks (Deck 1.14); the internal route the web UI uses does."""
        async with httpx.AsyncClient(
            base_url=f"{self.nc.base_url}/index.php/apps/deck",
            auth=(self.nc.username, self.nc.app_password),
            headers={"OCS-APIRequest": "true", "Accept": "application/json"},
            timeout=30,
        ) as c:
            r = await c.put(
                f"/cards/{card_id}/reorder",
                json={"cardId": card_id, "stackId": stack_id, "order": 0},
            )
            r.raise_for_status()

    async def create_card(self, board_id: int, stack_id: int, title: str, **fields: Any) -> int:
        async with self._client() as c:
            body: dict[str, Any] = {"title": title, "type": "plain", "order": 999}
            if fields.get("due"):
                body["duedate"] = fields["due"]
            if fields.get("notes"):
                body["description"] = fields["notes"]
            r = await c.post(f"/boards/{board_id}/stacks/{stack_id}/cards", json=body)
            r.raise_for_status()
            card_id = int(r.json()["id"])
            if fields.get("labels"):
                await self._sync_labels(c, board_id, stack_id, card_id, list(fields["labels"]))
            if fields.get("assignees"):
                await self._sync_assignees(
                    c, board_id, stack_id, card_id, list(fields["assignees"])
                )
            return card_id
