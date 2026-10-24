"""Regression tests for tool_result_to_message (rows preview fed to the model).

The ReAct loop used to strip ``rows`` entirely — the model saw only the row
count, could not reason about values, re-queried and exhausted the step budget
(found by the e2e benchmark: 100% failures on multi-step questions). These
tests pin the fixed contract: bounded preview, row_count, no unbounded dumps.
"""
from __future__ import annotations

import json

from app.agents.tools import MAX_CELL_CHARS, MAX_PREVIEW_ROWS, tool_result_to_message


def _parse(msg: str) -> dict:
    return json.loads(msg)


def test_rows_preview_included_with_columns():
    result = {
        "ok": True,
        "summary": "2 строк",
        "data": {"columns": ["city", "rides"], "rows": [["Москва", 10], ["Казань", 5]], "row_count": 2},
    }
    parsed = _parse(tool_result_to_message(result))
    assert parsed["row_count"] == 2
    assert parsed["rows_preview"] == [
        {"city": "Москва", "rides": 10},
        {"city": "Казань", "rides": 5},
    ]
    # the raw unbounded list must NOT leak into the history
    assert "rows" not in parsed or parsed["rows"] in (None, parsed["rows_preview"])


def test_empty_rows_still_reports_count():
    result = {"ok": True, "summary": "нет данных", "data": {"columns": ["x"], "rows": [], "row_count": 0}}
    parsed = _parse(tool_result_to_message(result))
    assert parsed["row_count"] == 0
    assert parsed["rows_preview"] == []


def test_many_rows_capped_and_flagged():
    rows = [[f"city_{i}", i] for i in range(500)]
    result = {"ok": True, "summary": "500 строк", "data": {"columns": ["city", "n"], "rows": rows}}
    parsed = _parse(tool_result_to_message(result))
    assert parsed["row_count"] == 500
    assert len(parsed["rows_preview"]) == MAX_PREVIEW_ROWS
    assert "из 500" in parsed["rows_preview_note"]
    # bounded message size even for wide results
    assert len(tool_result_to_message(result)) < 8_000


def test_long_cell_values_truncated():
    long_val = "x" * 500
    result = {"ok": True, "summary": "1 строк", "data": {"columns": ["blob"], "rows": [[long_val]]}}
    parsed = _parse(tool_result_to_message(result))
    assert len(parsed["rows_preview"][0]["blob"]) == MAX_CELL_CHARS


def test_none_cells_preserved_as_null():
    result = {"ok": True, "summary": "1 строк", "data": {"columns": ["a", "b"], "rows": [[None, 3]]}}
    parsed = _parse(tool_result_to_message(result))
    assert parsed["rows_preview"] == [{"a": None, "b": 3}]


def test_error_result_unchanged():
    result = {"ok": False, "summary": "SQL отклонён", "data": {"sql": "SELECT 1"}}
    parsed = _parse(tool_result_to_message(result))
    assert parsed["ok"] is False
    assert "rows_preview" not in parsed
    assert parsed["sql"] == "SELECT 1"


def test_result_without_rows_key_untouched():
    result = {"ok": True, "summary": "+12.5%", "data": {"change_pct": 12.5}}
    parsed = _parse(tool_result_to_message(result))
    assert parsed["change_pct"] == 12.5
    assert "rows_preview" not in parsed
