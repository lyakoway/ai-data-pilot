"""Tests for the SQL guard: validation, execution, and timeout behaviour."""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine, text

from app.core.sql_guard import (
    SqlExecutionError,
    SqlGuardError,
    SqlTimeoutError,
    run_sql,
    sanitize_sql,
)


@pytest.fixture()
def populated_engine(tmp_path):
    """A small file-backed SQLite DB with one table."""
    db = tmp_path / "sg.db"
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE t (id INTEGER, v REAL, name TEXT)"))
        c.execute(text("INSERT INTO t VALUES (1, 10.0, 'a'), (2, 20.0, 'b'), (3, 30.0, 'c')"))
    return eng


# --- Static validation (sanitize_sql) ---


def test_select_wraps_with_limit():
    out = sanitize_sql("SELECT * FROM t")
    assert "_q LIMIT" in out


def test_custom_row_limit_applied():
    out = sanitize_sql("SELECT * FROM t", row_limit=7)
    assert out.endswith("LIMIT 7")


def test_empty_sql_rejected():
    with pytest.raises(SqlGuardError):
        sanitize_sql("   ")


def test_multi_statement_rejected():
    with pytest.raises(SqlGuardError, match="один SQL-оператор"):
        sanitize_sql("SELECT 1; SELECT 2")


@pytest.mark.parametrize(
    "sql",
    ["DELETE FROM t", "DROP TABLE t", "INSERT INTO t VALUES (1)", "UPDATE t SET v=1"],
)
def test_forbidden_keywords_rejected(sql):
    with pytest.raises(SqlGuardError, match="SELECT / WITH"):
        sanitize_sql(sql)


def test_non_select_start_rejected():
    with pytest.raises(SqlGuardError):
        sanitize_sql("EXPLAIN SELECT 1")


def test_with_cte_is_allowed():
    out = sanitize_sql("WITH x AS (SELECT 1) SELECT * FROM x")
    assert out.startswith("SELECT * FROM (WITH x AS")


# --- Execution (run_sql) ---


def test_run_sql_returns_rows(populated_engine):
    r = run_sql(populated_engine, "SELECT id, v FROM t ORDER BY id")
    assert r["columns"] == ["id", "v"]
    assert r["row_count"] == 3
    assert r["rows"][0] == [1, 10.0]


def test_run_sql_serializes_values(populated_engine):
    r = run_sql(populated_engine, "SELECT name FROM t WHERE id = 1")
    assert r["rows"] == [["a"]]


def test_run_sql_execution_error_on_bad_column(populated_engine):
    with pytest.raises(SqlExecutionError):
        run_sql(populated_engine, "SELECT nonexistent FROM t")


def test_run_sql_guard_error_surfaces_directly(populated_engine):
    # Validation failures must NOT be wrapped into SqlExecutionError.
    with pytest.raises(SqlGuardError):
        run_sql(populated_engine, "DELETE FROM t")


# --- Timeout ---


def test_timeout_raises_sql_timeout_error(populated_engine):
    # A deliberately slow recursive CTE with a tiny timeout.
    slow = (
        "WITH RECURSIVE cnt(x) AS ("
        "SELECT 1 UNION ALL SELECT x+1 FROM cnt WHERE x < 5000000"
        ") SELECT COUNT(*) FROM cnt"
    )
    with pytest.raises(SqlTimeoutError):
        run_sql(populated_engine, slow, timeout=0.05)


def test_timeout_message_includes_limit(populated_engine):
    slow = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM c WHERE x<5000000) SELECT COUNT(*) FROM c"
    with pytest.raises(SqlTimeoutError, match="0.05"):
        run_sql(populated_engine, slow, timeout=0.05)
