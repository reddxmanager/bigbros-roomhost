"""Avatar interface plus the real D-ID streaming service.

MockAvatar is a no-op kept in the pipeline path so /turn runs without a face.

DIDAvatar is the real service. D-ID streaming is inherently per-browser: the
WebRTC peer lives in the guest tablet, so lip-sync cannot be driven from the
pipeline's render(audio) hook, which has no stream to target. Instead the
tablet endpoints use DIDAvatar to create a stream, relay the browser's WebRTC
answer and ICE candidates, and speak audio onto that stream. The face stays
face-and-mouth-only: it lip-syncs audio we generate with ElevenLabs, it does
not run its own brain or voice. This keeps the visible-routing seam at Claude,
per the locked architecture."""

import base64
import logging
import os
from typing import Any, Optional, Protocol

import httpx

logger = logging.getLogger(__name__)

DID_BASE = "https://api.d-id.com"
# A neutral default presenter image if none is configured. One avatar for now.
DEFAULT_AVATAR_IMAGE = (
    "https://create-images-results.d-id.com/DefaultPresenters/Emma_f/image.jpeg"
)


class DIDError(RuntimeError):
    """Any non-2xx from D-ID, after logging its kind and description."""


class StreamSessionError(DIDError):
    """The stream session is no longer valid (expired or closed). Recoverable by
    re-handshaking a fresh stream in the browser and retrying the speak."""


class Avatar(Protocol):
    async def render(self, audio: Optional[bytes], expression: str = "neutral") -> None:
        ...


class MockAvatar:
    async def render(self, audio: Optional[bytes], expression: str = "neutral") -> None:
        return None


class DIDAvatar:
    """Real D-ID V4 streaming avatar (legacy Talks Streams, the only path that
    lip-syncs our own ElevenLabs audio rather than D-ID's built-in voice).

    Reads DID_API_KEY from the environment (backend/.env) and fails loudly,
    naming the file and variable, if absent. DID_AVATAR_IMAGE_URL selects the
    presenter image; it falls back to a D-ID default if unset."""

    def __init__(self) -> None:
        key = os.environ.get("DID_API_KEY")
        if not key:
            raise RuntimeError(
                "DID_API_KEY is not set. "
                "Set DID_API_KEY in backend/.env (gitignored) and restart the server."
            )
        # D-ID keys come either as a ready-to-use base64 credential or as the
        # raw "username:password" pair. Encode the pair if we were given one.
        if ":" in key and not key.endswith("="):
            key = base64.b64encode(key.encode()).decode()
        self._auth = f"Basic {key}"
        self._avatar_image = os.environ.get("DID_AVATAR_IMAGE_URL") or DEFAULT_AVATAR_IMAGE

    def _headers(self, extra: Optional[dict] = None) -> dict:
        h = {"Authorization": self._auth, "Content-Type": "application/json"}
        if extra:
            h.update(extra)
        return h

    @staticmethod
    def _check(resp: httpx.Response, context: str) -> None:
        """Raise a legible, typed error on any D-ID 4xx/5xx. We no longer discard
        D-ID's JSON body: we log its kind and description, and distinguish a stale
        session (recoverable) from everything else."""
        if resp.status_code < 400:
            return
        kind: Optional[str] = None
        description: str = resp.text
        try:
            data = resp.json()
            kind = data.get("kind")
            description = data.get("description", resp.text)
        except Exception:
            pass
        logger.warning(
            "D-ID %s failed: status=%s kind=%s description=%s",
            context, resp.status_code, kind, description,
        )
        stale = kind == "SessionError" or (
            isinstance(description, str) and "session_id" in description.lower()
        )
        if resp.status_code == 400 and stale:
            raise StreamSessionError(description or "stale session")
        raise DIDError(f"D-ID {context} failed ({resp.status_code}): {kind}: {description}")

    async def create_stream(self) -> dict[str, Any]:
        """Create a stream. Returns id, session_id, the SDP offer, and ice_servers
        for the browser to complete the WebRTC handshake."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DID_BASE}/talks/streams",
                headers=self._headers(),
                json={"source_url": self._avatar_image, "stream_warmup": True},
            )
            self._check(resp, "create_stream")
            data = resp.json()
        return {
            "id": data["id"],
            "session_id": data["session_id"],
            "offer": data["offer"],
            "ice_servers": data.get("ice_servers", []),
            # The presenter image, so the browser can show a static host face in
            # the tile until the first real frame paints (no blank grey ring).
            "source_url": self._avatar_image,
        }

    async def send_sdp(self, stream_id: str, session_id: str, answer: dict) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DID_BASE}/talks/streams/{stream_id}/sdp",
                headers=self._headers(),
                json={"answer": answer, "session_id": session_id},
            )
            self._check(resp, "send_sdp")

    async def send_ice(self, stream_id: str, session_id: str, candidate: dict) -> None:
        # The browser sends {candidate, sdpMid, sdpMLineIndex}. A null candidate
        # signals end-of-candidates, which D-ID accepts.
        body = {"session_id": session_id, **candidate}
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DID_BASE}/talks/streams/{stream_id}/ice",
                headers=self._headers(),
                json=body,
            )
            self._check(resp, "send_ice")

    async def _upload_audio(self, audio: bytes) -> str:
        """Upload mp3 bytes to D-ID and return the url D-ID hosts. This is how our
        ElevenLabs audio becomes reachable for lip-sync without exposing localhost."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DID_BASE}/audios",
                headers={"Authorization": self._auth},
                files={"audio": ("reply.mp3", audio, "audio/mpeg")},
            )
            self._check(resp, "upload_audio")
            data = resp.json()
        url = data.get("url")
        if not url:
            raise DIDError(f"D-ID /audios returned no url: {data}")
        return url

    async def speak_audio(self, stream_id: str, session_id: str, audio: bytes) -> None:
        """Lip-sync the given mp3 audio onto an existing stream. Raises
        StreamSessionError if the stream session has gone stale."""
        audio_url = await self._upload_audio(audio)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{DID_BASE}/talks/streams/{stream_id}",
                headers=self._headers(),
                json={
                    "script": {"type": "audio", "audio_url": audio_url},
                    "config": {"stitch": True},
                    "session_id": session_id,
                },
            )
            self._check(resp, "speak_audio")

    async def close(self, stream_id: str, session_id: str) -> None:
        async with httpx.AsyncClient(timeout=30.0) as client:
            await client.request(
                "DELETE",
                f"{DID_BASE}/talks/streams/{stream_id}",
                headers=self._headers(),
                json={"session_id": session_id},
            )

    async def render(self, audio: Optional[bytes], expression: str = "neutral") -> None:
        # Interface compatibility only. Real lip-sync is per-browser-stream and
        # is driven by the tablet endpoints via speak_audio, not from here.
        return None
