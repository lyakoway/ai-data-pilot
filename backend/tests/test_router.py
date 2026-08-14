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
        # Data questions → Oleg
        ("Топ-10 городов по поездкам", "oleg"),
        ("Сколько выручки в июле?", "oleg"),
        ("Почему выручка упала в июле?", "oleg"),
        ("Сравни июнь и июль по продажам", "oleg"),
        ("Выгрузи в Excel активных пользователей", "oleg"),
        ("Динамика подписок по месяцам", "oleg"),
        ("How many active users?", "oleg"),
        # Docs questions → Ksyusha
        ("Как считается utilization?", "ksyusha"),
        ("Где хранится utilization?", "ksyusha"),
        ("Какой TTL у Redis pricing cache?", "ksyusha"),
        ("Что делает кнопка Reset errors?", "ksyusha"),
        ("Расскажи про data lineage", "ksyusha"),
        ("Как работает антифрод?", "ksyusha"),
        # Ambiguous → Oleg (primary agent)
        ("Привет", "oleg"),
        ("Что нового?", "oleg"),
    ],
)
def test_heuristic(question: str, expected: str):
    assert _heuristic_route(question) == expected


def test_docs_beats_data_on_overlap():
    # "как считается выручка" mentions a metric but asks HOW it's computed → docs.
    assert _heuristic_route("Как считается выручка?") == "ksyusha"
    # "выручка по регионам" is a data request → oleg.
    assert _heuristic_route("Выручка по регионам за 30 дней") == "oleg"


# --------------------------------------------------------------------------- #
# LLM classification (fake provider) + fallbacks
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_llm_route_docs(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["DOCS"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    assert await route_agent("anything", model_id="openai:gpt-4o") == "ksyusha"


@pytest.mark.asyncio
async def test_llm_route_data(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["DATA"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    assert await route_agent("anything", model_id="openai:gpt-4o") == "oleg"


@pytest.mark.asyncio
async def test_llm_garbage_falls_back_to_heuristic(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(responses=["не знаю, наверное то самое"], provider="openai")
    monkeypatch.setattr(router_module, "get_provider", lambda mid: fake)
    # Heuristic says docs for this question.
    assert await route_agent("Как считается utilization?", model_id="openai:gpt-4o") == "ksyusha"


@pytest.mark.asyncio
async def test_llm_failure_falls_back_to_heuristic(tmp_db, monkeypatch):
    class Broken:
        provider = "openai"

        async def complete(self, *a, **k):
            raise RuntimeError("429 insufficient balance")

    monkeypatch.setattr(router_module, "get_provider", lambda mid: Broken())
    assert await route_agent("Топ городов", model_id="openai:gpt-4o") == "oleg"


@pytest.mark.asyncio
async def test_mock_provider_uses_heuristic(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(router_module, "get_provider", lambda mid: MockProvider())
    assert await route_agent("Где хранится utilization?") == "ksyusha"


# --------------------------------------------------------------------------- #
# Chat integration: agent="auto" routes and prepends the router step
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_chat_auto_routes_to_ksyusha(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    # Mock provider → heuristic → docs question goes to Ksyusha.
    import app.agents.router as rm

    monkeypatch.setattr(rm, "get_provider", lambda mid: MockProvider())

    body = ChatRequest(message="Как считается utilization?", agent="auto", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "ksyusha"
    assert step is not None
    assert step["tool"] == "router"
    assert step["status"] == "done"
    assert "Ксюше" in step["summary"]


@pytest.mark.asyncio
async def test_chat_auto_routes_to_oleg(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    import app.agents.router as rm

    monkeypatch.setattr(rm, "get_provider", lambda mid: MockProvider())

    body = ChatRequest(message="Топ-10 городов по поездкам", agent="auto", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "oleg"
    assert "Олегу" in step["summary"]


@pytest.mark.asyncio
async def test_manual_agent_has_no_router_step(tmp_db):
    from app.api.routes.chat import _resolve_agent
    from app.schemas.dto import ChatRequest

    body = ChatRequest(message="что угодно", agent="oleg", model="mock")
    agent, step = await _resolve_agent(body, body.message)
    assert agent == "oleg"
    assert step is None
