"""FastAPI entry point. POST /turn drives one guest turn. WS /ws/dashboard streams ticket events.
Staff and device auth live in security.py; the server refuses to boot half-locked."""

import asyncio
import logging
import os

from dotenv import load_dotenv

load_dotenv()

# Logging first, before anything can fail silently. One readable line per
# event with a timestamp; level from BIGBROS_LOG_LEVEL (default INFO).
logging.basicConfig(
    level=os.environ.get("BIGBROS_LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("bigbros")

# Optional Sentry. If SENTRY_DSN is set (and sentry-sdk is installed), every
# unhandled exception lands in your inbox with a stack trace instead of dying
# quietly in a hotel room. If unset, this whole block is a no-op.
if os.environ.get("SENTRY_DSN"):
    try:
        import sentry_sdk

        sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], traces_sample_rate=0)
        logger.info("Sentry error reporting on")
    except ImportError:
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed; run pip install sentry-sdk")

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. "
        "Set ANTHROPIC_API_KEY in backend/.env (gitignored, see backend/.env.example) "
        "and restart the server. "
        "To run with the mocked brain instead, set BIGBROS_USE_MOCK_BRAIN=1."
    )

from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .pipeline import Pipeline
from .schema import RoomState, StatusUpdate, Ticket, TurnRequest, TurnResponse
from .security import (
    LISTEN_PER_MIN,
    MAX_AUDIO_BYTES,
    SPEAK_PER_MIN,
    STREAM_NEW_PER_MIN,
    TURN_PER_MIN,
    limiter,
    require_device,
    require_staff,
    staff_key_ok,
    validate_boot_config,
)
from .services.avatar import DEFAULT_AVATAR_IMAGE, DIDAvatar, DIDError, MockAvatar, StreamSessionError
from .services.avatar_simli import SimliAvatar
from .services.brain import ClaudeBrain, MockBrain
from .services.kuya import derive_tags, fetch_current_guests, sync_enabled
from .services.stt import ElevenLabsScribeSTT, MockSTT
from .services.tts import ElevenLabsTTS, MockTTS
from .store import store


validate_boot_config()

app = FastAPI(title="Big Bros Voice Agent")

# Locked to the real frontend origins. Comma-separated in the env, defaults
# cover local dev. The old allow_origins=["*"] is gone on purpose: wildcard
# CORS on an API with cookies-free key auth still invites drive-by calls
# from any page a staff member happens to have open.
_cors_origins = [
    o.strip()
    for o in os.environ.get(
        "BIGBROS_CORS_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",")
    if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

_use_mock_brain = os.environ.get("BIGBROS_USE_MOCK_BRAIN") == "1"
_brain = MockBrain() if _use_mock_brain else ClaudeBrain()
_brain_mode = "mock" if _use_mock_brain else "claude"

# Which face renders the host. "simli" is the default whenever a Simli key is
# present (a twentieth of D-ID's per-minute price for the same job); "did" is
# the legacy hackathon path, kept intact behind this switch in case a demo
# ever needs it back. BIGBROS_AVATAR_PROVIDER overrides the inference.
AVATAR_PROVIDER = os.environ.get("BIGBROS_AVATAR_PROVIDER") or (
    "simli" if os.environ.get("SIMLI_API_KEY") else "did"
)
logger.info("Avatar provider: %s", AVATAR_PROVIDER)


def _avatar_image_url() -> str:
    """The resting host photo. Provider-independent: the same image seeds the
    Simli face and covers the tile before wake."""
    return (
        os.environ.get("BIGBROS_AVATAR_IMAGE_URL")
        or os.environ.get("DID_AVATAR_IMAGE_URL")
        or DEFAULT_AVATAR_IMAGE
    )

# Timer-driven push. An open (un-acknowledged) ticket older than STALE_SECONDS
# fires a "still waiting" push to the dashboard, re-pushing at most once per
# REPUSH_SECONDS. Disabled by default (0) so a live demo stays deterministic and
# the guest re-request path is the hero; set BIGBROS_STALE_SECONDS to switch it
# on (e.g. 90 for a floor-monitoring run).
_STALE_SECONDS = int(os.environ.get("BIGBROS_STALE_SECONDS", "0"))
_REPUSH_SECONDS = int(os.environ.get("BIGBROS_REPUSH_SECONDS", "60"))

# KUYA guest sync cadence. Runs only when KUYA_BASE_URL is configured; 0
# disables the timer (manual /sync/guests still works). Every 3 hours by
# default: bookings barely move, and the dashboard's refresh button covers
# the check-in moment that cannot wait.
_KUYA_SYNC_SECONDS = int(os.environ.get("BIGBROS_KUYA_SYNC_SECONDS", "10800"))


async def _run_guest_sync() -> Optional[dict]:
    """One KUYA pull applied to the store. None means the pull failed or sync
    is not configured; the previous room state stays untouched either way."""
    suites = await fetch_current_guests()
    if suites is None:
        return None
    return await store.apply_guest_sync(suites, derive_tags)


@app.on_event("startup")
async def _start_staleness_loop() -> None:
    if _STALE_SECONDS <= 0:
        return

    async def loop() -> None:
        while True:
            await asyncio.sleep(min(15, _STALE_SECONDS))
            try:
                await store.push_stale(_STALE_SECONDS, _REPUSH_SECONDS)
            except Exception:  # never let the monitor kill the server
                logger.exception("Staleness monitor pass failed")

    asyncio.create_task(loop())


@app.on_event("startup")
async def _start_guest_sync_loop() -> None:
    if not sync_enabled():
        return

    async def loop() -> None:
        while True:
            try:
                await _run_guest_sync()
            except Exception:  # a sync miss must never kill the server
                logger.exception("Guest sync pass failed")
            if _KUYA_SYNC_SECONDS <= 0:
                return
            await asyncio.sleep(_KUYA_SYNC_SECONDS)

    asyncio.create_task(loop())

# The dashboard /turn path stays cheap: mock TTS and avatar, no media calls.
# The guest tablet path does real voice and lip-sync via the singletons below.
# D-ID streaming is per-browser, so the avatar cannot be driven from the
# pipeline's render() hook. The tablet endpoints orchestrate it instead.
pipeline = Pipeline(MockSTT(), _brain, MockTTS(), MockAvatar())

# Real media services, built lazily on first tablet use so offline/dashboard-only
# runs (and BIGBROS_USE_MOCK_BRAIN dev) never need ElevenLabs or D-ID keys.
_tts: Optional[ElevenLabsTTS] = None
_did: Optional[DIDAvatar] = None
_simli: Optional[SimliAvatar] = None
_stt: Optional[ElevenLabsScribeSTT] = None
# Pre-generated, content-free acknowledgments per language for the latency mask.
_ack_cache: dict[str, bytes] = {}
ACK_TEXT = {
    "ko": "음, 잠시만요. 바로 도와드릴게요.",
    "en": "Mm, let me sort that out.",
}


def get_tts() -> ElevenLabsTTS:
    global _tts
    if _tts is None:
        _tts = ElevenLabsTTS()
    return _tts


def get_did() -> DIDAvatar:
    global _did
    if _did is None:
        _did = DIDAvatar()
    return _did


def get_simli() -> SimliAvatar:
    global _simli
    if _simli is None:
        _simli = SimliAvatar()
    return _simli


def get_stt() -> ElevenLabsScribeSTT:
    global _stt
    if _stt is None:
        _stt = ElevenLabsScribeSTT()
    return _stt


def _b64(audio: Optional[bytes]) -> Optional[str]:
    """mp3 bytes to base64 for the Simli path, where the browser (not the
    backend) feeds audio to the avatar. None passes through untouched."""
    if not audio:
        return None
    import base64

    return base64.b64encode(audio).decode("ascii")


async def get_ack_audio(language: str, tts: ElevenLabsTTS) -> Optional[bytes]:
    lang = language if language in ACK_TEXT else "en"
    if lang not in _ack_cache:
        audio = await tts.speak(ACK_TEXT[lang], lang)
        if audio:
            _ack_cache[lang] = audio
    return _ack_cache.get(lang)


class StreamAnswer(BaseModel):
    session_id: str
    answer: dict


class StreamIce(BaseModel):
    session_id: str
    candidate: Optional[str] = None
    sdpMid: Optional[str] = None
    sdpMLineIndex: Optional[int] = None


class TabletAck(BaseModel):
    # room is legacy: identity now comes from the device token server-side.
    # Kept optional so older clients still parse; the value is ignored.
    room: str = ""
    # Stream ids are D-ID-only. The Simli path has no server-held stream (the
    # browser owns the WebRTC), so these default empty.
    stream_id: str = ""
    session_id: str = ""
    # Demo language control (defaults English). Drives the ack language and,
    # on /tablet/turn, the reply/TTS language via the pipeline override.
    language: str = "en"


class TabletTurn(BaseModel):
    # room is legacy: identity now comes from the device token server-side.
    room: str = ""
    text: str
    stream_id: str = ""
    session_id: str = ""
    language: str = "en"


class TabletSpeak(BaseModel):
    """Voice an already-decided reply. D-ID: voiced server-side onto the
    stream. Simli: the reply audio comes back as audio_b64 for the browser."""
    stream_id: str = ""
    session_id: str = ""
    text: str
    language: str = "en"


class StreamClose(BaseModel):
    session_id: str


class TabletTurnResponse(BaseModel):
    """Turn result for the tablet. tickets always land (the dashboard already
    has them); spoken says whether the avatar voiced the reply or the browser
    needs to re-handshake and call /tablet/speak."""
    reply: str
    tickets: list[Ticket]
    language: str
    spoken: bool
    # Reactive-face signal (concern | warm | neutral) for the tablet tint cue.
    sentiment: str = "neutral"
    # Simli path only: the reply audio (base64 mp3) for the browser to feed to
    # the avatar. None on the D-ID path, where the server voices the stream.
    audio_b64: Optional[str] = None


@app.middleware("http")
async def _log_unhandled(request, call_next):
    """Every unhandled exception gets a log line with the path that caused it
    before FastAPI turns it into a 500. Without this, a crash inside a turn is
    invisible unless someone is watching the terminal at that exact moment."""
    try:
        return await call_next(request)
    except Exception:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        raise


@app.get("/health")
async def health() -> dict:
    """Honest health: reports what is actually configured and running, so an
    uptime monitor pinging this learns more than 'the process exists'."""
    from . import db as _db

    return {
        "ok": True,
        "brain": _brain_mode,
        "persistence": "on" if _db.enabled() else "OFF (in-memory only)",
        "guest_sync": "on" if sync_enabled() else "off",
        "media_keys": {
            "elevenlabs": bool(os.environ.get("ELEVENLABS_API_KEY")),
            "did": bool(os.environ.get("DID_API_KEY")),
        },
    }


@app.get("/rooms", response_model=list[RoomState], dependencies=[Depends(require_staff)])
async def rooms() -> list[RoomState]:
    return list(store.rooms.values())


@app.post("/turn", response_model=TurnResponse, dependencies=[Depends(require_staff)])
async def turn(req: TurnRequest) -> TurnResponse:
    limiter.check("staff:/turn", TURN_PER_MIN)
    return await pipeline.run_turn(
        room=req.room,
        text=req.text,
        guest_session_id=f"session-{req.room}",
    )


@app.get("/tickets/history", dependencies=[Depends(require_staff)])
async def ticket_history(limit: int = Query(default=50, le=200)) -> list[dict]:
    """Recently closed tickets for the manager's history view, newest first.
    Includes ack_at and done_at so the dashboard can show how fast work moved
    (created to acknowledged to done). Live lanes never show these; history
    is where completed work goes to be measured."""
    from . import db as _db

    return [t.model_dump(mode="json") for t in _db.load_history(limit)]


@app.post("/sync/guests", dependencies=[Depends(require_staff)])
async def sync_guests() -> dict:
    """Manual guest refresh, for the check-in moment. Pulls current guests
    from KUYA and applies them to room state. The dashboard's refresh button
    calls this; the timer does the same thing on its own every cycle."""
    if not sync_enabled():
        raise HTTPException(
            status_code=503,
            detail="Guest sync is not configured. Set KUYA_BASE_URL in backend/.env.",
        )
    result = await _run_guest_sync()
    if result is None:
        raise HTTPException(status_code=502, detail="Could not reach KUYA for guest sync.")
    return {"ok": True, **result}


@app.post("/tickets/{ticket_id}/status", response_model=Ticket, dependencies=[Depends(require_staff)])
async def update_ticket_status(ticket_id: str, body: StatusUpdate) -> Ticket:
    """Server-authoritative status flip (open -> ack -> done, or cancelled).
    Mutates the ticket and broadcasts ticket.updated so every connected
    dashboard reflects the change, not just the tab that clicked."""
    ticket = await store.set_status(ticket_id, body.status)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticket '{ticket_id}'")
    await store.broadcast({
        "type": "ticket.updated",
        "ticket": ticket.model_dump(mode="json"),
        "reason": "status",
    })
    return ticket


@app.get("/tablet/room")
async def tablet_room(room: str = Depends(require_device)) -> dict:
    """The room this device is provisioned for, resolved server-side from the
    device token. The tablet renders its suite name from this instead of the
    staff-only /rooms listing, and can never claim a different room."""
    state = store.get_room(room)
    if state is None:
        raise HTTPException(status_code=404, detail=f"Device bound to unknown room '{room}'")
    return {"room": state.room, "suite_name": state.suite_name, "language": state.language}


@app.get("/tablet/avatar")
async def tablet_avatar(room: str = Depends(require_device)) -> dict:
    """The resting host photo plus which provider renders the live face, so
    the tablet knows which wake path to take. Read-only, opens nothing."""
    return {"source_url": _avatar_image_url(), "provider": AVATAR_PROVIDER}


@app.post("/tablet/avatar/session")
async def tablet_avatar_session(room: str = Depends(require_device)) -> dict:
    """Simli only: mint a browser session token. The API key stays here; the
    tablet hands the short-lived token to simli-client, which owns the WebRTC
    from there. Same rate bucket as D-ID stream creation."""
    if AVATAR_PROVIDER != "simli":
        raise HTTPException(status_code=400, detail="Avatar provider is not simli")
    limiter.check(f"device:{room}:/tablet/stream/new", STREAM_NEW_PER_MIN)
    return await get_simli().create_session()


@app.post("/tablet/listen")
async def tablet_listen(clip: UploadFile = File(...), room: str = Depends(require_device)) -> dict:
    """Transcribe a push-to-talk clip with ElevenLabs Scribe. Returns the text
    and the detected language (two-letter), which the tablet then sends to
    /tablet/turn as the reply language. Scribe only: this does not touch the
    D-ID stream or the pipeline. Fails loudly if ELEVENLABS_API_KEY is missing."""
    limiter.check(f"device:{room}:/tablet/listen", LISTEN_PER_MIN)
    audio = await clip.read()
    if not audio:
        raise HTTPException(status_code=400, detail="Empty audio clip")
    if len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio clip too large")
    text, language = await get_stt().transcribe(
        audio,
        filename=clip.filename or "clip.webm",
        content_type=clip.content_type or "audio/webm",
    )
    return {"text": text, "language": language}


@app.post("/tablet/stream/new")
async def tablet_stream_new(room: str = Depends(require_device)) -> dict:
    """Create a D-ID stream. Returns the WebRTC offer and ids for the browser
    to complete the handshake. D-ID and ElevenLabs keys never leave the server."""
    limiter.check(f"device:{room}:/tablet/stream/new", STREAM_NEW_PER_MIN)
    return await get_did().create_stream()


@app.post("/tablet/stream/{stream_id}/sdp")
async def tablet_stream_sdp(stream_id: str, body: StreamAnswer, room: str = Depends(require_device)) -> dict:
    await get_did().send_sdp(stream_id, body.session_id, body.answer)
    return {"ok": True}


@app.post("/tablet/stream/{stream_id}/ice")
async def tablet_stream_ice(stream_id: str, body: StreamIce, room: str = Depends(require_device)) -> dict:
    await get_did().send_ice(
        stream_id,
        body.session_id,
        {"candidate": body.candidate, "sdpMid": body.sdpMid, "sdpMLineIndex": body.sdpMLineIndex},
    )
    return {"ok": True}


@app.post("/tablet/stream/{stream_id}/close")
async def tablet_stream_close(stream_id: str, body: StreamClose, room: str = Depends(require_device)) -> dict:
    """Tear down a stream. The browser calls this for a prior stream before
    starting a new session so we never reuse a dead peer."""
    await get_did().close(stream_id, body.session_id)
    return {"ok": True}


async def _speak_on_stream(stream_id: str, session_id: str, audio: Optional[bytes]) -> bool:
    """Speak audio on a stream. Returns True if voiced, False on ANY failure.

    Voice is presentation, not substance: a D-ID rejection (stale session, out
    of credits, anything) must never fail the request path, because by the time
    we speak, the tickets have already landed. The browser reads spoken=False,
    retries once through a fresh handshake, and then tells the guest their
    request went through even though the avatar stayed quiet. The old behavior
    (502 on non-session errors) aborted whole turns when D-ID ran out of
    credits, which silently dropped guest requests. Never again."""
    if not audio:
        return False
    try:
        await get_did().speak_audio(stream_id, session_id, audio)
        return True
    except StreamSessionError:
        return False
    except DIDError as e:
        logger.error("Avatar voice unavailable (continuing without voice): %s", e)
        return False


async def _tts_or_none(text: str, language: str) -> Optional[bytes]:
    """TTS with the same philosophy: a voice synthesis failure logs loudly and
    returns None (spoken=False downstream) instead of exploding an endpoint
    whose real work (the tickets) already succeeded."""
    try:
        return await get_tts().speak(text, language)
    except Exception as e:
        logger.error("TTS failed (continuing without voice): %s", e)
        return None


@app.post("/tablet/ack")
async def tablet_ack(body: TabletAck, room: str = Depends(require_device)) -> dict:
    """Latency mask. The instant the guest turn ends, the avatar says a short,
    content-free acknowledgment in the room's language before the heavy turn
    returns. spoken=False means the stream went stale and the browser should
    re-handshake."""
    limiter.check(f"device:{room}:/tablet/speak", SPEAK_PER_MIN)
    try:
        audio = await get_ack_audio(body.language, get_tts())
    except Exception as e:
        logger.error("Ack TTS failed (continuing without voice): %s", e)
        audio = None
    if AVATAR_PROVIDER == "simli":
        # The browser feeds Simli; we just hand back the cached ack audio.
        return {
            "ok": True,
            "language": body.language,
            "spoken": audio is not None,
            "audio_b64": _b64(audio),
        }
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return {"ok": True, "language": body.language, "spoken": spoken}


@app.post("/tablet/turn", response_model=TabletTurnResponse)
async def tablet_turn(body: TabletTurn, room: str = Depends(require_device)) -> TabletTurnResponse:
    """One guest turn from the tablet. Runs the same pipeline as /turn (brain +
    tickets + dashboard broadcast), then voices the reply through ElevenLabs in
    the guest's language and lip-syncs it onto the caller's D-ID stream. If the
    stream is stale, tickets still land and spoken=False tells the browser to
    re-handshake and call /tablet/speak. The brain runs only once.

    Room identity comes from the device token, never from the body. The body's
    room field is legacy and ignored, so a tampered client cannot file tickets
    as another suite."""
    limiter.check(f"device:{room}:/tablet/turn", TURN_PER_MIN)
    resp = await pipeline.run_turn(
        room=room,
        text=body.text,
        guest_session_id=f"session-{room}",
        language=body.language,
    )
    audio = await _tts_or_none(resp.reply, resp.language)
    if AVATAR_PROVIDER == "simli":
        return TabletTurnResponse(
            reply=resp.reply,
            tickets=resp.tickets,
            language=resp.language,
            spoken=audio is not None,
            sentiment=resp.sentiment,
            audio_b64=_b64(audio),
        )
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return TabletTurnResponse(
        reply=resp.reply,
        tickets=resp.tickets,
        language=resp.language,
        spoken=spoken,
        sentiment=resp.sentiment,
    )


@app.post("/tablet/speak")
async def tablet_speak(body: TabletSpeak, room: str = Depends(require_device)) -> dict:
    """Voice a known reply onto a stream without re-running the brain. The browser
    calls this after a fresh re-handshake to retry a speak that went stale."""
    limiter.check(f"device:{room}:/tablet/speak", SPEAK_PER_MIN)
    audio = await _tts_or_none(body.text, body.language)
    if AVATAR_PROVIDER == "simli":
        return {"ok": True, "spoken": audio is not None, "audio_b64": _b64(audio)}
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return {"ok": True, "spoken": spoken}


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket, key: Optional[str] = Query(default=None)) -> None:
    # Browsers cannot set headers on a websocket, so the staff key rides the
    # query string. Reject before accept: an unauthenticated socket never sees
    # the snapshot (room numbers, allergies, raw guest utterances).
    if not staff_key_ok(key):
        await ws.close(code=4401, reason="Missing or invalid staff key")
        return
    await ws.accept()
    await store.subscribe(ws)

    snapshot_tickets = []
    for r in store.rooms.values():
        for t in r.open_requests:
            snapshot_tickets.append(t.model_dump(mode="json"))
    await ws.send_json({"type": "snapshot", "tickets": snapshot_tickets})

    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await store.unsubscribe(ws)
