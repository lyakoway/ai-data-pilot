"""Tests for the PostgreSQL data source (introspection via dialect-agnostic API).

The introspection uses SQLAlchemy's ``inspect()`` which works identically for
SQLite and PostgreSQL — so we test against SQLite. Real PostgreSQL connectivity
requires a running server (see docker-compose.test.yml) and is covered by the
end-to-end smoke test.
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, text

from app.agents.oleg import _dialect_note
from app.db import datasources as ds


@pytest.fixture()
def sqlite_engine(tmp_path):
    """A small SQLite DB mimicking an external database (same inspect() API)."""
    db = tmp_path / "ext.db"
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, price NUMERIC)"))
        c.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, total NUMERIC, order_date DATE)"))
    return eng


# --------------------------------------------------------------------------- #
# Introspection (dialect-agnostic)
# --------------------------------------------------------------------------- #


def test_introspect_finds_tables_and_columns(sqlite_engine):
    tables = ds._introspect_schema(sqlite_engine)
    names = {t["name"] for t in tables}
    assert names == {"products", "orders"}
    products = next(t for t in tables if t["name"] == "products")
    col_names = [c["name"] for c in products["columns"]]
    assert col_names == ["id", "name", "price"]


def test_introspect_respects_max_tables(tmp_path):
    db = tmp_path / "many.db"
    eng = create_engine(f"sqlite:///{db}")
    with eng.begin() as c:
        for i in range(60):
            c.execute(text(f"CREATE TABLE t{i} (id INTEGER)"))
    tables = ds._introspect_schema(eng, max_tables=50)
    assert len(tables) == 50


def test_build_postgres_catalog(sqlite_engine):
    tables = ds._introspect_schema(sqlite_engine)
    meta = {
        "name": "Shop DB",
        "connection": {"database": "shop", "host": "localhost"},
        "columns": tables,
    }
    catalog = ds._build_postgres_catalog(meta)
    assert "PostgreSQL source" in catalog
    assert "## products" in catalog
    assert "## orders" in catalog
    assert "PostgreSQL syntax" in catalog
    assert "DATE_TRUNC" in catalog


# --------------------------------------------------------------------------- #
# Source registration + metadata (connection storage, password masking)
# --------------------------------------------------------------------------- #


def _make_pg_meta(sid: str = "pg_test") -> dict:
    tables = [{"name": "t1", "columns": [{"name": "id", "type": "INTEGER"}]}]
    return {
        "id": sid,
        "name": "Test PG",
        "kind": "postgres",
        "description": "test",
        "table_name": None,
        "columns": tables,
        "row_count": None,
        "created_at": None,
        "connection": {
            "host": "localhost",
            "port": 5432,
            "database": "db",
            "username": "user",
            "password": "SECRET123",
        },
    }


def test_pg_source_roundtrip_with_connection(tmp_db):
    meta = _make_pg_meta()
    ds.app_db.save_datasource(meta)
    fetched = ds.get_source_meta("pg_test")
    assert fetched["kind"] == "postgres"
    assert fetched["connection"]["password"] == "SECRET123"  # server-side full access
    assert fetched["connection"]["host"] == "localhost"


def test_list_sources_never_leaks_password(tmp_db):
    meta = _make_pg_meta()
    ds.app_db.save_datasource(meta)
    listing = json.dumps(ds.list_sources())
    assert "SECRET123" not in listing
    assert "connection" not in listing


def test_get_dialect_postgres_vs_sqlite(tmp_db):
    meta = _make_pg_meta("pg_dialect")
    ds.app_db.save_datasource(meta)
    assert ds.get_dialect("pg_dialect") == "postgresql"
    assert ds.get_dialect("ridego") == "sqlite"
    assert ds.get_dialect("unknown-source") == "sqlite"


def test_dialect_note_switches_by_source(tmp_db):
    meta = _make_pg_meta("pg_note")
    ds.app_db.save_datasource(meta)
    pg_note = _dialect_note("pg_note")
    sqlite_note = _dialect_note("ridego")
    assert "PostgreSQL" in pg_note
    assert "DATE_TRUNC" in pg_note
    assert "SQLite" in sqlite_note


def test_get_schema_catalog_for_postgres(tmp_db):
    meta = _make_pg_meta("pg_cat")
    ds.app_db.save_datasource(meta)
    catalog = ds.get_schema_catalog("pg_cat")
    assert "PostgreSQL source" in catalog
    assert "## t1" in catalog


# --------------------------------------------------------------------------- #
# Registration failure (no real server available)
# --------------------------------------------------------------------------- #


def test_register_postgres_bad_host_raises(tmp_db):
    with pytest.raises(ValueError, match="Could not connect"):
        ds.register_postgres(
            name="Bad",
            host="nonexistent.invalid",
            port=5432,
            database="x",
            username="x",
            password="x",
            # Short connect timeout to keep the test fast.
        )


# --------------------------------------------------------------------------- #
# Engine building
# --------------------------------------------------------------------------- #


def test_build_postgres_url():
    url = ds._build_postgres_url(
        {"host": "h", "port": 5433, "database": "d", "username": "u", "password": "p"}
    )
    assert url == "postgresql+psycopg2://u:p@h:5433/d"


def test_get_engine_for_postgres(tmp_db):
    meta = _make_pg_meta("pg_engine")
    ds.app_db.save_datasource(meta)
    eng = ds.get_engine_for("pg_engine")
    assert "postgresql" in str(eng.url)
    ds.delete_source("pg_engine")
    assert ds.get_source_meta("pg_engine") is None
