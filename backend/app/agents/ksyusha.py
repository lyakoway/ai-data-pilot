"""Ksyusha — technical assistant over fake internal docs."""
from __future__ import annotations

from typing import Any

from app.core.docs_rag import ensure_docs, retrieve
from app.llm.base import ChatMessage
from app.llm.registry import get_provider

SYSTEM = """Ты — Ксюша, технический ассистент команды RideGo.
Отвечай по CONTEXT из внутренней документации. Если в контексте нет ответа —
честно скажи, чего не хватает. Указывай файлы/разделы. Язык = язык вопроса.

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
            f"Based on «{top.title}»:\n\n{top.text[:700]}\n\n"
            "See sources below. (Demo mode — connect a real model for richer answers.)"
        )
    return (
        f"По разделу «{top.title}»:\n\n{top.text[:700]}\n\n"
        "Подробности — в источниках ниже. "
        "(Демо-режим: подключите модель для более развёрнутого ответа.)"
    )


async def run_ksyusha(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
) -> dict[str, Any]:
    ensure_docs()
    chunks = retrieve(question, top_k=4)
    context = _format_context(chunks)
    provider = get_provider(model_id)

    answer = ""
    if provider.provider != "mock":
        answer = await provider.complete(
            SYSTEM.format(context=context),
            [ChatMessage("user", question)],
            lang=lang,
        )
    if not answer.strip():
        answer = _mock_answer(question, chunks, lang)

    sources = [
        {"id": c.doc_id, "title": c.title, "snippet": c.text[:220]}
        for c in chunks
    ]

    return {
        "agent": "ksyusha",
        "answer": answer,
        "sources": sources,
        "sql": None,
        "explanation": None,
        "columns": [],
        "rows": [],
        "chart": None,
        "excel_url": None,
        "tables_used": [],
        "suggestions": [
            "Где хранится utilization и как она считается?",
            "Как работает Redis pricing cache и какой TTL?",
            "Что делает кнопка Reset errors в админке?",
        ],
    }
