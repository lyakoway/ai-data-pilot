"""Auto-router: decide which agent answers a question — Oleg (data/SQL) or Ksyusha (docs/RAG).

Two-tier strategy:
1. Deterministic keyword heuristic — always available, powers demo mode and
   acts as the fallback when the LLM is slow, unavailable or replies garbage.
2. Cheap LLM classification — used when a real model is selected; one short
   call returning a single word (DATA / DOCS). Any parse failure falls back to
   the heuristic, so routing never blocks the answer.
"""
from __future__ import annotations

import re

from app.llm.base import ChatMessage
from app.llm.registry import get_provider

# Questions about how things work / are stored / are defined → docs (Ksyusha).
_DOCS_RE = re.compile(
    r"(как\s+(считается|работает|хранится|устроен|считать|настроить|выглядит)|"
    r"где\s+(хранится|лежит|найти)|что\s+(делает|такое|значит)|"
    r"документац|словар|метрик[ау]?\s+(называется|определ)|определени|"
    r"lineage|utilization|redis|ttl|антифрод|antifraud|reset\s+errors|positioncodes|"
    r"backend|логика\s+работы|почему\s+в\s+админке)",
    re.IGNORECASE,
)

# Questions about numbers, rankings, dynamics, exports → data (Oleg).
_DATA_RE = re.compile(
    r"(сколько|топ|выручк|поездк|подписк|город|регион|динамик|сравн|упал|упало|вырос|"
    r"раст[её]т|падает|изменени|сгруппируй|по\s+месяц|график|excel|таблиц|sql|"
    r"запрос|выгруз|метрик[ау]?\s+(по|за)|продаж|клиент|заказ|пользовател|"
    r"how\s+many|top|revenue|sales|chart)",
    re.IGNORECASE,
)

ROUTER_SYSTEM = """Ты — маршрутизатор вопросов аналитической платформы.
Определи, какому агенту адресовать вопрос. Верни РОВНО ОДНО СЛОВО без всего остального:

DATA — вопрос о цифрах и данных: сколько, топ, выручка, продажи, динамика, сравнение
       периодов, группировки, графики, выгрузки (нужен SQL-анализ базы).
DOCS — вопрос о том, как что-то устроено/считается/хранится, про документацию,
       определения метрик, backend-логику, lineage, TTL, кэши.

Примеры:
"Топ-10 городов по поездкам" → DATA
"Почему выручка упала в июле?" → DATA
"Сколько активных подписок?" → DATA
"Как считается utilization?" → DOCS
"Где хранится pricing cache и какой TTL?" → DOCS
"Что делает кнопка Reset errors?" → DOCS

Если сомневаешься — верни DATA.
"""


def _heuristic_route(question: str) -> str:
    """Deterministic keyword routing. Docs patterns win (more specific), then data."""
    if _DOCS_RE.search(question):
        return "ksyusha"
    if _DATA_RE.search(question):
        return "oleg"
    return "oleg"  # Oleg is the primary agent; ambiguous questions go to data.


def _parse_llm_verdict(raw: str) -> str | None:
    text = (raw or "").strip().upper()
    if "DOCS" in text:
        return "ksyusha"
    if "DATA" in text:
        return "oleg"
    return None


async def route_agent(question: str, model_id: str = "mock") -> str:
    """Return 'oleg' or 'ksyusha' for the question.

    Real providers get one cheap classification call; any failure or a mock
    provider falls back to the deterministic heuristic.
    """
    provider = get_provider(model_id)
    if provider.provider == "mock":
        return _heuristic_route(question)
    try:
        raw = await provider.complete(
            ROUTER_SYSTEM, [ChatMessage("user", question)], lang="ru"
        )
        verdict = _parse_llm_verdict(raw)
        if verdict:
            return verdict
    except Exception:  # noqa: BLE001 — routing must never block the answer
        pass
    return _heuristic_route(question)
