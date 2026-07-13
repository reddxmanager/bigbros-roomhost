"""In-memory state. Two suites, live ticket map, websocket subscribers, broadcast."""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

from fastapi import WebSocket

from . import db
from .schema import RoomState, Ticket


class Store:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomState] = {}
        self.tickets: dict[str, Ticket] = {}
        self.subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        db.init()
        self._seed_rooms()
        self._restore()

    def _restore(self) -> None:
        """Overlay persisted state onto the seeded rooms: guest profiles from
        the rooms table, then every still-active ticket back into its lane.
        A restart therefore resumes exactly where the last process stopped."""
        saved_rooms = db.load_rooms()
        for room_id, data in saved_rooms.items():
            r = self.rooms.get(room_id)
            if r is None:
                continue
            r.language = data["language"]
            r.standing_tags = data["standing_tags"]
            r.guest_name = data["guest_name"]
            r.notes = data["notes"]
            r.occupied = data["occupied"]
        for t in db.load_active_tickets():
            self.tickets[t.id] = t
            r = self.rooms.get(t.room)
            if r is not None:
                r.open_requests.append(t)

    def _seed_rooms(self) -> None:
        """Rooms start blank. Real guest context (name, allergies, occasion)
        arrives from KUYA via the guest sync. BIGBROS_DEMO_SEED=1 restores the
        hackathon personas for offline demos and mock-brain development."""
        demo = os.environ.get("BIGBROS_DEMO_SEED") == "1"
        self.rooms["4"] = RoomState(
            room="4",
            suite_name="Family Suite",
            language="ko" if demo else "en",
            standing_tags=["allergy:shellfish", "family:Kim"] if demo else [],
            open_requests=[],
        )
        self.rooms["honeymoon"] = RoomState(
            room="honeymoon",
            suite_name="Honeymoon Suite",
            language="en",
            standing_tags=["anniversary"] if demo else [],
            open_requests=[],
        )

    async def apply_guest_sync(self, suites: list[dict[str, Any]], derive_tags) -> dict[str, Any]:
        """Fold KUYA's current-guests payload into room state. Occupied suites
        get the guest's name, the raw special-requests note, and derived
        standing tags. Vacant suites are cleared so guest B never inherits
        guest A's context.

        Turnover cleanup: when a suite goes vacant, or the guest name changes
        (same-day turnover never reads vacant), the previous session's open
        tickets are auto-cancelled with a 'checkout' tag. They stay in the
        database as history; they just stop haunting the lanes, and they can
        no longer hijack the dedup for the next guest.

        Suites reported as unknown (calendar unreachable, hand-edited event
        that no longer parses) are skipped entirely: previous state holds,
        because wiping a live guest's allergy context over a parse failure is
        the worse error. Suite names map to rooms via kuya.py SUITE_TO_ROOM."""
        from .services.kuya import SUITE_TO_ROOM

        updated: list[str] = []
        cancelled = 0
        for entry in suites:
            room_id = SUITE_TO_ROOM.get(str(entry.get("room_type") or ""))
            if room_id is None:
                continue
            r = self.rooms.get(room_id)
            if r is None:
                continue
            if entry.get("unknown"):
                logger.warning(
                    "Guest sync: %s reported unknown (%s); keeping previous state",
                    room_id, entry.get("message") or "no detail",
                )
                continue
            occupied = bool(entry.get("occupied"))
            new_name = (entry.get("guest_name") or None) if occupied else None

            went_vacant = not occupied and (r.occupied or r.guest_name is not None)
            name_changed = (
                occupied
                and r.guest_name is not None
                and new_name is not None
                and new_name != r.guest_name
            )
            if went_vacant or name_changed:
                cancelled += await self._cancel_open_tickets(r, tag="checkout")

            r.occupied = occupied
            if occupied:
                r.guest_name = new_name
                r.notes = entry.get("special_requests") or None
                r.standing_tags = derive_tags(r.notes)
            else:
                r.guest_name = None
                r.notes = None
                r.standing_tags = []
            db.save_room(r)
            updated.append(room_id)
        return {"updated_rooms": updated, "cancelled_tickets": cancelled}

    async def _cancel_open_tickets(self, r: RoomState, tag: str) -> int:
        """Cancel every open or ack ticket for a room, tagging why. Used at
        turnover so the departed guest's requests close out visibly instead of
        sitting in a lane addressed to nobody."""
        count = 0
        now = datetime.now(timezone.utc)
        for t in list(r.open_requests):
            if t.status not in ("open", "ack"):
                continue
            t.status = "cancelled"
            t.done_at = now
            if tag not in t.tags:
                t.tags.append(tag)
            db.save_ticket(t)
            await self.broadcast({
                "type": "ticket.updated",
                "ticket": t.model_dump(mode="json"),
                "reason": "cancelled",
            })
            count += 1
        if count:
            logger.info("Turnover: cancelled %d open ticket(s) for room %s", count, r.room)
        return count

    def get_room(self, room: str) -> Optional[RoomState]:
        return self.rooms.get(room)

    async def append_ticket(self, t: Ticket) -> None:
        async with self._lock:
            self.tickets[t.id] = t
            r = self.rooms.get(t.room)
            if r is not None:
                r.open_requests.append(t)
        db.save_ticket(t)

    def persist_ticket(self, t: Ticket) -> None:
        """Write-through for callers that mutate a ticket in place (the dedup
        nudge bump and the cancellation path in the pipeline). Keeps the file
        mirroring the live board without those callers importing db."""
        db.save_ticket(t)

    async def set_status(self, ticket_id: str, status: str) -> Optional[Ticket]:
        """Flip a ticket's status in place. The same Ticket object lives in both
        self.tickets and the room's open_requests, so one mutation updates both.
        Returns the updated ticket, or None if the id is unknown."""
        async with self._lock:
            t = self.tickets.get(ticket_id)
            if t is None:
                return None
            t.status = status
            now = datetime.now(timezone.utc)
            if status == "ack" and t.ack_at is None:
                t.ack_at = now
            if status in ("done", "cancelled") and t.done_at is None:
                t.done_at = now
        db.save_ticket(t)
        return t

    async def push_stale(self, stale_seconds: int, repush_seconds: int) -> int:
        """Fire a still-waiting push for every ticket still 'open' (not yet even
        acknowledged) past stale_seconds, throttled so the same ticket re-pushes
        at most once per repush_seconds. This is the timer-driven half of a push:
        not a new staff action, just an alert that the previous action is stale.
        Returns how many tickets were pushed this pass."""
        now = datetime.now(timezone.utc)
        pushed = 0
        async with self._lock:
            stale: list[Ticket] = []
            for t in self.tickets.values():
                if t.status != "open":
                    continue
                if (now - t.created_at).total_seconds() < stale_seconds:
                    continue
                last = t.last_nudge_at or t.created_at
                if (now - last).total_seconds() < repush_seconds:
                    continue
                t.nudge_count += 1
                t.last_nudge_at = now
                if "repeat" not in t.tags:
                    t.tags.append("repeat")
                stale.append(t)
        for t in stale:
            db.save_ticket(t)
            await self.broadcast({
                "type": "ticket.updated",
                "ticket": t.model_dump(mode="json"),
                "reason": "push",
            })
            pushed += 1
        return pushed

    async def subscribe(self, ws: WebSocket) -> None:
        self.subscribers.add(ws)

    async def unsubscribe(self, ws: WebSocket) -> None:
        self.subscribers.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in list(self.subscribers):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.subscribers.discard(ws)


store = Store()
