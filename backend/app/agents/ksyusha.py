"""Ksyusha — technical assistant over fake internal docs.

Streams execution-trace steps (retrieval → answer) and asks the model to cite
sources inline as ``[1]``, ``[2]`` so the UI can render clickable references.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from app.core.docs_rag import ensure_docs, retrieve
from app.llm.base import ChatMessage
from app.llm.registry import get_provider

SYSTEM = """Ты — Ксюша, технический ассистент команды RideGo.
Отвечай по CONTEXT из внутренней документации. Если в контексте нет ответа —
честно скажи, чего не хватает. Указывай файлы/разделы. Язык = язык вопроса.

ОБЯЗАТЕЛЬНО цитируй источники прямо в тексте ответа в формате [1], [2] и т.д.,
где номер соответствует номеру фрагмента в CONTEXT. Пример:
«Utilization считается как active/available [1]. Кэш тарифов в Redis имеет TTL 15 минут [2].»

CONTEXT:
{context}
"""


def _format_context(chunks) -> str:
    parts = []
    for i, ch in enumerate(chunks, 1):
        parts.append(f"[{i}] {ch.title} ({ch.doc_id})\n{ch.text}")
    return "\n\n".join(parts) if parts else "(пусто)"


def _mock_answer(question: str, chunks, lang: str) -> str:
    if not chunks:
        if lang == "en":
            return "I couldn't find relevant docs. Try asking about utilization, Redis pricing cache, or anti-fraud."
        return (
            "Не нашла релевантных фрагментов в док-базе. "
            "Спросите про utilization, Redis pricing cache или anti-fraud."
        )
    top = chunks[0]
    if lang == "en":
        return (
            f"Based on «{top.title}» [1]:\n\n{top.text[:700]}\n\n"
            "See sources below. (Demo mode — connect a real model for richer answers.)"
        )
    return (
        f"По разделу «{top.title}» [1]:\n\n{top.text[:700]}\n\n"
        "Подробности — в источниках ниже. "
        "(Демо-режим: подключите модель для более развёрнутого ответа.)"
    )


# Step callback type (same as Oleg's).
StepCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_step(_step: dict[str, Any]) -> None:
    return None


def _new_step(index: int, tool: str, title: str) -> dict[str, Any]:
    return {
        "id": f"step_{index}",
        "title": title,
        "tool": tool,
        "status": "running",
        "summary": None,
        "detail": None,
        "duration_ms": None,
    }


async def run_ksyusha_streaming(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
    on_step: StepCallback = _noop_step,
) -> dict[str, Any]:
    """Run Ksyusha and emit execution-trace steps (retrieval → answer)."""
    provider = get_provider(model_id)
    is_mock = provider.provider == "mock"
    steps: list[dict[str, Any]] = []

    async def emit(step: dict[str, Any]) -> None:
        await on_step(dict(step))
        if step["status"] in ("done", "error"):
            steps.append(dict(step))

    # --- Step 1: retrieval ------------------------------------------------
    step = _new_step(1, "retrieval", "Ищу по документам" if lang != "en" else "Searching docs")
    await emit(step)
    t0 = time.perf_counter()
    ensure_docs()
    chunks = retrieve(question, top_k=4)
    context = _format_context(chunks)
    step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    step["status"] = "done"
    n = len(chunks)
    top_score = chunks[0].score if chunks else 0
    step["summary"] = (
        f"{n} фрагмент(ов)" if lang != "en" else f"{n} fragment(s)"
    )
    step["detail"] = {"fragments": n, "top_score": top_score}
    await emit(step)

    # --- Step 2: answer ---------------------------------------------------
    step = _new_step(2, "answer", "Формирую ответ" if lang != "en" else "Composing answer")
    await emit(step)
    t0 = time.perf_counter()
    answer = ""
    if not is_mock:
        try:
            answer = await provider.complete(
                SYSTEM.format(context=context),
                [ChatMessage("user", question)],
                lang=lang,
            )
        except Exception:  # noqa: BLE001 — provider failed (e.g. 429); fall back to mock
            answer = ""
    if not answer.strip():
        answer = _mock_answer(question, chunks, lang)
    step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    step["status"] = "done"
    step["summary"] = (answer[:140] + "…") if len(answer) > 140 else answer
    await emit(step)

    sources = []
    for c in chunks:
        src = {
            "id": c.doc_id,
            "title": c.title,
            "snippet": c.text[:220],
            "full_text": c.text,
            "score": c.score,
            "document_id": None,
            "filename": None,
            "page": None,
        }
        # Uploaded documents have doc_id = "{document_id}#{chunk_index}".
        if "#" in c.doc_id and not c.doc_id.endswith(".md"):
            doc_id = c.doc_id.rsplit("#", 1)[0]
            try:
                from app.db import app_db
                doc = app_db.get_document(doc_id)
                if doc:
                    src["document_id"] = doc_id
                    src["filename"] = doc["filename"]
                    src["page"] = c.page
            except Exception:  # noqa: BLE001
                pass
        sources.append(src)

    return {
        "agent": "ksyusha",
        "status": "demo" if is_mock else "ok",
        "warnings": [],
        "steps": steps,
        "answer": answer,
        "sources": sources,
        "sql": None,
        "explanation": None,
        "columns": [],
        "rows": [],
        "chart": None,
        "excel_url": None,
        "tables_used": [],
        "insights": {},
        "suggestions": [
            "Где хранится utilization и как она считается?",
            "Как работает Redis pricing cache и какой TTL?",
            "Что делает кнопка Reset errors в админке?",
        ],
    }


async def run_ksyusha(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
) -> dict[str, Any]:
    """Run Ksyusha synchronously (thin wrapper over the streaming variant)."""
    return await run_ksyusha_streaming(question, model_id=model_id, lang=lang, on_step=_noop_step)
