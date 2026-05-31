"""Brain interface. Step 1 is MockBrain with hardcoded canonical-sentence decomposition.
Step 2 replaces this with ClaudeBrain emitting real tool calls.

Keeping the interface tiny so the swap is one constructor line in main.py."""

from typing import Protocol

from ..schema import BrainResult, RoomState, TicketDraft


class Brain(Protocol):
    async def decide(self, text: str, context: RoomState) -> BrainResult:
        ...


class MockBrain:
    """Recognizes the canonical demo sentence by substring match and produces three drafts.
    Loose matching is deliberate so light variations of the canonical sentence still demo.

    This is the green-checkpoint stand-in. It will be deleted, not extended, in Step 2."""

    async def decide(self, text: str, context: RoomState) -> BrainResult:
        t = text.lower()

        wants_ac = ("aircon" in t) or ("air con" in t) or (" ac " in f" {t} ") or ("a/c" in t)
        wants_drink = ("san mig" in t) or ("san miguel" in t) or ("beer" in t)
        wants_rice = "rice" in t
        asks_breakfast = "breakfast" in t

        drafts: list[TicketDraft] = []
        reply_parts: list[str] = []

        if wants_ac:
            drafts.append(TicketDraft(
                dept="maintenance",
                summary="AC not cooling",
                priority="high",
            ))
            reply_parts.append("Got it on the AC, someone is on the way right away.")

        if wants_drink:
            drafts.append(TicketDraft(
                dept="bar",
                summary="San Miguel",
                item="San Miguel",
                qty=2,
                priority="normal",
            ))
            reply_parts.append("Two San Migs coming up.")

        if wants_rice:
            drafts.append(TicketDraft(
                dept="kitchen",
                summary="Extra rice",
                item="rice",
                qty=1,
                priority="normal",
            ))
            reply_parts.append("Extra rice on the way.")

        if asks_breakfast:
            reply_parts.append("Breakfast is from 7 to 10 in the main hall.")

        if not drafts and not reply_parts:
            reply_parts.append("Sorry, I did not catch that. Could you say it again?")

        return BrainResult(tickets=drafts, reply=" ".join(reply_parts))
