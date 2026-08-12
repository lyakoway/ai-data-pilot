"""Tests for the data sources registry and multi-source Oleg behaviour."""
from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.agents import oleg as oleg_module
from app.agents.oleg import run_oleg
from app.db import datasources as ds

CSV_TEXT = """region,revenue,rides,date
Центр,150000,1200,2026-04-01
Урал,80000,650,2026-04-01
Сибирь,45000,300,2026-04-01
Центр,165000,1300,2026-05-01
Урал,85000,700,2026-05-01
"""


@pytest.fixture()
def csv_source(tmp_db):
    meta = ds.ingest_csv("sales.csv", CSV_TEXT)
    yield meta
    # cleanup: remove if still present
    try:
        ds.delete_source(meta["id"])
    except KeyError:
        pass


# --- Registry ---


def test_ridego_is_always_listed(tmp_db):
    sources = ds.list_sources()
    ids = [s["id"] for s in sources]
    assert ds.RIDEGO_SOURCE_ID in ids


def test_ridego_cannot_be_deleted(tmp_db):
    with pytest.raises(ValueError):
        ds.delete_source(ds.RIDEGO_SOURCE_ID)


def test_unknown_source_raises_keyerror(tmp_db):
    with pytest.raises(KeyError):
        ds.get_engine_for("does_not_exist")
    with pytest.raises(KeyError):
        ds.get_schema_catalog("does_not_exist")


# --- CSV ingestion ---


def test_ingest_creates_source_with_inferred_types(csv_source):
    meta = csv_source
    assert meta["kind"] == "csv"
    assert meta["row_count"] == 5
    types = {c["name"]: c["type"] for c in meta["columns"]}
    assert types["region"] == "text"
    assert types["revenue"] == "integer"
    assert types["rides"] == "integer"
    assert types["date"] == "text"  # ISO dates kept as text


def test_ingest_table_is_queryable(csv_source):
    eng = ds.get_engine_for(csv_source["id"])
    with eng.connect() as c:
        row = c.execute(
            text(f'SELECT COUNT(*) FROM "{csv_source["table_name"]}"')
        ).scalar()
    assert row == 5


def test_csv_schema_catalog_describes_columns(csv_source):
    catalog = ds.get_schema_catalog(csv_source["id"])
    assert csv_source["table_name"] in catalog
    assert "revenue" in catalog
    assert "INTEGER" in catalog


def test_delete_source_removes_table_and_meta(csv_source):
    sid = csv_source["id"]
    table = csv_source["table_name"]
    ds.delete_source(sid)
    # meta gone
    assert all(s["id"] != sid for s in ds.list_sources())
    # table gone
    from app.db.datasources import _csv_engine

    with _csv_engine().connect() as c:
        exists = c.execute(
            text(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        ).fetchone()
    assert exists is None


def test_empty_csv_rejected(tmp_db):
    with pytest.raises(ValueError):
        ds.ingest_csv("empty.csv", "")


def test_header_only_csv_rejected(tmp_db):
    with pytest.raises(ValueError):
        ds.ingest_csv("header_only.csv", "a,b,c\n")


def test_invalid_filename_rejected(tmp_db):
    with pytest.raises(ValueError):
        ds.ingest_csv("", "a,b\n1,2\n")


# --- Oleg multi-source behaviour ---


@pytest.mark.asyncio
async def test_csv_with_mock_mode_honest_error(csv_source, monkeypatch):
    """Mock plans are RideGo-specific; a CSV source must not silently use them."""
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg(
        "выручка по регионам", model_id="mock", lang="ru", datasource_id=csv_source["id"]
    )
    assert r["status"] == "error"
    assert "демо" in r["answer"].lower() or "real" in r["answer"].lower()
    assert r["row_count"] == 0


@pytest.mark.asyncio
async def test_csv_with_real_model_builds_sql_against_csv_table(
    csv_source, monkeypatch, fake_provider_factory
):
    table = csv_source["table_name"]
    fake = fake_provider_factory(
        responses=[
            json.dumps(
                {
                    "sql": f'SELECT region, SUM(revenue) AS total FROM "{table}" GROUP BY region ORDER BY total DESC',
                    "chart_type": "bar",
                    "wants_excel": True,
                    "tables_used": [table],
                    "logic": "сумма revenue по регионам из CSV",
                }
            ),
            "Центр лидирует по выручке.",
        ],
        provider="openai",
    )
    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: fake)

    r = await run_oleg(
        "выручка по регионам",
        model_id="openai:gpt-4o-mini",
        lang="ru",
        datasource_id=csv_source["id"],
    )
    assert r["status"] == "ok"
    assert r["row_count"] == 3  # 3 regions
    assert r["rows"][0][0] == "Центр"
    assert r["insights"]["top"][0]["label"] == "Центр"


@pytest.mark.asyncio
async def test_ridego_source_still_uses_mock_plans(tmp_db, monkeypatch):
    """Backward compatibility: RideGo + mock still serves deterministic plans."""
    from app.llm.providers import MockProvider

    monkeypatch.setattr(oleg_module, "get_provider", lambda model_id: MockProvider())
    r = await run_oleg("топ городов", model_id="mock", lang="ru")
    assert r["status"] == "demo"
    assert r["row_count"] > 0
