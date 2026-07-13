# Big Bros: KUYA and ATE, Game Plan

A level-headed plan for connecting the two halves of the product, shipping a
demoable state, and pricing it. Written to be acted on, not admired. No em
dashes, per house writing rules.

## The cast

- **KUYA** (`bigbros-concierge`). The booking concierge on the resort website.
  ElevenLabs Conversational AI plus a FastAPI backend that reads and writes
  three Google Calendars. He already collects a free text `special_requests`
  field at booking and writes it into the calendar event.
- **ATE** (`GuestAssistanceDID`). The in room helper. Claude brain, ElevenLabs
  Scribe and TTS, a D-ID avatar, and a websocket dashboard of department ticket
  lanes. She already does multi intent decomposition, dedup, allergy auto tag,
  escalation, status flips, and the latency mask.

KUYA seats the guest. ATE looks after them once they are in the room. The whole
opportunity is in the handoff between them, and in making the back of house feel
effortless. That handoff does not exist in code yet. This plan closes it.

## Where things actually stand

KUYA is live and taking bookings. ATE is a working hackathon build: the brain,
the lanes, the dedup and the autotag are real. Two gaps separate it from the
product you described.

1. **ATE does not know the guest.** Her room state (allergies, language,
   occasion) is hardcoded in `store.py`. KUYA holds that information but never
   passes it.
2. **There was no "still waiting" signal.** That is now built (see below).

## What got built this session: the push

You defined a push precisely: not a guest poking staff, and not a new staff
action. It is an alert that an outstanding ticket has gone stale, either because
the guest asked ATE about it again or because a timer elapsed. The guest can
never fire it directly. It always goes through ATE.

This maps onto the dedup path that already existed, so it was an extension, not
a new system. What changed:

- **Ticket schema** gained `nudge_count` and `last_nudge_at`. A push bumps the
  count and stamps the time.
- **The re-request path** is the hero. When the guest chases something ATE is
  already tracking, she matches it to the open ticket (now with no time window,
  because an open ticket is still outstanding no matter how long ago it was
  raised), bumps the nudge, and broadcasts `reason: "push"` instead of silently
  duplicating. No second ticket is ever created for the same outstanding thing.
- **The dashboard reacts.** The card pulses a soft coral ring for one beat and a
  gentle two note chime plays, synthesized in the browser, no audio asset. A
  "still waiting" badge sits on the card and persists between pushes, showing the
  chase count (for example `2x`). Coral is the existing human and safety accent,
  so this reads as "a person is asking again," consistent with allergy and
  escalation. The pulse respects the reduced motion guard; the badge is the
  static fallback.
- **A timer backstop.** An open (not yet acknowledged) ticket older than a
  threshold re-pushes on its own, throttled so it nudges at most once per
  interval. It is off by default so a live demo stays deterministic, and switches
  on with `BIGBROS_STALE_SECONDS` for real floor monitoring.

The guest never holds a push because the only two sources are ATE's pipeline and
the server timer. The guest UI has no push control at all. That is the safeguard,
built into the architecture rather than bolted on.

Verified: backend compiles, the push and dedup logic passes a focused unit test
(three asks produce one ticket with `nudge_count` 2, a done ticket no longer
matches, the new fields serialize), and the frontend typechecks clean.

## The real decision: how KUYA hands the guest to ATE

This is the piece you wanted to think through rather than rush. Here is the shape
of it and a recommendation.

### What has to flow

At booking, KUYA learns things ATE needs the moment the guest walks in: allergies,
language, occasion (anniversary, kids), accessibility or comfort preferences (the
no fragrance sheets example), and VIP status. Today that lives as one free text
blob in the calendar event. ATE needs it as structured, department routable tags.

### Three ways to move it

| Option | How | Verdict |
|---|---|---|
| Shared profile store | A small table or service both apps read and write | Cleanest long term. Needs ATE to gain a real datastore (she is in memory today). |
| Google Calendar as the bus | KUYA already writes the booking event. ATE reads the room's active event and parses it. | Best for now. Zero new infrastructure, and the calendar already answers "who is in this room right now," which is exactly the room identity ATE keys on. |
| Manual seed at check in | Staff tap to import the booking into the room | Simplest, least magical. Fine as a fallback. |

**Recommendation: calendar bus now, profile store later.** Reuse what exists.
The migration path is clean because the data contract below does not change when
the storage does.

### Make the data machine readable at the source

Free text parsing is where this gets flaky. Fix it where the guest is already
talking: give KUYA a new tool that writes a structured block into the event, so
ATE parses deterministically instead of guessing from prose.

New KUYA tool (sketch):

```
set_guest_profile(booking_ref, room, language,
                  tags=["allergy:shellfish", "occasion:anniversary",
                        "housekeeping:no-fragrance", "vip"])
```

KUYA's agent is already in conversation, so it can confirm on the spot ("just to
confirm, a shellfish allergy?") before writing. ATE reads the active event for
the room, lifts the `PROFILE:` block, and seeds `RoomState.standing_tags` and
`language`. Old bookings with only prose fall back to a one time Claude parse.

### Generalize standing tags to any department

ATE auto tags allergies onto kitchen tickets today. Generalize that so a standing
preference can scope to any department. A tag carries an optional scope:

- `kitchen:allergy:shellfish` attaches to every kitchen ticket.
- `housekeeping:no-fragrance` attaches to every housekeeping ticket.
- `*:vip` attaches everywhere.

The pipeline appends a standing tag to a new ticket when the tag's scope matches
the ticket's department or is `*`. That one rule turns the no fragrance sheets
example, and anything like it, into a solved problem. The cook and the room
attendant never have to remember.

## QR first, with safeguards that do not nag

Start with QR codes. Smaller ask, demoable now, and the architecture does not
change when a tablet arrives later (both open the same PWA URL).

The QR opens `/tablet?room=4` carrying a signed room token. Safeguards that bound
abuse without adding guest friction or staff work:

- **Room bound, time bound token.** The QR encodes a signed token tied to the
  room and the active stay window. A scanned link only works for that room and
  only during that booking. Rotate it per booking. An expired token drops to a
  read only info mode (answers FAQs, raises no tickets).
- **The guest still never pushes staff.** Every utterance goes through ATE, who
  decides answer, ticket, clarify, or escalate. There is no direct line to a
  department. This is the same principle as the push.
- **Dedup absorbs spam.** Re-asking bumps a ticket, it does not multiply. Ten
  "bring water" in a row is one ticket with a high nudge count, which is actually
  useful signal, not noise.
- **Soft session rate limit.** Cap turns per minute per room; over the cap ATE
  replies politely that she is catching up. No hard wall in the guest's face.
- **Audit trail is already there.** Every ticket keeps `source_utterance` and
  `room`, so any abuse is traceable after the fact.

Net effect: guest friction is scan and talk, staff workload is unchanged because
dedup and autotag and the triage lane keep the board clean, and abuse is bounded
by an expiring room token plus dedup plus the rate limit.

## The demo script

The money shot is decomposition, then the still waiting beat, then the human
touches. Run it on two screens: the tablet (or a phone on the QR) and the
dashboard.

1. **One lazy sentence, four tickets.** Guest says something rambling: "can we
   get some rice and extra towels, and what time does the pool close, oh and the
   aircon is rattling." Watch four cards land across kitchen, housekeeping, front
   desk answer, and maintenance, each timestamped, each with the raw sentence
   kept underneath as the audit trail.
2. **ATE answers what she can.** The pool hours come back as spoken reply, no
   ticket. The physical things become tickets. This is the "I handle this versus
   a human handles this" split, on purpose.
3. **The allergen flags itself.** Because the room has a standing shellfish tag,
   the kitchen ticket carries `allergy:shellfish` even though the guest never
   mentioned it. The cook never has to remember.
4. **A special request rides a non kitchen ticket.** Ask for sheets with no
   fragrance. It lands on housekeeping with the preference attached. Same engine,
   any department.
5. **The push.** A few moments later, ask for the rice again. No new ticket. The
   existing kitchen card pulses, chimes, and shows "still waiting 2x." A manager
   glancing at the board sees exactly which guest is being kept waiting.
6. **The language flourish, last.** Guest speaks Korean, the avatar answers in
   Korean, the kitchen ticket still lands in English. Showpiece, not the core.

## Tablet versus QR, the investment question

You and your wife are circling the right tension. Here is the honest tradeoff.

| | QR (guest's phone) | In room tablet |
|---|---|---|
| Hardware cost | $0 | roughly $120 to $250 per room, plus mount and charging |
| Time to deploy | now | procurement, kiosk lockdown, mounting |
| Wow factor | modest | high, an always present concierge face |
| Mic and audio | varies by phone | controlled, better |
| Risk | none | theft, damage, cleaning, MDM overhead |

You do not have to choose. QR proves the value at zero hardware cost and demos
today. The tablet is the premium, in room showpiece. With only two suites, two
tablets is a cheap pilot, not a rollout. Because both surfaces are the same PWA
URL, going QR now throws away nothing later.

Your instinct that the overhead is best spent on speech and persona is correct.
That is the moat. Hardware is a deployment choice, not a differentiator. Lead with
QR, offer the tablet as an upsell and a flagship.

## Pricing and go to market

These are starting hypotheses to test against real willingness to pay, not
guarantees. Anthropic note to self: I am not a financial advisor, so treat the
numbers as a frame for your own decision.

### Watch your cost to serve

The variable cost driver is not Claude (a few turns a day is cents per room). It
is the voice and avatar minutes: ElevenLabs TTS and especially D-ID streaming,
which bills per minute of live avatar. Two levers protect margin:

- Offer an audio first or light avatar tier on QR (cheaper than live D-ID), and
  reserve the live streaming face for the tablet showpiece tier.
- Meter or cap heavy usage so a single chatty room cannot erase the margin.

### A tiered frame

| Tier | What | Indicative price |
|---|---|---|
| Starter | QR, audio or light avatar, dashboard, up to ~10 rooms | $99 to $149 per month |
| Pro | QR plus live avatar, multilingual round trip, up to ~30 rooms | $249 to $399 per month |
| Tablet add on | per room hardware at cost or financed, plus a small premium | hardware plus uplift |
| Restaurant | per location, scoped to floor and kitchen and bar | $79 to $199 per month |

Anchor the price against the labor it offsets (a fraction of one front desk or
floor shift) and against the guest experience software the property already pays
for. Flat per property with room bands sells more easily to a boutique than per
room metering.

### The wedge beyond the home resort

The look is already tokenized (CSS variables, a defined palette), and the ticket
schema is department agnostic. That makes reskinning cheap, which is your
expansion path:

- **Templates.** Beach Resort (today), Fine Dining, Urban Hotel, Cafe. Swap the
  theme tokens, the avatar persona, and the lane names. The engine is identical.
- **Restaurants map directly.** Kitchen, bar, floor or host, manager. Same
  decomposition, same still waiting push, same audit trail. A table QR instead of
  a room QR.
- **Sell motion.** Use the home resort as the reference and case study, since you
  own it and can move fast. Sign two or three nearby Zambales resorts or
  restaurants as design partners at a discount, capture testimonials and usage
  data, then widen. You are early and well located; use both.

## Build order from here

1. **KUYA to ATE handoff.** Add the `set_guest_profile` tool to KUYA, write the
   structured block, and have ATE read the room's active calendar event to seed
   standing tags and language. Decide calendar bus versus profile store first
   (recommendation: calendar bus now).
2. **Generalize standing tags to any department** in ATE's pipeline.
3. **QR token.** Signed, room bound, stay bound, with the read only fallback.
4. **Theme tokens into a template switch** so the second vertical is a config,
   not a fork.
5. **Tighten the push for the live demo** (tune the pulse duration and chime to
   taste on the real screens).

## Decisions that are still yours

- Calendar bus or shared profile store for the handoff. I recommend the calendar
  bus to start.
- Whether the live avatar lives in the QR tier or is reserved for the tablet, on
  cost grounds.
- The open spec questions remain open and should not be silently decided in code:
  the escalation threshold ("dirty towel" versus "this room is filthy"), the ETA
  policy (current rule is "right away" only), and how high to prioritize the
  multilingual round trip.
