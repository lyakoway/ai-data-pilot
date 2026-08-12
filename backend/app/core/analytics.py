"""Deterministic analytics layer for Oleg.

Principle: the LLM does NOT calculate numbers. This module computes all metrics
(sums, trends, top-N, outliers) in Python and returns ready-made `highlights`
that the answer-prompt renders into natural language. This keeps the figures in
the user-facing answer exact and reproducible.

Public entry point: :func:`compute_insights`.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from statistics import fmean, median
from typing import Any

# A column is treated as a date axis if its name hints so OR values parse as dates.
_DATE_NAME_RE = re.compile(r"(date|day|week|month|year|дата|день|нед|мес|год|период|time)", re.I)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
# Values whose |z-score| exceeds this are flagged as outliers. Set slightly
# below the textbook 2σ so that masked outliers (which inflate the std they
# were computed from) are still caught — a common case in small analytical slices.
_OUTLIER_Z_THRESHOLD = 1.9
_TREND_FLAT_PCT = 2.5  # |trend %| below this is reported as "flat"


def _is_date_value(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    if isinstance(v, str) and _ISO_DATE_RE.match(v):
        return True
    return False


def _looks_like_date_axis(name: str, values: list[Any]) -> bool:
    if not values:
        return False
    if _DATE_NAME_RE.search(name):
        return True
    hits = sum(1 for v in values if _is_date_value(v))
    return hits / len(values) >= 0.6


def _numeric_series(rows: list[list[Any]], columns: list[str], col_idx: int) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r[col_idx] if col_idx < len(r) else None
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.append(float(v))
    return out


def _zscore_outliers(labels: list[str], values: list[float]) -> list[dict[str, Any]]:
    """Flag points whose z-score magnitude exceeds the threshold."""
    if len(values) < 4:
        return []
    mu = fmean(values)
    # population std
    var = fmean([(x - mu) ** 2 for x in values])
    std = var ** 0.5
    if std == 0:
        return []
    out: list[dict[str, Any]] = []
    for label, x in zip(labels, values):
        z = (x - mu) / std
        if abs(z) >= _OUTLIER_Z_THRESHOLD:
            out.append({"label": label, "value": x, "z": round(z, 2)})
    # most extreme first
    out.sort(key=lambda o: abs(o["z"]), reverse=True)
    return out[:5]


def _trend(values: list[float]) -> tuple[str, float]:
    """Return (direction, pct_change_first_to_last). Uses a simple linear fit slope
    to decide direction, but reports magnitude as first→last change so it stays
    intuitive. Requires a monotonic date axis (already sorted ascending by caller)."""
    if len(values) < 2:
        return "flat", 0.0
    n = len(values)
    xs = list(range(n))
    mx = fmean(xs)
    my = fmean(values)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, values))
    den = sum((x - mx) ** 2 for x in xs)
    slope = num / den if den else 0.0
    # First→last percent change is the most legible magnitude for the user.
    first, last = values[0], values[-1]
    if first == 0:
        pct = 100.0 if last > 0 else 0.0
    else:
        pct = round(((last - first) / abs(first)) * 100, 1)
    if slope > 0 and pct > _TREND_FLAT_PCT:
        return "up", pct
    if slope < 0 and pct < -_TREND_FLAT_PCT:
        return "down", pct
    return "flat", pct


def _fmt_num(x: float) -> str:
    """Human-friendly number formatting (no locale dependency)."""
    if x == int(x):
        return f"{int(x):,}".replace(",", " ")
    return f"{round(x, 2):,}".replace(",", " ")


def _pct(numerator: float, denominator: float) -> str:
    if denominator == 0:
        return "0%"
    return f"{round(100 * numerator / denominator)}%"


def compute_insights(
    columns: list[str],
    rows: list[list[Any]],
    *,
    question: str = "",
    lang: str = "ru",
) -> dict[str, Any]:
    """Compute deterministic insights from a query result.

    Returns a dict with:
      - is_timeseries: bool
      - trend: {direction, pct} | None
      - top: [{label, value, share}] (top-3 by primary numeric column)
      - outliers: [{label, value, z}]
      - summary: {col: {sum, avg, min, max, median}}
      - highlights: [str, ...] — ready-made facts for the answer prompt
    """
    insights: dict[str, Any] = {
        "is_timeseries": False,
        "trend": None,
        "top": [],
        "outliers": [],
        "summary": {},
        "highlights": [],
    }
    if not columns or not rows:
        return insights

    # Identify label column (first column) and first numeric measure column.
    label_idx = 0
    label_col = columns[label_idx]
    labels = [str(r[label_idx]) if label_idx < len(r) else "" for r in rows]

    numeric_cols: list[tuple[int, str]] = []
    for i, col in enumerate(columns):
        if i == label_idx:
            continue
        series = _numeric_series(rows, columns, i)
        if series:
            numeric_cols.append((i, col))

    is_ts = _looks_like_date_axis(label_col, [r[label_idx] for r in rows if label_idx < len(r)])
    insights["is_timeseries"] = is_ts

    # --- Summaries for every numeric column ---
    for idx, col in numeric_cols:
        series = _numeric_series(rows, columns, idx)
        if not series:
            continue
        insights["summary"][col] = {
            "sum": round(sum(series), 2),
            "avg": round(fmean(series), 2),
            "min": round(min(series), 2),
            "max": round(max(series), 2),
            "median": round(median(series), 2),
            "count": len(series),
        }

    if not numeric_cols:
        return insights

    primary_idx, primary_col = numeric_cols[0]
    primary_series = _numeric_series(rows, columns, primary_idx)
    total = sum(primary_series)

    # --- Trend (only meaningful on a sorted date axis) ---
    if is_ts and len(primary_series) >= 2:
        direction, pct = _trend(primary_series)
        insights["trend"] = {"direction": direction, "pct": pct}

    # --- Top-N by primary measure ---
    ranked = sorted(zip(labels, primary_series), key=lambda kv: kv[1], reverse=True)
    top: list[dict[str, Any]] = []
    for label, value in ranked[:3]:
        share = round(value / total, 3) if total else 0.0
        top.append({"label": label, "value": round(value, 2), "share": share})
    insights["top"] = top

    # --- Outliers (z-score on primary measure) ---
    insights["outliers"] = _zscore_outliers(labels, primary_series)

    # --- Compose human-readable highlights (language-aware) ---
    insights["highlights"] = _compose_highlights(
        insights=insights,
        primary_col=primary_col,
        total=total,
        lang=lang,
    )
    return insights


def _compose_highlights(
    insights: dict[str, Any],
    primary_col: str,
    total: float,
    lang: str,
) -> list[str]:
    en = lang == "en"
    lines: list[str] = []

    # Total / overall magnitude
    if total:
        if en:
            lines.append(f"Total {primary_col}: {_fmt_num(total)}")
        else:
            lines.append(f"Итого {primary_col}: {_fmt_num(total)}")

    # Trend
    trend = insights.get("trend")
    if trend and trend["direction"] != "flat":
        arrow = {"up": "↑" if en else "рост", "down": "↓" if en else "падение"}[trend["direction"]]
        if en:
            lines.append(f"Trend: {trend['direction']} ({arrow} {abs(trend['pct'])}% first→last)")
        else:
            lines.append(f"Тренд: {arrow} на {abs(trend['pct'])}% (первое→последнее значение)")

    # Top items with share
    for i, item in enumerate(insights.get("top", []), start=1):
        share_pct = round(item["share"] * 100)
        if en:
            lines.append(f"#{i} {item['label']}: {_fmt_num(item['value'])} ({share_pct}% of total)")
        else:
            lines.append(f"№{i} {item['label']}: {_fmt_num(item['value'])} ({share_pct}% от итога)")

    # Outliers
    for o in insights.get("outliers", []):
        direction = "above" if o["z"] > 0 else "below"
        ru_dir = "выше" if o["z"] > 0 else "ниже"
        if en:
            lines.append(
                f"Outlier: {o['label']} = {_fmt_num(o['value'])} ({direction} average, z={o['z']})"
            )
        else:
            lines.append(
                f"Аномалия: {o['label']} = {_fmt_num(o['value'])} ({ru_dir} среднего, z={o['z']})"
            )

    return lines
