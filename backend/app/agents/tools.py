"""Agent tools for Oleg's ReAct loop.

Each tool is a thin wrapper over existing building blocks (sql_guard, analytics,
chart builder). Tools receive the caller's context (engine, columns/rows from
previous steps) and return a JSON-serialisable result that is fed back to the
LLM as the next message.

The ``finish`` tool is special: it terminates the loop and carries the final
natural-language answer.
"""
from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.engine import Engine

from app.core.analytics import compute_insights
from app.core.sql_guard import (
    SqlExecutionError,
    SqlGuardError,
    SqlTimeoutError,
    run_sql,
)

# Maximum number of loop iterations before the agent gives up.
MAX_LOOP_STEPS = 6


# --------------------------------------------------------------------------- #
# Tool result type
# --------------------------------------------------------------------------- #
#
# A tool returns a dict with at least:
#   {"ok": bool, "summary": str, "data": dict}
# ``summary`` is the short human-readable line shown in the execution trace;
# ``data`` is the structured payload fed back to the LLM (and stored in the
# step ``detail`` for the expandable UI).


def _ok(summary: str, **data: Any) -> dict[str, Any]:
    return {"ok": True, "summary": summary, "data": data}


def _err(summary: str, **data: Any) -> dict[str, Any]:
    return {"ok": False, "summary": summary, "data": data}


# --------------------------------------------------------------------------- #
# database_query
# --------------------------------------------------------------------------- #


def tool_database_query(args: dict[str, Any], *, engine: Engine, timeout: float | None = None, **_: Any) -> dict[str, Any]:
    """Execute a read-only SQL query against the analytics DB."""
    sql = (args.get("sql") or "").strip()
    if not sql:
        return _err("Пустой SQL" if args.get("_lang") != "en" else "Empty SQL")
    try:
        result = run_sql(engine, sql, timeout=timeout)
    except SqlGuardError as e:
        return _err(f"SQL отклонён: {e}", sql=sql)
    except SqlTimeoutError as e:
        return _err(str(e), sql=sql)
    except SqlExecutionError as e:
        return _err(f"Ошибка выполнения: {str(e)[:200]}", sql=sql)

    columns = result["columns"]
    rows = result["rows"]
    rc = result["row_count"]
    lang = args.get("_lang", "ru")
    summary = (
        f"{rc} строк" if lang != "en" else f"{rc} rows"
    ) if rc else ("нет данных" if lang != "en" else "no rows")
    return _ok(summary, sql=sql, columns=columns, rows=rows[:50], row_count=rc)


# --------------------------------------------------------------------------- #
# calculate
# --------------------------------------------------------------------------- #

_NUM_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


def tool_calculate(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Compute simple arithmetic. Accepts either an explicit expression or a
    pair of values to derive a percent change.

    Supported args:
      - ``expression``: arithmetic like "150000 - 138000" or "(150000/138000 - 1) * 100"
      - ``current`` + ``previous``: returns the percent change (growth rate)
      - ``values``: list of numbers → returns sum, avg, count
    """
    lang = args.get("_lang", "ru")

    if "current" in args and "previous" in args:
        try:
            cur = float(args["current"])
            prev = float(args["previous"])
        except (TypeError, ValueError):
            return _err("Нечисловые значения" if lang != "en" else "Non-numeric values")
        if prev == 0:
            return _ok("∞", current=cur, previous=prev, change_pct=None)
        pct = round((cur - prev) / abs(prev) * 100, 1)
        sign = "+" if pct >= 0 else ""
        summary = f"{sign}{pct}%"
        return _ok(summary, current=cur, previous=prev, change_pct=pct)

    if "values" in args:
        raw = args["values"]
        nums = [float(x) for x in _NUM_RE.findall(str(raw))] if isinstance(raw, str) else [
            float(x) for x in raw if isinstance(x, (int, float, str))
        ]
        nums = [n for n in nums if n is not None]
        if not nums:
            return _err("Нет чисел для расчёта" if lang != "en" else "No numbers")
        total = round(sum(nums), 2)
        avg = round(total / len(nums), 2)
        return _ok(f"sum={total}, avg={avg}", sum=total, avg=avg, count=len(nums))

    expr = (args.get("expression") or "").strip()
    if not expr:
        return _err("Нет expression/current+previous/values" if lang != "en" else "Missing expression")
    # Restricted evaluator: only digits, operators, parentheses, decimal point.
    if not re.fullmatch(r"[\d\s+\-*/().,%]+", expr):
        return _err("Недопустимые символы в expression" if lang != "en" else "Invalid chars in expression")
    try:
        value = eval(expr.replace(",", "."), {"__builtins__": {}}, {})  # noqa: S307 — sanitised above
    except Exception as e:  # noqa: BLE001
        return _err(f"Ошибка вычисления: {e}")
    value = round(float(value), 2) if isinstance(value, (int, float)) else value
    return _ok(str(value), result=value)


# --------------------------------------------------------------------------- #
# analyze
# --------------------------------------------------------------------------- #


def tool_analyze(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Run the deterministic analytics layer over a result set carried in args.

    Expects ``columns`` + ``rows`` (from a previous database_query step).
    """
    lang = args.get("_lang", "ru")
    columns = args.get("columns") or []
    rows = args.get("rows") or []
    if not columns or not rows:
        return _err("Нет данных для анализа" if lang != "en" else "No data to analyze")
    insights = compute_insights(list(columns), [list(r) for r in rows], lang=lang)
    n = len(insights.get("highlights", []))
    summary = f"{n} инсайт(ов)" if lang != "en" else f"{n} insight(s)"
    return _ok(summary, insights=insights)


# --------------------------------------------------------------------------- #
# create_chart
# --------------------------------------------------------------------------- #


def tool_create_chart(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Declare a chart to render. The actual chart payload is assembled by the
    caller from the most recent result set; here we just record the intent."""
    chart_type = (args.get("type") or "bar").lower()
    if chart_type not in {"bar", "line", "pie"}:
        return _err(f"Неизвестный тип графика: {chart_type}")
    x_key = args.get("x") or args.get("x_key")
    y_key = args.get("y") or args.get("y_key")
    return _ok(chart_type, chart_type=chart_type, x_key=x_key, y_key=y_key)


# --------------------------------------------------------------------------- #
# finish
# --------------------------------------------------------------------------- #


def tool_finish(args: dict[str, Any], **_: Any) -> dict[str, Any]:
    """Terminate the loop with a final answer. Always returns ok=True and a
    sentinel ``_finish`` flag the loop checks for."""
    answer = (args.get("answer") or "").strip()
    return {"ok": True, "summary": "Финальный ответ", "data": {"answer": answer}, "_finish": True}


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #

TOOLS: dict[str, Any] = {
    "database_query": tool_database_query,
    "calculate": tool_calculate,
    "analyze": tool_analyze,
    "create_chart": tool_create_chart,
    "finish": tool_finish,
}

TOOLS_DESCRIPTION = """У тебя есть следующие инструменты:

1. database_query — выполнить SQL-запрос к базе данных.
   args: {"sql": "SELECT ..."}
   Возвращает: {columns, rows, row_count}

2. calculate — арифметика: проценты, разница, среднее.
   Варианты args:
   - {"expression": "150000 - 138000"}
   - {"current": 150000, "previous": 138000}  → percent change
   - {"values": [10, 20, 30]}                 → sum, avg

3. analyze — проанализировать результат (тренды, топ, аномалии).
   args: {"columns": [...], "rows": [...]}
   Возвращает: insights с highlights.

4. create_chart — объявить график.
   args: {"type": "bar"|"line"|"pie", "x": "col", "y": "col"}

5. finish — завершить анализ и дать финальный ответ.
   args: {"answer": "текст ответа"}
"""


def dispatch_tool(
    name: str, args: dict[str, Any], *, engine: Engine = None, lang: str = "ru",
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute a tool by name. Unknown tools return an error result (never raise)."""
    fn = TOOLS.get(name)
    if fn is None:
        return _err(f"Неизвестный инструмент: {name}" if lang != "en" else f"Unknown tool: {name}")
    enriched = {**args, "_lang": lang}
    kwargs: dict[str, Any] = {"lang": lang}
    if engine is not None:
        kwargs["engine"] = engine
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return fn(enriched, **kwargs)
    except Exception as e:  # noqa: BLE001 — tools must never crash the loop
        return _err(f"Ошибка инструмента {name}: {str(e)[:200]}")


def tool_result_to_message(result: dict[str, Any]) -> str:
    """Serialise a tool result into a compact string for the LLM history."""
    data = {k: v for k, v in result.get("data", {}).items() if k != "rows"}
    if "rows" in result.get("data", {}):
        data["row_count"] = len(result["data"]["rows"])
    return json.dumps({"ok": result["ok"], "summary": result["summary"], **data}, ensure_ascii=False)
