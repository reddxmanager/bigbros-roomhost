"""Speech-to-text interface. Real STT is ElevenLabs Scribe (audio in, text + lang out).
Mock receives text and returns it unchanged with lang='und' so the pipeline can fall back
to the room's preferred language."""

from typing import Protocol, Tuple


class STT(Protocol):
    async def transcribe(self, audio_or_text) -> Tuple[str, str]:
        """Return (text, language_tag). Language 'und' means unknown."""
        ...


class MockSTT:
    async def transcribe(self, audio_or_text) -> Tuple[str, str]:
        return str(audio_or_text), "und"
