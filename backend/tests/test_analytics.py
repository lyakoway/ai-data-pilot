"""Tests for the deterministic analytics layer (app.core.analytics)."""
from __future__ import annotations

from app.core.analytics import compute_insights


# --- Trend / timeseries detection ---


def test_trend_up_detected_on_growing_series():
    cols = ["month", "revenue"]
    rows = [["2026-04", 100], ["2026-05", 120], ["2026-06", 140], ["2026-07", 160]]
    r = compute_insights(cols, rows, lang="ru")
    assert r["is_timeseries"] is True
    assert r["trend"]["direction"] == "up"
    assert r["trend"]["pct"] == 60.0  # 100 → 160 = +60%


def test_trend_down_detected_on_declining_series():
    cols = ["date", "rides"]
    rows = [["2026-07-01", 1000], ["2026-07-02", 800], ["2026-07-03", 500]]
    r = compute_insights(cols, rows, lang="en")
    assert r["trend"]["direction"] == "down"
    assert r["trend"]["pct"] == -50.0


def test_flat_series_when_change_under_threshold():
    cols = ["date", "v"]
    rows = [["2026-07-01", 100], ["2026-07-02", 101]]  # +1% → flat
    r = compute_insights(cols, rows)
    assert r["trend"]["direction"] == "flat"


def test_non_date_axis_is_not_timeseries():
    cols = ["region", "revenue"]
    rows = [["Центр", 100], ["Урал", 50]]
    r = compute_insights(cols, rows)
    assert r["is_timeseries"] is False
    assert r["trend"] is None


# --- Top-N ---


def test_top_n_sorted_by_value_desc():
    cols = ["region", "revenue"]
    rows = [["Урал", 50], ["Центр", 300], ["Сибирь", 150], ["Юг", 20]]
    r = compute_insights(cols, rows)
    top = r["top"]
    assert len(top) == 3
    assert top[0]["label"] == "Центр"
    assert top[0]["value"] == 300.0
    # total = 520 → 300/520 ≈ 0.577
    assert round(top[0]["share"], 2) == round(300 / 520, 2)


# --- Outliers ---


def test_outlier_flagged_when_zscore_above_threshold():
    # One value far below the rest
    cols = ["city", "v"]
    rows = [["A", 1000], ["B", 1050], ["C", 980], ["D", 1020], ["E", 5]]
    r = compute_insights(cols, rows)
    labels = {o["label"] for o in r["outliers"]}
    assert "E" in labels


def test_no_outliers_on_uniform_data():
    cols = ["city", "v"]
    rows = [["A", 100], ["B", 100], ["C", 100], ["D", 100]]
    r = compute_insights(cols, rows)
    assert r["outliers"] == []


# --- Summary stats ---


def test_summary_stats_are_correct():
    cols = ["x", "metric"]
    rows = [["a", 10], ["b", 20], ["c", 30]]
    r = compute_insights(cols, rows)
    s = r["summary"]["metric"]
    assert s["sum"] == 60.0
    assert s["avg"] == 20.0
    assert s["min"] == 10.0
    assert s["max"] == 30.0
    assert s["median"] == 20.0
    assert s["count"] == 3


# --- Highlights (language-aware) ---


def test_highlights_ru_contain_total_and_trend():
    cols = ["month", "revenue"]
    rows = [["2026-04", 100], ["2026-05", 130]]
    r = compute_insights(cols, rows, lang="ru")
    joined = " ".join(r["highlights"])
    assert "Итого" in joined
    assert "Тренд" in joined
    assert "рост" in joined


def test_highlights_en_use_english_terms():
    cols = ["month", "revenue"]
    rows = [["2026-04", 100], ["2026-05", 130]]
    r = compute_insights(cols, rows, lang="en")
    joined = " ".join(r["highlights"])
    assert "Total" in joined
    assert "Trend" in joined


# --- Edge cases ---


def test_empty_rows_returns_empty_insights():
    r = compute_insights(["a", "b"], [])
    assert r["highlights"] == []
    assert r["top"] == []


def test_empty_columns_returns_empty_insights():
    r = compute_insights([], [["x"]])
    assert r["highlights"] == []


def test_no_numeric_column_skips_trend_and_top():
    cols = ["label"]
    rows = [["a"], ["b"]]
    r = compute_insights(cols, rows)
    assert r["trend"] is None
    assert r["top"] == []


def test_single_row_has_no_trend():
    cols = ["month", "v"]
    rows = [["2026-04", 100]]
    r = compute_insights(cols, rows)
    assert r["trend"] is None
