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
    # Still-waiting "push" state. A push is never the guest poking staff directly:
    # it is fired by ATE when the guest re-asks about an outstanding ticket, or by
    # the staleness timer when an open ticket sits too long. nudge_count is how many
    # times the guest has chased this, last_nudge_at when the latest push fired.
    nudge_count: int = 0
    last_nudge_at: Optional[datetime] = None
    # Completion-speed tracking. ack_at stamps the first acknowledge; done_at
    # stamps terminal closure (done OR cancelled, one column on purpose, so
    # history can sort every closed ticket by one field). created->ack->done
    # is the metric managers actually manage.
    ack_at: Optional[datetime] = None
    done_at: Optional[datetime] = None


class TicketDraft(BaseModel):
    """What the brain emits. The pipeline fills id, created_at, source_utterance, status."""

    dept: Dept
    summary: str
    item: Optional[str] = None
    qty: Optional[int] = None
    priority: Priority = "normal"
    tags: list[str] = Field(default_factory=list)


class RoomState(BaseModel):
    """Per-room standing context plus the live list of open tickets for dedup.

    guest_name and notes arrive from KUYA's booking data via the guest sync
    (see services/kuya.py): notes is the raw special-requests text the guest
    typed at booking, passed to the brain verbatim; standing_tags carries the
    tags derived from it (allergy:x, occasion:y) that drive the autotag."""

    room: str
    suite_name: str
    language: str
    standing_tags: list[str] = Field(default_factory=list)
    open_requests: list[Ticket] = Field(default_factory=list)
    guest_name: Optional[str] = None
    notes: Optional[str] = None
    occupied: bool = True


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


class StatusUpdate(BaseModel):
    """Body for POST /tickets/{id}/status. Server-authoritative status flip."""
    status: Status


class TurnRequest(BaseModel):
    room: str
    text: str


class TurnResponse(BaseModel):
    reply: str
    tickets: list[Ticket]
    language: str
    # Cheap reactive-face signal derived from what the brain already decided
    # (no second model call): concern for problems/escalations, warm for happy
    # errands, neutral otherwise. Drives a subtle tablet tint. Defaults neutral
    # so a missing signal never breaks the reply.
    sentiment: str = "neutral"
