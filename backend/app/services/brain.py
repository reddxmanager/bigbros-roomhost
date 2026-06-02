"""Brain interface plus implementations.

MockBrain: hardcoded canonical-sentence decomposition. Kept for offline development
and to isolate pipeline bugs from brain bugs.

ClaudeBrain: real Anthropic tool-calling brain. One Claude turn per guest turn,
five tools per spec section 5, system prompt from brain_prompt.SYSTEM_PROMPT
with prompt caching applied."""

import asyncio
import json
from typing import Any, Protocol

import anthropic

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
        return f"GUEST SAID: {text}\n\nCONTEXT:\n{json.dumps(ctx, indent=2)}"

    def _parse(self, response: anthropic.types.Message) -> BrainResult:
        tickets: list[TicketDraft] = []
        clarifications: list[Clarification] = []
        escalations: list[Escalation] = []
        cancellations: list[Cancellation] = []
        reply_parts: list[str] = []

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
                # Free text outside tool calls. The spec wants all guest-facing
                # text routed through answer_guest, but capture stray text as a
                # fallback so the avatar still has something to say.
                reply_parts.append(block.text.strip())

        return BrainResult(
            tickets=tickets,
            reply=" ".join(reply_parts),
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
