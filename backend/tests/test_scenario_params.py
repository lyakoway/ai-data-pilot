"""Tests for parameterized scenarios: storage, substitution, and defaults."""
from __future__ import annotations

import pytest

from app.db import app_db
from app.api.routes.scenarios import _substitute


# --------------------------------------------------------------------------- #
# Storage: parameters round-trip through app.db
# --------------------------------------------------------------------------- #


def test_seed_scenarios_have_parameters(tmp_db):
    scen = {s["id"]: s for s in app_db.list_scenarios()}
    assert scen["sales-by-region"]["parameters"] is not None
    assert len(scen["sales-by-region"]["parameters"]) == 2  # period, group_by
    assert scen["top-cities-rides"]["parameters"] is not None
    # Scenarios without parameters stay None.
    assert scen["cancel-reasons"]["parameters"] is None


def test_create_scenario_with_parameters(tmp_db):
    sc = app_db.create_scenario(
        {
            "id": "test-param-1",
            "name": "Test",
            "agent": "oleg",
            "description": "",
            "prompt": "Выручка за {period} дней",
            "chart_type": "bar",
            "datasource_id": "ridego",
            "parameters": [{"name": "period", "type": "number", "default": 30}],
        }
    )
    fetched = app_db.get_scenario("test-param-1")
    assert fetched is not None
    assert fetched["parameters"] == [{"name": "period", "type": "number", "default": 30}]


def test_create_scenario_without_parameters(tmp_db):
    app_db.create_scenario(
        {
            "id": "test-noparam",
            "name": "No Params",
            "agent": "oleg",
            "description": "",
            "prompt": "Простой запрос",
            "chart_type": None,
            "datasource_id": None,
            "parameters": None,
        }
    )
    fetched = app_db.get_scenario("test-noparam")
    assert fetched is not None
    assert fetched["parameters"] is None


# --------------------------------------------------------------------------- #
# Substitution logic
# --------------------------------------------------------------------------- #


PROMPT = "Покажи выручку по {group_by} за последние {period} дней. Нужен {chart_type}."
PARAMS = [
    {"name": "period", "type": "number", "default": 30},
    {"name": "group_by", "type": "select", "options": ["регионам", "городам"], "default": "регионам"},
]


def test_substitute_uses_defaults_when_no_values():
    result = _substitute(PROMPT, PARAMS, None)
    assert "по регионам" in result
    assert "за последние 30 дней" in result
    # Undeclared placeholder {chart_type} is left intact.
    assert "{chart_type}" in result


def test_substitute_uses_provided_values():
    result = _substitute(PROMPT, PARAMS, {"period": 90, "group_by": "городам"})
    assert "по городам" in result
    assert "за последние 90 дней" in result


def test_substitute_partial_values_uses_defaults_for_rest():
    result = _substitute(PROMPT, PARAMS, {"period": 7})
    assert "по регионам" in result  # group_by fell back to default
    assert "за последние 7 дней" in result


def test_substitute_no_parameters_returns_prompt_unchanged():
    result = _substitute(PROMPT + " {extra}", None, None)
    assert result == PROMPT + " {extra}"


def test_substitute_leaves_unknown_placeholders_intact():
    result = _substitute("Hello {name}, period={period}", PARAMS, {"period": 10})
    assert "{name}" in result
    assert "period=10" in result


def test_substitute_empty_parameters_list():
    result = _substitute("Текст {x}", [], {"x": 1})
    assert result == "Текст {x}"


# --------------------------------------------------------------------------- #
# Migration: parameters column added to pre-existing tables
# --------------------------------------------------------------------------- #


def test_migration_is_idempotent(tmp_db):
    """Calling _init_db again (e.g. on restart) must not fail on existing column."""
    from sqlalchemy import inspect

    eng = app_db.get_app_engine()
    # First init already ran via tmp_db fixture. Verify column exists.
    cols = {c["name"] for c in inspect(eng).get_columns("scenarios")}
    assert "parameters" in cols
    # Re-running init must not raise (idempotent ALTER).
    from app.db.app_db import _init_db

    _init_db(eng)
    cols2 = {c["name"] for c in inspect(eng).get_columns("scenarios")}
    assert "parameters" in cols2
