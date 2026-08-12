"""Validate and safely execute read-only SQL against the analytics DB."""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
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
    """Raised when SQL fails static validation (forbidden keywords, multi-statement, …)."""


class SqlExecutionError(RuntimeError):
    """Raised when a validated query fails while executing (syntax/runtime error)."""


class SqlTimeoutError(RuntimeError):
    """Raised when a query exceeds the configured execution timeout."""


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


def run_sql(engine: Engine, sql: str, *, timeout: float | None = None) -> dict[str, Any]:
    """Validate and execute ``sql`` against ``engine``.

    ``SqlGuardError`` — static validation failure (never retried).
    ``SqlTimeoutError`` — query exceeded the timeout (surfaces to the user).
    ``SqlExecutionError`` — the DB rejected the validated query (retryable by the
    self-correction loop, e.g. a typo in a column name).
    """
    safe = sanitize_sql(sql)
    timeout = timeout if timeout is not None else get_settings().sql_timeout_sec

    def _exec() -> dict[str, Any]:
        with engine.connect() as conn:
            result = conn.execute(text(safe))
            columns = list(result.keys())
            rows = [list(r) for r in result.fetchall()]
        serializable = [[_jsonable(v) for v in row] for row in rows]
        return {"columns": columns, "rows": serializable, "sql": safe, "row_count": len(serializable)}

    # SQLite access is synchronous and blocking. Run it in a worker thread so we
    # can enforce a hard timeout without blocking the async event loop.
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="sqlguard") as pool:
            future = pool.submit(_exec)
            return future.result(timeout=timeout)
    except FuturesTimeoutError as e:
        raise SqlTimeoutError(
            f"Запрос превысил лимит времени ({timeout:g} с). Попробуйте сузить диапазон или агрегировать данные."
        ) from e
    except SqlGuardError:
        raise
    except SqlExecutionError:
        raise
    except Exception as e:  # SQLAlchemy OperationalError/ProgrammingError/etc.
        raise SqlExecutionError(str(e)) from e


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float, str, bool)):
        return v
    return str(v)


def analytics_engine() -> Engine:
    from app.db.seed import get_engine

    return get_engine()
