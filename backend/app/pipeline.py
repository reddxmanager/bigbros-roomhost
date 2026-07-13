"""One guest turn, end to end. Owns: STT, brain, ticket finalization (dedup + autotag),
broadcast, TTS, avatar. The brain decides; the pipeline enforces invariants from spec section 5."""

import asyncio
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from . import db
from .schema import BrainResult, Cancellation, Escalation, Ticket, TicketDraft, TurnResponse
from .services.avatar import Avatar
from .services.brain import Brain
from .services.stt import STT
from .services.tts import TTS
from .store import store


logger = logging.getLogger(__name__)

DEDUP_WINDOW_SECONDS = 120

# Quantity sanity cap. "500 beers" is a bored teenager, not an order. Anything
# above the cap is clamped and tagged verify-qty so staff see "12 (verify)"
# and confirm with the room instead of trusting the number.
MAX_SANE_QTY = 12

# What the avatar says when the brain fails twice in a row. Content-free and
# honest: no fake ticket confirmations for work that never happened.
FALLBACK_REPLY = {
    "en": "Sorry, I had a little trouble there. Could you say that again?",
    "ko": "죄송해요, 잠깐 문제가 있었어요. 다시 한 번 말씀해 주시겠어요?",
}

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

        # The brain call is the turn's single point of failure, so it gets one
        # retry and then a graceful spoken fallback. A guest hearing "could you
        # say that again" is a hiccup; a guest hearing nothing from a talking
        # face is a broken product.
        turn_started = time.monotonic()
        brain_error: Optional[str] = None
        try:
            result: BrainResult = await self.brain.decide(transcript, brain_context)
        except Exception as first_exc:
            logger.warning("Brain call failed, retrying once: %s", first_exc)
            await asyncio.sleep(0.5)
            try:
                result = await self.brain.decide(transcript, brain_context)
            except Exception as second_exc:
                logger.error("Brain call failed twice, using fallback: %s", second_exc)
                brain_error = f"{type(second_exc).__name__}: {second_exc}"
                result = BrainResult(
                    reply=FALLBACK_REPLY.get(effective_language, FALLBACK_REPLY["en"])
                )

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

            # Quantity sanity (invariant, so it lives here, not in the prompt).
            qty = draft.qty
            if qty is not None and qty > MAX_SANE_QTY:
                logger.warning(
                    "Clamping absurd qty %s -> %s for %r (room %s)",
                    qty, MAX_SANE_QTY, draft.summary, room,
                )
                qty = MAX_SANE_QTY
                if "verify-qty" not in tags:
                    tags.append("verify-qty")

            existing = self._find_dup(room_state, draft, now)
            if existing is not None:
                # The guest is chasing an outstanding request through ATE. This is
                # the only way a guest can fire a push: never directly at staff,
                # always mediated by the agent. Bump the still-waiting state and
                # broadcast reason="push" so the dashboard pulses and chimes.
                if "repeat" not in existing.tags:
                    existing.tags.append("repeat")
                existing.nudge_count += 1
                existing.last_nudge_at = now
                store.persist_ticket(existing)
                await store.broadcast({
                    "type": "ticket.updated",
                    "ticket": existing.model_dump(mode="json"),
                    "reason": "push",
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
                qty=qty,
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
            if target.done_at is None:
                target.done_at = now
            target.tags = list(target.tags) + (["cancelled"] if "cancelled" not in target.tags else [])
            store.persist_ticket(target)
            await store.broadcast({
                "type": "ticket.updated",
                "ticket": target.model_dump(mode="json"),
                "reason": "cancelled",
            })

        sentiment = self._derive_sentiment(result, finalized)

        audio = await self.tts.speak(result.reply, effective_language)
        await self.avatar.render(audio)

        # The turn log: audit trail, debugging record, and future daily digest.
        # last_usage exists on the real brain only; mocks simply log no tokens.
        latency_ms = int((time.monotonic() - turn_started) * 1000)
        usage = getattr(self.brain, "last_usage", None) or {}
        db.log_turn(
            room=room,
            utterance=text,
            reply=result.reply,
            language=effective_language,
            tickets=len(finalized),
            latency_ms=latency_ms,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            error=brain_error,
        )
        logger.info(
            "turn room=%s tickets=%d latency_ms=%d lang=%s%s",
            room, len(finalized), latency_ms, effective_language,
            " ERROR=" + brain_error if brain_error else "",
        )

        return TurnResponse(
            reply=result.reply,
            tickets=finalized,
            language=effective_language,
            sentiment=sentiment,
        )

    @staticmethod
    def _derive_sentiment(result: BrainResult, finalized: list[Ticket]) -> str:
        """A reactive-face signal read off the brain's already-decided output, no
        second model call. Concern wins over warm so a complaint inside a mixed
        sentence still reads as apology. Anything else stays neutral."""
        has_escalation = bool(result.escalations) or any(
            "escalation" in t.tags for t in finalized
        )
        depts = {t.dept for t in finalized}
        if has_escalation or "maintenance" in depts:
            return "concern"
        if depts & {"kitchen", "bar"}:
            return "warm"
        return "neutral"

    def _find_dup(self, room_state, draft: TicketDraft, now: datetime) -> Optional[Ticket]:
        """Match a new draft against an outstanding ticket for this room. Any
        open or ack ticket with the same dept and summary counts as the same
        request, with no time window: re-asking ten minutes later is still the
        guest chasing the same thing, so we push the existing ticket rather than
        duplicate it. Done and cancelled tickets never match. (now is unused now
        that the window is gone, kept for call-site stability.)"""
        for t in room_state.open_requests:
            if t.dept != draft.dept:
                continue
            if t.summary.lower() != draft.summary.lower():
                continue
            if t.status not in ("open", "ack"):
                continue
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
