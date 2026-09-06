"""Numerical accuracy contract tests for the analytics layer.

A deterministic golden set: given exact inputs, the analytics layer must return
exact numbers. This is the "Numerical Accuracy: 100%" guarantee — the LLM never
produces these figures, so they are fully reproducible.
"""
from __future__ import annotations

import pytest

from app.core.analytics import compute_insights


def _month_rows(series: list[int]) -> list[list]:
    return [[f"2026-0{i + 1}", v] for i, v in enumerate(series)]


# --- Percentage change (period comparison) ---


def test_pct_change_exact():
    r = compute_insights(["month", "v"], _month_rows([100, 120]))
    assert r["trend"] == {"direction": "up", "pct": 20.0}


def test_pct_decline_exact():
    r = compute_insights(["month", "v"], _month_rows([120, 100]))
    assert r["trend"]["direction"] == "down"
    assert r["trend"]["pct"] == pytest.approx(-16.7, abs=0.1)


def test_pct_growth_curve():
    r = compute_insights(["month", "v"], _month_rows([100, 150, 200, 400]))
    assert r["trend"]["direction"] == "up"
    # 100 → 400 = +300%
    assert r["trend"]["pct"] == 300.0


# --- Summary statistics ---


def test_sum_matches_manual_total():
    rows = [["m1", 1250.5], ["m2", 3100.25], ["m3", 649.25]]
    r = compute_insights(["month", "revenue"], rows)
    assert r["summary"]["revenue"]["sum"] == 5000.0


def test_avg_median_exact():
    rows = [["a", 10], ["b", 20], ["c", 30], ["d", 40]]
    r = compute_insights(["k", "v"], rows)
    s = r["summary"]["v"]
    assert s["avg"] == 25.0
    assert s["median"] == 25.0
    assert s["min"] == 10.0
    assert s["max"] == 40.0


# --- Top-N and shares ---


def test_top_share_sums_consistently():
    rows = [["Москва", 600], ["СПб", 300], ["Казань", 100]]
    r = compute_insights(["city", "revenue"], rows)
    assert r["top"][0]["label"] == "Москва"
    assert r["top"][0]["share"] == 0.6
    assert r["top"][1]["share"] == 0.3
    assert r["top"][2]["share"] == 0.1


# --- Anomaly detection (z-score) ---


def test_anomaly_detection_exact():
    rows = [["A", 1000], ["B", 1020], ["C", 980], ["D", 1010], ["E", 100]]
    r = compute_insights(["city", "rides"], rows)
    outliers = {o["label"]: o["z"] for o in r["outliers"]}
    assert "E" in outliers
    assert outliers["E"] < 0  # below average


# --- Real-world shape: revenue by region (RideGo) ---


def test_ridego_like_slice_exact():
    rows = [
        ["Центр", 252676.19, 1983],
        ["Северо-Запад", 182620.34, 1436],
        ["Урал", 156474.44, 1217],
    ]
    r = compute_insights(["region", "revenue_rub", "rides"], rows)
    assert r["summary"]["revenue_rub"]["sum"] == pytest.approx(591770.97, abs=0.01)
    assert r["top"][0]["label"] == "Центр"
    assert r["top"][0]["share"] == pytest.approx(0.427, abs=0.001)
