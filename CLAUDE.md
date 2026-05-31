# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repo state

Spec-only. No source code, no package manifests, no tests yet. The repo is `big-bros-voice-agent-spec.md` plus two mockup PNGs (`backend-mockup.png`, `flowchart.png`). The spec is the source of truth — when implementation begins, build against it, do not rewrite it from scratch.

## What this will be

An in-room tablet voice agent for **Big Bros White Sand** resort (Zambales). Guest speaks one messy sentence; the agent (a) replies in voice through a reactive D-ID avatar and (b) decomposes the sentence into structured tickets routed to department lanes on a staff PWA dashboard. Built for an ElevenLabs × D-ID hackathon, but intended to actually ship for the resort's Summer opening.

Two surfaces, both first-party:
- **Guest tablet** — visual, warm, avatar-led.
- **Staff PWA dashboard** — invisible back-of-house, websocket-fed ticket lanes.

## Architecture (locked decisions — do not relitigate without cause)

| Decision | Choice | Why it's locked |
|---|---|---|
| Brain | **Claude** (Anthropic API), one tool-calling turn per guest turn | Routing logic must live in our backend and be visible to judges. D-ID is face/mouth only, not brain. |
| Staff endpoint | **PWA** installable from URL | Sidesteps Apple App Store shipping block. Discord/Telegram/Viber/SMS were explicitly rejected. |
| Device | **In-room tablet**, not poolside kiosk | Room identity is implicit per device — that's what makes allergy auto-tag and profile context work without asking "what room?". |
| STT | ElevenLabs Scribe — keep the **detected language tag** | The language tag is how the avatar replies in the guest's language with zero config. Do not drop it. |
| TTS | ElevenLabs, language driven by Scribe's tag | |
| Avatar | D-ID V4 streaming agent | |
| Transport | websocket from backend → dashboard lanes | |

**Fallback only if time runs out:** D-ID's native agent with an LLM + knowledge base. The seam is at the brain — swapping loses visible routing, which is the demo's money shot. Avoid.

## Pipeline (capture → speak)

1. **Capture** — mic on tablet, endpoint on ~700 ms silence (tuned long so list-pauses don't fire half-turns).
2. **Transcribe** — ElevenLabs Scribe → text + language tag.
3. **Assemble context** — send Claude the sentence *plus* a compact state block: room number, standing tags (allergy, language, vip), currently-open requests. State-in-the-prompt is what makes it an agent, not a chatbot.
4. **Brain (one Claude turn)** — emits tool calls + a spoken-reply text in a single turn.
5. **Fan out** — each tool call becomes a structured ticket pushed over websocket to its department lane. **Dedup runs before insert.**
6. **Speak back** — reply text → ElevenLabs TTS in guest's language → D-ID lip-sync.
7. **Latency mask** — the moment the guest stops talking, the avatar emits an instant content-free ack ("Mm, let me sort that out") *before* the heavy turn returns. Dead air on a talking face reads as broken.

## The brain — tools and rules

Tools (small, orthogonal — resist adding more):

```
create_ticket(dept, summary, item?, qty?, priority, tags?)
answer_guest(text)              // info Claude can resolve itself
request_clarification(field)    // missing required slot
escalate(reason)                // complaints, money, safety
cancel_request(ticket_ref)      // "actually, never mind the rice"
```

`answer_guest` is a tool (not free chat) **on purpose** — it forces an explicit "I handle this" vs "human handles this" choice.

Triage buckets: **answer-in-place** (breakfast, wifi, pool hours), **actionable ticket** (bring/fix/cook), **needs-a-slot** (missing qty or room → clarify first), **escalate** (complaints / money / safety).

Invariants — these will surface as bugs if violated:

- **No fake ETA.** "Right away," never "in five minutes." Agent cannot see the kitchen.
- **Allergy auto-tag.** Every kitchen ticket for a room with a standing allergy carries the tag, *even on harmless items.* Cook never has to remember.
- **No silent misroutes.** Low-confidence department → drop into front-desk `needs_routing` triage lane, do not guess into a department.
- **Dedup + nudge.** Repeat request inside a short window bumps the existing ticket; does not create a new one.
- **Escalation never over-promises.** "A manager will come" — never refunds, never resolutions the agent cannot authorize.

## Data

**Ticket schema is uniform across all departments** — that uniformity is what lets one dashboard render every lane and what makes dedup/reporting work:

```
{ id, created_at, room, guest_session_id,
  dept,            // kitchen | bar | housekeeping | maintenance | frontdesk
  summary, item, qty,
  priority,        // urgent | high | normal
  tags,            // [allergy:shellfish, vip, repeat, ...]
  status,          // open | ack | done | cancelled
  source_utterance // raw guest line, for trust + debugging + pitch audit trail
}
```

Keep `source_utterance` on the ticket — it's the audit trail from voice → action and a pitch artifact.

**Room state** — only two suites total: Family Suite (Room 4, demo persona: allergy/kids) and Honeymoon Suite (demo persona: anniversary/romance). State per suite: standing tags + open_requests.

## Multilingual

**Reply in guest's language, ticket in staff's language (English or Tagalog).** Pitch line: "guest speaks Korean, avatar answers in Korean, kitchen ticket lands in English." Build this **last** — it is a showpiece flourish, not the essential path. Do not let it block the core pipeline.

## Aesthetic constraints

- **Tropical-modern, not literal tiki.** Translate resort materials (thatch, amakan, sand, white walls) into palette, not textures. Avoid Rainforest Cafe.
- **Palette:** warm sand / cream surfaces, white walls, **ocean teal** as the single confident accent, warm coral for human touches.
- **Icons: Phosphor only** (geometric outline). **No emojis, ever.**
- **No neon, no glow.** That's the default hackathon look — it fights a beach resort. Save it for other projects.
- **Motion budget is near-zero.** Guest screen: speaking-dots pulse + live blink. Dashboard: the ticket-lands transition (one component, reused across every lane). Everything else stays still on purpose.
- Guest tablet leans fully warm. Staff dashboard is the same family, calmer/stripped.

## Writing rules (for any UI/copy/comments/docs)

- **No em dashes anywhere in copy.** Use periods, commas, ellipsis, or restructure.

## Build order (essential path first, flourishes last)

1. Capture → transcribe → context → brain → ticket, one department, end to end.
2. Multi-intent split (the hero decomposition — the demo's money shot).
3. Two surfaces: guest tablet shell + staff dashboard lanes with the ticket-lands transition.
4. Speak-back through D-ID with the latency-mask acknowledgment.
5. Reactive face expression mapping (complaint vs happy request).
6. Dedup, escalation, triage lane, allergy autotag.
7. Multilingual round-trip.
8. KUYA profile-seed (booking concierge feeds in-room agent) and extend-stay handoff back to KUYA.

## Open questions still on the table

Documented in spec §11 — escalation line ("dirty towel" vs "this room is filthy"), ETA policy (current rule: "right away" only), multilingual priority. Do not silently decide these in code; flag if implementation forces a choice.
