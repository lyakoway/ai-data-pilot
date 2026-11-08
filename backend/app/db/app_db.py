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

from app.tenancy import DEFAULT_WORKSPACE_ID, require_workspace
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
    agent        TEXT NOT NULL,          -- 'atlas' | 'doc'
    message      TEXT,                   -- the user's question
    answer       TEXT,                   -- the agent's answer (truncated)
    datasource_id TEXT,
    model        TEXT,
    lang         TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id            TEXT PRIMARY KEY,
    filename      TEXT NOT NULL,
    content_type  TEXT NOT NULL DEFAULT '',
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    page_count    INTEGER NOT NULL DEFAULT 0,
    chunk_count   INTEGER NOT NULL DEFAULT 0,
    status        TEXT NOT NULL DEFAULT 'processing',
    error         TEXT,
    datasource_id TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  TEXT NOT NULL,
    page         INTEGER NOT NULL DEFAULT 1,
    label        TEXT,
    chunk_index  INTEGER NOT NULL DEFAULT 0,
    text         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
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
    "workspace_id": "global",
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
        # Migration: add the connection column to datasources (for postgres sources).
        _ensure_column(conn, "datasources", "connection", "TEXT")
        _ensure_column(conn, "documents", "datasource_id", "TEXT")
        # P0.2 tenancy (application-level isolation — NOT PostgreSQL RLS):
        # workspaces table + workspace_id on tenant tables. Chunks stay
        # unscoped: ownership is derived via JOIN documents (single source).
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS workspaces ("
            "id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)"
        ))
        _ensure_column(conn, "datasources", "workspace_id", "TEXT")
        _ensure_column(conn, "documents", "workspace_id", "TEXT")
        _ensure_column(conn, "scenarios", "workspace_id", "TEXT")
        _ensure_column(conn, "feedback", "workspace_id", "TEXT")
        # EXPLICIT migration rule (P0.2): every pre-existing row belongs to
        # the seeded default dev workspace — single-tenant legacy data. The
        # backfill is idempotent, logged, and verified by tests.
        _ws = "ws_default"
        conn.execute(text(
            "INSERT OR IGNORE INTO workspaces (id, name, created_at) "
            "VALUES (:id, :name, :now)"
        ), {"id": _ws, "name": "Локальная разработка (dev)",
            "now": datetime.utcnow().isoformat(timespec="seconds")})
        for _table in ("datasources", "documents", "scenarios", "feedback"):
            n = conn.execute(text(
                f"UPDATE {_table} SET workspace_id = :ws WHERE workspace_id IS NULL OR workspace_id = ''"
            ), {"ws": _ws}).rowcount
            if n:
                import logging
                logging.getLogger("app.db").info(
                    "tenancy migration: %s row(s) in %s -> workspace %s", n, _table, _ws
                )
        # Seed the built-in ridego source if absent.
        exists = conn.execute(
            text("SELECT 1 FROM datasources WHERE id = 'ridego'")
        ).fetchone()
        if not exists:
            conn.execute(
                text(
                    "INSERT INTO datasources (id, name, kind, description, table_name, columns, row_count, created_at, workspace_id) "
                    "VALUES (:id, :name, :kind, :description, :table_name, :columns, :row_count, :created_at, :workspace_id)"
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
                        "INSERT INTO scenarios (id, name, agent, description, prompt, chart_type, datasource_id, parameters, created_at, workspace_id) "
                        "VALUES (:id, :name, :agent, :description, :prompt, :chart_type, :datasource_id, :parameters, :created_at, :workspace_id)"
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
                        "workspace_id": "ws_default",
                        "created_at": now,
                    },
                )


def workspace_exists(workspace_id: str) -> bool:
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT 1 FROM workspaces WHERE id = :id"), {"id": workspace_id}
        ).fetchone()
    return row is not None


def list_workspaces() -> list[dict[str, Any]]:
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, created_at FROM workspaces ORDER BY id")
        ).fetchall()
    return [{"id": r[0], "name": r[1], "created_at": r[2]} for r in rows]


def reset_app_engine() -> None:
    """Drop the cached engine so the next call re-reads settings (used by tests)."""
    global _engine
    _engine = None


# --------------------------------------------------------------------------- #
# Scenarios CRUD
# --------------------------------------------------------------------------- #


def list_scenarios(workspace_id: str | None = None) -> list[dict[str, Any]]:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, agent, description, prompt, chart_type, datasource_id, parameters FROM scenarios WHERE workspace_id = :ws ORDER BY created_at"),
            {"ws": workspace_id},
        ).fetchall()
    return [_row_to_scenario(r) for r in rows]


def get_scenario(scenario_id: str, workspace_id: str | None = None) -> dict[str, Any] | None:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, agent, description, prompt, chart_type, datasource_id, parameters FROM scenarios WHERE id = :id AND workspace_id = :ws"
            ),
            {"id": scenario_id, "ws": workspace_id},
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


def create_scenario(sc: dict[str, Any], workspace_id: str | None = None) -> dict[str, Any]:
    require_workspace(workspace_id)
    eng = get_app_engine()
    now = datetime.utcnow().isoformat(timespec="seconds")
    params_json = json.dumps(sc["parameters"], ensure_ascii=False) if sc.get("parameters") else None
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO scenarios (id, name, agent, description, prompt, chart_type, datasource_id, parameters, created_at, workspace_id) "
                "VALUES (:id, :name, :agent, :description, :prompt, :chart_type, :datasource_id, :parameters, :created_at, :workspace_id)"
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
                "workspace_id": workspace_id,
            },
        )
    return sc


def delete_scenario(scenario_id: str, workspace_id: str | None = None) -> bool:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.begin() as conn:
        result = conn.execute(
            text("DELETE FROM scenarios WHERE id = :id AND workspace_id = :ws"),
            {"id": scenario_id, "ws": workspace_id},
        )
    return result.rowcount > 0


# --------------------------------------------------------------------------- #
# Datasources metadata CRUD
# --------------------------------------------------------------------------- #


def list_datasources(workspace_id: str | None = None, *, include_global: bool = True) -> list[dict[str, Any]]:
    """Tenant-scoped listing (fail-closed). Built-in demo sources (workspace
    'global') are included: they are product data, not tenant data."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, name, kind, description, table_name, columns, row_count, created_at, connection, workspace_id FROM datasources ORDER BY created_at")
        ).fetchall()
    out = []
    for r in rows:
        ws = r[9]
        if ws == workspace_id or (include_global and ws == "global"):
            out.append(_row_to_datasource(r))
    return out


def get_datasource(source_id: str, workspace_id: str | None = None) -> dict[str, Any] | None:
    """Ownership-aware read: a user source is visible ONLY to its workspace;
    global built-ins are visible to everyone."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, name, kind, description, table_name, columns, row_count, created_at, connection, workspace_id FROM datasources WHERE id = :id"
            ),
            {"id": source_id},
        ).fetchone()
    if row is None:
        return None
    # Ownership: tenant rows visible to their workspace only; built-in demo
    # sources (workspace 'global') visible to all — documented P0.2 decision.
    if row[9] not in (workspace_id, "global"):
        return None
    return _row_to_datasource(row)


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
        "connection": json.loads(r[8]) if len(r) > 8 and r[8] else None,
    }


def save_datasource(meta: dict[str, Any], workspace_id: str | None = None) -> dict[str, Any]:
    """Insert or replace a datasource metadata row (workspace-stamped)."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    columns_json = json.dumps(meta.get("columns"), ensure_ascii=False) if meta.get("columns") else None
    connection_json = json.dumps(meta.get("connection"), ensure_ascii=False) if meta.get("connection") else None
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO datasources (id, name, kind, description, table_name, columns, row_count, created_at, connection, workspace_id) "
                "VALUES (:id, :name, :kind, :description, :table_name, :columns, :row_count, :created_at, :connection, :workspace_id)"
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
                "connection": connection_json,
                "workspace_id": meta.get("workspace_id", workspace_id),
            },
        )
    return meta


def delete_datasource_row(source_id: str, workspace_id: str | None = None) -> bool:
    """Delete only OWN sources; global built-ins are undeletable here."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.begin() as conn:
        result = conn.execute(
            text("DELETE FROM datasources WHERE id = :id AND workspace_id = :ws"),
            {"id": source_id, "ws": workspace_id},
        )
    return result.rowcount > 0


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #


def save_feedback(fb: dict[str, Any], workspace_id: str | None = None) -> dict[str, Any]:
    """Persist a feedback vote (workspace-stamped)."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    now = datetime.utcnow().isoformat(timespec="seconds")
    with eng.begin() as conn:
        result = conn.execute(
            text(
                "INSERT INTO feedback (vote, agent, message, answer, datasource_id, model, lang, created_at, workspace_id) "
                "VALUES (:vote, :agent, :message, :answer, :datasource_id, :model, :lang, :created_at, :workspace_id)"
            ),
            {
                "vote": fb["vote"],
                "agent": fb.get("agent", "atlas"),
                "message": (fb.get("message") or "")[:1000],
                "answer": (fb.get("answer") or "")[:2000],
                "datasource_id": fb.get("datasource_id"),
                "model": fb.get("model"),
                "lang": fb.get("lang", "ru"),
                "created_at": now,
                "workspace_id": workspace_id,
            },
        )
        fb["id"] = result.lastrowid
    fb["created_at"] = now
    return fb


def feedback_stats(workspace_id: str | None = None) -> dict[str, Any]:
    """Aggregate feedback counts (tenant-scoped, fail-closed)."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        up = conn.execute(text("SELECT COUNT(*) FROM feedback WHERE vote = 'up' AND workspace_id = :ws"), {"ws": workspace_id}).scalar() or 0
        down = conn.execute(text("SELECT COUNT(*) FROM feedback WHERE vote = 'down' AND workspace_id = :ws"), {"ws": workspace_id}).scalar() or 0
        # Per-agent breakdown
        rows = conn.execute(
            text(
                "SELECT agent, vote, COUNT(*) as n FROM feedback WHERE workspace_id = :ws "
                "GROUP BY agent, vote"
            ), {"ws": workspace_id},
        ).fetchall()
        per_agent: dict[str, dict[str, int]] = {}
        for agent, vote, n in rows:
            per_agent.setdefault(agent, {"up": 0, "down": 0})
            per_agent[agent][vote] = int(n)

    return {
        "up": int(up),
        "down": int(down),
        "total": int(up + down),
        "satisfaction": round(int(up) / max(int(up + down), 1) * 100, 1),
        "per_agent": per_agent,
    }


def feedback_list(limit: int = 20, agent: str | None = None, workspace_id: str | None = None) -> list[dict[str, Any]]:
    """Recent feedback entries (tenant-scoped, fail-closed)."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    query = (
        "SELECT id, vote, agent, message, answer, model, lang, created_at "
        "FROM feedback WHERE workspace_id = :ws"
    )
    params: dict[str, Any] = {"limit": limit, "ws": workspace_id}
    if agent:
        query += " AND agent = :agent"
        params["agent"] = agent
    query += " ORDER BY created_at DESC LIMIT :limit"

    with eng.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
    return [
        {
            "id": r[0], "vote": r[1], "agent": r[2],
            "message": (r[3] or "")[:120],
            "answer": (r[4] or "")[:160],
            "model": r[5], "lang": r[6], "created_at": r[7],
        }
        for r in rows
    ]


# --------------------------------------------------------------------------- #
# Documents CRUD
# --------------------------------------------------------------------------- #


def list_documents(workspace_id: str | None = None) -> list[dict[str, Any]]:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text("SELECT id, filename, content_type, size_bytes, page_count, chunk_count, status, error, datasource_id, created_at FROM documents WHERE workspace_id = :ws ORDER BY created_at DESC"),
            {"ws": workspace_id},
        ).fetchall()
    return [
        {
            "id": r[0], "filename": r[1], "content_type": r[2],
            "size_bytes": r[3], "page_count": r[4], "chunk_count": r[5],
            "status": r[6], "error": r[7], "datasource_id": r[8], "created_at": r[9],
        }
        for r in rows
    ]


def get_document(doc_id: str, workspace_id: str | None = None) -> dict[str, Any] | None:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT id, filename, content_type, size_bytes, page_count, chunk_count, status, error, datasource_id, created_at FROM documents WHERE id = :id AND workspace_id = :ws"),
            {"id": doc_id, "ws": workspace_id},
        ).fetchone()
    if not row:
        return None
    return {
        "id": row[0], "filename": row[1], "content_type": row[2],
        "size_bytes": row[3], "page_count": row[4], "chunk_count": row[5],
        "status": row[6], "error": row[7], "datasource_id": row[8], "created_at": row[9],
    }


def save_document(doc: dict[str, Any], workspace_id: str | None = None) -> dict[str, Any]:
    require_workspace(workspace_id)
    eng = get_app_engine()
    row = {**doc, "workspace_id": workspace_id}
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT OR REPLACE INTO documents (id, filename, content_type, size_bytes, page_count, chunk_count, status, error, datasource_id, created_at, workspace_id) "
                "VALUES (:id, :filename, :content_type, :size_bytes, :page_count, :chunk_count, :status, :error, :datasource_id, :created_at, :workspace_id)"
            ),
            row,
        )
    return doc


def delete_document_row(doc_id: str, workspace_id: str | None = None) -> bool:
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.begin() as conn:
        conn.execute(text("DELETE FROM chunks WHERE document_id = :id"), {"id": doc_id})
        result = conn.execute(
            text("DELETE FROM documents WHERE id = :id AND workspace_id = :ws"),
            {"id": doc_id, "ws": workspace_id},
        )
    return result.rowcount > 0


def insert_chunks(doc_id: str, chunks: list[dict[str, Any]]) -> int:
    eng = get_app_engine()
    with eng.begin() as conn:
        for ch in chunks:
            conn.execute(
                text(
                    "INSERT INTO chunks (document_id, page, label, chunk_index, text) "
                    "VALUES (:document_id, :page, :label, :chunk_index, :text)"
                ),
                {"document_id": doc_id, "page": ch.get("page", 1), "label": ch.get("label"),
                 "chunk_index": ch.get("index", 0), "text": ch["text"]},
            )
    return len(chunks)


def get_chunks_for_search(workspace_id: str | None = None) -> list[dict[str, Any]]:
    """Chunks of READY documents visible to ``workspace_id`` (fail-closed:
    no workspace → TenantContextError, never a global fallback). Uploaded
    documents are tenant data; the built-in product docs (data/docs) are a
    separate corpus handled by docs_rag.load_chunks(), not by this table."""
    require_workspace(workspace_id)
    eng = get_app_engine()
    with eng.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.document_id, c.page, c.label, c.chunk_index, c.text, d.filename "
                "FROM chunks c JOIN documents d ON d.id = c.document_id "
                "WHERE d.status = 'ready' AND d.workspace_id = :ws "
                "ORDER BY c.document_id, c.chunk_index"
            ),
            {"ws": workspace_id},
        ).fetchall()
    return [
        {"document_id": r[0], "page": r[1], "label": r[2], "chunk_index": r[3], "text": r[4], "filename": r[5]}
        for r in rows
    ]
