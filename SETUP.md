# Setup Guide

Everything needed to run, deploy, and troubleshoot ATE (this repo) and KUYA (`bigbros-concierge`). Written for a tired human at 1am, not for a DevOps team. No em dashes, per house rules.

---

## 1. What runs where

| Thing | What it is | Where it runs |
|---|---|---|
| ATE backend | FastAPI server: the brain, tickets, websocket | Locally: `uvicorn` in `backend/`. Deployed: Render |
| ATE frontend | Dashboard (`/`) + guest tablet (`/tablet`) | Locally: `npm run dev` in `frontend/`. Deployed: Netlify |
| KUYA backend | Booking API reading Google Calendars | Railway (or wherever it currently lives) |
| KUYA frontend | Resort website + booking widget | Netlify |

Rule of thumb: **backends hold every secret. Frontends hold none.**

## 2. First-time local run

Two cmd windows.

Window 1 (backend):
```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID\backend
uvicorn app.main:app
```

Window 2 (frontend):
```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID\frontend
npm run dev
```

Then open http://localhost:5173 for the dashboard, http://localhost:5173/tablet for the tablet.

**After ANY backend code change, restart window 1** (Ctrl+C, run uvicorn again). Python does not pick up changes in a running server. Half of all mystery 404s are an old server still running.

## 3. The keys, in one place

All in `backend\.env` (ATE) unless marked KUYA. Never committed to git; the server refuses to boot without the required ones.

| Variable | What it unlocks | Needed when |
|---|---|---|
| `ANTHROPIC_API_KEY` | The Claude brain | Always |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | Voice in and out | Tablet use |
| `SIMLI_API_KEY`, `SIMLI_FACE_ID` | The talking face (current provider) | Tablet use |
| `DID_API_KEY` | The old talking face (legacy, off unless you flip the provider) | Only with `BIGBROS_AVATAR_PROVIDER=did` |
| `BIGBROS_AVATAR_PROVIDER` | `simli` or `did`. Auto-detects simli when its key is set | Rarely |
| `BIGBROS_AVATAR_IMAGE_URL` | The resting host photo on the tablet | Optional (falls back to the old D-ID var) |
| `BIGBROS_STAFF_KEY` | Dashboard access | Always |
| `BIGBROS_DEVICE_TOKENS` | Tablet room identity, `token:room` pairs | Always |
| `BIGBROS_CORS_ORIGINS` | Which frontend URLs may call the backend | Deployed only |
| `KUYA_BASE_URL` | Guest sync source. **Empty = sync off, everything else still works** | When wiring the bridge |
| `KUYA_SERVICE_KEY` | Auth for the sync | With KUYA_BASE_URL |
| `SERVICE_API_KEY` (KUYA .env) | Same value as KUYA_SERVICE_KEY, other side of the handshake | With the bridge |
| `OWNER_API_KEY` (KUYA .env) | Owner endpoints; also set as X-Owner-Key header on the ElevenLabs owner agent's tools | KUYA production |
| `BIGBROS_DEV_OPEN=1` | Skips all auth. **Local only, never on Render** | Lazy local dev |
| `BIGBROS_DEMO_SEED=1` | Hackathon personas (shellfish allergy, anniversary) | Pitches and offline demos |
| `BIGBROS_KUYA_SYNC_SECONDS` | Sync cadence, default 10800 (3 hours) | Rarely |
| `BIGBROS_DB_PATH` | Where the SQLite file lives, default `backend/data/bigbros.db` | Deployed (see section 6) |
| `BIGBROS_LOG_LEVEL` | Log verbosity, default INFO | Rarely (DEBUG when chasing a bug) |
| `SENTRY_DSN` | Optional: emails you every crash with a stack trace (needs `pip install sentry-sdk` and a free sentry.io account) | When you want errors to find you |

Generate any new key with:
```
python -c "import secrets; print(secrets.token_hex(24))"
```
(Type that into any cmd window. It just prints a random string; copy it into .env.)

## 4a. The avatar: Simli setup (the morning-after checklist)

D-ID is out; Simli is in (roughly 5 cents a minute instead of a dollar). The
whole swap is four steps and the same host face survives:

1. **Account.** Sign up at simli.com, open the dashboard. Free tier includes
   trial minutes, so this works before any card is entered.
2. **Face.** In the Simli dashboard, create a new face and upload the SAME
   host photo the tablet already shows (the one behind
   `DID_AVATAR_IMAGE_URL`). Simli generates the face and gives you a face id.
3. **Keys.** In `backend\.env` add:
   ```
   SIMLI_API_KEY=<from the Simli dashboard>
   SIMLI_FACE_ID=<from step 2>
   ```
   Nothing else. The provider auto-switches to Simli when the key exists.
4. **Install and run.** One-time in `frontend\`:
   ```
   npm install
   ```
   then restart uvicorn and `npm run dev` as usual. The tablet footer will
   read "Powered by Simli and ElevenLabs" when the swap is live.

To go back to D-ID (why would you): set `BIGBROS_AVATAR_PROVIDER=did` in
backend\.env and restart. Both code paths stay in the repo.

What changed under the hood, for future-you: the backend mints a short-lived
Simli session token (API key never reaches the browser), the tablet opens
WebRTC straight to Simli, and each reply's ElevenLabs audio is handed to the
browser (base64), converted to PCM16, and streamed to Simli for lip-sync.
There is no more warmup-frame dance; Simli shows a real idle face from the
first connected frame.

## 4. Wiring the KUYA bridge (when ready, not before)

1. Generate one key with the command above.
2. KUYA backend host (Railway): add `SERVICE_API_KEY=<that key>`.
3. ATE `backend\.env`: add `KUYA_BASE_URL=<KUYA backend URL>` and `KUYA_SERVICE_KEY=<same key>`.
4. Restart both backends.
5. Test: make a booking through KUYA with "allergic to peanuts" in special requests, press **Refresh guests** on the dashboard, ask the tablet for food, and watch the allergy tag ride the kitchen ticket.

Until step 3 happens, the Refresh guests button reports "not configured" and nothing is broken.

## 5. Provisioning devices

- **A tablet:** open `https://<frontend>/tablet?token=<its token>` once on the device. The token sticks. The token IS the room; the server ignores any other room claim.
- **A dashboard screen:** open the dashboard, paste the staff key at the gate, once per device. Wrong key saved? Add `?reset=1` to the URL.
- **A department wall tablet:** bookmark `https://<frontend>/?dept=kitchen` (or bar, housekeeping, maintenance, frontdesk). The dropdown also remembers its last choice per device.

## 6. Deploying

**Render (ATE backend):** set every ATE variable from section 3 in the Environment tab. `BIGBROS_CORS_ORIGINS` = your Netlify URL. Do NOT set `BIGBROS_DEV_OPEN`. Regenerate any key that ever appeared in a chat, screenshot, or email.

**Persistence on Render:** Render's free-tier filesystem is wiped on every deploy and restart, which would defeat the whole point of the ticket database. Attach a persistent disk to the service (smallest size, a dollar or two a month), mount it at `/data`, and set `BIGBROS_DB_PATH=/data/bigbros.db`. Locally you need nothing; the file just appears in `backend\data\`.

**Netlify (ATE frontend):** only `VITE_API_BASE` = the Render URL. No secrets here, ever.

**Railway (KUYA backend):** existing Google vars plus `OWNER_API_KEY` and `SERVICE_API_KEY`.

## 7. When something breaks, read this table first

| Symptom | Meaning | Fix |
|---|---|---|
| `RuntimeError: Refusing to start without auth` | Working as intended: keys missing | Add `BIGBROS_STAFF_KEY` + `BIGBROS_DEVICE_TOKENS` to .env, or `BIGBROS_DEV_OPEN=1` locally |
| `{"detail":"Not Found"}` in browser | You opened the backend URL directly. It has no homepage | Open the frontend URL instead. Backend health check: `/health` |
| 404 on something that should exist | Old server still running from before a code change | Restart uvicorn |
| 401 | Wrong or missing key for that surface | Staff key for dashboard, device token for tablet, matching keys on the bridge |
| 402 / `InsufficientCreditsError` in backend log | Avatar provider has no credits | Top up (or swap providers, section 4a). Everything else keeps working; tickets still land |
| 502 from a tablet action | An upstream media service rejected us. Auth and backend are fine | Check the uvicorn window for the failure line |
| `Avatar provider is not simli` (400) | Tablet asked for a Simli session while the backend runs D-ID | Set `SIMLI_API_KEY`/`SIMLI_FACE_ID` or `BIGBROS_AVATAR_PROVIDER`, restart, reload the tablet |
| Face connects but never speaks | Reply audio failed to decode or stream | Browser console will show `simli speak failed`; the on-screen text and tickets still work |
| 502 from Refresh guests | KUYA unreachable | Is the KUYA backend up? Is `KUYA_BASE_URL` right? |
| 503 from Refresh guests | Bridge not wired yet | Section 4, when you feel like it |
| 429 | Rate limit doing its job | Wait a minute. If it fires on honest use, raise the budget in `security.py` |
| "Connection lost. Reconnecting" banner | Wifi blip; the dashboard is retrying on its own | Wait. It self-heals and refetches everything on reconnect |
| "Staff key rejected" banner | The stored key no longer matches the backend | `?reset=1` on the URL, enter the current key |
| Guest heard "could you say that again?" | The brain call failed twice; the turn was retried and then gracefully dropped | Check the log and the turns table for the error. Once is a blip; often means check `ANTHROPIC_API_KEY` or Anthropic status |
| Tickets vanished after restart | Should not happen anymore (SQLite persistence). If it does: check the boot log for "Persistence DISABLED" | Fix the path in `BIGBROS_DB_PATH`; on Render, mount a disk (section 6) |
| `Persistence DISABLED` in boot log | The database file could not be created | Check the path exists and is writable; the server still runs, just in-memory |
| Where did the shellfish demo go? | Demo personas are opt-in now | `BIGBROS_DEMO_SEED=1` |

## 8. What is deliberately NOT done yet

From `AUDIT_AND_HARDENING.md`:
1. ~~Persistence~~ DONE. SQLite write-through; open tickets and guest context survive restarts
2. ~~Websocket auto-reconnect~~ DONE. Backoff retry with a banner; state self-heals on reconnect
3. ~~Brain-call retry~~ DONE. One retry, then a graceful spoken fallback; malformed tool calls are skipped, not fatal
4. ~~Logging~~ DONE. Timestamped logs, a per-turn audit table (latency, tokens, errors) in the database, honest `/health`, optional Sentry
5. Fleet stack for other hotels: travel router + Tailscale + Fully Kiosk + MDM (section 4 of the audit doc)

The core hardening list is finished. What remains is deployment work (Render disk, Tailscale, kiosk hardware) and growth work (tenancy, staff roles, theming per hotel), both of which happen when the pilot demands them, not before.
