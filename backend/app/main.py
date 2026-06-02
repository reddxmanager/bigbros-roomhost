"""FastAPI entry point. POST /turn drives one guest turn. WS /ws/dashboard streams ticket events."""

import os

from dotenv import load_dotenv

load_dotenv()

if not os.environ.get("ANTHROPIC_API_KEY"):
    raise RuntimeError(
        "ANTHROPIC_API_KEY is not set. "
        "Set ANTHROPIC_API_KEY in backend/.env (gitignored — see backend/.env.example) "
        "and restart the server. "
        "To run with the mocked brain instead, set BIGBROS_USE_MOCK_BRAIN=1."
    )

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .pipeline import Pipeline
from .schema import RoomState, TurnRequest, TurnResponse
from .services.avatar import MockAvatar
from .services.brain import ClaudeBrain, MockBrain
from .services.stt import MockSTT
from .services.tts import MockTTS
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

pipeline = Pipeline(MockSTT(), _brain, MockTTS(), MockAvatar())


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
