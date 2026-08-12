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
import time
from typing import Any, Awaitable, Callable

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
    steps: list[dict[str, Any]] | None = None,
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
        "steps": steps or [],
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
    steps: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if lang == "en":
        answer = f"I couldn't run the query. {message}"
    else:
        answer = f"Не удалось выполнить запрос. {message}"
    return {
        "agent": "oleg",
        "status": status,
        "warnings": warnings or [],
        "steps": steps or [],
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
    """Run Oleg synchronously and return the final result (with ``steps`` list).

    Thin wrapper over :func:`run_oleg_streaming` with a no-op step callback.
    """
    return await run_oleg_streaming(
        question=question,
        model_id=model_id,
        lang=lang,
        force_excel=force_excel,
        datasource_id=datasource_id,
        on_step=_noop_step,
    )


# --------------------------------------------------------------------------- #
# Streaming variant with execution-trace steps (foundation for the agent loop)
# --------------------------------------------------------------------------- #

# A callback the caller passes to receive trace steps as they happen.
# ``on_step`` is awaited with a step dict (see _new_step) on every step
# transition. Implementations may forward it over SSE, accumulate it, or ignore.
StepCallback = Callable[[dict[str, Any]], Awaitable[None]]


async def _noop_step(_step: dict[str, Any]) -> None:
    """Default no-op callback (used by the non-streaming ``run_oleg`` wrapper)."""
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


# --------------------------------------------------------------------------- #
# Agent loop (ReAct) — for multi-step questions
# --------------------------------------------------------------------------- #

import re as _re

# Questions matching these signals need a multi-step plan, not a single SQL.
_COMPLEX_RE = _re.compile(
    r"(почему|сравн|причин|динамик|упал|упало|выросл|вырос|изменен|раст[её]т|падает|"
    r"why|compare|reason|trend|drop|dropped|grow|grew|change|decline|increase)",
    _re.IGNORECASE,
)


def _is_complex_question(question: str) -> bool:
    """Heuristic: questions about causes, comparisons, or trends need the loop."""
    return bool(_COMPLEX_RE.search(question))


# Prompt-based tool-calling system. The model replies with a single JSON object
# per turn; we parse it, dispatch the tool, and feed the result back as a "tool"
# message until the model calls ``finish``.
LOOP_SYSTEM = """Ты — аналитик Олег. Пользователь задал вопрос, требующий нескольких шагов анализа.
Ты принимаешь решения пошагово: на каждом ходу выбираешь ОДИН инструмент и возвращаешь
строго JSON без markdown:

{{"thought": "краткое рассуждение (1 предложение)", "tool": "<имя>", "args": {{...}}}}

Доступные инструменты:
{tools}

Правила:
- За один ход — ровно один инструмент.
- Когда данных достаточно для ответа — вызови finish с полным текстом ответа.
- В args SQL используй только таблицы/поля из SCHEMA. Только SELECT/WITH.
- Язык ответа = язык вопроса. Не выдумывай цифры — считай через calculate или бери из database_query.

SCHEMA:
""".format(tools=__import__("app.agents.tools", fromlist=["TOOLS_DESCRIPTION"]).TOOLS_DESCRIPTION)


async def _run_loop(
    *,
    question: str,
    provider: Any,
    lang: str,
    engine: Any,
    schema_catalog: str,
    steps: list[dict[str, Any]],
    emit: Any,
) -> dict[str, Any]:
    """ReAct loop: the model picks tools until it calls ``finish``.

    Returns a full result dict (same shape as ``_ok_response``) on success,
    or an error dict if the loop exhausts its budget.
    """
    from app.agents.tools import MAX_LOOP_STEPS, dispatch_tool, tool_result_to_message

    messages: list[ChatMessage] = [ChatMessage("user", question)]
    last_chart: dict[str, Any] | None = None
    last_columns: list[str] = []
    last_rows: list[list[Any]] = []
    last_sql: str | None = None
    last_insights: dict[str, Any] = {}
    step_index = len(steps)

    for _ in range(MAX_LOOP_STEPS):
        step_index += 1
        think_step = _new_step(step_index, "agent", "Агент размышляет" if lang != "en" else "Agent reasoning")
        await emit(think_step)
        t0 = time.perf_counter()
        raw = await provider.complete(
            LOOP_SYSTEM + schema_catalog, list(messages), lang=lang
        )
        think_step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        decision = _extract_json(raw)
        if not decision or "tool" not in decision:
            think_step["status"] = "error"
            think_step["summary"] = (raw or "")[:140]
            await emit(think_step)
            break

        tool_name = decision["tool"]
        tool_args = decision.get("args") or {}
        thought = (decision.get("thought") or "")[:160]
        think_step["status"] = "done"
        think_step["summary"] = thought or tool_name
        await emit(think_step)

        # Dispatch the tool.
        step_index += 1
        tool_step = _new_step(step_index, tool_name, f"Вызываю: {tool_name}")
        await emit(tool_step)
        t0 = time.perf_counter()
        result = dispatch_tool(tool_name, tool_args, engine=engine, lang=lang)
        tool_step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        tool_step["status"] = "done" if result.get("ok") else "error"
        tool_step["summary"] = result.get("summary", "")[:140]
        tool_step["detail"] = {
            k: v for k, v in result.get("data", {}).items()
            if k not in ("rows",) or tool_name == "database_query"
        }
        await emit(tool_step)

        # Track the latest result set for the final response payload.
        if tool_name == "database_query" and result.get("ok"):
            last_columns = result["data"].get("columns", [])
            last_rows = result["data"].get("rows", [])
            last_sql = result["data"].get("sql")
        if tool_name == "analyze" and result.get("ok"):
            last_insights = result["data"].get("insights", {})
        if tool_name == "create_chart" and result.get("ok"):
            last_chart = _build_chart(
                {
                    "chart_type": result["data"].get("chart_type"),
                    **({"x_key": result["data"]["x_key"]} if result["data"].get("x_key") else {}),
                    **({"y_key": result["data"]["y_key"]} if result["data"].get("y_key") else {}),
                },
                last_columns,
                last_rows,
            )

        if result.get("_finish"):
            answer = result["data"].get("answer", "")
            return {
                "agent": "oleg",
                "status": "ok",
                "warnings": [],
                "steps": steps,
                "answer": answer,
                "sql": last_sql,
                "explanation": None,
                "columns": last_columns[:100],
                "rows": last_rows[:100],
                "row_count": len(last_rows),
                "chart": last_chart,
                "excel_url": None,
                "tables_used": [],
                "insights": last_insights,
                "suggestions": [
                    "Сохрани это как сценарий",
                    "Сравни с другим периодом",
                    "Выгрузи в Excel",
                ],
            }

        # Feed the result back to the model.
        messages.append(ChatMessage("assistant", json.dumps(decision, ensure_ascii=False)))
        messages.append(ChatMessage("tool", tool_result_to_message(result)))

    # Loop exhausted without finish.
    return _error_response(
        plan={"sql": last_sql, "logic": None, "tables_used": []},
        message=(
            f"Агент не завершил анализ за {MAX_LOOP_STEPS} шагов. Попробуйте переформулировать вопрос."
            if lang != "en"
            else f"Agent did not finish within {MAX_LOOP_STEPS} steps."
        ),
        lang=lang,
        steps=steps,
    )


async def _mock_loop(
    *,
    question: str,
    lang: str,
    engine: Any,
    steps: list[dict[str, Any]],
    emit: Any,
) -> dict[str, Any]:
    """Deterministic scripted loop for demo mode (no LLM). Simulates the
    'compare periods' scenario: query two months → calculate change → analyze."""
    from app.agents.tools import dispatch_tool, tool_result_to_message

    step_index = len(steps)

    async def do_tool(name: str, args: dict[str, Any], title: str) -> dict[str, Any]:
        nonlocal step_index
        step_index += 1
        s = _new_step(step_index, name, title)
        await emit(s)
        t0 = time.perf_counter()
        r = dispatch_tool(name, args, engine=engine, lang=lang)
        s["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        s["status"] = "done" if r.get("ok") else "error"
        s["summary"] = r.get("summary", "")[:140]
        s["detail"] = {k: v for k, v in r.get("data", {}).items() if k != "rows"}
        await emit(s)
        return r

    # Step 1: revenue by month.
    r1 = await do_tool(
        "database_query",
        {"sql": (
            "SELECT strftime('%Y-%m', ride_date) AS month, "
            "ROUND(SUM(revenue_rub), 0) AS revenue, COUNT(*) AS rides "
            "FROM fact_rides WHERE is_partner = 0 "
            "GROUP BY month ORDER BY month DESC LIMIT 3"
        )},
        "Запрашиваю выручку по месяцам",
    )
    cols = r1["data"].get("columns", [])
    rows = r1["data"].get("rows", [])
    # Determine current/previous from the result (last two months).
    cur = prev = None
    if len(rows) >= 2:
        # rows: [ [month, revenue, rides], ... ] newest first
        try:
            cur = float(rows[0][1])
            prev = float(rows[1][1])
            cur_rides = float(rows[0][2])
            prev_rides = float(rows[1][2])
        except (IndexError, TypeError, ValueError):
            cur = prev = cur_rides = prev_rides = None

    # Step 2: revenue change.
    change_pct = None
    if cur is not None and prev is not None:
        r2 = await do_tool(
            "calculate",
            {"current": cur, "previous": prev},
            "Считаю изменение выручки",
        )
        change_pct = r2["data"].get("change_pct")
        # Step 3: rides change.
        if cur_rides is not None and prev_rides is not None:
            await do_tool(
                "calculate",
                {"current": cur_rides, "previous": prev_rides},
                "Считаю изменение количества поездок",
            )

    # Step 4: analyze.
    r4 = await do_tool("analyze", {"columns": cols, "rows": rows}, "Анализирую данные")
    insights = r4["data"].get("insights", {}) if r4.get("ok") else {}

    # Step 5: chart.
    chart = _build_chart({"chart_type": "bar"}, cols, rows)

    # Step 6: finish.
    step_index += 1
    fin = _new_step(step_index, "finish", "Формирую ответ")
    await emit(fin)
    fin["status"] = "done"
    fin["summary"] = "Готово"
    await emit(fin)

    # Compose a deterministic answer.
    if change_pct is not None:
        direction = "выросла" if change_pct >= 0 else "снизилась"
        if lang == "en":
            direction = "grew" if change_pct >= 0 else "decreased"
            answer = (
                f"Revenue {direction} by {abs(change_pct)}% month-over-month. "
                f"Highlights: " + "; ".join(insights.get("highlights", [])[:3])
            )
        else:
            answer = (
                f"Выручка {direction} на {abs(change_pct)}% по сравнению с предыдущим месяцем. "
                f"Ключевые наблюдения: " + "; ".join(insights.get("highlights", [])[:3])
            )
    else:
        answer = _mock_answer(question, cols, rows, lang)

    return {
        "agent": "oleg",
        "status": "demo",
        "warnings": [],
        "steps": steps,
        "answer": answer,
        "sql": r1["data"].get("sql"),
        "explanation": "Сравнение выручки по месяцам, расчёт изменения, анализ факторов.",
        "columns": cols[:100],
        "rows": rows[:100],
        "row_count": len(rows),
        "chart": chart,
        "excel_url": None,
        "tables_used": ["fact_rides"],
        "insights": insights,
        "suggestions": [
            "Сохрани это как сценарий",
            "Сравни с другим периодом",
            "Выгрузи в Excel",
        ],
    }



async def run_oleg_streaming(
    question: str,
    model_id: str = "mock",
    lang: str = "ru",
    force_excel: bool = False,
    datasource_id: str = RIDEGO_SOURCE_ID,
    on_step: StepCallback = _noop_step,
) -> dict[str, Any]:
    """Run Oleg and emit execution-trace steps via ``on_step`` as work proceeds.

    This is the canonical implementation; :func:`run_oleg` delegates here with a
    no-op callback. Each step is a dict shaped like a future tool-call so the UI
    contract stays stable when a real agent loop lands.
    """
    provider = get_provider(model_id)
    is_mock = provider.provider == "mock"
    steps: list[dict[str, Any]] = []

    async def emit(step: dict[str, Any]) -> None:
        """Notify the SSE listener of a step transition and, on completion/error,
        freeze a copy into the final ``steps`` list. A snapshot is sent so later
        mutations of the same dict don't retroactively change the frame."""
        await on_step(dict(step))
        if step["status"] in ("done", "error"):
            steps.append(dict(step))

    # Resolve the active data source.
    schema_catalog = get_schema_catalog(datasource_id) if datasource_id else SCHEMA_CATALOG
    try:
        engine = get_engine_for(datasource_id) if datasource_id else _legacy_engine()
    except KeyError:
        schema_catalog = SCHEMA_CATALOG
        engine = _legacy_engine()
    is_ridego = datasource_id == RIDEGO_SOURCE_ID

    # --- Route: complex multi-step questions go through the ReAct loop,
    #     simple questions use the fast linear flow below. CSV sources always
    #     use the linear flow (mock plans are RideGo-specific; the loop's mock
    #     script is RideGo-specific too). ---
    if is_ridego and _is_complex_question(question):
        if is_mock:
            return await _mock_loop(
                question=question, lang=lang, engine=engine, steps=steps, emit=emit
            )
        return await _run_loop(
            question=question,
            provider=provider,
            lang=lang,
            engine=engine,
            schema_catalog=schema_catalog,
            steps=steps,
            emit=emit,
        )

    # --- Step 1: planning -------------------------------------------------
    step = _new_step(1, "planner", "Анализирую запрос" if lang != "en" else "Analysing request")
    await emit(step)
    t0 = time.perf_counter()
    plan = await _generate_plan(provider, question, lang, schema_catalog, allow_mock=is_ridego)
    step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    if not plan or not plan.get("sql"):
        if is_mock and is_ridego:
            plan = _mock_plan(question)
        else:
            step["status"] = "error"
            step["summary"] = "Модель не вернула SQL" if lang != "en" else "No SQL returned"
            await emit(step)
            return _error_response(
                plan=plan,
                message=(
                    "В демо-режиме нельзя строить запросы по пользовательским данным. "
                    "Подключите реальную модель (OpenAI / Anthropic / Z.ai / Ollama)."
                    if lang != "en"
                    else "Demo mode can't query uploaded data. Connect a real model."
                ),
                lang=lang,
                steps=steps,
            )
    step["status"] = "done"
    logic = plan.get("logic") or ""
    step["summary"] = (logic[:140] + "…") if len(logic) > 140 else logic or "План готов"
    step["detail"] = {"logic": logic, "tables_used": plan.get("tables_used") or []}
    await emit(step)

    warnings: list[str] = []

    # --- Step 2: database query (+ optional self-correction) -------------
    result: dict[str, Any] | None = None
    step_index = 2
    for attempt in range(MAX_SQL_ATTEMPTS):
        title = "Получаю данные из БД" if lang != "en" else "Querying the database"
        if attempt > 0:
            title = "Исправляю запрос" if lang != "en" else "Repairing the query"
        step = _new_step(step_index, "database_query", title)
        await emit(step)
        t0 = time.perf_counter()
        try:
            result = run_sql(engine, plan["sql"])
            step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            step["status"] = "done"
            rc = result["row_count"]
            step["summary"] = f"{rc} строк" if lang != "en" else f"{rc} rows"
            step["detail"] = {"sql": plan["sql"], "row_count": rc}
            await emit(step)
            break
        except (SqlGuardError, SqlTimeoutError) as e:
            step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            step["status"] = "error"
            step["summary"] = str(e)[:140]
            await emit(step)
            return _error_response(plan=plan, message=str(e), lang=lang, steps=steps)
        except SqlExecutionError as e:
            step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
            if is_mock or attempt >= MAX_SQL_REPAIR_ROUNDS:
                step["status"] = "error"
                step["summary"] = str(e)[:140]
                await emit(step)
                if attempt >= MAX_SQL_REPAIR_ROUNDS:
                    msg = (
                        f"SQL не выполнился после {MAX_SQL_ATTEMPTS} попыток. Последняя ошибка: {e}"
                        if lang != "en"
                        else f"SQL failed after {MAX_SQL_ATTEMPTS} attempts. Last error: {e}"
                    )
                    return _error_response(plan=plan, message=msg, lang=lang, warnings=[str(e)], steps=steps)
                return _error_response(plan=plan, message=str(e), lang=lang, steps=steps)
            step["status"] = "error"
            step["summary"] = f"Ошибка: {str(e)[:100]}"
            await emit(step)
            repaired = await _repair_plan(provider, question, plan["sql"], str(e), lang, schema_catalog)
            if repaired and repaired.get("sql") and repaired["sql"] != plan["sql"]:
                plan = repaired
                warnings.append(
                    f"Самокоррекция: переписал запрос (попытка {attempt + 2})."
                    if lang != "en"
                    else f"Self-correction: rewrote query (attempt {attempt + 2})."
                )
                step_index += 1
                continue
            return _error_response(
                plan=plan,
                message=("Не удалось исправить SQL автоматически." if lang != "en"
                         else "Could not repair the SQL automatically."),
                lang=lang,
                warnings=[str(e)],
                steps=steps,
            )

    assert result is not None
    columns, rows = result["columns"], result["rows"]

    # --- Step 3: deterministic analytics ---------------------------------
    step_index += 1
    step = _new_step(step_index, "analyze", "Считаю метрики" if lang != "en" else "Computing metrics")
    await emit(step)
    t0 = time.perf_counter()
    insights = compute_insights(columns, rows, question=question, lang=lang)
    step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    step["status"] = "done"
    n_hl = len(insights.get("highlights", []))
    step["summary"] = f"{n_hl} инсайт(ов)" if lang != "en" else f"{n_hl} insight(s)"
    step["detail"] = {"highlights": insights.get("highlights", [])}
    await emit(step)

    # --- Step 4 (optional): chart ---------------------------------------
    chart_type = plan.get("chart_type")
    if chart_type in {"bar", "line", "pie"}:
        step_index += 1
        step = _new_step(step_index, "chart", "Готовлю визуализацию" if lang != "en" else "Building chart")
        await emit(step)
        t0 = time.perf_counter()
        chart = _build_chart(plan, columns, rows)
        step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        step["status"] = "done"
        step["summary"] = chart_type
        step["detail"] = {"chart_type": chart_type, "points": len(chart["points"]) if chart else 0}
        await emit(step)

    # --- Step 5: answer --------------------------------------------------
    step_index += 1
    step = _new_step(step_index, "answer", "Формирую ответ" if lang != "en" else "Composing answer")
    await emit(step)
    t0 = time.perf_counter()
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
    step["duration_ms"] = int((time.perf_counter() - t0) * 1000)
    step["status"] = "done"
    step["summary"] = (answer[:140] + "…") if len(answer) > 140 else answer
    await emit(step)

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
        steps=steps,
    )

