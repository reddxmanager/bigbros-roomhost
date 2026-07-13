"""Simli streaming avatar: the face layer that replaced D-ID.

Simli's model fits this product better than D-ID's did. The backend mints a
short-lived session token (the API key never leaves the server), the browser
opens WebRTC straight to Simli with that token, and the tablet streams the
ElevenLabs reply audio to Simli as PCM16, getting lip-synced video back.
Pricing is per-minute pay-as-you-go, roughly a twentieth of D-ID's rate.

Config, in backend/.env:
  SIMLI_API_KEY   from app.simli.com
  SIMLI_FACE_ID   a face created at app.simli.com (upload the same host photo
                  the tablet already shows and Simli generates the face)

Endpoints (verified against simli-client v3 source):
  POST https://api.simli.ai/compose/token   header x-simli-api-key  -> session_token
  GET  https://api.simli.ai/compose/ice     header x-simli-api-key  -> RTCIceServer[]
"""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

SIMLI_API_URL = "https://api.simli.ai"

# Session budget. maxSessionLength caps runaway cost on an abandoned tablet;
# maxIdleTime lets Simli hang up on a silent session before our own 45s
# tablet sleep would (Simli idle means no audio arriving, which is normal
# between guest turns, so keep it comfortably above the sleep timeout).
MAX_SESSION_SECONDS = 30 * 60
MAX_IDLE_SECONDS = 3 * 60


class SimliAvatar:
    """Mints browser session tokens. Fails loudly at construction if the env
    is not set, mirroring how the other services behave."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("SIMLI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "SIMLI_API_KEY is not set. "
                "Set SIMLI_API_KEY in backend/.env (gitignored) and restart the server."
            )
        self._face_id = os.environ.get("SIMLI_FACE_ID")
        if not self._face_id:
            raise RuntimeError(
                "SIMLI_FACE_ID is not set. Create a face at app.simli.com "
                "(upload the host photo), then set SIMLI_FACE_ID in backend/.env."
            )

    async def create_session(self) -> dict:
        """One browser session: a token plus ICE servers. The tablet hands the
        token to simli-client, which owns the WebRTC from there."""
        headers = {
            "Content-Type": "application/json",
            "x-simli-api-key": self._api_key,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{SIMLI_API_URL}/compose/token",
                headers=headers,
                json={
                    "faceId": self._face_id,
                    "handleSilence": True,
                    "maxSessionLength": MAX_SESSION_SECONDS,
                    "maxIdleTime": MAX_IDLE_SECONDS,
                },
            )
            if resp.status_code != 200:
                logger.error(
                    "Simli token mint failed: status=%s body=%s",
                    resp.status_code, resp.text[:300],
                )
                raise RuntimeError(f"Simli session failed ({resp.status_code})")
            token = resp.json().get("session_token")

            ice_servers: list = []
            try:
                ice = await client.get(f"{SIMLI_API_URL}/compose/ice", headers=headers)
                if ice.status_code == 200:
                    ice_servers = ice.json() or []
            except Exception as exc:
                # simli-client falls back to a public STUN server on its own,
                # so a missing ICE list degrades, never blocks.
                logger.warning("Simli ICE fetch failed (continuing): %s", exc)

        return {"session_token": token, "ice_servers": ice_servers}
