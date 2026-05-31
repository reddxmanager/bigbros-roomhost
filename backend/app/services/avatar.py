"""Avatar interface. Real avatar is D-ID V4 streaming. Mock is a no-op so the pipeline runs."""

from typing import Optional, Protocol


class Avatar(Protocol):
    async def render(self, audio: Optional[bytes], expression: str = "neutral") -> None:
        ...


class MockAvatar:
    async def render(self, audio: Optional[bytes], expression: str = "neutral") -> None:
        return None
