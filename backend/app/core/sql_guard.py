"""Validate and safely execute read-only SQL against the analytics DB."""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH|PRAGMA|CREATE|REPLACE|"
    r"TRUNCATE|GRANT|REVOKE|COPY|VACUUM|INTO\s+OUTFILE)\b",
    re.IGNORECASE,
)


class SqlGuardError(ValueError):
    pass


def sanitize_sql(sql: str, row_limit: int | None = None) -> str:
    cleaned = sql.strip().rstrip(";")
    if not cleaned:
        raise SqlGuardError("Пустой SQL")
    if ";" in cleaned:
        raise SqlGuardError("Разрешён только один SQL-оператор")
    if FORBIDDEN.search(cleaned):
        raise SqlGuardError("Разрешены только SELECT / WITH запросы")
    # Must start with SELECT or WITH
    head = cleaned.lstrip().split(None, 1)[0].upper()
    if head not in {"SELECT", "WITH"}:
        raise SqlGuardError("Запрос должен начинаться с SELECT или WITH")
    limit = row_limit or get_settings().sql_row_limit
    # Wrap to enforce limit without breaking existing LIMIT
    return f"SELECT * FROM ({cleaned}) AS _q LIMIT {limit}"


def run_sql(engine: Engine, sql: str) -> dict[str, Any]:
    safe = sanitize_sql(sql)
    with engine.connect() as conn:
        result = conn.execute(text(safe))
        columns = list(result.keys())
        rows = [list(r) for r in result.fetchall()]
    # JSON-friendly values
    serializable = []
    for row in rows:
        serializable.append([_jsonable(v) for v in row])
    return {"columns": columns, "rows": serializable, "sql": safe, "row_count": len(serializable)}


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def analytics_engine() -> Engine:
    from app.db.seed import get_engine

    return get_engine()
