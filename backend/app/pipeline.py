"""One guest turn, end to end. Owns: STT, brain, ticket finalization (dedup + autotag),
broadcast, TTS, avatar. The brain decides; the pipeline enforces invariants from spec section 5."""

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from .schema import BrainResult, Cancellation, Escalation, Ticket, TicketDraft, TurnResponse
from .services.avatar import Avatar
from .services.brain import Brain
from .services.stt import STT
from .services.tts import TTS
from .store import store


logger = logging.getLogger(__name__)

DEDUP_WINDOW_SECONDS = 120

# Words to ignore when the cancellation falls back to token overlap, so common
# filler in a phrase like "the rice order" does not match unrelated tickets.
_CANCEL_STOPWORDS = {
    "the", "a", "an", "my", "our", "that", "this", "those", "these",
    "ticket", "request", "order", "please", "for", "on", "of", "to", "it",
}


class Pipeline:
    def __init__(self, stt: STT, brain: Brain, tts: TTS, avatar: Avatar) -> None:
        self.stt = stt
        self.brain = brain
        self.tts = tts
        self.avatar = avatar

    async def run_turn(
        self,
        room: str,
        text: str,
        guest_session_id: str,
        language: Optional[str] = None,
    ) -> TurnResponse:
        room_state = store.get_room(room)
        if room_state is None:
            return TurnResponse(
                reply=f"Unknown room '{room}'. Known rooms: {list(store.rooms.keys())}.",
                tickets=[],
                language="en",
            )

        transcript, detected = await self.stt.transcribe(text)
        # Effective reply language. An explicit override (the demo language
        # control, standing in for Scribe's detected-language tag on the typed
        # path) wins; then the real Scribe tag; then the room's standing
        # language. This drives the spoken reply and TTS voice only. Ticket
        # summaries stay English, enforced by the brain prompt and unaffected here.
        if language:
            effective_language = language
        elif detected != "und":
            effective_language = detected
        else:
            effective_language = room_state.language

        # Feed the brain the effective language without mutating stored room
        # state, so the reply text comes back in the right language per turn.
        brain_context = room_state.model_copy(update={"language": effective_language})
        result: BrainResult = await self.brain.decide(transcript, brain_context)

        finalized: list[Ticket] = []
        now = datetime.now(timezone.utc)
        standing_allergies = [
            tag for tag in room_state.standing_tags if tag.startswith("allergy:")
        ]

        for draft in result.tickets:
            tags = list(draft.tags)
            if draft.dept == "kitchen":
                for a in standing_allergies:
                    if a not in tags:
                        tags.append(a)

            existing = self._find_dup(room_state, draft, now)
            if existing is not None:
                if "repeat" not in existing.tags:
                    existing.tags.append("repeat")
                await store.broadcast({
                    "type": "ticket.updated",
                    "ticket": existing.model_dump(mode="json"),
                    "reason": "nudge",
                })
                continue

            ticket = Ticket(
                id=str(uuid.uuid4()),
                created_at=now,
                room=room,
                guest_session_id=guest_session_id,
                dept=draft.dept,
                summary=draft.summary,
                item=draft.item,
                qty=draft.qty,
                priority=draft.priority,
                tags=tags,
                status="open",
                source_utterance=text,
            )
            await store.append_ticket(ticket)
            await store.broadcast({
                "type": "ticket.created",
                "ticket": ticket.model_dump(mode="json"),
            })
            finalized.append(ticket)

        for esc in result.escalations:
            ticket = Ticket(
                id=str(uuid.uuid4()),
                created_at=now,
                room=room,
                guest_session_id=guest_session_id,
                dept="frontdesk",
                summary=f"Escalation: {esc.reason}",
                item=None,
                qty=None,
                priority="urgent",
                tags=["escalation"],
                status="open",
                source_utterance=text,
            )
            await store.append_ticket(ticket)
            await store.broadcast({
                "type": "ticket.created",
                "ticket": ticket.model_dump(mode="json"),
            })
            finalized.append(ticket)

        for cancel in result.cancellations:
            target = self._resolve_cancellation(room_state, cancel)
            if target is None:
                continue
            target.status = "cancelled"
            target.tags = list(target.tags) + (["cancelled"] if "cancelled" not in target.tags else [])
            await store.broadcast({
                "type": "ticket.updated",
                "ticket": target.model_dump(mode="json"),
                "reason": "cancelled",
            })

        audio = await self.tts.speak(result.reply, effective_language)
        await self.avatar.render(audio)

        return TurnResponse(reply=result.reply, tickets=finalized, language=effective_language)

    def _find_dup(self, room_state, draft: TicketDraft, now: datetime) -> Optional[Ticket]:
        for t in room_state.open_requests:
            if t.dept != draft.dept:
                continue
            if t.summary.lower() != draft.summary.lower():
                continue
            if t.status not in ("open", "ack"):
                continue
            age = (now - t.created_at).total_seconds()
            if age <= DEDUP_WINDOW_SECONDS:
                return t
        return None

    def _resolve_cancellation(self, room_state, cancel: Cancellation) -> Optional[Ticket]:
        """Resolve cancel.ticket_ref to an open ticket.

        Primary path: the brain is instructed to pass the exact ticket id from
        OPEN_REQUESTS, so we match by id first. Fallback: if the ref is not an
        exact id (the brain passed a phrase instead), fall back to token overlap
        against summary/item/dept, newest-first, and log a warning so we can see
        when the id path was skipped."""
        open_tickets = [
            t for t in room_state.open_requests if t.status in ("open", "ack")
        ]

        ref = cancel.ticket_ref.strip()
        for t in open_tickets:
            if t.id == ref:
                return t

        ref_tokens = self._content_tokens(ref)
        for t in reversed(open_tickets):
            hay = f"{t.summary} {t.item or ''} {t.dept}"
            if ref_tokens & self._content_tokens(hay):
                logger.warning(
                    "cancel_request fell back to token overlap: ref=%r matched "
                    "ticket id=%s summary=%r. The brain did not pass a ticket id.",
                    cancel.ticket_ref, t.id, t.summary,
                )
                return t
        return None

    @staticmethod
    def _content_tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+", text.lower())) - _CANCEL_STOPWORDS
