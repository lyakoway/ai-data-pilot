"""LLM suggestion generation: mock fallback, fake-LLM path, caching."""
import json

import pytest

from app.db import datasources as ds


@pytest.fixture()
def csv_meta(tmp_db):
    meta = ds.ingest_csv("works.csv", "name,price\na,100\nb,200\n")
    yield meta
    try:
        ds.delete_source(meta["id"])
    except KeyError:
        pass


@pytest.mark.asyncio
async def test_mock_model_returns_heuristics(csv_meta):
    out = await ds.suggest_questions_smart(csv_meta["id"], model_id="mock", lang="ru")
    assert out  # heuristic questions


@pytest.mark.asyncio
async def test_llm_suggestions_cached(csv_meta, monkeypatch):
    class FakeLLM:
        provider = "openai"
        calls = 0

        async def complete(self, system, messages, lang="ru"):
            FakeLLM.calls += 1
            # The prompt must now carry sample rows, not only column names.
            assert "Примеры строк" in system and "a | 100" in system
            assert "ЗАПРЕЩЕНЫ" in system  # structure-question ban present
            return json.dumps(
                ["Вопрос один", "Вопрос один", "Сколько всего работ?", "Какая работа самая дорогая?"]
            )

    import app.llm.registry as llm_registry

    monkeypatch.setattr(llm_registry, "get_provider", lambda mid: FakeLLM())

    first = await ds.suggest_questions_smart(csv_meta["id"], model_id="openai:gpt-4o", lang="ru")
    # Duplicated LLM answer is deduped.
    assert len(first) == 3
    assert len(set(first)) == 3
    assert "самая дорогая" in " ".join(first)
    assert FakeLLM.calls == 1
    # Second call hits the cache — no extra LLM call.
    second = await ds.suggest_questions_smart(csv_meta["id"], model_id="openai:gpt-4o", lang="ru")
    assert second == first
    assert FakeLLM.calls == 1


@pytest.mark.asyncio
async def test_llm_garbage_falls_back_to_heuristics(csv_meta, monkeypatch):
    import app.llm.registry as llm_registry

    class Garbage:
        provider = "openai"

        async def complete(self, *a, **k):
            return "не json вообще"

    monkeypatch.setattr(llm_registry, "get_provider", lambda mid: Garbage())
    out = await ds.suggest_questions_smart(csv_meta["id"], model_id="openai:gpt-4o", lang="ru")
    assert out  # heuristic fallback, no exception
