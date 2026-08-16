"""Auto-router for data sources: pick the best source for a question.

Two-tier strategy (same pattern as the agent router):
1. LLM classification — show available sources with their schemas, ask which one
   fits the question. One cheap call.
2. Heuristic fallback — keyword overlap between the question and source metadata
   (name, description, column names). Powers demo mode and LLM failures.
"""
from __future__ import annotations

import re

from app.llm.base import ChatMessage
from app.llm.registry import get_provider

SOURCE_ROUTER_SYSTEM = """Ты — маршрутизатор источников данных. Тебе дают вопрос пользователя и список
доступных источников с их таблицами/колонками. Выбери ОДИН источник, который
лучше всего подходит для ответа на вопрос.

Верни РОВНО ID источника (только ID, без объяснений).

Правила:
- Если вопрос упоминает конкретные данные из одного источника — выбери его.
- Если нужны данные из нескольких источников — выбери "all_uploads" (все загрузки).
- Если вопрос общий (не привязан к конкретным данным) — выбери "ridego".
- Если сомневаешься — выбери "all_uploads" (даёт максимум таблиц).
"""


def _source_options() -> list[dict]:
    """List available sources with searchable metadata."""
    from app.db.datasources import list_sources

    options = []
    for s in list_sources():
        # Build a searchable text from name + description
        meta_text = f"{s['name']} {s.get('description', '')}"
        # For CSV sources, include column names from the catalog
        if s["kind"] == "csv":
            from app.db.datasources import get_source_meta
            m = get_source_meta(s["id"])
            if m and m.get("columns"):
                cols = m["columns"]
                if cols and isinstance(cols[0], dict) and "columns" in cols[0]:
                    # Postgres-style: list of tables with columns
                    for t in cols[:5]:
                        meta_text += " " + " ".join(c["name"] for c in t.get("columns", [])[:10])
                else:
                    # CSV-style: flat column list
                    meta_text += " " + " ".join(c.get("name", "") for c in cols[:15])
        options.append({
            "id": s["id"],
            "name": s["name"],
            "kind": s["kind"],
            "searchable": meta_text.lower(),
        })
    return options


def _tokenize(text: str) -> set[str]:
    """Tokenize with Russian stemming (reuse docs_rag's stemmer)."""
    from app.core.docs_rag import _tokenize as _tokenize_stemmed
    return _tokenize_stemmed(text)


def _heuristic_route(question: str, options: list[dict]) -> str:
    """Keyword overlap between question and source metadata.

    Specific sources win over 'all_uploads' (which is a fallback for
    cross-source questions or when nothing specific matches).
    """
    q_tokens = _tokenize(question)
    if not q_tokens:
        return "ridego"

    # Split: specific sources vs the virtual all_uploads
    specific = [o for o in options if o["id"] != "all_uploads"]
    virtual = [o for o in options if o["id"] == "all_uploads"]

    best_id = "ridego"
    best_score = 0
    for opt in specific:
        src_tokens = _tokenize(opt["searchable"])
        overlap = len(q_tokens & src_tokens)
        # Prefer uploaded sources over ridego on ties (more specific to user's data)
        score = overlap + (1 if opt["kind"] == "csv" else 0)
        if score > best_score:
            best_score = score
            best_id = opt["id"]

    # Specific source matched well — use it
    if best_score >= 2:
        return best_id

    # No specific match — try all_uploads (covers everything)
    if virtual:
        return "all_uploads"

    return best_id if best_score > 0 else "ridego"


def _format_sources_for_llm(options: list[dict]) -> str:
    lines = []
    for opt in options:
        lines.append(f"ID: {opt['id']} | {opt['name']} | {opt['searchable'][:200]}")
    return "\n".join(lines)


async def route_source(question: str, model_id: str = "mock") -> str:
    """Return the best datasource_id for the question.

    Real models get one cheap classification call; mock/failed LLM falls back
    to the deterministic heuristic.
    """
    options = _source_options()

    # Only one source — no routing needed.
    if len(options) <= 1:
        return options[0]["id"] if options else "ridego"

    # Only ridego — no point routing.
    if all(o["id"] == "ridego" for o in options):
        return "ridego"

    provider = get_provider(model_id)
    if provider.provider == "mock":
        return _heuristic_route(question, options)

    try:
        raw = await provider.complete(
            SOURCE_ROUTER_SYSTEM + "\n\nИсточники:\n" + _format_sources_for_llm(options),
            [ChatMessage("user", question)],
            lang="ru",
        )
        # Parse the response — should be just the ID
        text = raw.strip().strip('"').strip("'")
        # Find a matching source ID
        for opt in options:
            if opt["id"] in text:
                return opt["id"]
        # Fallback: try to find by name
        for opt in options:
            if opt["name"].lower() in text.lower():
                return opt["id"]
    except Exception:  # noqa: BLE001 — routing must never block the answer
        pass

    return _heuristic_route(question, options)
