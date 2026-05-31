"""One guest turn, end to end. Owns: STT, brain, ticket finalization (dedup + autotag),
broadcast, TTS, avatar. The brain decides; the pipeline enforces invariants from spec section 5."""

import uuid
from datetime import datetime, timezone
from typing import Optional

from .schema import BrainResult, Ticket, TicketDraft, TurnResponse
from .services.avatar import Avatar
from .services.brain import Brain
from .services.stt import STT
from .services.tts import TTS
from .store import store


DEDUP_WINDOW_SECONDS = 120


class Pipeline:
    def __init__(self, stt: STT, brain: Brain, tts: TTS, avatar: Avatar) -> None:
        self.stt = stt
        self.brain = brain
        self.tts = tts
        self.avatar = avatar

    async def run_turn(self, room: str, text: str, guest_session_id: str) -> TurnResponse:
        room_state = store.get_room(room)
        if room_state is None:
            return TurnResponse(
                reply=f"Unknown room '{room}'. Known rooms: {list(store.rooms.keys())}.",
                tickets=[],
                language="en",
            )

        transcript, language = await self.stt.transcribe(text)
        if language == "und":
            language = room_state.language

        result: BrainResult = await self.brain.decide(transcript, room_state)

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

        audio = await self.tts.speak(result.reply, language)
        await self.avatar.render(audio)

        return TurnResponse(reply=result.reply, tickets=finalized, language=language)

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
