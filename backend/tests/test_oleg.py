"""Tests for Oleg's self-correction loop and analytics integration.

These tests monkeypatch ``app.agents.oleg.get_provider`` to inject a scripted
provider, so no real LLM / API keys are required. They run against the isolated
temp DB provided by the ``tmp_db`` fixture.
"""
from __future__ import annotations

import json

import pytest

from app.agents import oleg as oleg_module
from app.agents.oleg import run_oleg, run_oleg_streaming


def _plan(sql: str, **extra) -> str:
    """Build a JSON plan string as the LLM would return it."""
    payload = {
        "sql": sql,
        "chart_type": None,
        "wants_excel": False,
        "tables_used": ["fact_rides"],
        "logic": "test plan",
    }
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _patch_provider(monkeypatch: pytest.MonkeyPatch, fake):
    """Redirect ``get_provider`` inside oleg to return ``fake``."""
    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: fake)


# --- Demo mode (mock provider): deterministic, status=demo ---


@pytest.mark.asyncio
async def test_demo_mode_returns_demo_status_and_insights(tmp_db, monkeypatch):
    # Mock provider triggers the deterministic demo path (no LLM calls).
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    r = await run_oleg("Покажи выручку по регионам за 30 дней", model_id="mock", lang="ru")

    assert r["status"] == "demo"
    assert r["row_count"] > 0
    assert "highlights" in r["insights"]
    assert len(r["insights"]["highlights"]) > 0
    assert r["warnings"] == []


@pytest.mark.asyncio
async def test_demo_mode_kpis_are_consistent(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    r = await run_oleg("топ городов по поездкам", model_id="mock", lang="ru")
    assert r["status"] == "demo"
    assert r["columns"][0] == "city_name"
    # Top city must be Москва or Санкт-Петербург (largest in seed).
    assert r["rows"][0][0] in {"Москва", "Санкт-Петербург"}


# --- Real-model path: self-correction ---


@pytest.mark.asyncio
async def test_self_correction_repairs_broken_sql(tmp_db, monkeypatch, fake_provider_factory):
    # Call 1: broken column. Call 2 (repair): fixed query. Call 3 (answer): prose.
    fake = fake_provider_factory(
        responses=[
            _plan("SELECT bad_col FROM fact_rides LIMIT 5"),
            _plan(
                "SELECT city_id, COUNT(*) AS rides FROM fact_rides GROUP BY city_id ORDER BY rides DESC LIMIT 5",
                chart_type="bar",
                logic="fixed",
            ),
            "Данные получены после исправления.",
        ],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("test", model_id="openai:gpt-4o-mini", lang="ru")

    assert r["status"] == "ok"  # NOT demo — real-model path
    assert r["row_count"] == 5
    assert fake.calls == 3  # plan + repair + answer
    assert any("коррекц" in w.lower() for w in r["warnings"])


@pytest.mark.asyncio
async def test_honest_failure_when_sql_cannot_be_repaired(tmp_db, monkeypatch, fake_provider_factory):
    # Provider keeps returning the same broken SQL → no progress → honest error.
    fake = fake_provider_factory(
        responses=[_plan("SELECT nonexistent_col FROM fact_rides LIMIT 5")],
        provider="anthropic",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("unfixable", model_id="anthropic:claude-sonnet-5", lang="ru")

    assert r["status"] == "error"
    assert r["row_count"] == 0  # no silent mock substitution
    assert r["rows"] == []
    assert "Не удалось" in r["answer"] or "не удалось" in r["answer"].lower()


@pytest.mark.asyncio
async def test_guard_error_not_retried(tmp_db, monkeypatch, fake_provider_factory):
    # A forbidden-keyword SQL is a guard error → must surface immediately,
    # without spending repair attempts.
    fake = fake_provider_factory(
        responses=[_plan("DELETE FROM fact_rides")],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("delete test", model_id="openai:gpt-4o", lang="ru")

    assert r["status"] == "error"
    assert fake.calls == 1  # only the plan call — no repair attempted


@pytest.mark.asyncio
async def test_timeout_surfaces_as_error(tmp_db, monkeypatch, fake_provider_factory):
    # A query that will time out → status=error, no hang.
    slow = _plan(
        "WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x<50000000) SELECT COUNT(*) FROM cnt"
    )
    fake = fake_provider_factory(responses=[slow, slow], provider="openai")
    _patch_provider(monkeypatch, fake)

    # Lower the timeout for the test by patching the setting.
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(get_settings().__class__, "sql_timeout_sec", 0.05, raising=False)

    r = await run_oleg("slow query", model_id="openai:gpt-4o", lang="ru")
    assert r["status"] == "error"


# --- Real-model happy path ---


@pytest.mark.asyncio
async def test_real_model_valid_sql_returns_ok_with_insights(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(
        responses=[
            _plan(
                "SELECT c.region, ROUND(SUM(r.revenue_rub), 2) AS revenue_rub, COUNT(*) AS rides "
                "FROM fact_rides r JOIN dim_city c ON c.city_id = r.city_id "
                "WHERE r.is_partner = 0 GROUP BY c.region ORDER BY revenue_rub DESC",
                chart_type="bar",
                wants_excel=True,
                logic="выручка по регионам",
            ),
            "Выручка распределена по регионам.",
        ],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("выручка по регионам", model_id="openai:gpt-4o", lang="ru")

    assert r["status"] == "ok"
    assert r["row_count"] > 0
    assert len(r["insights"]["highlights"]) > 0
    assert r["insights"]["top"][0]["label"]  # top region is named
    assert r["excel_url"]  # wants_excel triggered
    assert fake.calls == 2  # plan + answer (no repair needed)


# --- Empty result ---


@pytest.mark.asyncio
async def test_empty_result_has_no_highlights(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(
        responses=[
            _plan("SELECT COUNT(*) AS c FROM fact_rides WHERE ride_date > '2099-01-01'"),
            "Нет данных за этот период.",
        ],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("будущее", model_id="openai:gpt-4o", lang="ru")
    assert r["insights"]["highlights"] == []


# --- Force excel ---


@pytest.mark.asyncio
async def test_force_excel_overrides_plan(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(
        responses=[
            _plan(
                "SELECT city_id, COUNT(*) AS rides FROM fact_rides GROUP BY city_id LIMIT 5",
                wants_excel=False,  # plan says no excel
            ),
            "ok",
        ],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("x", model_id="openai:gpt-4o", lang="ru", force_excel=True)
    assert r["excel_url"] is not None  # force_excel won


# --------------------------------------------------------------------------- #
# Execution-trace steps (streaming)
# --------------------------------------------------------------------------- #


def _step_tools(result: dict) -> list[str]:
    return [s["tool"] for s in result.get("steps", [])]


@pytest.mark.asyncio
async def test_steps_present_in_demo_mode(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    r = await run_oleg("выручка по регионам", model_id="mock", lang="ru")
    tools = _step_tools(r)
    # planner → database_query → analyze → chart → answer
    assert tools == ["planner", "database_query", "analyze", "chart", "answer"]
    assert all(s["status"] == "done" for s in r["steps"])
    assert all(s["duration_ms"] is not None for s in r["steps"])


@pytest.mark.asyncio
async def test_step_database_query_has_sql_and_row_count(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    r = await run_oleg("топ городов", model_id="mock", lang="ru")
    db_step = next(s for s in r["steps"] if s["tool"] == "database_query")
    assert db_step["detail"]["sql"]
    assert db_step["detail"]["row_count"] == r["row_count"]
    assert "строк" in db_step["summary"]  # "8 строк"


@pytest.mark.asyncio
async def test_step_analyze_has_highlights(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    r = await run_oleg("выручка по регионам", model_id="mock", lang="ru")
    analyze_step = next(s for s in r["steps"] if s["tool"] == "analyze")
    assert isinstance(analyze_step["detail"]["highlights"], list)
    assert len(analyze_step["detail"]["highlights"]) > 0


@pytest.mark.asyncio
async def test_streaming_callback_receives_running_then_done(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    _patch_provider(monkeypatch, MockProvider())
    seen: list[tuple[str, str]] = []

    async def on_step(step):
        seen.append((step["status"], step["tool"]))

    await run_oleg_streaming("топ", model_id="mock", lang="ru", on_step=on_step)
    # Every step emits running then done → pairs must alternate.
    statuses = [s for s, _ in seen]
    assert statuses[0] == "running"
    assert statuses[1] == "done"
    assert statuses.count("running") == statuses.count("done")


@pytest.mark.asyncio
async def test_self_correction_produces_repair_step(tmp_db, monkeypatch, fake_provider_factory):
    fake = fake_provider_factory(
        responses=[
            _plan("SELECT bad_col FROM fact_rides LIMIT 5"),
            _plan(
                "SELECT city_id, COUNT(*) AS rides FROM fact_rides GROUP BY city_id ORDER BY rides DESC LIMIT 5",
                chart_type="bar",
                logic="fixed",
            ),
            "Готово.",
        ],
        provider="openai",
    )
    _patch_provider(monkeypatch, fake)

    r = await run_oleg("test", model_id="openai:gpt-4o-mini", lang="ru")
    db_steps = [s for s in r["steps"] if s["tool"] == "database_query"]
    # Initial attempt failed (error) + repair succeeded (done) → at least 2 db steps.
    assert len(db_steps) >= 2
    assert any(s["status"] == "error" for s in db_steps)
    assert any(s["status"] == "done" for s in db_steps)


@pytest.mark.asyncio
async def test_steps_absent_for_error_response(tmp_db, monkeypatch):
    from app.llm.providers import MockProvider

    # CSV source + mock → honest error (steps still present up to the failure point).
    from app.db import datasources as ds

    meta = ds.ingest_csv("x.csv", "a,b\n1,2\n")
    try:
        _patch_provider(monkeypatch, MockProvider())
        r = await run_oleg("test", model_id="mock", lang="ru", datasource_id=meta["id"])
        assert r["status"] == "error"
        # The planner step ran and errored.
        assert any(s["tool"] == "planner" and s["status"] == "error" for s in r["steps"])
    finally:
        ds.delete_source(meta["id"])
