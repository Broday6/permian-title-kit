"""Append-only event log for deals, in one SQLite file.

Every change to a deal is an event: who did what, when, with the full payload.
Nothing is ever updated or deleted (SQLite triggers refuse it), so a deal's
status is always derived from its history and the history is the audit trail.
Each event also carries a SHA-256 hash chained to the one before it, so
``Store.verify()`` can prove the file hasn't been edited behind the program's
back.

Payload values must be JSON: fractions are stored as "n/d" strings and money
as decimal strings, never floats.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    deal_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    prev_hash TEXT NOT NULL,
    hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_by_deal ON events (deal_id, id);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'the deal log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'the deal log is append-only'); END;
"""
GENESIS = "0" * 64


@dataclass(frozen=True)
class StoredEvent:
    id: int
    deal_id: str
    ts: str
    actor: str
    type: str
    payload: dict[str, Any]
    prev_hash: str
    hash: str


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(prev: str, eid: int, deal_id: str, ts: str, actor: str, etype: str, payload: str) -> str:
    return hashlib.sha256("\n".join([prev, str(eid), deal_id, ts, actor, etype, payload]).encode("utf-8")).hexdigest()


def default_db() -> Path:
    return Path(os.environ.get("PTK_DEALS_DB") or "deals.db")


def default_actor() -> str:
    return os.environ.get("PTK_USER") or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


class Store:
    """The deals database. ``Store(path)`` creates it on first use."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_db()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=15, isolation_level=None, check_same_thread=False)
        self._lock = threading.RLock()  # the dashboard serves requests on several threads over one connection
        self._db.executescript(SCHEMA)

    @property
    def home(self) -> Path:
        """Folder beside the database where drafts, packages and the outbox go."""
        return self.path.resolve().parent

    def close(self) -> None:
        self._db.close()

    def append(self, deal_id: str, etype: str, payload: dict[str, Any] | Callable[[sqlite3.Cursor], dict[str, Any] | None],
               actor: str, ts: str | None = None) -> StoredEvent | None:
        """Append one event. ``payload`` may be a function of the open transaction's cursor.

        A function runs inside the write lock, so it can number things (``next_number``) or
        claim something exactly once; returning None appends nothing and returns None.
        """
        ts = ts or now_iso()
        with self._lock:
            cur = self._db.cursor()
            cur.execute("BEGIN IMMEDIATE")
            try:
                data = payload(cur) if callable(payload) else payload
                if data is None:
                    cur.execute("ROLLBACK")
                    return None
                body = canonical(data)
                row = cur.execute("SELECT id, hash FROM events ORDER BY id DESC LIMIT 1").fetchone()
                eid, prev = (row[0] + 1, row[1]) if row else (1, GENESIS)
                digest = _digest(prev, eid, deal_id, ts, actor, etype, body)
                cur.execute("INSERT INTO events (id, deal_id, ts, actor, type, payload, prev_hash, hash) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (eid, deal_id, ts, actor, etype, body, prev, digest))
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise
        return StoredEvent(eid, deal_id, ts, actor, etype, json.loads(body), prev, digest)

    @staticmethod
    def next_number(cur: sqlite3.Cursor, deal_id: str, etype: str) -> int:
        """1 + how many ``etype`` events the deal has, read inside the write transaction (T#, M#, OP# never collide)."""
        return cur.execute("SELECT COUNT(*) FROM events WHERE deal_id = ? AND type = ?", (deal_id, etype)).fetchone()[0] + 1

    @staticmethod
    def has_event(cur: sqlite3.Cursor, deal_id: str, etype: str, fragment: str) -> bool:
        """Does the deal already have an ``etype`` event whose canonical payload contains ``fragment``?"""
        return cur.execute("SELECT 1 FROM events WHERE deal_id = ? AND type = ? AND instr(payload, ?) > 0 LIMIT 1",
                           (deal_id, etype, fragment)).fetchone() is not None

    def events(self, deal_id: str | None = None) -> list[StoredEvent]:
        sql = "SELECT id, deal_id, ts, actor, type, payload, prev_hash, hash FROM events"
        args: tuple = ()
        if deal_id is not None:
            sql += " WHERE deal_id = ?"
            args = (deal_id,)
        with self._lock:
            rows = self._db.execute(sql + " ORDER BY id", args).fetchall()
        return [StoredEvent(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7]) for r in rows]

    def deal_ids(self) -> list[str]:
        """Every deal, oldest first. CRM records share the log as ``crm:`` streams and aren't deals."""
        with self._lock:
            rows = self._db.execute("SELECT deal_id, MIN(id) FROM events WHERE deal_id NOT LIKE 'crm:%' "
                                    "GROUP BY deal_id ORDER BY MIN(id)").fetchall()
        return [r[0] for r in rows]

    def stream_events(self, prefix: str) -> list[StoredEvent]:
        """Every event whose stream id starts with ``prefix`` (e.g. ``crm:``), in log order."""
        with self._lock:
            rows = self._db.execute("SELECT id, deal_id, ts, actor, type, payload, prev_hash, hash FROM events "
                                    "WHERE substr(deal_id, 1, ?) = ? ORDER BY id", (len(prefix), prefix)).fetchall()
        return [StoredEvent(r[0], r[1], r[2], r[3], r[4], json.loads(r[5]), r[6], r[7]) for r in rows]

    def exists(self, deal_id: str) -> bool:
        with self._lock:
            return self._db.execute("SELECT 1 FROM events WHERE deal_id = ? LIMIT 1", (deal_id,)).fetchone() is not None

    def verify(self) -> list[str]:
        """Recompute the hash chain. Returns a list of problems (empty when the log is intact)."""
        problems: list[str] = []
        prev = GENESIS
        expected_id = 1
        with self._lock:
            rows = self._db.execute("SELECT id, deal_id, ts, actor, type, payload, prev_hash, hash FROM events ORDER BY id").fetchall()
        for r in rows:
            eid, deal_id, ts, actor, etype, payload, prev_hash, digest = r
            if eid != expected_id:
                problems.append(f"event {eid}: expected id {expected_id}; events are missing")
            if prev_hash != prev:
                problems.append(f"event {eid}: previous-hash link is broken")
            if _digest(prev_hash, eid, deal_id, ts, actor, etype, payload) != digest:
                problems.append(f"event {eid}: contents don't match its hash (edited outside ptk)")
            prev, expected_id = digest, eid + 1
        return problems
