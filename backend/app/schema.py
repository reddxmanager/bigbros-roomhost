"""Pydantic models. The Ticket shape is the source of truth across backend and frontend."""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


Dept = Literal["kitchen", "bar", "housekeeping", "maintenance", "frontdesk"]
Priority = Literal["urgent", "high", "normal"]
Status = Literal["open", "ack", "done", "cancelled"]


class Ticket(BaseModel):
    """Spec section 6. Uniform across all departments. source_utterance is the audit trail."""

    id: str
    created_at: datetime
    room: str
    guest_session_id: str
    dept: Dept
    summary: str
    item: Optional[str] = None
    qty: Optional[int] = None
    priority: Priority
    tags: list[str] = Field(default_factory=list)
    status: Status = "open"
    source_utterance: str


class TicketDraft(BaseModel):
    """What the brain emits. The pipeline fills id, created_at, source_utterance, status."""

    dept: Dept
    summary: str
    item: Optional[str] = None
    qty: Optional[int] = None
    priority: Priority = "normal"
    tags: list[str] = Field(default_factory=list)


class RoomState(BaseModel):
    """Per-room standing context plus the live list of open tickets for dedup."""

    room: str
    suite_name: str
    language: str
    standing_tags: list[str] = Field(default_factory=list)
    open_requests: list[Ticket] = Field(default_factory=list)


class Clarification(BaseModel):
    """request_clarification tool output: ask the guest one focused question."""
    field: str
    question: str


class Escalation(BaseModel):
    """escalate tool output: route to a human manager. Never auto-promises a fix."""
    reason: str
    guest_message: str


class Cancellation(BaseModel):
    """cancel_request tool output: cancel a previously created ticket for this room."""
    ticket_ref: str
    guest_message: str


class BrainResult(BaseModel):
    """One Claude turn's output, decomposed by tool. The pipeline applies these
    against the store: tickets become Ticket rows (with dedup + allergy autotag),
    escalations become urgent frontdesk tickets, cancellations mark matched
    tickets as status=cancelled. reply is the spoken text for the avatar."""

    tickets: list[TicketDraft] = Field(default_factory=list)
    reply: str = ""
    clarifications: list[Clarification] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    cancellations: list[Cancellation] = Field(default_factory=list)


class TurnRequest(BaseModel):
    room: str
    text: str


class TurnResponse(BaseModel):
    reply: str
    tickets: list[Ticket]
    language: str
