"""Text-to-speech interface. Real TTS is ElevenLabs, voice selected by language tag.
Mock returns None (no audio) so the pipeline runs without producing sound."""

from typing import Optional, Protocol


class TTS(Protocol):
    async def speak(self, text: str, language: str) -> Optional[bytes]:
        ...


class MockTTS:
    async def speak(self, text: str, language: str) -> Optional[bytes]:
        return None
