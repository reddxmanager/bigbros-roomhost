"""In-memory state. Two suites, live ticket map, websocket subscribers, broadcast."""

import asyncio
from typing import Optional

from fastapi import WebSocket

from .schema import RoomState, Ticket


class Store:
    def __init__(self) -> None:
        self.rooms: dict[str, RoomState] = {}
        self.tickets: dict[str, Ticket] = {}
        self.subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._seed_rooms()

    def _seed_rooms(self) -> None:
        self.rooms["4"] = RoomState(
            room="4",
            suite_name="Family Suite",
            language="ko",
            standing_tags=["allergy:shellfish", "family:Kim"],
            open_requests=[],
        )
        self.rooms["honeymoon"] = RoomState(
            room="honeymoon",
            suite_name="Honeymoon Suite",
            language="en",
            standing_tags=["anniversary"],
            open_requests=[],
        )

    def get_room(self, room: str) -> Optional[RoomState]:
        return self.rooms.get(room)

    async def append_ticket(self, t: Ticket) -> None:
        async with self._lock:
            self.tickets[t.id] = t
            r = self.rooms.get(t.room)
            if r is not None:
                r.open_requests.append(t)

    async def set_status(self, ticket_id: str, status: str) -> Optional[Ticket]:
        """Flip a ticket's status in place. The same Ticket object lives in both
        self.tickets and the room's open_requests, so one mutation updates both.
        Returns the updated ticket, or None if the id is unknown."""
        async with self._lock:
            t = self.tickets.get(ticket_id)
            if t is None:
                return None
            t.status = status
            return t

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
