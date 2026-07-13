# Tomorrow, Step by Step

A slow, verbose walkthrough for the morning after. Nothing here assumes memory of last night. Each step says what to type, what you should see, and what to do if you see something else. Coffee first. No em dashes, per house rules.

Estimated total time: 45 to 60 minutes, most of it watching things work.

---

## Step 0. Save the work (5 minutes)

Last night touched a lot of files and none of it is committed. Before anything else, put it in git so no experiment today can lose it.

Open a cmd window:

```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID
git add -A
git commit -m "Security pass, KUYA bridge, persistence, observability, edge case fixes, Simli swap"
```

Then the other repo:

```
cd C:\Users\PC\Documents\GitHub\bigbros-concierge
git add -A
git commit -m "Owner and service auth, anonymized calendar titles, current-guests endpoint"
```

**You should see:** a list of changed files and a commit confirmation.
**If you see** "nothing to commit": fine, it means you already committed. Move on.

Do not push yet if you don't want to. The commit alone is the safety net.

## Step 1. Install the new frontend package (3 minutes)

The Simli client library is declared in package.json but not yet downloaded.

```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID\frontend
npm install
```

**You should see:** npm chewing for a bit, then something like "added 12 packages". No red.
**If you see** red ERR lines: copy them and bring them to me before continuing. Do not fight npm alone at 9am.

Then prove the code is healthy:

```
npm run build
```

**You should see:** `tsc -b && vite build` run, then green "built in X.XXs" like last time.
**If it fails:** the error names a file and line. Copy it, bring it to me. This is the one step I could not verify last night (the new package had to be installed first), so this is the moment of truth for the frontend.

## Step 2. Make your Simli account (10 minutes)

1. Go to **simli.com** in your browser and sign up. Free trial minutes are included, no card needed to start.
2. Once you are in their dashboard, find your **API key**. It lives in the dashboard's API or profile section. Copy it somewhere handy.
3. Now create the face. Find **Create a face** (or Faces, then New). Upload the SAME host photo the tablet already uses. That photo is whatever URL sits in your `DID_AVATAR_IMAGE_URL` line in `backend\.env`. If you only have the URL, open it in a browser, right-click, Save image as, then upload that file to Simli.
4. Simli processes the photo and gives you a **face id** (a string of letters and numbers). Copy it.

**You should have:** two strings, an API key and a face id.
**If face creation is confusing:** their dashboard changes now and then. The two things you need are always called roughly "API key" and "face id". Their Discord is active if something looks completely different.

## Step 3. Add the two keys (2 minutes)

Open `C:\Users\PC\Documents\GitHub\GuestAssistanceDID\backend\.env` in a text editor and add at the bottom:

```
SIMLI_API_KEY=paste-your-api-key-here
SIMLI_FACE_ID=paste-your-face-id-here
```

No quotes, no spaces around the `=`. Save the file.

That is the entire switch. The backend sees the Simli key and automatically prefers it over D-ID. You do not need to remove any D-ID lines.

## Step 4. Start the backend (2 minutes)

```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID\backend
uvicorn app.main:app
```

**You should see, in order:**
- a line containing `Avatar provider: simli`  (the swap took)
- a line containing `Persistence on: data\bigbros.db`  (the database is alive)
- `Uvicorn running on http://127.0.0.1:8000`

**If it refuses to boot** with the auth error: your `BIGBROS_STAFF_KEY` / `BIGBROS_DEVICE_TOKENS` lines are missing from .env. They were added two nights ago; check the file.
**If you see** `Avatar provider: did`: the SIMLI_API_KEY line did not save or has a typo. Fix .env, Ctrl+C, start again.
**If you see** `Persistence DISABLED`: the data folder could not be created. Tell me, but the server still runs.

Leave this window open. It is now also your log: every guest turn will print a line here.

## Step 5. Start the frontend (1 minute)

Second cmd window:

```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID\frontend
npm run dev
```

**You should see:** Vite's "Local: http://localhost:5173".

## Step 6. Check the dashboard (3 minutes)

Open **http://localhost:5173** in your browser.

**You should see:** the staff key gate (if this browser has not stored the key yet). Enter your staff key from .env.

Then the board. Notice what is new since you last looked closely:
- a **Department dropdown** in the toolbar (try it: pick Kitchen, the board becomes one wide lane; pick All departments to go back)
- a **Refresh guests** button (it will say "not configured" if you click it, because the KUYA bridge is not wired yet; that is correct and fine)
- a **History** button (empty for now, it fills as tickets complete)

## Step 7. First contact with the new face (5 minutes)

Open **http://localhost:5173/tablet?token=YOUR-FAMILY-SUITE-TOKEN** (the token is in your .env on the `BIGBROS_DEVICE_TOKENS` line, the part before `:4`). If this browser already stored the token two nights ago, plain `/tablet` works.

1. Check the footer. It should read **"Powered by Simli and ElevenLabs."** If it says D-ID, the backend did not restart after step 3, or the page needs a hard reload (Ctrl+Shift+R).
2. Tap **"Tap to wake your host."**
3. **You should see:** the host photo, then within a few seconds a live face, then hear the greeting ("Hi there, how can I help you?"). This is Simli rendering and ElevenLabs speaking. Notice there is no long blank stare like D-ID had; the face is live almost immediately.

**If the face connects but stays silent:** press F12, look at the browser console for `simli speak failed`. The text and tickets still work; the voice pipeline needs a look. Bring me the console line.
**If it cannot connect at all:** the backend window will show a `Simli token mint failed` line with a status code. 401 means the API key is wrong; bring me anything else.

## Step 8. Run a real turn, watch everything land (5 minutes)

With the tablet awake and the dashboard visible side by side:

1. Type (or speak): *"The aircon is not cooling, can we also get two San Migs and extra rice, and what time is breakfast?"*
2. **You should see, within a few seconds:**
   - the avatar speaks a reply and receipts appear on the tablet
   - THREE cards land on the dashboard: Maintenance (AC), Bar (San Miguel x 2), Kitchen (extra rice), and the breakfast question answered in voice with no ticket, because it is an answer-in-place
   - each card shows a small age ("0m")
   - the backend window prints a `turn room=4 tickets=3` line with latency
3. Click a card: it expands to show the guest's exact words. Click **Acknowledge** on one, then **Mark done**.
4. **You should see:** the done card sits for five minutes, then quietly leaves the board. Click **History**: it is there, with open-to-ack and ack-to-done times. That is your completion-speed tracking, alive.

## Step 9. The restart test (3 minutes)

This is the test that used to fail catastrophically and now should not.

1. File a ticket from the tablet (anything: "extra towels please").
2. Go to the backend window and press **Ctrl+C**. The server dies mid-shift.
3. Start it again: `uvicorn app.main:app`
4. Reload the dashboard.
5. **You should see:** the towels ticket still on the board, same age, same status. Persistence pays for itself right here. Two nights ago that ticket would have simply ceased to exist.

## Step 10. The quantity guard, for fun (1 minute)

Tell the tablet: *"Send up 500 beers."*

**You should see:** a bar ticket for qty 12 with a `verify-qty` tag. The bored-teenager defense, working.

## Step 11. Optional, only if the morning went smoothly: wire the KUYA bridge

This makes real bookings feed guest context (allergies, occasions) into the tablet. It is fully described in **SETUP.md section 4** (five steps: one generated key, one env var on KUYA's host, two env vars on ATE, restart both). If the morning was bumpy, skip it; the system runs fine without it and the Refresh guests button will keep politely saying "not configured".

## Step 12. Commit again (1 minute)

If everything above worked:

```
cd C:\Users\PC\Documents\GitHub\GuestAssistanceDID
git add -A
git commit -m "Simli swap verified working locally"
```

(There may be nothing new to commit if you changed only .env, which git rightly ignores. The commit that matters was step 0.)

---

## What NOT to do tomorrow

- Do not deploy to Render/Netlify yet. Local first, deploy when local is boring.
- Do not top up D-ID. That subscription is now a museum piece.
- Do not set `BIGBROS_DEV_OPEN=1` anywhere. You have real keys; use them.
- Do not troubleshoot alone past ten minutes on any step. Copy the error, bring it to the chat, save the morning.

## Where everything lives

- **SETUP.md** - keys, deploys, and the troubleshooting table (start there when anything errors)
- **AUDIT_AND_HARDENING.md** - the security work and the deploy checklist
- **EDGE_CASES.md** - what was found, what was fixed, what deliberately waits
- **This file** - delete it once tomorrow is done; it will have served its purpose
