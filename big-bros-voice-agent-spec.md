# Big Bros White Sand — In-Room Voice Agent

**Build & decision reference, v1**
ElevenLabs x D-ID hackathon entry. Working draft, meant to be marked up. This is synthesized from the planning conversation; everything here is a decision we can still change.

---

## 1. What it is

A voice agent on an in-room tablet at Big Bros White Sand. A guest speaks one natural sentence. The agent answers them out loud through a warm, reactive avatar, and in the same breath routes whatever they asked for to the right department, automatically.

Two surfaces, both ours, nothing borrowed:

- **Guest side** is visual: the D-ID avatar face, warm and reactive.
- **Staff side** is invisible: a back-of-house ops dashboard where tickets land in department lanes.

The avatar earns its place only on the guest side, where a face that reacts ("of course, someone's on the way") beats a chatbot. Staff want a ping and a ticket, not a face to watch.

---

## 2. Why it wins

Three things to say out loud in the pitch:

1. **The four-into-one moment.** One lazy sentence detonates into multiple department tickets plus a spoken reply, with no app-tapping. That decomposition is the demo's money shot.
2. **Real deployment context.** This is welded to an actual resort opening in Zambales. It is not a generic vertical-customer-service avatar. Judges read "real product for a real place."
3. **The dual win.** Most hackathon builds die Monday morning. This one becomes a real asset for the Summer opening whether it places or not.

The lesson from past ElevenLabs winners (GibberLink, etc.): the prize goes to the most screenshot-able uncanny moment, not the most useful tool. Utility is table stakes. The shareable clip is the win. So everything below is built to produce that clip.

---

## 3. Architecture decisions (locked)

| Decision | Choice | Why |
|---|---|---|
| Who is the brain? | **Claude**, not the D-ID-native agent | Our routing logic is the whole point. It must live in our backend and be visible to judges. D-ID is only the face and mouth. |
| Staff endpoint | **PWA ops dashboard** | Rejected Discord, Telegram, Viber, SMS. The dashboard is ours, installs from a URL (no App Store, sidesteps the Apple-shipping block), works on every staff phone, costs nothing, and demos better than a chat message because we control the screen. |
| Device model | **In-room tablet**, not poolside kiosk | Room identity is automatic. That is what makes the allergy auto-tag and profile magic work cleanly. A shared kiosk would force a "what room?" question on every session. |
| Sponsors used | ElevenLabs (Scribe STT + TTS), D-ID (V4 avatar), Claude (routing brain) | Hits both sponsor APIs natively in one flow. |

**Fallback:** if time runs short, the D-ID-native agent (hand it an LLM + knowledge base) is the safety net. The seam is at the brain: swap our backend loop for D-ID's built-in agent and lose the visible routing. Avoid unless forced.

---

## 4. The pipeline

(Accompanies the flow diagram. Stages run top to bottom; the brain splits into two rails.)

**Canonical test utterance** (deliberately messy, because messy is where the agent earns its keep):

> "Hey, the aircon in our room's not really cooling, and can we get two more San Migs and some extra rice? Oh, what time's breakfast?"

Room context already held: Room 4, Family Suite, the Kim family, Korean-speaking, shellfish allergy on file.

1. **Capture.** Mic on the in-room tablet streams audio to the backend. Endpoint on roughly 700 ms of silence closes the turn. Tuned slightly long so a guest pausing between list items ("San Migs... and some rice...") does not fire three half-baked turns.
2. **Transcribe.** Audio to text via ElevenLabs Scribe. Output is the string plus a detected language tag. The language tag is gold: it is how the avatar replies in Korean with nobody configuring it. Keep it.
3. **Assemble context.** Do not send Claude just the sentence. Send the sentence plus a compact state block: room number, standing tags (allergy, language), and currently-open requests. State-in-the-prompt is the difference between an agent and a chatbot.
4. **The brain (one Claude turn).** One response does four things at once via tool calls plus a text reply. From the test sentence: a maintenance ticket (AC, high priority), a bar ticket (2 San Miguel), a kitchen ticket (extra rice, shellfish tag attached), and a spoken reply that also answers breakfast in place. Note what it does silently: recognizes breakfast as answer-in-place (no ticket), attaches the allergy tag the guest never mentioned, and does not invent an ETA.
5. **Fan out.** Each tool call becomes a structured ticket pushed over websocket to its lane. Dedup runs before insert.
6. **Speak back.** Claude's reply text goes to ElevenLabs TTS in the guest's language, then to D-ID for lip-sync. The face carries a flicker of apology on the AC complaint, warms on the food. Same words, delivered by a reactive face, land as hospitality instead of a vending machine.
7. **Latency mask.** STT + Claude + TTS + render stacks up to a beat of delay, and dead air on a talking face feels broken. The moment the guest stops, the avatar gives an instant content-free acknowledgment ("Mm, let me sort that out") before the heavy turn returns, then delivers the real confirmation when it lands.

---

## 5. The brain

### Tools (keep small and orthogonal)

```
create_ticket(dept, summary, item?, qty?, priority, tags?)
answer_guest(text)              // info it can resolve itself
request_clarification(field)    // when a required slot is missing
escalate(reason)                // complaints, money, safety
cancel_request(ticket_ref)      // "actually, never mind the rice"
```

`answer_guest` exists as a tool (rather than just letting Claude chat) on purpose: it forces an explicit choice between "I handle this" and "this needs a human."

### Triage taxonomy (four buckets)

| Bucket | Trigger | Action |
|---|---|---|
| Answer-in-place | Checkout time, wifi, pool hours, breakfast | `answer_guest`, no ticket |
| Actionable ticket | Bring / fix / cook something | `create_ticket` |
| Needs a slot | Missing quantity, or missing room | `request_clarification` first |
| Escalate | Complaint, money, safety | `escalate`, never auto-promise a fix |

### Key rules

- **No fake ETA.** The agent says "right away," never "in five minutes." It cannot see the kitchen. (Flagged for review, see section 11.)
- **Allergy auto-tag.** Every kitchen ticket for a room with a standing allergy carries the flag, even on harmless items, so the cook never has to remember.
- **Confidence / triage lane.** When Claude cannot confidently assign a department ("the thing by the bed is broken"), it does not guess into a lane. It asks one question or drops the ticket into a front-desk triage lane marked needs_routing. Silent misroutes destroy staff trust on day one.
- **Dedup + nudge.** A repeat request within a short window bumps the existing ticket instead of making a new one ("I've nudged the kitchen on that rice").
- **Escalation never over-promises.** Complaints get "a manager will come," full stop. No refunds, no resolutions the agent cannot authorize.

### Edge cases pre-decided

Multi-intent split (the hero case). Ambiguous department (triage lane). Missing quantity or room (clarify). Info vs action (breakfast). Complaints (escalate). Repeats (dedup). Standing-pref enrichment (allergy autotag). Out-of-scope like "book me a flight" (graceful decline, offer front desk). Language mismatch (reply in guest language, ticket in staff language). Mid-conversation correction (cancel_request within a short window).

---

## 6. Data model

### Ticket schema (uniform across all departments)

```
{
  id, created_at,
  room, guest_session_id,
  dept,            // kitchen | bar | housekeeping | maintenance | frontdesk
  summary,         // human-readable, what staff sees
  item, qty,       // nullable, for service or food requests
  priority,        // urgent | high | normal
  tags,            // [allergy:shellfish, vip, repeat, ...]
  status,          // open | ack | done | cancelled
  source_utterance // the raw guest line, for trust and debugging
}
```

A uniform shape is what lets one dashboard render every lane, and what makes dedup and reporting work. Keeping `source_utterance` on the ticket lets staff sanity-check what the guest actually said, and shows the audit trail from voice to action in the pitch.

### Room / guest state

Two suites, so the whole world is two records: Family Suite (Room 4) and Honeymoon Suite. Each holds standing tags (allergy, language, vip), and open_requests. The two suites map onto two demo personas: Family = allergy / kids context, Honeymoon = anniversary / romance context that shows off the reactive face.

---

## 7. The two surfaces

### Guest tablet (visual)

Header with the resort wordmark and the suite name. A large avatar tile (the D-ID video) with a live indicator. A caption block showing what the avatar is currently saying, primary line in the guest's language with an English gloss beneath. Below that, the action confirmations as receipts: maintenance "on the way," bar "sent," kitchen "sent" with the shellfish-safe note. A quiet info line for anything answered in place ("Breakfast 7 to 10, main hall"). A footer mic indicator: "Listening for your next request."

That single screen is the pitch: a lazy sentence became three receipts plus a spoken reply, breakfast answered with no ticket, allergy protected unasked.

### Staff dashboard (invisible backend, PWA)

Same palette, stripped for speed and glanceability under pressure. Department lanes: kitchen, bar, housekeeping, maintenance, front-desk triage. Tickets land in their lane in real time over websocket. High-priority sorts to the top of its lane.

**The one animation that matters:** a ticket card sliding or fading into its lane the instant the guest speaks. Build that transition once, reuse it for every lane. Everything else stays still on purpose. This is the entire dynamic-screen surface, so the animation problem is contained to one component.

---

## 8. Aesthetic direction

**Tropical-modern, not literal tiki.** Translate the resort's materials (thatch, amakan, white walls, beach sand) into a palette, not textures. Thatch textures and tribal fonts read as Rainforest Cafe. The sophisticated read is warm sand and cream surfaces, white walls, one confident accent pulled from the water.

- **Accent:** ocean teal as the single confident accent. Warm coral for the human touches. Neutral walls.
- **Icons:** Phosphor (the ReddX house set). Geometric outline, no emojis ever.
- **No glow.** Neon and glow are the default hackathon-demo look and they fight a beach resort. Save that energy for ReddX. Big Bros wants warmth.
- **Motion budget:** near zero. The guest screen has a speaking-dots pulse and a live blink. The dashboard has the ticket-lands transition. Nothing else moves.
- **Two registers, one family:** guest tablet leans fully warm. Staff dashboard is the calmer back-of-house sibling, same palette and accent, stripped down.
- In the real PWA, push the warmth further than a mockup can: real sand and teak surface panels, not just accent colors.

**House writing rule:** no em dashes anywhere in copy. Use periods, commas, ellipsis, or restructure.

---

## 9. Multilingual handling

Reply in the guest's language (from the Scribe language tag), ticket in the staff's language (English or Tagalog). One sentence to say in the pitch: "the guest speaks Korean, the avatar answers in Korean, the kitchen ticket lands in English." That detail signals a real Philippine resort serving mixed guests, not a generic template.

Reality check: non-English guests (Korean, Chinese) are possible but not frequent at Big Bros. Treat multilingual as a genuine showpiece for the pitch, but build it last so it cannot sink the essential path.

---

## 10. Stretch goals

- **KUYA seeds the profile.** The booking concierge (KUYA, submitted earlier) captures the guest profile at reservation: allergy, "Honeymoon Suite, anniversary trip," language. The in-room agent consumes it during the stay. This is the strong integration: it justifies the flex and sharpens the hero demo, because the allergy protection and the anniversary touch are sourced from KUYA. (The empty version, in-room agent calling KUYA to book a room the guest is already in, is skipped.)
- **Extend-stay routes back to KUYA.** "Can we stay one more night?" is a booking action the in-room agent should not own. It detects booking-intent and hands back to KUYA.
- **Multilingual KUYA.** If multilingual is in, KUYA must capture bookings in the guest's language too, and hand a clean profile forward.

---

## 11. Open questions (for review)

These are judgment calls the spec cannot make. Front-of-house instinct is the actual spec here.

1. **Where is the escalation line?** "Dirty towel" is a ticket. "This room is filthy" is an escalation. Where exactly does the line sit?
2. **ETA policy.** The current rule is "right away," never a committed time, because the agent cannot see the kitchen. Keep that, or let it commit to times? This changes the tone of every reply.
3. **Multilingual priority.** If real guests are 95% English and Tagalog, confirm multilingual stays a build-last flourish.

---

## 12. Demo script (the clip that sells it)

Guest half-asleep by the pool, or just back in the room. One sentence: "bring me a beer and the pool light's busted." Cut to the bartender's phone and the maintenance lane both pinging. Zero app-tapping, two departments, one yawn. That is the resort fantasy, and it is the thing we hand the judges and turn on in July.

---

## 13. Build order

Essential path first, flourishes last.

1. Capture to transcribe to context to brain to ticket, one department, end to end.
2. Multi-intent split (the hero decomposition).
3. The two surfaces: guest tablet shell + staff dashboard lanes with the ticket-lands transition.
4. Speak-back through D-ID with the latency-mask acknowledgment.
5. Reactive face expression mapping (complaint vs happy request).
6. Dedup, escalation, triage lane, allergy autotag.
7. Multilingual round-trip.
8. KUYA profile-seed and extend-stay routing.

---

## 14. Stack summary

- **STT:** ElevenLabs Scribe (CPU-only if needed, per existing pipeline constraints)
- **Brain:** Claude (Anthropic API), one tool-calling turn per guest turn
- **TTS:** ElevenLabs, language set by the Scribe tag
- **Avatar:** D-ID V4 streaming agent (face and mouth only)
- **Transport:** websocket, backend to dashboard lanes
- **Staff surface:** PWA (installs from URL, no App Store)
- **State:** tiny store, two room records + open requests
- **Stretch integration:** KUYA booking concierge

---

*Source of truth for this build. When ready for implementation prompts (brain system prompt, dashboard scaffold, D-ID wiring), write them against this doc with a fresh context.*
