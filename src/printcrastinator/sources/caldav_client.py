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
    """CalDAV access. Default: the Nextcloud account. `dav_url`/credentials override it for
    other servers; `id_prefix` keeps collection ids unique across sources."""

    def __init__(
        self,
        nc: NextcloudConfig,
        db: Database | None = None,
        *,
        dav_url: str = "",
        username: str = "",
        password: str = "",
        id_prefix: str = "",
        name_override: str = "",
    ) -> None:
        self.nc = nc
        self.db = db
        self.dav_url = dav_url or f"{nc.base_url}/remote.php/dav/"
        self.username = username or nc.username
        self.password = password or nc.app_password
        self.id_prefix = id_prefix
        self.name_override = name_override
        self._client: caldav.DAVClient | None = None
        self._my_address: str | None = None

    def _dav(self) -> caldav.DAVClient:
        if self._client is None:
            self._client = caldav.DAVClient(
                url=self.dav_url, username=self.username, password=self.password, timeout=30
            )
        return self._client

    def _raw_calendars(self) -> list[caldav.Calendar]:
        """Principal discovery; if the URL is itself a calendar collection, use it directly."""
        try:
            cals = self._dav().principal().calendars()
            if cals:
                return cals
        except Exception as exc:
            log.debug("principal discovery failed for %s: %s", self.dav_url, exc)
        cal = caldav.Calendar(client=self._dav(), url=self.dav_url)
        cal.get_supported_components()  # raises if this is not a calendar
        return [cal]

    def collections(self) -> list[Collection]:
        out: list[Collection] = []
        cals = self._raw_calendars()
        for cal in cals:
            try:
                comps = set(cal.get_supported_components())
            except Exception:  # some servers omit the property
                comps = {"VEVENT", "VTODO"}
            url = str(cal.url)
            cid = self.id_prefix + url.rstrip("/").rsplit("/", 1)[-1]
            try:
                name = cal.get_display_name() or cid
            except Exception:
                name = cid
            if self.name_override:
                name = self.name_override if len(cals) == 1 else f"{self.name_override}: {name}"
            out.append(
                Collection(
                    id=cid,
                    name=name,
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
        # Nextcloud/caldav returns nothing for a window that ends exactly at the next
        # midnight; query a padded window and let the parser keep only `day`.
        start = datetime.combine(day - timedelta(days=1), datetime.min.time(), tz)
        end = datetime.combine(day + timedelta(days=2), datetime.min.time(), tz)
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

    def fetch_birthdays(self, calendar_id: str, day_from: date, day_to: date) -> list:
        """Occurrences of the birthday calendar in the range (expanded server-side)."""
        from .parse import parse_birthdays

        col = next((c for c in self.collections() if c.id == calendar_id and c.vevent), None)
        if col is None:
            return []
        tz = datetime.now().astimezone().tzinfo
        start = datetime.combine(day_from - timedelta(days=1), datetime.min.time(), tz)
        end = datetime.combine(day_to + timedelta(days=2), datetime.min.time(), tz)
        key = f"bdays:{col.id}:{day_from}:{day_to}"
        ctag = self.ctag(col)
        cached = self._cached(key, ctag)
        if cached is None:
            found = self._calendar(col).search(start=start, end=end, event=True, expand=True)
            cached = [e.data for e in found]
            self._store(key, ctag, cached)
        out = []
        seen: set[str] = set()
        for ics in cached:
            for b in parse_birthdays(ics, day_from, day_to):
                if b.uid not in seen:
                    seen.add(b.uid)
                    out.append(b)
        return sorted(out, key=lambda b: (b.day, b.name.lower()))

    def test_connection(self) -> str:
        cols = self.collections()
        return f"OK: {len(cols)} calendars/lists found"

    # ---- write-back -------------------------------------------------------------------------

    def _find_todo(self, uid: str, list_id: str):
        for col in self.collections():
            if col.id == list_id and col.vtodo:
                return self._calendar(col).todo_by_uid(uid)
        raise LookupError(f"task list {list_id} not found")

    def complete_task(self, uid: str, list_id: str) -> None:
        todo = self._find_todo(uid, list_id)
        todo.complete()

    def uncomplete_task(self, uid: str, list_id: str) -> None:
        todo = self._find_todo(uid, list_id)
        todo.uncomplete()

    def _collection(self, list_id: str, *, vtodo: bool = True) -> caldav.Calendar:
        for col in self.collections():
            if col.id == list_id and (col.vtodo if vtodo else col.vevent):
                return self._calendar(col)
        raise LookupError(f"{'task list' if vtodo else 'calendar'} {list_id} not found")

    @staticmethod
    def _apply_todo_fields(comp: Any, fields: dict[str, Any]) -> None:
        """fields: title, notes, due, start (date|None), priority (0-9), tags (list), location."""
        if fields.get("title") is not None:
            comp["SUMMARY"] = fields["title"]
        if fields.get("notes") is not None:
            comp["DESCRIPTION"] = fields["notes"]
        if fields.get("location") is not None:
            comp["LOCATION"] = fields["location"]
        for key, prop in (("due", "DUE"), ("start", "DTSTART")):
            if key in fields and fields[key] != "keep":
                comp.pop(prop, None)
                if fields[key] is not None:
                    comp.add(prop, fields[key])
        if fields.get("priority") is not None:
            comp.pop("PRIORITY", None)
            comp.add("PRIORITY", int(fields["priority"]))
        if fields.get("tags") is not None:
            comp.pop("CATEGORIES", None)
            if fields["tags"]:
                comp.add("CATEGORIES", list(fields["tags"]))

    def create_task(self, list_id: str, title: str, **fields: Any) -> str:
        cal = self._collection(list_id)
        todo = cal.save_todo(summary=title)
        comp = todo.icalendar_component
        self._apply_todo_fields(comp, {k: v for k, v in fields.items() if k != "title"})
        todo.save()
        return str(comp.get("UID", ""))

    def update_task(self, uid: str, list_id: str, **fields: Any) -> None:
        todo = self._find_todo(uid, list_id)
        self._apply_todo_fields(todo.icalendar_component, fields)
        todo.save()

    def create_event(
        self,
        calendar_id: str,
        title: str,
        start: datetime | date,
        end: datetime | date | None = None,
        *,
        description: str = "",
        location: str = "",
        attendees: list[str] | None = None,
    ) -> str:
        cal = self._collection(calendar_id, vtodo=False)
        tz = datetime.now().astimezone().tzinfo
        if isinstance(start, datetime) and start.tzinfo is None:
            start = start.replace(tzinfo=tz)  # naive times are local, not UTC
        if isinstance(end, datetime) and end.tzinfo is None:
            end = end.replace(tzinfo=tz)
        if end is None:
            end = start + (
                timedelta(days=1) if not isinstance(start, datetime) else timedelta(hours=1)
            )
        kwargs: dict[str, Any] = {"summary": title, "dtstart": start, "dtend": end}
        if description:
            kwargs["description"] = description
        if location:
            kwargs["location"] = location
        ev = cal.save_event(**kwargs)
        if attendees:
            self._set_attendees(ev.icalendar_component, attendees)
            ev.save()
        return str(ev.icalendar_component.get("UID", ""))

    # ---- attendees -----------------------------------------------------------------------

    def my_address(self) -> str:
        """The user's own calendar address (mailto:…) for the ORGANIZER property."""
        if self._my_address is None:
            self._my_address = ""
            try:
                from caldav.elements import cdav

                addrs = self._dav().principal().get_property(cdav.CalendarUserAddressSet())
                for a in addrs or []:
                    if str(a).lower().startswith("mailto:"):
                        self._my_address = str(a)
                        break
            except Exception as exc:
                log.debug("calendar-user-address-set unavailable: %s", exc)
        return self._my_address

    @staticmethod
    def parse_attendee(text: str) -> tuple[str, str]:
        """'Name <mail>' | 'mail' | 'Name' -> (name, mail). Name-only has no mail."""
        text = text.strip()
        if "<" in text and text.endswith(">"):
            name, mail = text[:-1].split("<", 1)
            return name.strip(), mail.strip()
        if "@" in text:
            return "", text
        return text, ""

    def _set_attendees(self, comp: Any, attendees: list[str]) -> None:
        """Replace ATTENDEEs. Every attendee needs a mail address (a cal-address URI);
        the ORGANIZER is set to the user's own address so Nextcloud sends invitations."""
        from icalendar import vCalAddress, vText

        comp.pop("ATTENDEE", None)
        missing = [a for a in attendees if not self.parse_attendee(a)[1]]
        if missing:
            raise ValueError(
                f"attendees need an e-mail address (name <mail>): {', '.join(missing)}"
            )
        for a in attendees:
            name, mail = self.parse_attendee(a)
            addr = vCalAddress(f"mailto:{mail}")
            if name:
                addr.params["CN"] = vText(name)
            addr.params["RSVP"] = vText("TRUE")
            addr.params["CUTYPE"] = vText("INDIVIDUAL")
            addr.params["ROLE"] = vText("REQ-PARTICIPANT")
            addr.params["PARTSTAT"] = vText("NEEDS-ACTION")
            comp.add("ATTENDEE", addr, encode=0)
        if attendees and comp.get("ORGANIZER") is None and self.my_address():
            org = vCalAddress(self.my_address())
            comp.add("ORGANIZER", org, encode=0)

    def _find_event(self, uid: str, calendar_id: str = ""):
        uid = uid.split("@", 1)[0]  # strip a recurrence suffix
        cols = [
            c for c in self.collections() if c.vevent and (not calendar_id or c.id == calendar_id)
        ]
        for col in cols:
            try:
                return self._calendar(col).event_by_uid(uid)
            except Exception:
                continue
        raise LookupError(f"event {uid} not found")

    def update_event(self, uid: str, calendar_id: str = "", **fields: Any) -> None:
        """fields: title, start, end, description, location, attendees (list of 'Name <mail>').
        An attendees list replaces the whole set; [] removes everyone."""
        ev = self._find_event(uid, calendar_id)
        comp = ev.icalendar_component
        tz = datetime.now().astimezone().tzinfo
        if fields.get("title") is not None:
            comp["SUMMARY"] = fields["title"]
        if fields.get("description") is not None:
            comp["DESCRIPTION"] = fields["description"]
        if fields.get("location") is not None:
            comp["LOCATION"] = fields["location"]
        for key, prop in (("start", "DTSTART"), ("end", "DTEND")):
            v = fields.get(key)
            if v is not None:
                if isinstance(v, datetime) and v.tzinfo is None:
                    v = v.replace(tzinfo=tz)
                comp.pop(prop, None)
                comp.add(prop, v)
        if fields.get("attendees") is not None:
            self._set_attendees(comp, list(fields["attendees"]))
        ev.save()

    def delete_event(self, uid: str, calendar_id: str = "") -> None:
        self._find_event(uid, calendar_id).delete()
