"""LLM provider abstraction."""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


@dataclass
class ChatMessage:
    # "user" | "assistant" | "system" | "tool"
    # "tool" carries a tool-call result back to the model in the agent loop.
    role: str
    content: str


class LLMProvider(Protocol):
    provider: str
    model: str

    def available(self) -> bool: ...

    async def stream(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> AsyncIterator[str]: ...

    async def complete(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> str: ...
