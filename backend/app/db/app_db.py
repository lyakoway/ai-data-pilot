"""Application database (app.db) — scenarios, data source metadata, feedback.

This is the operational store for user-generated content, kept separate from the
analytics DB (RideGo / uploaded CSVs). Backed by SQLite via SQLAlchemy.

Three tables:
  - scenarios   : saved one-click scenarios (previously a JSON file)
  - datasources : metadata for uploaded CSV sources (previously a JSON file)
  - feedback    : 👍/👎 ratings on answers (previously UI-only)

The ``ridego`` built-in data source is registered on first init.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db.schema_catalog import SEED_SCENARIOS

DDL = """
CREATE TABLE IF NOT EXISTS scenarios (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    agent         TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    prompt        TEXT NOT NULL,
    chart_type    TEXT,
    datasource_id TEXT,
    parameters    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS datasources (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    table_name  TEXT,
    columns     TEXT,
    row_count   INTEGER,
    created_at  TEXT
);

CREATE TABLE IF NOT EXISTS feedback (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    vote         TEXT NOT NULL,          -- 'up' | 'down'
    agent        TEXT NOT NULL,          -- 'oleg' | 'ksyusha'
    message      TEXT,                   -- the user's question
    answer       TEXT,                   -- the agent's answer (truncated)
    datasource_id TEXT,
    model        TEXT,
    lang         TEXT,
    created_at   TEXT NOT NULL
);
"""

RIDEGO_SOURCE_ROW = {
    "id": "ridego",
    "name": "RideGo (демо)",
    "kind": "ridego",
    "description": "Встроенная демо-БД микромобильности: города, поездки, подписки.",
    "table_name": None,
    "columns": None,
    "row_count": None,
    "created_at": None,
}


_engine: Engine | None = None


def _ensure_column(conn, table: str, column: str, coltype: str) -> None:
    """Add ``column`` to ``table`` if it doesn't already exist (idempotent migration)."""
    cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
    if column not in cols:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))


def get_app_engine() -> Engine:
    """Return the shared app-DB engine, creating tables + seeding defaults on first use."""
    global _engine
    if _engine is None:
        url = get_settings().app_db_url
        _engine = create_engine(url)
        _init_db(_engine)
    return _engine


def _init_db(engine: Engine) -> None:
    with engine.begin() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(text(s))
        # Migration: add the parameters column to pre-existing scenarios tables
        # (CREATE TABLE IF NOT EXISTS won't alter an existing table).
        _ensure_column(conn, "scenarios", "parameters", "TEXT")
        # Seed the built-in ridego source if absent.
        exists = conn.execute(
            text("SELECT 1 FROM datasources WHERE id = 'ridego'")
        ).fetchone()
        if not exists:
            conn.execute(
                text(
                    "INSERT INTO datasources (id, name, kind, description, table_name, columns, row_count, created_at) "
                    "VALUES (:id, :name, :kind, :description, :table_name, :columns, :row_count, :created_at)"
                ),
                RIDEGO_SOURCE_ROW,
            )
        # Seed default scenarios if the table is empty.
        n = conn.execute(text("SELECT COUNT(*) FROM scenarios")).scalar()
        if not n:
            now = datetime.utcnow().isoformat(timespec="seconds")
            for sc in SEED_SCENARIOS:
                conn.execute(
                    text(
                        "INSERT INTO scenarios (id, name, agent, description, prompt, chart_type, datasource_id, parameters, created_at) "
                        "VALUES (:id, :name, :agent, :description, :prompt, :chart_type, :datasource_id, :parameters, :created_at)"
                    ),
                    {
                        "id": sc["id"],
                        "name": sc["name"],
                        "agent": sc["agent"],
                        "description": sc.get("description", ""),
                        "prompt": sc["prompt"],
                        "chart_type": sc.get("chart_type"),
                        "datasource_id": sc.get("datasource_id"),
                        "parameters": json.dumps(sc["parameters"], ensure_ascii=False) if sc.get("parameters") else None,
                        "created_at": now,
                    },
                )


def reset_app_engine() -> None:
    """Drop the cached engine so the next call re-reads settings (used by tests)."""
    global _engine
    _engine = None


# --------------------------------------------------------------------------- #
# Scenarios CRUD
# --------------------------------------------------------------------------- #


def list_scenarios() -> list[dict[str, Any]]:
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, agent, description, prompt, chart_type, datasource_id, parameters FROM scenarios ORDER BY created_at")
        ).fetchall()
    return [_row_to_scenario(r) for r in rows]


def get_scenario(scenario_id: str) -> dict[str, Any] | None:
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, agent, description, prompt, chart_type, datasource_id, parameters FROM scenarios WHERE id = :id"
            ),
            {"id": scenario_id},
        ).fetchone()
    return _row_to_scenario(row) if row else None


def _row_to_scenario(r: tuple) -> dict[str, Any]:
    return {
        "id": r[0],
        "name": r[1],
        "agent": r[2],
        "description": r[3],
        "prompt": r[4],
        "chart_type": r[5],
        "datasource_id": r[6],
        "parameters": json.loads(r[7]) if r[7] else None,
    }


def create_scenario(sc: dict[str, Any]) -> dict[str, Any]:
    eng = get_app_engine()
    now = datetime.utcnow().isoformat(timespec="seconds")
    params_json = json.dumps(sc["parameters"], ensure_ascii=False) if sc.get("parameters") else None
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scenarios (id, name, agent, description, prompt, chart_type, datasource_id, parameters, created_at) "
                "VALUES (:id, :name, :agent, :description, :prompt, :chart_type, :datasource_id, :parameters, :created_at)"
            ),
            {
                "id": sc["id"],
                "name": sc["name"],
                "agent": sc["agent"],
                "description": sc.get("description", ""),
                "prompt": sc["prompt"],
                "chart_type": sc.get("chart_type"),
                "datasource_id": sc.get("datasource_id"),
                "parameters": params_json,
                "created_at": now,
            },
        )
    return sc


def delete_scenario(scenario_id: str) -> bool:
    eng = get_app_engine()
    with eng.begin() as conn:
        result = conn.execute(text("DELETE FROM scenarios WHERE id = :id"), {"id": scenario_id})
    return result.rowcount > 0


# --------------------------------------------------------------------------- #
# Datasources metadata CRUD
# --------------------------------------------------------------------------- #


def list_datasources() -> list[dict[str, Any]]:
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, kind, description, table_name, columns, row_count, created_at FROM datasources ORDER BY created_at")
        ).fetchall()
    out = []
    for r in rows:
        out.append(_row_to_datasource(r))
    return out


def get_datasource(source_id: str) -> dict[str, Any] | None:
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, kind, description, table_name, columns, row_count, created_at FROM datasources WHERE id = :id"
            ),
            {"id": source_id},
        ).fetchone()
    return _row_to_datasource(row) if row else None


def _row_to_datasource(r: tuple) -> dict[str, Any]:
    return {
        "id": r[0],
        "name": r[1],
        "kind": r[2],
        "description": r[3],
        "table_name": r[4],
        "columns": json.loads(r[5]) if r[5] else None,
        "row_count": r[6],
        "created_at": r[7],
    }


def save_datasource(meta: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a datasource metadata row."""
    eng = get_app_engine()
    columns_json = json.dumps(meta.get("columns"), ensure_ascii=False) if meta.get("columns") else None
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO datasources (id, name, kind, description, table_name, columns, row_count, created_at) "
                "VALUES (:id, :name, :kind, :description, :table_name, :columns, :row_count, :created_at)"
            ),
            {
                "id": meta["id"],
                "name": meta["name"],
                "kind": meta["kind"],
                "description": meta.get("description", ""),
                "table_name": meta.get("table_name"),
                "columns": columns_json,
                "row_count": meta.get("row_count"),
                "created_at": meta.get("created_at"),
            },
        )
    return meta


def delete_datasource_row(source_id: str) -> bool:
    eng = get_app_engine()
    with eng.begin() as conn:
        result = conn.execute(text("DELETE FROM datasources WHERE id = :id"), {"id": source_id})
    return result.rowcount > 0


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


def save_feedback(fb: dict[str, Any]) -> dict[str, Any]:
    """Persist a feedback vote. Returns the input enriched with id/created_at."""
    eng = get_app_engine()
    now = datetime.utcnow().isoformat(timespec="seconds")
    with eng.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO feedback (vote, agent, message, answer, datasource_id, model, lang, created_at) "
                "VALUES (:vote, :agent, :message, :answer, :datasource_id, :model, :lang, :created_at)"
            ),
            {
                "vote": fb["vote"],
                "agent": fb.get("agent", "oleg"),
                "message": (fb.get("message") or "")[:1000],
                "answer": (fb.get("answer") or "")[:2000],
                "datasource_id": fb.get("datasource_id"),
                "model": fb.get("model"),
                "lang": fb.get("lang", "ru"),
                "created_at": now,
            },
        )
        fb["id"] = result.lastrowid
    fb["created_at"] = now
    return fb


def feedback_stats() -> dict[str, int]:
    """Aggregate counts for the UI / monitoring."""
    eng = get_app_engine()
    with eng.connect() as conn:
        up = conn.execute(text("SELECT COUNT(*) FROM feedback WHERE vote = 'up'")).scalar() or 0
        down = conn.execute(text("SELECT COUNT(*) FROM feedback WHERE vote = 'down'")).scalar() or 0
    return {"up": int(up), "down": int(down)}
