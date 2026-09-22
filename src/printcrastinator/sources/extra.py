"""Extra calendars outside Nextcloud: CalDAV accounts/collections and ICS feeds (webcal)."""

from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime, time, timedelta

import httpx
import recurring_ical_events
from icalendar import Calendar as ICal

from ..config import ExtraCalendar, NextcloudConfig
from ..db import Database
from ..models import CalendarEvent
from .caldav_client import CalDavClient, Collection

log = logging.getLogger(__name__)


def _ics_url(url: str) -> str:
    if url.startswith("webcal://"):
        return "https://" + url[len("webcal://") :]
    return url


def fetch_ics_events(
    cal: ExtraCalendar, day: date, db: Database | None = None
) -> list[CalendarEvent]:
    """Download the feed (cached by ETag/Last-Modified) and expand today's occurrences."""
    url = _ics_url(cal.url)
    auth = (cal.username, cal.password) if cal.username else None
    headers: dict[str, str] = {}
    cached = db.cache_get(f"ics:{cal.id}") if db else None
    if cached:
        validators = cached[1].get("validators", {})
        if validators.get("etag"):
            headers["If-None-Match"] = validators["etag"]
        if validators.get("last_modified"):
            headers["If-Modified-Since"] = validators["last_modified"]
    text: str | None = None
    with httpx.Client(timeout=30, follow_redirects=True, auth=auth) as c:
        r = c.get(url, headers=headers)
        if r.status_code == 304 and cached:
            text = cached[1]["text"]
        else:
            r.raise_for_status()
            text = r.text
            if db:
                validators = {
                    "etag": r.headers.get("etag", ""),
                    "last_modified": r.headers.get("last-modified", ""),
                }
                db.cache_put(
                    f"ics:{cal.id}",
                    hashlib.sha1(text.encode()).hexdigest(),
                    {"text": text, "validators": validators},
                )
    return ics_events_for_day(text, day, cal.id, cal.name)


def ics_events_for_day(text: str, day: date, cal_id: str, cal_name: str) -> list[CalendarEvent]:
    tz = datetime.now().astimezone().tzinfo
    ical = ICal.from_ical(text)
    out: list[CalendarEvent] = []
    for comp in recurring_ical_events.of(ical).at(day):
        if str(comp.get("STATUS", "")).upper() == "CANCELLED":
            continue
        dtstart = comp.get("DTSTART").dt
        dtend = comp.get("DTEND").dt if comp.get("DTEND") is not None else None
        all_day = not isinstance(dtstart, datetime)
        if all_day:
            start = datetime.combine(dtstart, time.min, tz)
            end = datetime.combine(dtend, time.min, tz) if dtend else start + timedelta(days=1)
        else:
            start = (dtstart if dtstart.tzinfo else dtstart.replace(tzinfo=tz)).astimezone(tz)
            if dtend is None:
                end = start
            else:
                end = (dtend if dtend.tzinfo else dtend.replace(tzinfo=tz)).astimezone(tz)
        rid = comp.get("RECURRENCE-ID")
        uid = str(comp.get("UID", ""))
        if rid is not None:
            uid = f"{uid}@{rid.dt}"
        else:
            uid = f"{uid}@{start.isoformat()}"
        out.append(
            CalendarEvent(
                uid=uid,
                title=str(comp.get("SUMMARY", "")).strip() or "(untitled)",
                start=start,
                end=end,
                all_day=all_day,
                calendar_id=cal_id,
                calendar_name=cal_name,
                location=str(comp.get("LOCATION", "") or ""),
            )
        )
    return out


def fetch_extra(
    cals: list[ExtraCalendar],
    nc: NextcloudConfig,
    day: date,
    disabled: set[str],
    db: Database | None,
) -> tuple[list[Collection], list[CalendarEvent], dict[str, str]]:
    """All extra calendars. Returns (collections for the Calendars page, events, errors)."""
    collections: list[Collection] = []
    events: list[CalendarEvent] = []
    errors: dict[str, str] = {}
    for cal in cals:
        if not cal.url:
            continue
        try:
            if cal.kind == "caldav":
                client = CalDavClient(
                    nc,
                    db,
                    dav_url=cal.url,
                    username=cal.username,
                    password=cal.password,
                    id_prefix=cal.id + ":",
                    name_override=cal.name,
                )
                cols, evs = client.fetch_events(day, disabled)
                collections.extend(cols)
                events.extend(evs)
            else:
                collections.append(Collection(cal.id, cal.name, cal.url, False, True))
                if cal.id not in disabled:
                    events.extend(fetch_ics_events(cal, day, db))
        except Exception as exc:
            log.warning("extra calendar %s failed: %s", cal.name, exc)
            errors[cal.name] = str(exc)
    return collections, events, errors
