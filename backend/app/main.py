"""FastAPI entry point. POST /turn drives one guest turn. WS /ws/dashboard streams ticket events."""

import os

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. "
        "Set ANTHROPIC_API_KEY in backend/.env (gitignored, see backend/.env.example) "
        "and restart the server. "
        "To run with the mocked brain instead, set BIGBROS_USE_MOCK_BRAIN=1."
    )

from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from .pipeline import Pipeline
from .schema import RoomState, StatusUpdate, Ticket, TurnRequest, TurnResponse
from .services.avatar import DIDAvatar, DIDError, MockAvatar, StreamSessionError
from .services.brain import ClaudeBrain, MockBrain
from .services.stt import MockSTT
from .services.tts import ElevenLabsTTS, MockTTS
from .store import store


app = FastAPI(title="Big Bros Voice Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_use_mock_brain = os.environ.get("BIGBROS_USE_MOCK_BRAIN") == "1"
_brain = MockBrain() if _use_mock_brain else ClaudeBrain()
_brain_mode = "mock" if _use_mock_brain else "claude"

# The dashboard /turn path stays cheap: mock TTS and avatar, no media calls.
# The guest tablet path does real voice and lip-sync via the singletons below.
# D-ID streaming is per-browser, so the avatar cannot be driven from the
# pipeline's render() hook. The tablet endpoints orchestrate it instead.
pipeline = Pipeline(MockSTT(), _brain, MockTTS(), MockAvatar())

# Real media services, built lazily on first tablet use so offline/dashboard-only
# runs (and BIGBROS_USE_MOCK_BRAIN dev) never need ElevenLabs or D-ID keys.
_tts: Optional[ElevenLabsTTS] = None
_did: Optional[DIDAvatar] = None
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
    room: str
    stream_id: str
    session_id: str


class TabletTurn(BaseModel):
    room: str
    text: str
    stream_id: str
    session_id: str


class TabletSpeak(BaseModel):
    """Voice an already-decided reply onto a stream. Used by the browser to
    re-speak after a fresh re-handshake, without re-running the brain."""
    stream_id: str
    session_id: str
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


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "brain": _brain_mode}


@app.get("/rooms", response_model=list[RoomState])
async def rooms() -> list[RoomState]:
    return list(store.rooms.values())


@app.post("/turn", response_model=TurnResponse)
async def turn(req: TurnRequest) -> TurnResponse:
    return await pipeline.run_turn(
        room=req.room,
        text=req.text,
        guest_session_id=f"session-{req.room}",
    )


@app.post("/tickets/{ticket_id}/status", response_model=Ticket)
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


@app.post("/tablet/stream/new")
async def tablet_stream_new() -> dict:
    """Create a D-ID stream. Returns the WebRTC offer and ids for the browser
    to complete the handshake. D-ID and ElevenLabs keys never leave the server."""
    return await get_did().create_stream()


@app.post("/tablet/stream/{stream_id}/sdp")
async def tablet_stream_sdp(stream_id: str, body: StreamAnswer) -> dict:
    await get_did().send_sdp(stream_id, body.session_id, body.answer)
    return {"ok": True}


@app.post("/tablet/stream/{stream_id}/ice")
async def tablet_stream_ice(stream_id: str, body: StreamIce) -> dict:
    await get_did().send_ice(
        stream_id,
        body.session_id,
        {"candidate": body.candidate, "sdpMid": body.sdpMid, "sdpMLineIndex": body.sdpMLineIndex},
    )
    return {"ok": True}


@app.post("/tablet/stream/{stream_id}/close")
async def tablet_stream_close(stream_id: str, body: StreamClose) -> dict:
    """Tear down a stream. The browser calls this for a prior stream before
    starting a new session so we never reuse a dead peer."""
    await get_did().close(stream_id, body.session_id)
    return {"ok": True}


async def _speak_on_stream(stream_id: str, session_id: str, audio: Optional[bytes]) -> bool:
    """Speak audio on a stream. Returns True if voiced, False if the session was
    stale (recoverable by re-handshake). Non-session D-ID errors surface as 502."""
    if not audio:
        return False
    try:
        await get_did().speak_audio(stream_id, session_id, audio)
        return True
    except StreamSessionError:
        return False
    except DIDError as e:
        raise HTTPException(status_code=502, detail=f"Avatar voice unavailable: {e}")


@app.post("/tablet/ack")
async def tablet_ack(body: TabletAck) -> dict:
    """Latency mask. The instant the guest turn ends, the avatar says a short,
    content-free acknowledgment in the room's language before the heavy turn
    returns. spoken=False means the stream went stale and the browser should
    re-handshake."""
    room_state = store.get_room(body.room)
    language = room_state.language if room_state else "en"
    audio = await get_ack_audio(language, get_tts())
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return {"ok": True, "language": language, "spoken": spoken}


@app.post("/tablet/turn", response_model=TabletTurnResponse)
async def tablet_turn(body: TabletTurn) -> TabletTurnResponse:
    """One guest turn from the tablet. Runs the same pipeline as /turn (brain +
    tickets + dashboard broadcast), then voices the reply through ElevenLabs in
    the guest's language and lip-syncs it onto the caller's D-ID stream. If the
    stream is stale, tickets still land and spoken=False tells the browser to
    re-handshake and call /tablet/speak. The brain runs only once."""
    resp = await pipeline.run_turn(
        room=body.room,
        text=body.text,
        guest_session_id=f"session-{body.room}",
    )
    audio = await get_tts().speak(resp.reply, resp.language)
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return TabletTurnResponse(
        reply=resp.reply, tickets=resp.tickets, language=resp.language, spoken=spoken
    )


@app.post("/tablet/speak")
async def tablet_speak(body: TabletSpeak) -> dict:
    """Voice a known reply onto a stream without re-running the brain. The browser
    calls this after a fresh re-handshake to retry a speak that went stale."""
    audio = await get_tts().speak(body.text, body.language)
    spoken = await _speak_on_stream(body.stream_id, body.session_id, audio)
    return {"ok": True, "spoken": spoken}


@app.websocket("/ws/dashboard")
async def ws_dashboard(ws: WebSocket) -> None:
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
