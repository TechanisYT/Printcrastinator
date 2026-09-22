"""SQLite state. All access goes through the Database class; connections are short-lived."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from .config import db_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_prints (
    date TEXT PRIMARY KEY,
    claimed_at TEXT NOT NULL,
    printed_on_paper INTEGER,
    task_count INTEGER
);
CREATE TABLE IF NOT EXISTS seen_items (
    uid TEXT NOT NULL,
    source TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    PRIMARY KEY (uid, source)
);
CREATE TABLE IF NOT EXISTS suppressed (
    uid TEXT NOT NULL,
    due TEXT NOT NULL DEFAULT '',
    hidden_at TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (uid, due)
);
CREATE TABLE IF NOT EXISTS stack_rules (
    board_id INTEGER NOT NULL,
    stack_id INTEGER NOT NULL,
    always_print INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (board_id, stack_id)
);
CREATE TABLE IF NOT EXISTS list_rules (
    list_id TEXT PRIMARY KEY,
    always_print INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS calendar_prefs (
    calendar_id TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS caldav_cache (
    collection_id TEXT PRIMARY KEY,
    ctag TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS print_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    at TEXT NOT NULL,
    ok INTEGER NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else db_path()
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: sqlite3.Connection | None = None
        if str(self.path) == ":memory:":
            self._mem = sqlite3.connect(":memory:", check_same_thread=False)
        with self.connect() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        if self._mem is not None:
            con = self._mem
            try:
                yield con
                con.commit()
            except Exception:
                con.rollback()
                raise
            return
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("PRAGMA journal_mode=WAL")
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    # ---- daily gate -------------------------------------------------------------

    def claim_daily(self, day: date) -> bool:
        """Atomically claim today's print. True if this call won the claim."""
        with self.connect() as con:
            cur = con.execute(
                "INSERT OR IGNORE INTO daily_prints(date, claimed_at) VALUES (?, ?)",
                (day.isoformat(), _now()),
            )
            return cur.rowcount == 1

    def release_daily(self, day: date) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM daily_prints WHERE date = ?", (day.isoformat(),))

    def mark_daily_printed(self, day: date, *, on_paper: bool, task_count: int) -> None:
        with self.connect() as con:
            con.execute(
                "UPDATE daily_prints SET printed_on_paper = ?, task_count = ? WHERE date = ?",
                (int(on_paper), task_count, day.isoformat()),
            )

    def daily_status(self, day: date) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM daily_prints WHERE date = ?", (day.isoformat(),)
            ).fetchone()
            return dict(row) if row else None

    # ---- seen items -------------------------------------------------------------

    def seen_count(self) -> int:
        with self.connect() as con:
            return con.execute("SELECT COUNT(*) FROM seen_items").fetchone()[0]

    def is_seen(self, uid: str, source: str) -> bool:
        with self.connect() as con:
            return (
                con.execute(
                    "SELECT 1 FROM seen_items WHERE uid = ? AND source = ?", (uid, source)
                ).fetchone()
                is not None
            )

    def mark_seen(self, items: list[tuple[str, str]]) -> None:
        with self.connect() as con:
            con.executemany(
                "INSERT OR IGNORE INTO seen_items(uid, source, first_seen) VALUES (?, ?, ?)",
                [(uid, source, _now()) for uid, source in items],
            )

    def unseen(self, items: list[tuple[str, str]]) -> set[tuple[str, str]]:
        if not items:
            return set()
        with self.connect() as con:
            seen = set()
            for uid, source in items:
                if con.execute(
                    "SELECT 1 FROM seen_items WHERE uid = ? AND source = ?", (uid, source)
                ).fetchone():
                    seen.add((uid, source))
            return set(items) - seen

    # ---- suppression --------------------------------------------------------------

    def suppress(self, uid: str, due: str, title: str = "") -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO suppressed(uid, due, hidden_at, title) VALUES (?, ?, ?, ?)",
                (uid, due, _now(), title),
            )

    def unsuppress(self, uid: str, due: str) -> None:
        with self.connect() as con:
            con.execute("DELETE FROM suppressed WHERE uid = ? AND due = ?", (uid, due))

    def suppressed_keys(self) -> set[tuple[str, str]]:
        with self.connect() as con:
            return {(r[0], r[1]) for r in con.execute("SELECT uid, due FROM suppressed")}

    def suppressed_rows(self) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [
                dict(r) for r in con.execute("SELECT * FROM suppressed ORDER BY hidden_at DESC")
            ]

    # ---- rules ----------------------------------------------------------------------

    def set_stack_rule(self, board_id: int, stack_id: int, always: bool) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO stack_rules(board_id, stack_id, always_print)"
                " VALUES (?, ?, ?)",
                (board_id, stack_id, int(always)),
            )

    def always_print_stacks(self) -> set[tuple[int, int]]:
        with self.connect() as con:
            return {
                (r[0], r[1])
                for r in con.execute(
                    "SELECT board_id, stack_id FROM stack_rules WHERE always_print = 1"
                )
            }

    def set_list_rule(self, list_id: str, always: bool) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO list_rules(list_id, always_print) VALUES (?, ?)",
                (list_id, int(always)),
            )

    def always_print_lists(self) -> set[str]:
        with self.connect() as con:
            return {
                r[0] for r in con.execute("SELECT list_id FROM list_rules WHERE always_print = 1")
            }

    def set_calendar_enabled(self, calendar_id: str, enabled: bool) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO calendar_prefs(calendar_id, enabled) VALUES (?, ?)",
                (calendar_id, int(enabled)),
            )

    def disabled_calendars(self) -> set[str]:
        with self.connect() as con:
            return {
                r[0]
                for r in con.execute("SELECT calendar_id FROM calendar_prefs WHERE enabled = 0")
            }

    # ---- caldav cache -----------------------------------------------------------------

    def cache_get(self, collection_id: str) -> tuple[str, Any] | None:
        with self.connect() as con:
            row = con.execute(
                "SELECT ctag, payload_json FROM caldav_cache WHERE collection_id = ?",
                (collection_id,),
            ).fetchone()
            return (row[0], json.loads(row[1])) if row else None

    def cache_put(self, collection_id: str, ctag: str, payload: Any) -> None:
        with self.connect() as con:
            con.execute(
                "INSERT OR REPLACE INTO caldav_cache(collection_id, ctag, payload_json, fetched_at)"
                " VALUES (?, ?, ?, ?)",
                (collection_id, ctag, json.dumps(payload), _now()),
            )

    # ---- log / kv ---------------------------------------------------------------------

    def log(self, kind: str, ok: bool, detail: str = "") -> None:
        with self.connect() as con:
            con.execute(
                "INSERT INTO print_log(kind, at, ok, detail) VALUES (?, ?, ?, ?)",
                (kind, _now(), int(ok), detail[:2000]),
            )

    def recent_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.connect() as con:
            return [
                dict(r)
                for r in con.execute("SELECT * FROM print_log ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
            return row[0] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self.connect() as con:
            con.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, value))
