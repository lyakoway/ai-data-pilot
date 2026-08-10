"""Analyst Oleg — NL → SQL → execute → explain (+ chart / excel)."""
from __future__ import annotations

import json
import re
from typing import Any

from app.core.export_xlsx import export_rows
from app.core.sql_guard import SqlGuardError, analytics_engine, run_sql
from app.db.schema_catalog import SCHEMA_CATALOG
from app.llm.base import ChatMessage
from app.llm.registry import get_provider

PLAN_SYSTEM = """Ты — аналитик Олег. Строишь SQL для базы RideGo.
Верни ТОЛЬКО JSON без markdown:
{
  "sql": "SELECT ...",
  "chart_type": "bar" | "line" | "pie" | null,
  "wants_excel": true/false,
  "tables_used": ["fact_rides", "..."],
  "logic": "краткое пояснение методологии на языке пользователя"
}

Правила:
- Только SELECT / WITH. Никаких мутаций.
- Используй только таблицы и поля из SCHEMA.
- Для «последние N дней» опирайся на MAX(ride_date) или MAX(registered_at).
- Партнёрские поездки исключай (is_partner = 0), если пользователь не просит иное.
- SQLite синтаксис (date(), julianday и т.п. допустимы).
- LIMIT внутри запроса можно, но не обязателен.

SCHEMA:
"""

ANSWER_SYSTEM = """Ты — аналитик Олег. По результату SQL дай короткий деловой ответ.
Структура:
1) Прямой ответ / ключевые цифры
2) 2–4 наблюдения (тренды, топы, аномалии)
Не выдумывай цифры вне таблицы. Язык ответа = язык вопроса пользователя.
Если данных мало — скажи об этом.
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


async def run_oleg(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
    force_excel: bool = False,
) -> dict[str, Any]:
    provider = get_provider(model_id)
    plan: dict[str, Any] | None = None

    if provider.provider != "mock":
        raw = await provider.complete(
            PLAN_SYSTEM + SCHEMA_CATALOG,
            [ChatMessage("user", question)],
            lang=lang,
        )
        plan = _extract_json(raw)

    if not plan or not plan.get("sql"):
        plan = _mock_plan(question)

    engine = analytics_engine()
    try:
        result = run_sql(engine, plan["sql"])
    except SqlGuardError as e:
        return {
            "agent": "oleg",
            "answer": f"Не смог выполнить SQL: {e}",
            "sql": plan.get("sql"),
            "explanation": plan.get("logic"),
            "columns": [],
            "rows": [],
            "chart": None,
            "excel_url": None,
            "tables_used": plan.get("tables_used") or [],
            "suggestions": [
                "Покажи топ-10 городов по поездкам",
                "Выручка по регионам за 30 дней",
            ],
        }
    except Exception:  # noqa: BLE001 — fall back to deterministic SQL
        plan = _mock_plan(question)
        result = run_sql(engine, plan["sql"])

    columns, rows = result["columns"], result["rows"]

    answer = ""
    if provider.provider != "mock":
        table_preview = {
            "columns": columns,
            "rows": rows[:30],
            "row_count": len(rows),
            "logic": plan.get("logic"),
            "sql": result["sql"],
        }
        answer = await provider.complete(
            ANSWER_SYSTEM,
            [
                ChatMessage(
                    "user",
                    f"Вопрос: {question}\n\nРезультат JSON:\n{json.dumps(table_preview, ensure_ascii=False)}",
                )
            ],
            lang=lang,
        )
    if not answer.strip():
        answer = _mock_answer(question, columns, rows, lang)

    wants_excel = bool(plan.get("wants_excel")) or force_excel or len(rows) >= 15
    excel_url = None
    if wants_excel and rows:
        path = export_rows(columns, rows, name="oleg_report")
        excel_url = f"/api/exports/{path.name}"

    chart_type = plan.get("chart_type")
    chart = None
    if chart_type in {"bar", "line", "pie"} and columns and rows:
        # x = first col, y = first numeric col after
        x_key = columns[0]
        y_key = None
        for i, col in enumerate(columns[1:], start=1):
            if rows and isinstance(rows[0][i], (int, float)):
                y_key = col
                break
        if y_key is None and len(columns) > 1:
            y_key = columns[1]
        if y_key:
            yi = columns.index(y_key)
            chart = {
                "type": chart_type,
                "x_key": x_key,
                "y_key": y_key,
                "points": [
                    {"x": str(r[0]), "y": float(r[yi]) if isinstance(r[yi], (int, float)) else 0}
                    for r in rows[:20]
                ],
            }

    return {
        "agent": "oleg",
        "answer": answer,
        "sql": plan.get("sql"),
        "explanation": plan.get("logic"),
        "columns": columns,
        "rows": rows[:100],
        "row_count": len(rows),
        "chart": chart,
        "excel_url": excel_url,
        "tables_used": plan.get("tables_used") or [],
        "suggestions": [
            "Сохрани это как сценарий",
            "Сравни с прошлым месяцем",
            "Выгрузи в Excel",
        ],
    }
