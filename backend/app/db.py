"""SQLite persistence. One file, no server, stdlib only.

The in-memory Store stays the hot working set; this module is the
write-through layer beneath it. Every ticket create or mutation lands here
the moment it happens, and boot restores active work (open and ack tickets)
plus per-room guest context, so a server restart never eats a guest request.

Done and cancelled tickets stay in the file as history for future reporting;
they are simply not reloaded into the live lanes.

Path comes from BIGBROS_DB_PATH (default backend/data/bigbros.db). On a host
with an ephemeral filesystem (Render free tier), point this at a mounted
persistent disk, e.g. /data/bigbros.db. See SETUP.md.
"""

import json
import logging
import os
import sqlite3
import threading
from typing import Any, Optional

from .schema import RoomState, Ticket

logger = logging.getLogger(__name__)

_DEFAULT_PATH = os.path.join("data", "bigbros.db")

_conn: Optional[sqlite3.Connection] = None
# sqlite3 objects are not thread-safe by default; FastAPI may touch this from
# worker threads. One process-wide lock is plenty at resort scale.
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    room TEXT NOT NULL,
    guest_session_id TEXT NOT NULL,
    dept TEXT NOT NULL,
    summary TEXT NOT NULL,
    item TEXT,
    qty INTEGER,
    priority TEXT NOT NULL,
    tags TEXT NOT NULL,
    status TEXT NOT NULL,
    source_utterance TEXT NOT NULL,
    nudge_count INTEGER NOT NULL DEFAULT 0,
    last_nudge_at TEXT,
    ack_at TEXT,
    done_at TEXT
);
CREATE TABLE IF NOT EXISTS rooms (
    room TEXT PRIMARY KEY,
    suite_name TEXT NOT NULL,
    language TEXT NOT NULL,
    standing_tags TEXT NOT NULL,
    guest_name TEXT,
    notes TEXT,
    occupied INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    room TEXT NOT NULL,
    utterance TEXT NOT NULL,
    reply TEXT NOT NULL,
    language TEXT NOT NULL,
    tickets INTEGER NOT NULL,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    error TEXT
);
"""


def init() -> None:
    """Open (and create if needed) the database. Called once at import time
    by the store. Failure logs loudly and leaves persistence off rather than
    blocking boot: a broken disk should degrade to the old in-memory behavior,
    not take the whole agent down."""
    global _conn
    path = os.environ.get("BIGBROS_DB_PATH", _DEFAULT_PATH)
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.executescript(_SCHEMA)
        # Migrations for databases created before a column existed. ALTER is
        # a no-op error when the column is already there; that is fine.
        for col in ("ack_at", "done_at"):
            try:
                _conn.execute(f"ALTER TABLE tickets ADD COLUMN {col} TEXT")
            except sqlite3.OperationalError:
                pass
        _conn.commit()
        logger.info("Persistence on: %s", path)
    except Exception as exc:
        _conn = None
        logger.error(
            "Persistence DISABLED, running in-memory only (restarts will lose "
            "tickets). Could not open %s: %s", path, exc,
        )


def enabled() -> bool:
    return _conn is not None


def save_ticket(t: Ticket) -> None:
    """Insert or update one ticket. Called on create and on every mutation
    (status flip, nudge bump, cancellation), so the file always mirrors the
    live board."""
    if _conn is None:
        return
    row = (
        t.id,
        t.created_at.isoformat(),
        t.room,
        t.guest_session_id,
        t.dept,
        t.summary,
        t.item,
        t.qty,
        t.priority,
        json.dumps(t.tags),
        t.status,
        t.source_utterance,
        t.nudge_count,
        t.last_nudge_at.isoformat() if t.last_nudge_at else None,
        t.ack_at.isoformat() if t.ack_at else None,
        t.done_at.isoformat() if t.done_at else None,
    )
    try:
        with _lock:
            _conn.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET tags=excluded.tags, "
                "status=excluded.status, nudge_count=excluded.nudge_count, "
                "last_nudge_at=excluded.last_nudge_at, priority=excluded.priority, "
                "ack_at=excluded.ack_at, done_at=excluded.done_at",
                row,
            )
            _conn.commit()
    except Exception as exc:
        logger.error("Failed to persist ticket %s: %s", t.id, exc)


def save_room(r: RoomState) -> None:
    """Persist a room's standing context (guest name, notes, tags). Open
    tickets are persisted separately; this is only the per-guest profile the
    KUYA sync maintains."""
    if _conn is None:
        return
    try:
        with _lock:
            _conn.execute(
                "INSERT INTO rooms VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(room) DO UPDATE SET suite_name=excluded.suite_name, "
                "language=excluded.language, standing_tags=excluded.standing_tags, "
                "guest_name=excluded.guest_name, notes=excluded.notes, "
                "occupied=excluded.occupied",
                (
                    r.room,
                    r.suite_name,
                    r.language,
                    json.dumps(r.standing_tags),
                    r.guest_name,
                    r.notes,
                    1 if r.occupied else 0,
                ),
            )
            _conn.commit()
    except Exception as exc:
        logger.error("Failed to persist room %s: %s", r.room, exc)


def load_rooms() -> dict[str, dict[str, Any]]:
    """Room profiles by room id, for overlaying onto the seeded rooms at boot."""
    if _conn is None:
        return {}
    try:
        with _lock:
            rows = _conn.execute(
                "SELECT room, suite_name, language, standing_tags, guest_name, "
                "notes, occupied FROM rooms"
            ).fetchall()
    except Exception as exc:
        logger.error("Failed to load rooms: %s", exc)
        return {}
    out: dict[str, dict[str, Any]] = {}
    for room, suite_name, language, tags, guest_name, notes, occupied in rows:
        out[room] = {
            "suite_name": suite_name,
            "language": language,
            "standing_tags": json.loads(tags),
            "guest_name": guest_name,
            "notes": notes,
            "occupied": bool(occupied),
        }
    return out


def log_turn(
    room: str,
    utterance: str,
    reply: str,
    language: str,
    tickets: int,
    latency_ms: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """One row per guest turn: what was said, what was answered, how many
    tickets landed, how long the turn took, what it cost in tokens, and the
    error if the brain fell over. This is the audit trail, the debugging
    record, and the raw material for a daily digest, all in one table."""
    if _conn is None:
        return
    from datetime import datetime, timezone

    try:
        with _lock:
            _conn.execute(
                "INSERT INTO turns (at, room, utterance, reply, language, "
                "tickets, latency_ms, input_tokens, output_tokens, error) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    datetime.now(timezone.utc).isoformat(),
                    room,
                    utterance,
                    reply,
                    language,
                    tickets,
                    latency_ms,
                    input_tokens,
                    output_tokens,
                    error,
                ),
            )
            _conn.commit()
    except Exception as exc:
        logger.error("Failed to log turn: %s", exc)


def load_active_tickets() -> list[Ticket]:
    """Open and ack tickets, oldest first, for restoring the live lanes at
    boot. Done and cancelled stay in the file as history only."""
    return _load_tickets("WHERE status IN ('open','ack') ORDER BY created_at")


def load_history(limit: int = 50) -> list[Ticket]:
    """Recently closed tickets (done or cancelled), newest first, for the
    manager's history view. done_at stamps both terminal states, so one sort
    key covers everything."""
    return _load_tickets(
        "WHERE status IN ('done','cancelled') "
        "ORDER BY COALESCE(done_at, created_at) DESC LIMIT ?",
        (limit,),
    )


def _load_tickets(clause: str, params: tuple = ()) -> list[Ticket]:
    if _conn is None:
        return []
    try:
        with _lock:
            rows = _conn.execute(
                "SELECT id, created_at, room, guest_session_id, dept, summary, "
                "item, qty, priority, tags, status, source_utterance, "
                "nudge_count, last_nudge_at, ack_at, done_at FROM tickets "
                + clause,
                params,
            ).fetchall()
    except Exception as exc:
        logger.error("Failed to load tickets: %s", exc)
        return []
    tickets: list[Ticket] = []
    for row in rows:
        try:
            tickets.append(
                Ticket(
                    id=row[0],
                    created_at=row[1],
                    room=row[2],
                    guest_session_id=row[3],
                    dept=row[4],
                    summary=row[5],
                    item=row[6],
                    qty=row[7],
                    priority=row[8],
                    tags=json.loads(row[9]),
                    status=row[10],
                    source_utterance=row[11],
                    nudge_count=row[12],
                    last_nudge_at=row[13],
                    ack_at=row[14],
                    done_at=row[15],
                )
            )
        except Exception as exc:
            logger.error("Skipping corrupt ticket row %s: %s", row[0], exc)
    return tickets
