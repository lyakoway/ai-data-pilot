"""Regression tests for provider streaming with thinking models and for the
explicit retry/timeout policy.

Background (found by the token benchmark): GLM thinking models stream
``reasoning_content`` deltas first and only then ``content``; the old
``stream()`` silently produced nothing during the whole reasoning phase, and
transport policy was implicit SDK defaults (600 s, hidden retries).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.llm.base import ChatMessage
from app.llm.providers import OpenAIProvider


@dataclass
class _Delta:
    content: str | None = None
    reasoning_content: str | None = None


@dataclass
class _Choice:
    delta: _Delta = field(default_factory=_Delta)


@dataclass
class _Chunk:
    choices: list[_Choice] = field(default_factory=list)


class _FakeCompletions:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    async def create(self, **_: Any) -> "_FakeStream":
        return _FakeStream(self._chunks)


class _FakeStream:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        self._iter = iter(self._chunks)
        return self

    async def __anext__(self) -> _Chunk:
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration


class _FakeClient:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self.chat = type("Chat", (), {})()
        self.chat.completions = _FakeCompletions(chunks)


def _thinking_chunks() -> list[_Chunk]:
    return [
        _Chunk([_Choice(_Delta(reasoning_content="думаю"))]),
        _Chunk([_Choice(_Delta(reasoning_content="..."))]),
        _Chunk([_Choice(_Delta(content="Ответ: 42"))]),
        _Chunk([_Choice(_Delta(content="."))]),
        # usage-only final chunk without choices must be skipped safely
        _Chunk([]),
    ]


@pytest.fixture()
def provider_with_fake_stream(monkeypatch: pytest.MonkeyPatch) -> OpenAIProvider:
    p = OpenAIProvider(model="glm-4.6")
    monkeypatch.setattr(p, "_get_client", lambda: _FakeClient(_thinking_chunks()))
    return p


async def test_stream_yields_answer_only_no_reasoning_leak(provider_with_fake_stream):
    tokens = [t async for t in provider_with_fake_stream.stream("sys", [ChatMessage("user", "q")])]
    assert tokens == ["Ответ: 42", "."]


async def test_stream_events_separates_reasoning_and_answer(provider_with_fake_stream):
    events = [e async for e in provider_with_fake_stream.stream_events("sys", [ChatMessage("user", "q")])]
    kinds = [e["type"] for e in events]
    assert kinds == ["reasoning_delta", "reasoning_delta", "answer_delta", "answer_delta"]
    assert events[0]["text"] == "думаю"
    assert events[2]["text"] == "Ответ: 42"
    # all reasoning events precede the first answer event (GLM contract)
    first_answer = kinds.index("answer_delta")
    assert all(k == "reasoning_delta" for k in kinds[:first_answer])


def test_transport_policy_explicit_and_bounded():
    p = OpenAIProvider(model="gpt-4o-mini")
    assert p.MAX_RETRIES == 3, "retries must be bounded (SDK honours Retry-After on 429)"
    assert p.REQUEST_TIMEOUT.read == 300.0
    assert p.REQUEST_TIMEOUT.connect == 10.0


def test_client_is_shared_between_calls(monkeypatch: pytest.MonkeyPatch):
    p = OpenAIProvider(model="gpt-4o-mini")
    monkeypatch.setattr(type(p), "_api_key", lambda self: "test-key")
    c1 = p._get_client()
    c2 = p._get_client()
    assert c1 is c2, "one client per provider instance (connection pooling)"
    assert c1.max_retries == 3
