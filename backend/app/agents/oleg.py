"""Analyst Oleg — NL → SQL → execute → analyze → explain (+ chart / excel).

Reliability contract (Phase 1):
- The SQL planner may attempt up to ``MAX_SQL_ATTEMPTS`` self-correction rounds.
  On a runtime SQL error the agent feeds the error back to the LLM and asks it
  to rewrite the query. Guard errors (forbidden keywords, multi-statement) are
  never retried.
- Silent mock substitution on real-model failures is gone. Mock data is only
  ever served when the user explicitly picked the offline demo mode, and such
  answers are flagged with ``status="demo"``.
- All figures quoted in the natural-language answer are computed deterministically
  by :mod:`app.core.analytics`; the LLM only renders them into prose.
"""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.analytics import compute_insights
from app.core.export_xlsx import export_rows
from app.core.sql_guard import (
    SqlExecutionError,
    SqlGuardError,
    SqlTimeoutError,
    run_sql,
)
from app.db.datasources import RIDEGO_SOURCE_ID, get_engine_for, get_schema_catalog
from app.db.schema_catalog import SCHEMA_CATALOG
from app.llm.base import ChatMessage
from app.llm.registry import get_provider

# Total planner attempts: 1 initial + this many repair rounds.
MAX_SQL_REPAIR_ROUNDS = 2
MAX_SQL_ATTEMPTS = 1 + MAX_SQL_REPAIR_ROUNDS

PLAN_SYSTEM = """Ты — аналитик Олег. Строишь SQL для аналитической базы данных.
Верни ТОЛЬКО JSON без markdown:
{
  "sql": "SELECT ...",
  "chart_type": "bar" | "line" | "pie" | null,
  "wants_excel": true/false,
  "tables_used": ["..."],
  "logic": "краткое пояснение методологии на языке пользователя"
}

Правила:
- Только SELECT / WITH. Никаких мутаций.
- Используй только таблицы и поля из SCHEMA.
- SQLite синтаксис (date(), julianday и т.п. допустимы).
- LIMIT внутри запроса можно, но не обязателен.

SCHEMA:
"""

# Asks the model to rewrite a query that failed at runtime.
# Note: literal braces in the JSON example are escaped ({{ }}) because the
# template is processed with str.format() for {error}/{sql} below.
REPAIR_SYSTEM = """Ты — аналитик Олег. Предыдущий SQL упал с ошибкой БД.
Перепиши запрос так, чтобы он выполнился. Используй только таблицы/поля из SCHEMA.
Верни ТОЛЬКО JSON в том же формате: {{sql, chart_type, wants_excel, tables_used, logic}}.

ОШИБКА:
{error}

ПРОВЛЕМНЫЙ SQL:
{sql}

SCHEMA:
"""

# Renders pre-computed insights into prose. The model must NOT invent numbers —
# every figure it mentions must come from the provided HIGHLIGHTS list.
ANSWER_SYSTEM = """Ты — аналитик Олег. По результату SQL дай короткий деловой ответ.
Структура:
1) Прямой ответ / ключевые цифры
2) 2–4 наблюдения на основе HIGHLIGHTS

КРИТИЧЕСКИ ВАЖНО:
- Используй ТОЛЬКО цифры из блока HIGHLIGHTS. Не вычисляй и не выдумывай проценты сам.
- Язык ответа = язык вопроса пользователя.
- Если HIGHLIGHTS пуст или данных мало — честно скажи, что данных недостаточно.
- Не повторяй SQL, не пересказывай таблицу построчно.
"""


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def _mock_plan(question: str) -> dict[str, Any]:
    q = question.lower()
    if "отмен" in q or "cancel" in q or "причин" in q:
        return {
            "sql": (
                "SELECT cancel_reason AS reason, COUNT(*) AS cnt "
                "FROM fact_subscriptions "
                "WHERE status = 'cancelled' AND brand = 'boost' AND cancel_reason IS NOT NULL "
                "GROUP BY cancel_reason ORDER BY cnt DESC"
            ),
            "chart_type": "pie",
            "wants_excel": True,
            "tables_used": ["fact_subscriptions"],
            "logic": "Считал отменённые подписки brand=boost, группировка по cancel_reason.",
        }
    if "проникновен" in q or ("подписк" in q and "город" in q) or "inhouse" in q:
        return {
            "sql": (
                "WITH users_city AS ("
                "  SELECT city_id, COUNT(*) AS total_users FROM dim_user GROUP BY city_id"
                "), active_subs AS ("
                "  SELECT city_id, COUNT(DISTINCT user_id) AS active_users "
                "  FROM fact_subscriptions WHERE status = 'active' GROUP BY city_id"
                ") "
                "SELECT c.city_name, c.country, "
                "COALESCE(a.active_users, 0) AS active_subscription_users, "
                "u.total_users, "
                "ROUND(100.0 * COALESCE(a.active_users, 0) / u.total_users, 2) AS penetration_pct "
                "FROM dim_city c "
                "JOIN users_city u ON u.city_id = c.city_id "
                "LEFT JOIN active_subs a ON a.city_id = c.city_id "
                "WHERE c.is_inhouse = 1 "
                "ORDER BY penetration_pct ASC LIMIT 8"
            ),
            "chart_type": "bar",
            "wants_excel": True,
            "tables_used": ["dim_city", "dim_user", "fact_subscriptions"],
            "logic": "InHouse города; доля active subscribers / total users.",
        }
    if "регион" in q or "выручк" in q or "продаж" in q:
        return {
            "sql": (
                "WITH bounds AS ("
                "  SELECT date(MAX(ride_date), '-30 day') AS d_from, MAX(ride_date) AS d_to "
                "  FROM fact_rides"
                ") "
                "SELECT c.region, ROUND(SUM(r.revenue_rub), 2) AS revenue_rub, "
                "COUNT(*) AS rides "
                "FROM fact_rides r "
                "JOIN dim_city c ON c.city_id = r.city_id "
                "JOIN bounds b ON r.ride_date BETWEEN b.d_from AND b.d_to "
                "WHERE r.is_partner = 0 "
                "GROUP BY c.region ORDER BY revenue_rub DESC"
            ),
            "chart_type": "bar",
            "wants_excel": True,
            "tables_used": ["fact_rides", "dim_city"],
            "logic": "Выручка за 30 дней от MAX(ride_date), без партнёрских поездок, группировка по region.",
        }
    if "уникальн" in q and "пользовател" in q:
        return {
            "sql": (
                "SELECT COUNT(DISTINCT user_id) AS registered_users "
                "FROM dim_user u "
                "JOIN dim_city c ON c.city_id = u.city_id "
                "WHERE c.country = 'Россия' AND u.registered_at <= date('2026-07-01')"
            ),
            "chart_type": None,
            "wants_excel": False,
            "tables_used": ["dim_user", "dim_city"],
            "logic": "Уникальные user_id с registration_country=Россия до 2026-07-01.",
        }
    # default: top cities
    return {
        "sql": (
            "SELECT c.city_name, COUNT(*) AS rides_count, "
            "COUNT(DISTINCT r.user_id) AS unique_users, "
            "ROUND(SUM(r.distance_km), 1) AS distance_km "
            "FROM fact_rides r "
            "JOIN dim_city c ON c.city_id = r.city_id "
            "WHERE r.is_partner = 0 "
            "GROUP BY c.city_name "
            "ORDER BY rides_count DESC LIMIT 10"
        ),
        "chart_type": "bar",
        "wants_excel": True,
        "tables_used": ["fact_rides", "dim_city"],
        "logic": "Топ городов по поездкам, партнёрские исключены.",
    }


def _mock_answer(question: str, columns: list[str], rows: list[list[Any]], lang: str) -> str:
    if not rows:
        return "Данных по запросу не нашлось." if lang != "en" else "No data for this query."
    preview = rows[:5]
    if lang == "en":
        lines = [f"Query done: {len(rows)} rows.", f"Columns: {', '.join(columns)}.", "Top rows:"]
    else:
        lines = [f"Готово: {len(rows)} строк.", f"Колонки: {', '.join(columns)}.", "Первые строки:"]
    for r in preview:
        lines.append("- " + ", ".join(f"{c}={v}" for c, v in zip(columns, r)))
    return "\n".join(lines)


def _build_chart(plan: dict[str, Any], columns: list[str], rows: list[list[Any]]) -> dict[str, Any] | None:
    chart_type = plan.get("chart_type")
    if chart_type not in {"bar", "line", "pie"} or not columns or not rows:
        return None
    x_key = columns[0]
    y_key = None
    for i, col in enumerate(columns[1:], start=1):
        if rows and isinstance(rows[0][i], (int, float)):
            y_key = col
            break
    if y_key is None and len(columns) > 1:
        y_key = columns[1]
    if not y_key:
        return None
    yi = columns.index(y_key)
    return {
        "type": chart_type,
        "x_key": x_key,
        "y_key": y_key,
        "points": [
            {"x": str(r[0]), "y": float(r[yi]) if isinstance(r[yi], (int, float)) else 0}
            for r in rows[:20]
        ],
    }


def _ok_response(
    *,
    answer: str,
    plan: dict[str, Any],
    columns: list[str],
    rows: list[list[Any]],
    row_count: int,
    insights: dict[str, Any],
    status: str,
    warnings: list[str] | None = None,
    force_excel: bool = False,
) -> dict[str, Any]:
    wants_excel = bool(plan.get("wants_excel")) or force_excel or len(rows) >= 15
    excel_url = None
    if wants_excel and rows:
        path = export_rows(columns, rows, name="oleg_report")
        excel_url = f"/api/exports/{path.name}"

    return {
        "agent": "oleg",
        "status": status,
        "warnings": warnings or [],
        "answer": answer,
        "sql": plan.get("sql"),
        "explanation": plan.get("logic"),
        "columns": columns,
        "rows": rows[:100],
        "row_count": row_count,
        "chart": _build_chart(plan, columns, rows),
        "excel_url": excel_url,
        "tables_used": plan.get("tables_used") or [],
        "insights": insights,
        "suggestions": [
            "Сохрани это как сценарий",
            "Сравни с прошлым месяцем",
            "Выгрузи в Excel",
        ],
    }


def _error_response(
    *,
    plan: dict[str, Any] | None,
    message: str,
    lang: str,
    status: str = "error",
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if lang == "en":
        answer = f"I couldn't run the query. {message}"
    else:
        answer = f"Не удалось выполнить запрос. {message}"
    return {
        "agent": "oleg",
        "status": status,
        "warnings": warnings or [],
        "answer": answer,
        "sql": plan.get("sql") if plan else None,
        "explanation": plan.get("logic") if plan else None,
        "columns": [],
        "rows": [],
        "row_count": 0,
        "chart": None,
        "excel_url": None,
        "tables_used": (plan.get("tables_used") if plan else None) or [],
        "insights": {},
        "suggestions": [
            "Покажи топ-10 городов по поездкам",
            "Выручка по регионам за 30 дней",
        ],
    }


def _legacy_engine():
    """Fallback to the seeded RideGo engine when no/unknown datasource is given."""
    from app.core.sql_guard import analytics_engine

    return analytics_engine()


async def _generate_plan(
    provider, question: str, lang: str, schema_catalog: str, *, allow_mock: bool = True
) -> dict[str, Any] | None:
    """Ask the LLM for a JSON plan. Returns ``None`` if the call yields nothing usable.

    ``allow_mock`` controls whether the deterministic RideGo mock plans may be
    used. They are RideGo-specific, so they must not be served for uploaded CSV
    sources (their schemas differ entirely).
    """
    if provider.provider == "mock":
        if allow_mock:
            return _mock_plan(question)
        return None  # force the caller down the honest "needs a real model" path
    raw = await provider.complete(
        PLAN_SYSTEM + schema_catalog,
        [ChatMessage("user", question)],
        lang=lang,
    )
    return _extract_json(raw)


async def _repair_plan(
    provider, question: str, broken_sql: str, error: str, lang: str, schema_catalog: str
) -> dict[str, Any] | None:
    """Feed a runtime error back to the LLM and ask for a corrected plan."""
    system = REPAIR_SYSTEM.format(error=error[:500], sql=broken_sql) + schema_catalog
    raw = await provider.complete(system, [ChatMessage("user", question)], lang=lang)
    return _extract_json(raw)


async def run_oleg(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
    force_excel: bool = False,
    datasource_id: str = RIDEGO_SOURCE_ID,
) -> dict[str, Any]:
    provider = get_provider(model_id)
    is_mock = provider.provider == "mock"

    # Resolve the active data source: schema text + engine. Falls back to RideGo.
    schema_catalog = get_schema_catalog(datasource_id) if datasource_id else SCHEMA_CATALOG
    try:
        engine = get_engine_for(datasource_id) if datasource_id else _legacy_engine()
    except KeyError:
        # Unknown source id — fall back to the default RideGo engine and note it.
        schema_catalog = SCHEMA_CATALOG
        engine = _legacy_engine()

    # Mock plans are RideGo-specific; only allow them for the built-in source.
    is_ridego = datasource_id == RIDEGO_SOURCE_ID
    plan = await _generate_plan(provider, question, lang, schema_catalog, allow_mock=is_ridego)
    if not plan or not plan.get("sql"):
        if is_mock and is_ridego:
            plan = _mock_plan(question)
        else:
            return _error_response(
                plan=plan,
                message=(
                    "В демо-режиме нельзя строить запросы по пользовательским данным. "
                    "Подключите реальную модель (OpenAI / Anthropic / Z.ai / Ollama)."
                    if lang != "en"
                    else "Demo mode can't query uploaded data. Connect a real model."
                ),
                lang=lang,
            )

    warnings: list[str] = []

    # --- Self-correction loop: try, and on runtime errors ask the LLM to repair ---
    result: dict[str, Any] | None = None
    attempts_made = 1
    for attempt in range(MAX_SQL_ATTEMPTS):
        try:
            result = run_sql(engine, plan["sql"])
            break
        except SqlGuardError as e:
            # Static validation failure — never worth retrying; surface to the user.
            return _error_response(plan=plan, message=str(e), lang=lang)
        except SqlTimeoutError as e:
            return _error_response(plan=plan, message=str(e), lang=lang)
        except SqlExecutionError as e:
            if is_mock:
                # In demo mode the plans are hand-written and must succeed. If one
                # fails we surface it rather than fabricate data.
                return _error_response(plan=plan, message=str(e), lang=lang)
            if attempt >= MAX_SQL_REPAIR_ROUNDS:
                msg = (
                    f"SQL не выполнился после {MAX_SQL_ATTEMPTS} попыток. Последняя ошибка: {e}"
                    if lang != "en"
                    else f"SQL failed after {MAX_SQL_ATTEMPTS} attempts. Last error: {e}"
                )
                return _error_response(plan=plan, message=msg, lang=lang, warnings=[str(e)])
            repaired = await _repair_plan(provider, question, plan["sql"], str(e), lang, schema_catalog)
            attempts_made += 1
            if repaired and repaired.get("sql") and repaired["sql"] != plan["sql"]:
                plan = repaired
                warnings.append(
                    f"Самокоррекция: переписал запрос (попытка {attempts_made})."
                    if lang != "en"
                    else f"Self-correction: rewrote query (attempt {attempts_made})."
                )
                continue
            # Model couldn't produce a different query — give up honestly.
            return _error_response(
                plan=plan,
                message=(
                    "Не удалось исправить SQL автоматически."
                    if lang != "en"
                    else "Could not repair the SQL automatically."
                ),
                lang=lang,
                warnings=[str(e)],
            )

    assert result is not None  # loop only exits cleanly with a result
    columns, rows = result["columns"], result["rows"]

    # --- Deterministic analytics layer (no LLM math) ---
    insights = compute_insights(columns, rows, question=question, lang=lang)

    # --- Answer: LLM renders insights into prose, or mock fallback ---
    answer = ""
    if not is_mock:
        table_preview = {
            "columns": columns,
            "rows": rows[:30],
            "row_count": len(rows),
            "logic": plan.get("logic"),
            "sql": result["sql"],
            "highlights": insights["highlights"],
        }
        answer = await provider.complete(
            ANSWER_SYSTEM,
            [
                ChatMessage(
                    "user",
                    f"Вопрос: {question}\n\nHIGHLIGHTS:\n- "
                    + "\n- ".join(insights["highlights"])
                    + f"\n\nРезультат JSON:\n{json.dumps(table_preview, ensure_ascii=False)}",
                )
            ],
            lang=lang,
        )
    if not answer.strip():
        answer = _mock_answer(question, columns, rows, lang)

    status = "demo" if is_mock else "ok"
    return _ok_response(
        answer=answer,
        plan=plan,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        insights=insights,
        status=status,
        warnings=warnings or None,
        force_excel=force_excel,
    )
