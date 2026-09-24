"""Tests for the auto-router: heuristic + LLM classification + chat integration."""
from __future__ import annotations

import pytest

from app.agents import router as router_module
from app.agents.router import _heuristic_route, route_agent


# --------------------------------------------------------------------------- #
# Heuristic routing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question, expected",
    [
        # Data questions → Atlas
        ("Топ-10 городов по поездкам", "atlas"),
        ("Сколько выручки в июле?", "atlas"),
        ("Почему выручка упала в июле?", "atlas"),
        ("Сравни июнь и июль по продажам", "atlas"),
        ("Выгрузи в Excel активных пользователей", "atlas"),
        ("Динамика подписок по месяцам", "atlas"),
        ("How many active users?", "atlas"),
        # Docs questions → Doc
        ("Как считается utilization?", "doc"),
        ("Где хранится utilization?", "doc"),
        ("Какой TTL у Redis pricing cache?", "doc"),
        ("Что делает кнопка Reset errors?", "doc"),
        ("Расскажи про data lineage", "doc"),
        ("Как работает антифрод?", "doc"),
        # Ambiguous → Atlas (primary agent)
        ("Привет", "atlas"),
        ("Что нового?", "atlas"),
    ],
)
def test_heuristic(question: str, expected: str):
    assert _heuristic_route(question) == expected


def test_docs_beats_data_on_overlap():
    # "как считается выручка" mentions a metric but asks HOW it's computed → docs.
    assert _heuristic_route("Как считается выручка?") == "doc"
    # "выручка по регионам" is a data request → atlas.
    assert _heuristic_route("Выручка по регионам за 30 дней") == "atlas"


# --------------------------------------------------------------------------- #
# LLM classification (fake provider) + fallbacks
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_route_docs(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["DOCS"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    assert await route_agent("anything", model_id="openai:gpt-4o") == "doc"


@pytest.mark.asyncio
async def test_llm_route_data(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["DATA"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    assert await route_agent("anything", model_id="openai:gpt-4o") == "atlas"


@pytest.mark.asyncio
async def test_llm_garbage_falls_back_to_heuristic(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["не знаю, наверное то самое"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    # Heuristic says docs for this question.
    assert await route_agent("Как считается utilization?", model_id="openai:gpt-4o") == "doc"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_heuristic(tmp_db, monkeypatch):
    class Broken:
        provider = "openai"

        async def complete(self, *a, **k):
            raise RuntimeError("429 insufficient balance")

    monkeypatch.setattr(router_module, "get_provider", lambda mid: Broken())
    assert await route_agent("Топ городов", model_id="openai:gpt-4o") == "atlas"


@pytest.mark.asyncio
async def test_mock_provider_uses_heuristic(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(router_module, "get_provider", lambda mid: MockProvider())
    assert await route_agent("Где хранится utilization?") == "doc"


# --------------------------------------------------------------------------- #
# Chat integration: agent="auto" routes and prepends the router step
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_auto_routes_to_doc(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    # Mock provider → heuristic → docs question goes to Doc.
    import app.agents.router as rm

    monkeypatch.setattr(rm, "get_provider", lambda mid: MockProvider())

    body = ChatRequest(message="Как считается utilization?", agent="auto", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "doc"
    assert step is not None
    assert step["tool"] == "router"
    assert step["status"] == "done"
    assert "Доку" in step["summary"]


@pytest.mark.asyncio
async def test_chat_auto_routes_to_atlas(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    import app.agents.router as rm

    monkeypatch.setattr(rm, "get_provider", lambda mid: MockProvider())

    body = ChatRequest(message="Топ-10 городов по поездкам", agent="auto", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "atlas"
    assert "Атласу" in step["summary"]


@pytest.mark.asyncio
async def test_manual_agent_has_no_router_step(tmp_db):
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    body = ChatRequest(message="что угодно", agent="atlas", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "atlas"
    assert step is None
