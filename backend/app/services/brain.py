"""Brain interface plus implementations.

MockBrain: hardcoded canonical-sentence decomposition. Kept for offline development
and to isolate pipeline bugs from brain bugs.

ClaudeBrain: real Anthropic tool-calling brain. One Claude turn per guest turn,
five tools per spec section 5, system prompt from brain_prompt.SYSTEM_PROMPT
with prompt caching applied."""

import asyncio
import json
import logging
from typing import Any, Protocol

import anthropic

logger = logging.getLogger(__name__)

from ..schema import (
    BrainResult,
    Cancellation,
    Clarification,
    Escalation,
    RoomState,
    TicketDraft,
)
from .brain_prompt import SYSTEM_PROMPT, TOOLS


class Brain(Protocol):
    async def decide(self, text: str, context: RoomState) -> BrainResult:
        ...


class MockBrain:
    """Hardcoded canonical-sentence stand-in. Used in Step 1 for the green checkpoint.
    Kept available so the pipeline can be exercised without an API key."""

    async def decide(self, text: str, context: RoomState) -> BrainResult:
        t = text.lower()

        wants_ac = ("aircon" in t) or ("air con" in t) or (" ac " in f" {t} ") or ("a/c" in t)
        wants_drink = ("san mig" in t) or ("san miguel" in t) or ("beer" in t)
        wants_rice = "rice" in t
        asks_breakfast = "breakfast" in t

        drafts: list[TicketDraft] = []
        reply_parts: list[str] = []

        if wants_ac:
            drafts.append(TicketDraft(dept="maintenance", summary="AC not cooling", priority="high"))
            reply_parts.append("Got it on the AC, someone is on the way right away.")
        if wants_drink:
            drafts.append(TicketDraft(dept="bar", summary="San Miguel", item="San Miguel", qty=2, priority="normal"))
            reply_parts.append("Two San Migs coming up.")
        if wants_rice:
            drafts.append(TicketDraft(dept="kitchen", summary="Extra rice", item="rice", qty=1, priority="normal"))
            reply_parts.append("Extra rice on the way.")
        if asks_breakfast:
            reply_parts.append("Breakfast is from 7 to 10 in the main hall.")

        if not drafts and not reply_parts:
            reply_parts.append("Sorry, I did not catch that. Could you say it again?")

        return BrainResult(tickets=drafts, reply=" ".join(reply_parts))


class ClaudeBrain:
    """Real brain: one Claude tool-calling turn per guest turn.

    The Anthropic SDK is synchronous, so each call is dispatched to a thread
    via asyncio.to_thread to keep the FastAPI event loop free.

    Construction validates that anthropic.Anthropic() can be instantiated.
    The API key check itself happens in main.py before construction so we can
    fail with a clear message naming backend/.env."""

    DEFAULT_MODEL = "claude-opus-4-7"
    MAX_TOKENS = 2048

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._client = anthropic.Anthropic()
        self._model = model
        self._last_usage: dict[str, int] | None = None

    @property
    def last_usage(self) -> dict[str, int] | None:
        """Token + cache stats from the most recent decide() call. For diagnostics."""
        return self._last_usage

    async def decide(self, text: str, context: RoomState) -> BrainResult:
        user_block = self._render_context(text, context)

        response = await asyncio.to_thread(
            self._client.messages.create,
            model=self._model,
            max_tokens=self.MAX_TOKENS,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            tools=TOOLS,
            messages=[{"role": "user", "content": user_block}],
        )

        self._last_usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
        }

        return self._parse(response)

    def _render_context(self, text: str, context: RoomState) -> str:
        open_reqs = [
            {
                "id": t.id,
                "dept": t.dept,
                "summary": t.summary,
                "status": t.status,
                "created_at": t.created_at.isoformat(),
            }
            for t in context.open_requests
            if t.status in ("open", "ack")
        ]
        ctx = {
            "room": context.room,
            "suite_name": context.suite_name,
            "language": context.language,
            "standing_tags": context.standing_tags,
            "open_requests": open_reqs,
        }
        # Booking context from KUYA, when the guest sync has run. The raw
        # special-requests note goes in verbatim: the keyword-derived tags
        # cover the kitchen autotag, but the note carries everything else
        # ("ground floor please", "surprise cake at 8") that no keyword
        # list catches.
        if context.guest_name:
            ctx["guest_name"] = context.guest_name
        if context.notes:
            ctx["booking_notes"] = context.notes
        return f"GUEST SAID: {text}\n\nCONTEXT:\n{json.dumps(ctx, indent=2)}"

    def _parse(self, response: anthropic.types.Message) -> BrainResult:
        tickets: list[TicketDraft] = []
        clarifications: list[Clarification] = []
        escalations: list[Escalation] = []
        cancellations: list[Cancellation] = []
        # The spoken reply is built ONLY from guest-facing tool outputs
        # (answer_guest / request_clarification.question / escalate.guest_message
        # / cancel_request.guest_message), collected here by _dispatch_tool.
        reply_parts: list[str] = []
        # Raw text the model emits outside any tool call. This is NOT guest-facing
        # speech: it is usually the model narrating its reasoning ("the context
        # says their language is...", "I'll respond in ... per the rule"). Kept
        # separate so it can never be concatenated into the spoken reply alongside
        # a real tool reply. Used only as a last-resort fallback below.
        stray_text: list[str] = []

        for block in response.content:
            if block.type == "tool_use":
                self._dispatch_tool(
                    block.name,
                    block.input,
                    tickets=tickets,
                    clarifications=clarifications,
                    escalations=escalations,
                    cancellations=cancellations,
                    reply_parts=reply_parts,
                )
            elif block.type == "text" and block.text.strip():
                stray_text.append(block.text.strip())

        reply = " ".join(reply_parts)
        # Fallback only: if no guest-facing tool produced any reply, fall back to
        # the stray text so the avatar still has something to say. Never merge the
        # two: that is what leaked the model's reasoning into the spoken reply.
        if not reply and stray_text:
            reply = " ".join(stray_text)

        return BrainResult(
            tickets=tickets,
            reply=reply,
            clarifications=clarifications,
            escalations=escalations,
            cancellations=cancellations,
        )

    def _dispatch_tool(
        self,
        name: str,
        args: dict[str, Any],
        *,
        tickets: list[TicketDraft],
        clarifications: list[Clarification],
        escalations: list[Escalation],
        cancellations: list[Cancellation],
        reply_parts: list[str],
    ) -> None:
        # Each arm guards its own parse: one malformed tool call from the model
        # must never 500 the whole turn. A skipped tool is logged and the rest
        # of the turn (other tickets, the spoken reply) still lands.
        try:
            if name == "create_ticket":
                tickets.append(TicketDraft(**args))
            elif name == "answer_guest":
                reply_parts.append(args["text"])
            elif name == "request_clarification":
                clarifications.append(Clarification(**args))
                reply_parts.append(args["question"])
            elif name == "escalate":
                escalations.append(Escalation(**args))
                reply_parts.append(args["guest_message"])
            elif name == "cancel_request":
                cancellations.append(Cancellation(**args))
                reply_parts.append(args["guest_message"])
            # Unknown tool name: silently ignore. The schema constrains the model.
        except Exception as exc:
            logger.error(
                "Skipping malformed %s tool call args=%r: %s", name, args, exc
            )
