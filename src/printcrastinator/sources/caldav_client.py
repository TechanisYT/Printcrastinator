"""Synchronous CalDAV access (run inside asyncio.to_thread). Pure parsing lives in parse.py."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import caldav
from caldav.elements.base import BaseElement

from ..config import NextcloudConfig
from ..db import Database
from .parse import parse_vevent_components, parse_vtodo

log = logging.getLogger(__name__)


class GetCTag(BaseElement):
    tag = "{http://calendarserver.org/ns/}getctag"


@dataclass(frozen=True)
class Collection:
    id: str  # last URL path segment, stable
    name: str
    url: str
    vtodo: bool
    vevent: bool


class CalDavClient:
    def __init__(self, nc: NextcloudConfig, db: Database | None = None) -> None:
        self.nc = nc
        self.db = db
        self._client: caldav.DAVClient | None = None

    def _dav(self) -> caldav.DAVClient:
        if self._client is None:
            self._client = caldav.DAVClient(
                url=f"{self.nc.base_url}/remote.php/dav/",
                username=self.nc.username,
                password=self.nc.app_password,
                timeout=30,
            )
        return self._client

    def collections(self) -> list[Collection]:
        out: list[Collection] = []
        for cal in self._dav().principal().calendars():
            try:
                comps = set(cal.get_supported_components())
            except Exception:  # some servers omit the property
                comps = {"VEVENT", "VTODO"}
            url = str(cal.url)
            cid = url.rstrip("/").rsplit("/", 1)[-1]
            out.append(
                Collection(
                    id=cid,
                    name=cal.get_display_name() or cid,
                    url=url,
                    vtodo="VTODO" in comps,
                    vevent="VEVENT" in comps,
                )
            )
        return out

    def _calendar(self, col: Collection) -> caldav.Calendar:
        return caldav.Calendar(client=self._dav(), url=col.url)

    def ctag(self, col: Collection) -> str:
        try:
            return str(self._calendar(col).get_property(GetCTag()) or "")
        except Exception as exc:
            log.debug("ctag unavailable for %s: %s", col.id, exc)
            return ""

    def _cached(self, key: str, ctag: str) -> Any | None:
        if not self.db or not ctag:
            return None
        hit = self.db.cache_get(key)
        if hit and hit[0] == ctag:
            return hit[1]
        return None

    def _store(self, key: str, ctag: str, payload: Any) -> None:
        if self.db and ctag:
            self.db.cache_put(key, ctag, payload)

    def todos_raw(self, col: Collection) -> list[str]:
        """ICS strings of pending VTODOs, cached by ctag."""
        key = f"todos:{col.id}"
        ctag = self.ctag(col)
        cached = self._cached(key, ctag)
        if cached is not None:
            return cached
        data = [t.data for t in self._calendar(col).get_todos(include_completed=False)]
        self._store(key, ctag, data)
        return data

    def events_raw(self, col: Collection, day: date) -> list[str]:
        """ICS strings of events overlapping `day`, expanded, cached by ctag+day."""
        key = f"events:{col.id}:{day.isoformat()}"
        ctag = self.ctag(col)
        cached = self._cached(key, ctag)
        if cached is not None:
            return cached
        tz = datetime.now().astimezone().tzinfo
        start = datetime.combine(day, datetime.min.time(), tz)
        end = start + timedelta(days=1)
        found = self._calendar(col).search(start=start, end=end, event=True, expand=True)
        data = [e.data for e in found]
        self._store(key, ctag, data)
        return data

    # ---- high level --------------------------------------------------------------------

    def fetch_tasks(self, today: date):
        # Nextcloud mirrors every Deck board as a read-only VTODO collection
        # ("app-generated--deck--board-N"). Those come from the Deck API instead.
        cols = [c for c in self.collections() if c.vtodo and not c.id.startswith("app-generated--")]
        items = []
        for col in cols:
            for ics in self.todos_raw(col):
                item = parse_vtodo(ics, col.id, col.name)
                if item is not None:
                    items.append(item)
        return cols, items

    def fetch_events(self, day: date, disabled: set[str]):
        cols = [c for c in self.collections() if c.vevent]
        events = []
        for col in cols:
            if col.id in disabled:
                continue
            for ics in self.events_raw(col, day):
                events.extend(parse_vevent_components(ics, col.id, col.name, day))
        return cols, events

    def test_connection(self) -> str:
        cols = self.collections()
        return f"OK: {len(cols)} calendars/lists found"
