"""Tests for the ReAct agent loop (multi-step questions)."""
from __future__ import annotations

import json

import pytest

from app.agents import oleg as oleg_module
from app.agents.oleg import _is_complex_question, run_oleg


# --------------------------------------------------------------------------- #
# Question classifier
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Покажи топ городов", False),
        ("Выручка по регионам за 30 дней", False),
        ("Почему выручка упала в июле?", True),
        ("Сравни июнь и июль", True),
        ("Найди причины оттока", True),
        ("Какая динамика подписок?", True),
        ("Why did revenue drop?", True),
        ("Compare Q1 and Q2", True),
        ("Топ-10 городов", False),
    ],
)
def test_classifier(question: str, expected: bool):
    assert _is_complex_question(question) is expected


# --------------------------------------------------------------------------- #
# Mock loop (demo mode) — scripted compare-periods scenario
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mock_loop_runs_multi_step(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg(
        "Почему выручка упала в июле по сравнению с июнем?", model_id="mock", lang="ru"
    )
    assert r["status"] == "demo"
    tools = [s["tool"] for s in r["steps"]]
    # The scripted loop queries data, computes changes, analyzes, finishes.
    assert "database_query" in tools
    assert "calculate" in tools
    assert "analyze" in tools
    assert "finish" in tools
    assert len(r["steps"]) >= 4  # multi-step, not linear


@pytest.mark.asyncio
async def test_mock_loop_answer_mentions_change(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg("Почему выручка упала?", model_id="mock", lang="ru")
    # The deterministic answer includes a percent change figure.
    assert "%" in r["answer"]
    assert r["insights"]  # analyze ran
    assert r["chart"] is not None  # chart assembled from the result set


@pytest.mark.asyncio
async def test_mock_loop_has_chart_and_rows(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg("Сравни выручку по месяцам", model_id="mock", lang="ru")
    assert r["row_count"] > 0
    assert r["columns"]  # month, revenue, rides
    assert r["chart"]["type"] == "bar"


# --------------------------------------------------------------------------- #
# Simple questions still use the linear flow (hybrid router)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_simple_question_uses_linear_flow(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg("Покажи топ городов по поездкам", model_id="mock", lang="ru")
    tools = [s["tool"] for s in r["steps"]]
    # Linear flow, not loop: planner → database_query → analyze → chart → answer.
    assert tools == ["planner", "database_query", "analyze", "chart", "answer"]
    assert "calculate" not in tools
    assert "finish" not in tools


# --------------------------------------------------------------------------- #
# Real-model loop with a scripted (fake) provider
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_run_loop_reaches_finish(tmp_db, monkeypatch, fake_provider_factory):
    """A real provider drives the loop through tool-calls until finish."""
    # Scripted decisions: query → analyze → finish.
    decisions = [
        json.dumps({
            "thought": "Сначала получу выручку по месяцам",
            "tool": "database_query",
            "args": {"sql": (
                "SELECT strftime('%Y-%m', ride_date) AS month, "
                "ROUND(SUM(revenue_rub), 0) AS revenue "
                "FROM fact_rides WHERE is_partner = 0 "
                "GROUP BY month ORDER BY month DESC LIMIT 3"
            )},
        }),
        json.dumps({
            "thought": "Проанализирую результат",
            "tool": "analyze",
            "args": {},  # analyze will use the previous result via messages context
        }),
        json.dumps({
            "thought": "Достаточно данных для ответа",
            "tool": "finish",
            "args": {"answer": "Выручка изменилась. См. детали выше."},
        }),
    ]
    fake = fake_provider_factory(responses=decisions, provider="openai")
    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: fake)

    r = await run_oleg("Почему выручка упала в июле?", model_id="openai:gpt-4o", lang="ru")
    assert r["status"] == "ok"
    tools = [s["tool"] for s in r["steps"]]
    # Loop emits agent(reason) + tool steps. Must include finish.
    assert "finish" in tools
    assert "agent" in tools  # reasoning steps
    assert "Выручка изменилась" in r["answer"]
    # provider called once per decision (3)
    assert fake.calls == 3


@pytest.mark.asyncio
async def test_run_loop_step_limit(tmp_db, monkeypatch, fake_provider_factory):
    """If the model never calls finish, the loop hits MAX_LOOP_STEPS and errors."""
    from app.agents.tools import MAX_LOOP_STEPS

    # Always returns a database_query decision, never finish.
    looping = json.dumps({
        "thought": "ещё запрос",
        "tool": "database_query",
        "args": {"sql": "SELECT 1"},
    })
    fake = fake_provider_factory(responses=[looping], provider="openai")
    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: fake)

    r = await run_oleg("Почему выручка упала?", model_id="openai:gpt-4o", lang="ru")
    assert r["status"] == "error"
    # Exactly MAX_LOOP_STEPS iterations.
    db_steps = [s for s in r["steps"] if s["tool"] == "database_query"]
    assert len(db_steps) == MAX_LOOP_STEPS


@pytest.mark.asyncio
async def test_run_loop_handles_invalid_model_output(tmp_db, monkeypatch, fake_provider_factory):
    """If the model returns garbage, the loop stops gracefully (not a crash)."""
    fake = fake_provider_factory(responses=["это не json"], provider="openai")
    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: fake)

    r = await run_oleg("Почему выручка упала в июле?", model_id="openai:gpt-4o", lang="ru")
    assert r["status"] == "error"
    # The agent reasoning step errored.
    assert any(s["tool"] == "agent" and s["status"] == "error" for s in r["steps"])
