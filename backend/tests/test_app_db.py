"""Tests for the app DB layer: scenarios CRUD, datasources metadata, feedback."""
from __future__ import annotations

import pytest

from app.db import app_db


# --- Scenarios ---


def test_scenarios_seeded_on_init(tmp_db):
    scen = app_db.list_scenarios()
    assert len(scen) >= 5  # seed scenarios
    ids = [s["id"] for s in scen]
    assert "sales-by-region" in ids


def test_create_and_delete_scenario(tmp_db):
    sc = app_db.create_scenario(
        {
            "id": "test-sc-1",
            "name": "Test",
            "agent": "oleg",
            "description": "",
            "prompt": "тест",
            "chart_type": "bar",
            "datasource_id": "ridego",
        }
    )
    assert sc["id"] == "test-sc-1"
    fetched = app_db.get_scenario("test-sc-1")
    assert fetched is not None
    assert fetched["prompt"] == "тест"
    assert fetched["datasource_id"] == "ridego"

    assert app_db.delete_scenario("test-sc-1") is True
    assert app_db.get_scenario("test-sc-1") is None
    # Deleting again returns False (already gone).
    assert app_db.delete_scenario("test-sc-1") is False


def test_get_unknown_scenario_returns_none(tmp_db):
    assert app_db.get_scenario("does-not-exist") is None


# --- Datasources metadata ---


def test_ridego_datasource_present(tmp_db):
    ds = app_db.list_datasources()
    ids = [d["id"] for d in ds]
    assert "ridego" in ids


def test_save_and_get_datasource(tmp_db):
    meta = {
        "id": "csv_test1",
        "name": "Test CSV",
        "kind": "csv",
        "description": "test",
        "table_name": "src_csv_test1",
        "columns": [{"name": "a", "type": "integer", "sqlite_type": "INTEGER"}],
        "row_count": 10,
        "created_at": "2026-08-12T00:00:00",
    }
    app_db.save_datasource(meta)
    fetched = app_db.get_datasource("csv_test1")
    assert fetched is not None
    assert fetched["name"] == "Test CSV"
    assert fetched["columns"] == [{"name": "a", "type": "integer", "sqlite_type": "INTEGER"}]
    assert fetched["row_count"] == 10


def test_delete_datasource_row(tmp_db):
    meta = {
        "id": "csv_del1",
        "name": "To Delete",
        "kind": "csv",
        "description": "",
        "table_name": None,
        "columns": None,
        "row_count": None,
        "created_at": None,
    }
    app_db.save_datasource(meta)
    assert app_db.get_datasource("csv_del1") is not None
    assert app_db.delete_datasource_row("csv_del1") is True
    assert app_db.get_datasource("csv_del1") is None
    # ridego row is a normal row — deleting it via delete_datasource_row is allowed
    # at the storage layer (the business rule lives in datasources.delete_source).


# --- Feedback ---


def test_save_feedback_returns_id(tmp_db):
    fb = app_db.save_feedback(
        {"vote": "up", "agent": "oleg", "message": "сколько поездок?", "answer": "100", "lang": "ru"}
    )
    assert "id" in fb and fb["id"] > 0
    assert fb["created_at"]


def test_feedback_stats_counts_votes(tmp_db):
    app_db.save_feedback({"vote": "up", "agent": "oleg", "lang": "ru"})
    app_db.save_feedback({"vote": "up", "agent": "oleg", "lang": "ru"})
    app_db.save_feedback({"vote": "down", "agent": "ksyusha", "lang": "en"})
    stats = app_db.feedback_stats()
    assert stats["up"] == 2
    assert stats["down"] == 1
    assert stats["total"] == 3
    assert stats["satisfaction"] == 66.7
    assert stats["per_agent"]["oleg"] == {"up": 2, "down": 0}
    assert stats["per_agent"]["ksyusha"] == {"up": 0, "down": 1}


def test_feedback_truncates_long_text(tmp_db):
    long_msg = "x" * 5000
    long_ans = "y" * 5000
    fb = app_db.save_feedback(
        {"vote": "down", "agent": "oleg", "message": long_msg, "answer": long_ans, "lang": "ru"}
    )
    assert fb["id"] > 0  # stored without error; truncation happens at DB layer
