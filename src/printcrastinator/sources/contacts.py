"""Nextcloud Contacts over CardDAV: find contacts by name and set their birthday.

Only the BDAY line of a vCard is touched; everything else is written back byte-for-byte,
guarded by the card's ETag (If-Match) so concurrent edits are not overwritten.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any
from xml.sax.saxutils import escape

import httpx

from ..config import NextcloudConfig

_HREF_RE = re.compile(r"<d:href>([^<]+)</d:href>", re.I)


@dataclass(frozen=True)
class Contact:
    href: str
    etag: str
    name: str
    birthday: str  # "YYYY-MM-DD", "--MM-DD" (year unknown) or ""
    book: str

    def to_dict(self) -> dict[str, Any]:
        return {"href": self.href, "name": self.name, "birthday": self.birthday, "book": self.book}


def _unfold(vcard: str) -> list[str]:
    lines: list[str] = []
    for raw in vcard.replace("\r\n", "\n").split("\n"):
        if raw.startswith((" ", "\t")) and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _prop(lines: list[str], name: str) -> str:
    for ln in lines:
        key = ln.split(":", 1)[0].split(";", 1)[0].upper()
        if key == name:
            return ln.split(":", 1)[1] if ":" in ln else ""
    return ""


def parse_bday(value: str, params: str = "") -> str:
    """vCard BDAY value -> 'YYYY-MM-DD' or '--MM-DD'."""
    v = value.strip()
    omit_year = "X-APPLE-OMIT-YEAR" in params.upper()
    m = re.match(r"^(\d{4})-?(\d{2})-?(\d{2})", v)
    if m:
        y, mo, d = m.groups()
        return f"--{mo}-{d}" if omit_year or y == "1604" else f"{y}-{mo}-{d}"
    m = re.match(r"^--(\d{2})-?(\d{2})", v)
    if m:
        return f"--{m.group(1)}-{m.group(2)}"
    return v


def _bday_of(lines: list[str]) -> str:
    for ln in lines:
        head = ln.split(":", 1)[0]
        if head.split(";", 1)[0].upper() == "BDAY" and ":" in ln:
            return parse_bday(ln.split(":", 1)[1], head)
    return ""


def set_bday_line(vcard: str, birthday: str | None) -> str:
    """Return the vCard with BDAY replaced (birthday 'YYYY-MM-DD' | '--MM-DD') or removed."""
    lines = vcard.replace("\r\n", "\n").split("\n")
    out: list[str] = []
    skip_continuation = False
    for ln in lines:
        if skip_continuation and ln.startswith((" ", "\t")):
            continue
        skip_continuation = False
        if ln.split(":", 1)[0].split(";", 1)[0].upper() == "BDAY":
            skip_continuation = True
            continue
        out.append(ln)
    if birthday:
        if birthday.startswith("--"):
            mm, dd = birthday[2:].split("-")
            new_line = f"BDAY;X-APPLE-OMIT-YEAR=1604:1604-{mm}-{dd}"
        else:
            date.fromisoformat(birthday)  # validate
            new_line = f"BDAY:{birthday}"
        idx = next((i for i, ln in enumerate(out) if ln.upper().startswith("END:VCARD")), len(out))
        out.insert(idx, new_line)
    text = "\r\n".join(ln for ln in out if ln != "")
    return text + "\r\n"


class ContactsClient:
    def __init__(self, nc: NextcloudConfig) -> None:
        self.nc = nc
        self.root = f"{nc.base_url}/remote.php/dav/addressbooks/users/{nc.username}/"

    def _client(self) -> httpx.Client:
        return httpx.Client(auth=(self.nc.username, self.nc.app_password), timeout=60)

    def books(self) -> list[tuple[str, str]]:
        """[(href, display name)] of writable address books (system/app generated skipped)."""
        body = (
            '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:displayname/>'
            "<d:resourcetype/></d:prop></d:propfind>"
        )
        with self._client() as c:
            r = c.request("PROPFIND", self.root, headers={"Depth": "1"}, content=body)
            r.raise_for_status()
        out = []
        for block in r.text.split("<d:response>")[1:]:
            href = _HREF_RE.search(block)
            name = re.search(r"<d:displayname>([^<]*)</d:displayname>", block)
            if not href or "addressbook" not in block:
                continue
            h = href.group(1)
            slug = h.rstrip("/").rsplit("/", 1)[-1]
            if slug.startswith("z-"):  # z-server-generated--system, z-app-generated--…
                continue
            out.append((h, name.group(1) if name else slug))
        return out

    def find(self, query: str, limit: int = 20) -> list[Contact]:
        """Contacts whose formatted name contains `query` (case-insensitive), all books."""
        q = escape(query.strip())
        report = (
            '<?xml version="1.0"?><c:addressbook-query xmlns:d="DAV:" '
            'xmlns:c="urn:ietf:params:xml:ns:carddav"><d:prop><d:getetag/><c:address-data/>'
            '</d:prop><c:filter><c:prop-filter name="FN">'
            '<c:text-match collation="i;unicode-casemap" '
            f'match-type="contains">{q}</c:text-match></c:prop-filter></c:filter></c:addressbook-query>'
        )
        found: list[Contact] = []
        with self._client() as c:
            for href, book in self.books():
                r = c.request(
                    "REPORT", self.nc.base_url + href, headers={"Depth": "1"}, content=report
                )
                if r.status_code >= 400:
                    continue
                for block in r.text.split("<d:response>")[1:]:
                    h = _HREF_RE.search(block)
                    etag = re.search(r"<d:getetag>([^<]*)</d:getetag>", block)
                    data = re.search(
                        r"<c(?:ard)?:address-data[^>]*>(.*?)</c(?:ard)?:address-data>", block, re.S
                    )
                    if not (h and data):
                        continue
                    vcard = (
                        data.group(1)
                        .replace("&#13;", "")
                        .replace("&lt;", "<")
                        .replace("&gt;", ">")
                        .replace("&amp;", "&")
                    )
                    lines = _unfold(vcard)
                    found.append(
                        Contact(
                            href=h.group(1),
                            etag=(etag.group(1) if etag else "").replace("&quot;", '"'),
                            name=_prop(lines, "FN") or _prop(lines, "N"),
                            birthday=_bday_of(lines),
                            book=book,
                        )
                    )
                    if len(found) >= limit:
                        return found
        return found

    def set_birthday(self, href: str, birthday: str | None) -> Contact:
        """birthday: 'YYYY-MM-DD', '--MM-DD' (no year) or None to remove."""
        url = self.nc.base_url + href
        with self._client() as c:
            r = c.get(url)
            r.raise_for_status()
            # gzip responses carry a weak ETag (W/"…"); If-Match needs the strong form
            etag = r.headers.get("etag", "").removeprefix("W/")
            new_card = set_bday_line(r.text, birthday)
            headers = {"Content-Type": "text/vcard; charset=utf-8"}
            if etag:
                headers["If-Match"] = etag
            w = c.put(url, content=new_card.encode("utf-8"), headers=headers)
            w.raise_for_status()
            lines = _unfold(new_card)
            return Contact(
                href=href,
                etag=w.headers.get("etag", ""),
                name=_prop(lines, "FN"),
                birthday=_bday_of(lines),
                book="",
            )
