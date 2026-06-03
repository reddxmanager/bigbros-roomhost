"""Five-case test harness for the real Claude brain (spec section 5).

Exercises the brain through the full pipeline so dedup, escalation routing,
and cancellation handling are all included in each case's outcome.

Each case prints:
  - the guest sentence
  - the raw tool calls the brain emitted (name + input)
  - the final spoken reply
  - the resulting pipeline state (new tickets, nudges, cancellations)
  - PASS / FAIL against the spec expectation

Exit code: 0 if all five pass, 1 otherwise."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Room 4 is Korean-speaking, so guest-facing replies come back as Korean.
# Force UTF-8 on stdout/stderr so the Windows cp1252 default doesn't crash.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# Make backend/ importable when running this script directly.
BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from dotenv import load_dotenv

load_dotenv(BACKEND_DIR / ".env")

if not os.environ.get("ANTHROPIC_API_KEY"):
    print(
        "ANTHROPIC_API_KEY is not set. "
        "Set ANTHROPIC_API_KEY in backend/.env (gitignored, see backend/.env.example) "
        "and re-run.",
        file=sys.stderr,
    )
    sys.exit(2)

from app.pipeline import Pipeline
from app.services.avatar import MockAvatar
from app.services.brain import ClaudeBrain
from app.services.stt import MockSTT
from app.services.tts import MockTTS
from app.store import store


CANONICAL = (
    "Hey, the aircon in our room's not really cooling, and can we get two more "
    "San Migs and some extra rice? Oh, what time's breakfast?"
)


@dataclass
class TurnTrace:
    """What changed in the store across one turn."""

    text: str
    reply: str
    new_tickets: list[dict] = field(default_factory=list)
    nudged_tickets: list[dict] = field(default_factory=list)
    cancelled_tickets: list[dict] = field(default_factory=list)
    tool_calls_raw: list[dict] = field(default_factory=list)


def snapshot_room(room: str) -> dict[str, dict]:
    r = store.get_room(room)
    return {t.id: t.model_dump() for t in (r.open_requests if r else [])}


def diff_state(before: dict[str, dict], room: str) -> tuple[list[dict], list[dict], list[dict]]:
    after = snapshot_room(room)
    new = [t for tid, t in after.items() if tid not in before]
    nudged = [
        t for tid, t in after.items()
        if tid in before
        and "repeat" in t["tags"]
        and "repeat" not in before[tid]["tags"]
    ]
    cancelled = [
        t for tid, t in after.items()
        if tid in before
        and t["status"] == "cancelled"
        and before[tid]["status"] != "cancelled"
    ]
    return new, nudged, cancelled


async def run_turn(pipeline: Pipeline, brain: ClaudeBrain, room: str, text: str) -> TurnTrace:
    before = snapshot_room(room)

    room_state = store.get_room(room)
    brain_result = await brain.decide(text, room_state)

    response = await pipeline.run_turn(room, text, guest_session_id=f"session-{room}")
    new, nudged, cancelled = diff_state(before, room)

    raw = []
    for d in brain_result.tickets:
        raw.append({"tool": "create_ticket", "input": d.model_dump(exclude_none=True)})
    for c in brain_result.clarifications:
        raw.append({"tool": "request_clarification", "input": c.model_dump()})
    for e in brain_result.escalations:
        raw.append({"tool": "escalate", "input": e.model_dump()})
    for x in brain_result.cancellations:
        raw.append({"tool": "cancel_request", "input": x.model_dump()})
    if brain_result.reply:
        raw.append({"tool": "answer_guest", "input": {"text": brain_result.reply}})

    return TurnTrace(
        text=text,
        reply=response.reply,
        new_tickets=[t.model_dump(mode="json") for t in response.tickets],
        nudged_tickets=nudged,
        cancelled_tickets=cancelled,
        tool_calls_raw=raw,
    )


def reset_room(room: str) -> None:
    r = store.get_room(room)
    if r is not None:
        r.open_requests.clear()
    store.tickets.clear()


def print_trace(trace: TurnTrace) -> None:
    print(f'  guest: "{trace.text}"')
    print(f"  tool calls:")
    for c in trace.tool_calls_raw:
        print(f"    - {c['tool']}({json.dumps(c['input'], ensure_ascii=False)})")
    print(f'  reply: "{trace.reply}"')
    if trace.new_tickets:
        print("  new tickets:")
        for t in trace.new_tickets:
            tags = f" tags={t['tags']}" if t['tags'] else ""
            print(f"    - {t['dept']:12} {t['summary']:30} priority={t['priority']}{tags}")
    if trace.nudged_tickets:
        print("  nudged (dedup):")
        for t in trace.nudged_tickets:
            print(f"    - {t['dept']:12} {t['summary']:30}  -> repeat tag added")
    if trace.cancelled_tickets:
        print("  cancelled:")
        for t in trace.cancelled_tickets:
            print(f"    - {t['dept']:12} {t['summary']:30}  -> status=cancelled")


# -----------------------------------------------------------------------------
# Test cases
# -----------------------------------------------------------------------------

async def case_1_canonical(pipeline, brain) -> tuple[bool, list[str]]:
    """Multi-intent split: maintenance + bar + kitchen, breakfast in reply, shellfish tag."""
    reset_room("4")
    trace = await run_turn(pipeline, brain, "4", CANONICAL)
    print_trace(trace)

    depts = {t["dept"] for t in trace.new_tickets}
    fails = []
    if "maintenance" not in depts:
        fails.append("missing maintenance ticket")
    if "bar" not in depts:
        fails.append("missing bar ticket")
    if "kitchen" not in depts:
        fails.append("missing kitchen ticket")
    kitchen = [t for t in trace.new_tickets if t["dept"] == "kitchen"]
    if kitchen and "allergy:shellfish" not in kitchen[0]["tags"]:
        fails.append("kitchen ticket missing allergy:shellfish tag")
    # Breakfast must be answered in place. Room 4 replies in Korean, so accept
    # the breakfast word in either language, and require the grounded 6 AM start
    # from RESORT FACTS. The old 7-10 hallucination would now fail this, on
    # purpose: this assertion guards the grounding, it does not assume English.
    reply = trace.reply
    breakfast_mentioned = any(w in reply.lower() for w in ("breakfast",)) or any(
        w in reply for w in ("조식", "아침")
    )
    breakfast_grounded = "6" in reply
    if not (breakfast_mentioned and breakfast_grounded):
        fails.append("breakfast not answered with the grounded 6 to 10 from RESORT FACTS")
    return (not fails, fails)


async def case_2_ambiguous(pipeline, brain) -> tuple[bool, list[str]]:
    """Unclear department: expect clarification or frontdesk triage, not a guess."""
    reset_room("4")
    trace = await run_turn(pipeline, brain, "4", "The thing by the bed is broken.")
    print_trace(trace)

    fails = []
    used_clarification = any(c["tool"] == "request_clarification" for c in trace.tool_calls_raw)
    used_frontdesk = any(t["dept"] == "frontdesk" for t in trace.new_tickets)
    guessed_other = any(
        t["dept"] in ("housekeeping", "maintenance") and "thing" in t["source_utterance"].lower()
        for t in trace.new_tickets
    )
    if guessed_other:
        fails.append("guessed into a department instead of clarifying or routing to frontdesk")
    if not (used_clarification or used_frontdesk):
        fails.append("neither clarification nor frontdesk triage was used")
    return (not fails, fails)


async def case_3_complaint(pipeline, brain) -> tuple[bool, list[str]]:
    """Complaint + refund demand: expect escalate, no auto-promised fix."""
    reset_room("4")
    trace = await run_turn(pipeline, brain, "4", "This room is filthy and I want a refund.")
    print_trace(trace)

    fails = []
    escalations = [c for c in trace.tool_calls_raw if c["tool"] == "escalate"]
    if not escalations:
        fails.append("no escalate tool call emitted")
    else:
        msg = escalations[0]["input"].get("guest_message", "").lower()
        for forbidden in ["refund", "free night", "discount", "complimentary", "compensation"]:
            if forbidden in msg:
                fails.append(f"escalation guest_message promises '{forbidden}'")
                break
    return (not fails, fails)


async def case_4_cancel(pipeline, brain) -> tuple[bool, list[str]]:
    """Cancel after canonical: expect cancel_request matching the rice ticket."""
    reset_room("4")
    print("  --- setup turn: canonical sentence ---")
    setup = await run_turn(pipeline, brain, "4", CANONICAL)
    print_trace(setup)
    print("  --- cancel turn ---")
    trace = await run_turn(pipeline, brain, "4", "Actually, never mind the rice.")
    print_trace(trace)

    fails = []
    cancels = [c for c in trace.tool_calls_raw if c["tool"] == "cancel_request"]
    if not cancels:
        fails.append("no cancel_request emitted")
    if not trace.cancelled_tickets:
        fails.append("no ticket was actually cancelled in the store")
    else:
        cancelled_depts = {t["dept"] for t in trace.cancelled_tickets}
        if "kitchen" not in cancelled_depts:
            fails.append("cancelled ticket was not the kitchen rice ticket")
    return (not fails, fails)


async def case_5_repeat(pipeline, brain) -> tuple[bool, list[str]]:
    """Repeat within dedup window: expect nudge, not a new ticket."""
    reset_room("4")
    print("  --- setup turn: canonical sentence ---")
    setup = await run_turn(pipeline, brain, "4", CANONICAL)
    print_trace(setup)
    print("  --- repeat turn (within dedup window) ---")
    trace = await run_turn(pipeline, brain, "4", "Could we get more rice?")
    print_trace(trace)

    fails = []
    new_kitchen = [t for t in trace.new_tickets if t["dept"] == "kitchen"]
    if new_kitchen:
        fails.append(f"second rice request created a NEW kitchen ticket: {new_kitchen[0]['summary']}")
    nudged_or_answered = (
        any(t["dept"] == "kitchen" for t in trace.nudged_tickets)
        or any(c["tool"] == "answer_guest" for c in trace.tool_calls_raw)
        and not any(c["tool"] == "create_ticket" and c["input"].get("dept") == "kitchen" for c in trace.tool_calls_raw)
    )
    if not nudged_or_answered:
        fails.append("repeat was neither nudged by pipeline nor answered-in-place by brain")
    return (not fails, fails)


CASES: list[tuple[str, Callable]] = [
    ("1. Canonical multi-intent (maintenance + bar + kitchen + breakfast)", case_1_canonical),
    ("2. Ambiguous: 'the thing by the bed is broken'", case_2_ambiguous),
    ("3. Complaint with refund demand", case_3_complaint),
    ("4. Cancel: 'never mind the rice' after canonical", case_4_cancel),
    ("5. Repeat: 'more rice' within dedup window", case_5_repeat),
]


async def main() -> int:
    print(f"Model: {ClaudeBrain.DEFAULT_MODEL}")
    print()

    brain = ClaudeBrain()
    pipeline = Pipeline(MockSTT(), brain, MockTTS(), MockAvatar())

    results: list[tuple[str, bool, list[str]]] = []

    for label, case in CASES:
        print("=" * 80)
        print(f"CASE {label}")
        print("=" * 80)
        try:
            ok, fails = await case(pipeline, brain)
        except Exception as e:
            print(f"  EXCEPTION: {e!r}")
            ok, fails = False, [f"exception: {e!r}"]
        results.append((label, ok, fails))
        if brain.last_usage:
            u = brain.last_usage
            print(
                f"  usage: in={u['input_tokens']} out={u['output_tokens']} "
                f"cache_write={u['cache_creation_input_tokens']} "
                f"cache_read={u['cache_read_input_tokens']}"
            )
        print(f"  RESULT: {'PASS' if ok else 'FAIL'}")
        if fails:
            for f in fails:
                print(f"    - {f}")
        print()

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    for label, ok, _ in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

    all_passed = all(ok for _, ok, _ in results)
    print()
    print("ALL PASS" if all_passed else "SOME FAILURES")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
