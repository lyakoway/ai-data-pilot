"""Concrete LLM providers: mock, OpenAI, Anthropic, Z.ai, Ollama."""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator

import httpx

from app.config import get_settings
from app.llm.base import ChatMessage

settings = get_settings()


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\S+\s*", text) or [text]


def _norm_role(role: str) -> str:
    """Normalise a ChatMessage role for provider payloads.

    The agent loop uses role ``"tool"`` to carry tool results. Provider chat APIs
    only accept ``"tool"`` alongside their native function-calling schema, so in
    our prompt-based flow we fold tool results into the ``"user"`` turn.
    """
    return "user" if role == "tool" else role


class MockProvider:
    provider = "mock"

    def __init__(self, model: str = "mock") -> None:
        self.model = model

    def available(self) -> bool:
        return True

    async def stream(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> AsyncIterator[str]:
        text = await self.complete(system, messages, lang)
        for token in _tokenize(text):
            await asyncio.sleep(0.01)
            yield token

    async def complete(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> str:
        # Structured JSON requests (SQL planner / analyzer) are handled upstream
        # in agents with deterministic mock fallbacks. Here we only answer free text.
        question = messages[-1].content if messages else ""
        if "SQL_PLAN" in system or "Верни ТОЛЬКО JSON" in system or "Return ONLY JSON" in system:
            return ""
        if lang == "en":
            return (
                f"Demo reply for “{question}”. "
                "Connect OpenAI / Anthropic / Z.ai / Ollama for full answers."
            )
        return (
            f"Демо-ответ на «{question}». "
            "Подключите OpenAI / Anthropic / Z.ai / Ollama для полного режима."
        )


class OpenAIProvider:
    provider = "openai"
    base_url: str | None = None

    def __init__(self, model: str = "gpt-4o-mini") -> None:
        self.model = model

    def _api_key(self) -> str | None:
        return settings.openai_api_key

    def available(self) -> bool:
        return bool(self._api_key())

    async def stream(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> AsyncIterator[str]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key(), base_url=self.base_url)
        payload = [{"role": "system", "content": system}] + [
            {"role": _norm_role(m.role), "content": m.content} for m in messages
        ]
        stream = await client.chat.completions.create(
            model=self.model, messages=payload, stream=True, temperature=0.2
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield delta

    async def complete(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=self._api_key(), base_url=self.base_url)
        payload = [{"role": "system", "content": system}] + [
            {"role": _norm_role(m.role), "content": m.content} for m in messages
        ]
        resp = await client.chat.completions.create(
            model=self.model, messages=payload, temperature=0.1
        )
        return resp.choices[0].message.content or ""


class ZaiProvider(OpenAIProvider):
    provider = "zai"
    base_url = "https://api.z.ai/api/paas/v4"

    def __init__(self, model: str = "glm-4.6") -> None:
        self.model = model

    def _api_key(self) -> str | None:
        return settings.zai_api_key


class AnthropicProvider:
    provider = "anthropic"

    def __init__(self, model: str = "claude-sonnet-5") -> None:
        self.model = model

    def available(self) -> bool:
        return bool(settings.anthropic_api_key)

    async def stream(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> AsyncIterator[str]:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        payload = [{"role": _norm_role(m.role), "content": m.content} for m in messages if m.role != "system"]
        async with client.messages.stream(
            model=self.model,
            system=system,
            messages=payload,
            max_tokens=2048,
            temperature=0.2,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def complete(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> str:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        payload = [{"role": _norm_role(m.role), "content": m.content} for m in messages if m.role != "system"]
        resp = await client.messages.create(
            model=self.model,
            system=system,
            messages=payload,
            max_tokens=2048,
            temperature=0.1,
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts)


class OllamaProvider:
    provider = "ollama"

    def __init__(self, model: str = "llama3.1") -> None:
        self.model = model

    def available(self) -> bool:
        try:
            r = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=1.0)
            if r.status_code != 200:
                return False
            names = {m.get("name", "") for m in r.json().get("models", [])}
            return self.model in names or f"{self.model}:latest" in names
        except Exception:
            return False

    async def stream(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> AsyncIterator[str]:
        import json

        payload: dict = {
            "model": self.model,
            "stream": True,
            "messages": [{"role": "system", "content": system}]
            + [{"role": _norm_role(m.role), "content": m.content} for m in messages],
        }
        if settings.ollama_num_gpu is not None:
            payload["options"] = {"num_gpu": settings.ollama_num_gpu}
        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "POST", f"{settings.ollama_base_url}/api/chat", json=payload
            ) as resp:
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    token = data.get("message", {}).get("content")
                    if token:
                        yield token

    async def complete(
        self, system: str, messages: list[ChatMessage], lang: str = "ru"
    ) -> str:
        chunks: list[str] = []
        async for token in self.stream(system, messages, lang):
            chunks.append(token)
        return "".join(chunks)
