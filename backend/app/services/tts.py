"""Text-to-speech interface.

MockTTS returns None (no audio) so the pipeline runs silently in dev.
ElevenLabsTTS is the real service: it turns the brain's reply text into mp3
audio. The voice is one multilingual voice, so the Scribe language tag does
not need to pick a different voice. The text itself carries the language and
eleven_multilingual_v2 speaks it. We keep the same speak(text, language)
signature so the pipeline does not change shape."""

import os
from typing import Optional, Protocol

import httpx

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL = "eleven_multilingual_v2"


class TTS(Protocol):
    async def speak(self, text: str, language: str) -> Optional[bytes]:
        ...


class MockTTS:
    async def speak(self, text: str, language: str) -> Optional[bytes]:
        return None


class ElevenLabsTTS:
    """Real ElevenLabs TTS. Returns mp3 bytes (mp3_44100_128).

    Reads ELEVENLABS_API_KEY and ELEVENLABS_VOICE_ID from the environment
    (loaded from backend/.env). Fails loudly, naming the file and the variable,
    if either is absent."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. "
                "Set ELEVENLABS_API_KEY in backend/.env (gitignored) and restart the server."
            )
        self._voice_id = os.environ.get("ELEVENLABS_VOICE_ID")
        if not self._voice_id:
            raise RuntimeError(
                "ELEVENLABS_VOICE_ID is not set. "
                "Set ELEVENLABS_VOICE_ID in backend/.env (gitignored) and restart the server."
            )

    async def speak(self, text: str, language: str) -> Optional[bytes]:
        if not text.strip():
            return None
        url = ELEVENLABS_TTS_URL.format(voice_id=self._voice_id)
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                url,
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json={
                    "text": text,
                    "model_id": ELEVENLABS_MODEL,
                    "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
                },
            )
            resp.raise_for_status()
            return resp.content
