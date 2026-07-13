# Audit and Hardening Plan

Full inventory of ATE (`GuestAssistanceDID`) and KUYA (`bigbros-concierge`), findings by severity, and a phased roadmap to make this sellable to hotels. Written 2026-07-13. No em dashes, per house rules.

---

## 1. Inventory

### ATE (in-room voice agent)

| Layer | What exists | State |
|---|---|---|
| Backend | FastAPI, ~1,500 lines. `/turn`, `/tablet/*` (listen, ack, turn, speak, stream lifecycle), `/tickets/{id}/status`, `/ws/dashboard`, `/rooms`, `/health` | Working |
| Brain | Claude tool-calling (opus-4-7), 5 tools, prompt caching, mock brain fallback | Working |
| Pipeline | Dedup, allergy autotag, escalation, cancellation, sentiment, push/nudge with staleness timer | Working |
| Store | **In-memory only.** Two hardcoded rooms. Wiped on every restart | Demo-grade |
| Frontend | React/Vite. `/` staff dashboard (5 lanes), `/tablet` guest surface, PWA manifest + service worker | Working |
| Media | ElevenLabs Scribe STT + TTS, D-ID V4 streaming, latency-mask ack cache | Working |
| Deploy | Render (backend) + Netlify (frontend), split origin | Working |
| Secrets | `.env` gitignored and never committed. Verified against git history | Clean |
| Tests | Two manual scripts (`smoke_ws.py`, `test_brain.py`). No test suite, no CI | Missing |
| Logging | Two `logger.warning` calls in the entire backend | Missing |
| Auth | **None. Zero. On anything.** | Missing |

### KUYA (booking concierge)

| Layer | What exists | State |
|---|---|---|
| Backend | FastAPI, ~1,600 lines. Availability, rates, booking, directions, owner endpoints | Working, live |
| Calendar | Google service account, three calendars | Working |
| Frontend | Landing site + ElevenLabs Conversational AI widget | Live |
| Owner app | Separate Vite app: bookings list, block dates, occupancy | Working |
| CORS | Origin allowlist from env, better than ATE | OK |
| Auth | **None on owner endpoints** | Missing |

---

## 2. Findings

### CRITICAL. Fix before any outside demo.

**C1. The staff dashboard is public.** `/ws/dashboard` accepts any connection. Anyone with the Netlify URL sees every ticket: room numbers, raw guest utterances, and allergy tags. Allergy data is health information. This is the single biggest liability in the project.

**C2. Anyone can mutate tickets.** `POST /tickets/{id}/status` has no auth. A stranger can mark every open ticket done.

**C3. Anyone can burn your API budget.** `/turn` and `/tablet/*` are open. Each call hits Claude, ElevenLabs, and D-ID, all paid. A script in a loop is a denial-of-wallet attack. There is no rate limiting anywhere.

**C4. KUYA owner endpoints are open.** `/api/owner/bookings` leaks all guest bookings. `/api/owner/block-dates` lets anyone block your booking calendar for a year. No auth, no token, nothing.

**C5. Room identity is a URL parameter.** `?room=4` is client-controlled. Any browser can impersonate any room, inherit its allergy tags, and file tickets as that guest. Room identity must be bound to the device (provisioned device token), not the URL.

**C6. CORS is `allow_origins=["*"]`** on ATE with all methods and headers. Combined with no auth, any website a staff member visits can silently call your backend from their browser.

### HIGH. Fix before the pilot next door.

**H1. Every restart loses every ticket.** The store is in-memory and Render restarts dynos. A guest asks for a towel, the server restarts, the request silently ceases to exist and nobody knows. For a real hotel this is the worst reliability hole. Needs Postgres (Render has a free tier) or at minimum SQLite with a persistent disk.

**H2. The dashboard never reconnects.** `ws.ts` has no retry, no heartbeat, no "connection lost" banner. Hotel wifi blips constantly. Staff will be staring at a stale board that looks fine. This is how requests get missed and you get blamed.

**H3. No error handling around the brain.** The Anthropic call has no try/except, no retry, no timeout budget. `TicketDraft(**args)` throws on any malformed tool arg. Any of these mid-turn is a raw 500: the guest gets silence from a talking face, which the spec itself calls "broken."

**H4. No audio size limit on `/tablet/listen`.** An arbitrary-size upload goes straight to memory and then to ElevenLabs.

**H5. `GuestInput` (the typed demo box) ships on the production dashboard page.** Anyone who can see the dashboard can inject guest turns into any room.

**H6. Dedup is exact-string matching on summary.** Claude phrases the same request differently across turns ("Extra rice" vs "More rice for room 4"), so real duplicates will slip through. Fine for the demo script, brittle in the wild. Item + dept matching, or embedding similarity later.

### MEDIUM. Design debt.

**M1. Logging is nearly absent.** Two warnings total. No request IDs, no turn transcripts persisted, no error tracker, no metrics. You said it yourself: you do not know what is missing until it breaks. Logging is how you find out *before* it breaks.

**M2. Rooms are hardcoded in `store.py`.** Selling to another hotel currently means editing Python. Rooms, departments, standing tags, and languages must be config or database rows. This is the multi-tenant seam.

**M3. `/health` says `{ok: true}` even when Claude, ElevenLabs, or D-ID are down.** It should check dependencies and report version, so your monitoring actually monitors something.

**M4. The staleness loop swallows all exceptions silently** (`except Exception: pass`). If it breaks, you will never know. Log it.

**M5. No CI, no tests.** One typo in `brain_prompt.py` and you find out live in a hotel lobby. Even a smoke test on PR would catch most of it.

**M6. `@app.on_event("startup")` is deprecated** in current FastAPI. Cosmetic, migrate to lifespan when convenient.

### What is genuinely good (do not touch)

Uniform ticket schema. Tool-based brain with the answer/act/clarify/escalate split. Dedup-as-push design. Allergy autotag in the pipeline, not the prompt. Server-authoritative status flips. Secrets handling. The mock seams (MockBrain, MockSTT) are exactly right and will make testing cheap. The bones are good. He can be rebuilt.

---

## 3. The department split

Cheapest correct version, no backend change needed:

1. Add a dropdown in the dashboard header: **All departments / Kitchen / Bar / Housekeeping / Maintenance / Front Desk.**
2. Filter `LANES` by the selection. Single lane selected renders wider cards.
3. Initialize from `?dept=kitchen` URL param so each department's wall tablet bookmarks its own view, and persist the choice in `localStorage` so it survives reloads.
4. Escalations and `needs_routing` always visible to Front Desk regardless of filter.

Later, when staff auth exists (Phase 1), the login role sets the default dropdown value. The dropdown stays as the manager override, which is what you asked for: managers see everything, kitchen sees kitchen.

---

## 4. Fleet: tablets in other people's buildings

You asked for VPN, cellular backup, an alternative to hotel wifi, remote telemetry, and remote control. Here is the stack that covers all of it without building any of it yourself:

### Network (solves VPN + wifi + cellular in one box)

- **Per-site travel router with SIM failover** (GL.iNet Spitz/Puli class, roughly $100 to $170 per site). The tablet only ever joins *your* router's SSID. The router uses hotel ethernet or wifi as primary and an LTE SIM as automatic failover. You never touch guest wifi and never depend on the hotel's captive portal.
- **Tailscale on the router, the tablet, and the backend** (WireGuard mesh; free tier covers up to 100 devices). Every device gets a stable private IP, all traffic is encrypted end to end, and the ACLs mean a tablet can reach the backend and nothing else. Crucially, this lets you take the dashboard and admin surfaces **off the public internet entirely**, which erases most of the CRITICAL section's attack surface in one move.

### Device management (solves kiosk + telemetry + remote control)

- **Fully Kiosk Browser** (one-time license per device, cheap) for the kiosk itself: locked-down single-app mode, remote REST/MQTT admin, **battery level, screen state, device temperature reporting**, scheduled screen off/on, remote reload, remote screenshot for support calls.
- **An Android MDM** on top for fleet control: **Headwind MDM** (open source, self-hostable, fits the Tailscale setup) or a commercial tier (Esper, Scalefusion; verify current pricing, it changes). MDM gives you remote lock, remote wipe, remote restart, app pinning, data usage per device, and enrollment QR codes so provisioning a new hotel is "scan this code."
- **Remote power-on is physics, not software.** A dead tablet cannot be woken remotely. Solution: keep it on power, and put a **smart plug** on each charger so you can hard power-cycle from anywhere. Tablet set to auto-boot on power (most Androids in kiosk setups support this via MDM).
- **App-level heartbeat** as the layer you own: the tablet POSTs every few minutes to your backend with battery %, app version, and connectivity state. When a device goes quiet you get an alert before the hotel calls you. This is a small endpoint plus one background timer in the PWA, and it feeds the same dashboard you already have.

### Observability (solves "I don't know what broke")

- **Sentry** (free tier) on both backend and frontend. Every unhandled exception, with stack trace, arrives in your inbox instead of dying silently in a hotel room.
- **Structured JSON logging** with a per-turn request ID, persisted turn log (utterance, brain output, tickets created, latency per stage, token usage; `last_usage` is already collected and currently thrown away).
- **Uptime monitoring** (UptimeRobot or Better Stack, free tiers) on `/health`, after H3 makes `/health` honest.
- **Daily digest**: turns handled, tickets by department, error count, per-device battery floor. One scheduled job, and it doubles as the report you show prospects.

### Things you did not list but will need

- **Privacy compliance.** You store guest voice transcripts and allergy (health) data. The Philippines Data Privacy Act of 2012 applies, and hotel clients will ask. Needs: a retention window (e.g. purge utterances 30 days after checkout), a one-line guest disclosure at the tablet, and a data processing note in your sales contract.
- **Per-hotel tenancy.** `hotel_id` on rooms, tickets, and devices, with scoped API keys, before hotel number two signs. Retrofitting tenancy is far more painful than adding it early.
- **Guest session reset on checkout.** Open requests and context must clear when the room turns over, or guest B hears about guest A's rice.
- **The tablet is in a stranger's hands.** Assume the guest mashes buttons, unplugs it, and tries to exit the app. Kiosk mode covers exit; auto-relaunch on crash and auto-reconnect on network return cover the rest.
- **Abuse of the voice channel.** Guests will curse at it, order 500 beers, and try prompt injection. Add per-room rate limits, a qty sanity cap in the pipeline, and keep escalation as the pressure valve.
- **A kill switch per device** (MDM lockdown or Fully Kiosk remote lock) for when a hotel stops paying or a tablet walks off.

---

## 5. Roadmap

### Phase 0: lock the doors (before any cold call demo)
1. DONE. API key auth on all staff/admin endpoints and the websocket (KUYA owner endpoints too). `backend/app/security.py`; staff key gate in the dashboard; `X-Owner-Key` on KUYA.
2. DONE. Device token for tablets; room bound to token server-side, not URL. Provision with `/tablet?token=XYZ` once per device; tokens live in `BIGBROS_DEVICE_TOKENS`.
3. DONE. CORS locked to the real origins on ATE via `BIGBROS_CORS_ORIGINS`.
4. DONE. Rate limiting on `/turn` and `/tablet/*` (in-memory sliding window); 10 MB cap on audio uploads.
5. DONE. `GuestInput` renders only in dev builds.
6. DONE (as SQLite, not Postgres). Write-through persistence in `backend/app/db.py`: every ticket create/mutation and every room profile change lands in the file; boot restores active tickets and guest context. Postgres becomes worth it at multi-hotel tenancy, not before. On Render, mount a persistent disk and set `BIGBROS_DB_PATH` (see SETUP.md section 6).
7. DONE. Websocket reconnect with backoff + "connection lost" banner; auth rejection stops the retry loop and says so. State self-heals from the snapshot on reconnect.
8. DONE. One retry around the brain call, then a graceful spoken fallback in the guest's language. Malformed tool calls are logged and skipped, never fatal. Every turn lands in a `turns` audit table with latency, token usage, and any error.

Deploy checklist for the DONE items: set `BIGBROS_STAFF_KEY`, `BIGBROS_DEVICE_TOKENS`, and `BIGBROS_CORS_ORIGINS` on Render (the server refuses to boot without the first two unless `BIGBROS_DEV_OPEN=1`); set `OWNER_API_KEY` on KUYA's host and add the same value as an `X-Owner-Key` header on the ElevenLabs owner agent's tool calls; open `/tablet?token=...` once on each tablet; enter the staff key once on each dashboard device (reset with `?reset=1`).

### Phase 1: the pilot next door
9. Department dropdown (section 3).
10. Sentry + structured turn logging + honest `/health` + uptime monitor.
11. Tablet heartbeat endpoint + basic ops view (last seen, battery, version).
12. Fully Kiosk on the tablet, travel router + Tailscale at the site, smart plug on the charger.
13. Session reset on checkout; retention purge job.
14. Smoke test suite + CI.

### Phase 2: sellable to hotel number two
15. `hotel_id` tenancy, config-driven rooms/departments, scoped keys.
16. Staff logins with roles (role sets default department view).
17. MDM enrollment flow, provisioning QR, per-device kill switch.
18. Daily digest report (doubles as sales collateral).
19. KUYA to ATE profile handoff (already planned in GAME_PLAN.md; it slots in cleanly after tenancy).

Order matters: Phase 0 is a week of unglamorous work and it converts the project from "impressive demo" to "thing you can leave running in a building you do not control."
