"""Tests for schema-based question suggestions."""
from __future__ import annotations

from app.db.datasources import suggest_questions


def test_ridego_keeps_hand_tuned_defaults():
    s = suggest_questions({"kind": "ridego"})
    assert s["ru"][0] == "Выручка по регионам за 30 дней"
    assert len(s["ru"]) == 3
    assert len(s["en"]) == 3


def test_csv_source_suggestions_from_columns():
    meta = {
        "kind": "csv",
        "table_name": "sales",
        "columns": [
            {"name": "region", "type": "text"},
            {"name": "revenue", "type": "integer"},
            {"name": "date", "type": "text"},
        ],
    }
    s = suggest_questions(meta)
    assert "Топ-10 region по revenue" in s["ru"]
    assert "Сумма revenue по region" in s["ru"]
    assert "Динамика revenue по месяцам" in s["ru"]
    assert "Top-10 region by revenue" in s["en"]


def test_postgres_suggestions_use_measures_not_ids():
    meta = {
        "kind": "postgres",
        "columns": [
            {"name": "orders", "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "status", "type": "character varying"},
                {"name": "total", "type": "numeric"},
                {"name": "order_date", "type": "date"},
            ]},
        ],
    }
    s = suggest_questions(meta)
    assert any("total" in q for q in s["ru"])
    assert not any(" по id" in q or "id по " in q for q in s["ru"])


def test_postgres_rich_tables_beat_plain_ones():
    # auth_permission has no measures; orders does — orders must win.
    meta = {
        "kind": "postgres",
        "columns": [
            {"name": "auth_permission", "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "name", "type": "character varying"},
            ]},
            {"name": "orders", "columns": [
                {"name": "id", "type": "bigint"},
                {"name": "city", "type": "text"},
                {"name": "amount", "type": "numeric"},
                {"name": "created", "type": "timestamp without time zone"},
            ]},
        ],
    }
    s = suggest_questions(meta)
    assert any("amount" in q for q in s["ru"]), s["ru"]
    assert not any("name по id" in q for q in s["ru"])


def test_metric_only_columns_give_average():
    meta = {
        "kind": "csv",
        "table_name": "sensor",
        "columns": [{"name": "temperature", "type": "real"}],
    }
    s = suggest_questions(meta)
    assert "Среднее temperature" in s["ru"]


def test_nothing_useful_falls_back_to_generic():
    meta = {"kind": "csv", "table_name": "x", "columns": [{"name": "id", "type": "integer"}]}
    s = suggest_questions(meta)
    assert s["ru"]  # non-empty fallback ("Сколько всего записей?" / generic)


def test_list_sources_exposes_suggestions(tmp_db):
    from app.db import datasources as ds

    out = ds.list_sources()
    assert out, "expected at least the ridego source"
    assert all("suggestions" in s for s in out)
    ridego = next(s for s in out if s["id"] == "ridego")
    assert ridego["suggestions"]["ru"][0] == "Выручка по регионам за 30 дней"
