"""Tests for Ksyusha's RAG polish: streaming steps, sources with full_text + score, citations."""
from __future__ import annotations

import pytest

from app.agents.ksyusha import run_ksyusha, run_ksyusha_streaming
from app.core.docs_rag import retrieve


# --------------------------------------------------------------------------- #
# Retrieval score normalization
# --------------------------------------------------------------------------- #


def test_retrieval_scores_normalized_0_to_1(tmp_db):
    chunks = retrieve("redis pricing cache TTL", top_k=4)
    assert len(chunks) >= 1
    # Best match is always 1.0
    assert chunks[0].score == 1.0
    # All scores in [0, 1]
    for ch in chunks:
        assert 0.0 <= ch.score <= 1.0


def test_retrieval_single_result_score_is_1(tmp_db):
    chunks = retrieve("utilization", top_k=4)
    assert len(chunks) >= 1
    assert chunks[0].score == 1.0


# --------------------------------------------------------------------------- #
# Streaming + steps
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ksyusha_returns_steps(tmp_db):
    r = await run_ksyusha("Где хранится utilization?", model_id="mock", lang="ru")
    assert len(r["steps"]) == 2
    tools = [s["tool"] for s in r["steps"]]
    assert tools == ["retrieval", "answer"]
    assert all(s["status"] == "done" for s in r["steps"])


@pytest.mark.asyncio
async def test_ksyusha_streaming_callback(tmp_db):
    seen: list[tuple[str, str]] = []

    async def on_step(step):
        seen.append((step["status"], step["tool"]))

    await run_ksyusha_streaming("redis cache", model_id="mock", lang="ru", on_step=on_step)
    # Each step emits running then done.
    assert seen[0] == ("running", "retrieval")
    assert seen[1] == ("done", "retrieval")
    assert seen[2] == ("running", "answer")
    assert seen[3] == ("done", "answer")


@pytest.mark.asyncio
async def test_ksyusha_retrieval_step_has_fragment_count(tmp_db):
    r = await run_ksyusha("utilization", model_id="mock", lang="ru")
    retrieval = r["steps"][0]
    assert retrieval["detail"]["fragments"] == len(r["sources"])
    assert "top_score" in retrieval["detail"]


# --------------------------------------------------------------------------- #
# Sources with full_text + score
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_sources_have_full_text_and_score(tmp_db):
    r = await run_ksyusha("utilization", model_id="mock", lang="ru")
    assert len(r["sources"]) >= 1
    for src in r["sources"]:
        assert "full_text" in src
        assert len(src["full_text"]) >= len(src["snippet"])
        assert "score" in src
        assert 0.0 <= src["score"] <= 1.0


# --------------------------------------------------------------------------- #
# Citations in answer
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_mock_answer_contains_citation(tmp_db):
    r = await run_ksyusha("Где хранится utilization?", model_id="mock", lang="ru")
    # Mock answer includes [1] citation.
    assert "[1]" in r["answer"]


@pytest.mark.asyncio
async def test_system_prompt_has_citation_rule(tmp_db):
    from app.agents.ksyusha import SYSTEM
    assert "[1]" in SYSTEM or "цитируй" in SYSTEM.lower() or "cite" in SYSTEM.lower()


@pytest.mark.asyncio
async def test_ksyusha_status_demo_in_mock_mode(tmp_db):
    r = await run_ksyusha("utilization", model_id="mock", lang="ru")
    assert r["status"] == "demo"
