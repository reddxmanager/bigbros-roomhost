"""Speech-to-text interface. Real STT is ElevenLabs Scribe (audio in, text + lang out).
Mock receives text and returns it unchanged with lang='und' so the pipeline can fall back
to the room's preferred language."""

import os
import re
from typing import Protocol, Tuple

import httpx

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL = "scribe_v1"

# Scribe returns an ISO-639-3 language_code (e.g. "eng", "kor"). Map the ones we
# voice to the two-letter codes the pipeline override, TTS, and ack already use.
# Anything else falls back to its first two letters, then to English.
_LANG_MAP = {"eng": "en", "kor": "ko"}

# Scribe annotates non-speech audio events in parentheses or brackets, e.g.
# "(whistling)", "(cat meowing)", "[laughter]". Strip these so they never reach
# the transcript box or the brain. A capture that is only noise then cleans to
# empty, and the tablet declines to fire a turn (resets to "Tap to speak").
_NON_SPEECH = re.compile(r"[\(\[][^)\]]*[\)\]]")


def _strip_non_speech(text: str) -> str:
    cleaned = _NON_SPEECH.sub(" ", text)
    # Collapse the whitespace the removals leave behind, and trim the ends.
    return " ".join(cleaned.split())


class STT(Protocol):
    async def transcribe(self, audio_or_text) -> Tuple[str, str]:
        """Return (text, language_tag). Language 'und' means unknown."""
        ...


class MockSTT:
    async def transcribe(self, audio_or_text) -> Tuple[str, str]:
        return str(audio_or_text), "und"


class ElevenLabsScribeSTT:
    """Real ElevenLabs Scribe transcription. Takes audio bytes, returns the
    transcript plus the detected language (mapped to our two-letter codes).

    Reads ELEVENLABS_API_KEY from the environment (loaded from backend/.env) and
    fails loudly, naming the file and variable, if absent."""

    def __init__(self) -> None:
        self._api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "ELEVENLABS_API_KEY is not set. "
                "Set ELEVENLABS_API_KEY in backend/.env (gitignored) and restart the server."
            )

    async def transcribe(
        self,
        audio: bytes,
        filename: str = "clip.webm",
        content_type: str = "audio/webm",
    ) -> Tuple[str, str]:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                ELEVENLABS_STT_URL,
                headers={"xi-api-key": self._api_key},
                data={"model_id": SCRIBE_MODEL},
                files={"file": (filename, audio, content_type)},
            )
            resp.raise_for_status()
            data = resp.json()
        text = _strip_non_speech((data.get("text") or "").strip())
        code = (data.get("language_code") or "").lower()
        language = _LANG_MAP.get(code, code[:2] if code else "en")
        return text, language
