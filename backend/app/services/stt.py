"""Speech-to-text interface. Real STT is ElevenLabs Scribe (audio in, text + lang out).
Mock receives text and returns it unchanged with lang='und' so the pipeline can fall back
to the room's preferred language."""

import os
import re
from typing import Protocol, Tuple

import httpx

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SCRIBE_MODEL = "scribe_v1"

# Scribe returns an ISO-639-3 language_code (e.g. "eng", "kor", "fil"). Map the
# codes we plausibly see to the two-letter tags the pipeline override, TTS, and
# ack already use. ISO-639-3 -> ISO-639-1 is NOT a prefix chop: taking the first
# two letters collides, e.g. "fil" (Filipino/Tagalog) -> "fi", which is Finnish.
# So map explicitly; unmapped codes pass through verbatim (see _to_lang).
_LANG_MAP = {
    "eng": "en",
    "kor": "ko",
    "fil": "tl",  # Filipino
    "tgl": "tl",  # Tagalog
    "spa": "es",
    "jpn": "ja",
    "cmn": "zh",  # Mandarin
    "zho": "zh",  # Chinese (macrolanguage)
    "fra": "fr",
    "deu": "de",
}


def _to_lang(code: str) -> str:
    """Resolve a Scribe language_code to the tag the pipeline/TTS/brain use.

    Known codes map to their ISO-639-1 two-letter form. An UNKNOWN code passes
    through verbatim, never truncated: the first-two-letters chop is exactly what
    produced the fil -> fi (Finnish) collision. The full ISO-639-3 code is safe
    downstream because the brain (Claude) reads ISO-639-3 directly and TTS
    (eleven_multilingual_v2) reads the language off the text, not this code.
    Passing it through preserves the guest's language for anything Scribe can
    detect; English is the fallback only when there is no detection at all."""
    if not code:
        return "en"
    return _LANG_MAP.get(code, code)

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
        language = _to_lang(code)
        return text, language
