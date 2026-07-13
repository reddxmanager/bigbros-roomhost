"""Auth and rate limiting. Two credentials, one limiter.

Staff surfaces (dashboard websocket, ticket status flips, /rooms, the typed
/turn path) require the shared staff key. Tablet surfaces require a device
token, and the token IS the room identity: the server resolves room from
token so a browser can never impersonate a suite by editing a URL.

Dev mode: set BIGBROS_DEV_OPEN=1 to run with no credentials configured
(local development, mock brain runs). In dev-open mode the tablet falls back
to the client-sent room. Never set BIGBROS_DEV_OPEN on a deployed backend.
"""

import logging
import os
import secrets
import time
from collections import defaultdict, deque
from typing import Optional

from fastapi import Header, HTTPException, Query

logger = logging.getLogger(__name__)

STAFF_KEY = os.environ.get("BIGBROS_STAFF_KEY", "")
DEV_OPEN = os.environ.get("BIGBROS_DEV_OPEN") == "1"

# BIGBROS_DEVICE_TOKENS format: "token1:room1,token2:room2"
# Example: "a1b2c3:4,d4e5f6:honeymoon"
_raw_tokens = os.environ.get("BIGBROS_DEVICE_TOKENS", "")
DEVICE_TOKENS: dict[str, str] = {}
for pair in _raw_tokens.split(","):
    pair = pair.strip()
    if not pair:
        continue
    token, _, room = pair.partition(":")
    if token and room:
        DEVICE_TOKENS[token.strip()] = room.strip()


def validate_boot_config() -> None:
    """Refuse to boot half-locked. Either credentials are configured, or the
    operator explicitly opted into open dev mode. No silent fail-open."""
    if DEV_OPEN:
        logger.warning(
            "BIGBROS_DEV_OPEN=1: auth is DISABLED. Local development only."
        )
        return
    problems = []
    if not STAFF_KEY:
        problems.append("BIGBROS_STAFF_KEY is not set")
    if not DEVICE_TOKENS:
        problems.append("BIGBROS_DEVICE_TOKENS is not set")
    if problems:
        raise RuntimeError(
            "Refusing to start without auth: "
            + "; ".join(problems)
            + ". Set them in backend/.env (see .env.example), "
            "or set BIGBROS_DEV_OPEN=1 for local development only."
        )


def staff_key_ok(key: Optional[str]) -> bool:
    if DEV_OPEN and not STAFF_KEY:
        return True
    return bool(key) and bool(STAFF_KEY) and secrets.compare_digest(key, STAFF_KEY)


async def require_staff(x_staff_key: Optional[str] = Header(default=None)) -> None:
    """Dependency for staff HTTP endpoints. The websocket path checks the key
    itself via staff_key_ok, since headers are awkward from browser websockets."""
    if not staff_key_ok(x_staff_key):
        raise HTTPException(status_code=401, detail="Missing or invalid staff key")


async def require_device(
    x_device_token: Optional[str] = Header(default=None),
    room: Optional[str] = Query(default=None),
) -> str:
    """Dependency for tablet endpoints. Returns the room this device is bound
    to. The room query param is honored only in dev-open mode with no tokens
    configured, so local development keeps working without provisioning."""
    if x_device_token and x_device_token in DEVICE_TOKENS:
        return DEVICE_TOKENS[x_device_token]
    if DEV_OPEN and not DEVICE_TOKENS:
        return room or "4"
    raise HTTPException(status_code=401, detail="Missing or invalid device token")


class RateLimiter:
    """In-memory sliding window. Good enough for a single backend instance,
    which is what we run. Keyed by credential (device token or staff key
    presence) plus endpoint bucket, so one hot tablet cannot starve another."""

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> None:
        now = time.monotonic()
        q = self._hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Slow down and try again shortly.",
            )
        q.append(now)


limiter = RateLimiter()

# Per-minute budgets. Generous for one human on a tablet, hostile to a loop.
TURN_PER_MIN = 12          # brain + TTS + D-ID, the expensive path
LISTEN_PER_MIN = 30        # push-to-talk clips
STREAM_NEW_PER_MIN = 6     # D-ID stream creation
SPEAK_PER_MIN = 30         # ack/speak re-voices
MAX_AUDIO_BYTES = 10 * 1024 * 1024  # 10 MB clip cap for /tablet/listen
