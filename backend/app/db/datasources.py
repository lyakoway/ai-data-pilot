"""Data sources registry — decouples Oleg from the hardcoded RideGo schema.

A *data source* bundles together everything Oleg needs to answer questions:
  - a human-readable name and description
  - a schema catalog (the text injected into the LLM prompt)
  - a SQLAlchemy engine pointing at the actual database

The built-in ``ridego`` source is always available and backed by the seeded demo
DB. User-uploaded CSVs are stored as tables in a separate SQLite database
(``data/csv_sources.db``) and registered here so Oleg can query them with the
same machinery.

Persistence: source metadata lives in the app database (``app.db``) alongside
scenarios and feedback. The CSV data itself lives in ``csv_sources.db``.
"""
from __future__ import annotations

import csv
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.config import get_settings
from app.db import app_db
from app.db.schema_catalog import SCHEMA_CATALOG as RIDEGO_CATALOG
from app.db.seed import get_engine as get_ridego_engine

# The built-in source id. Always present; cannot be deleted.
RIDEGO_SOURCE_ID = "ridego"

# SQLite column type affinity chosen per detected column kind.
_SQLITE_TYPE = {
    "integer": "INTEGER",
    "real": "REAL",
    "text": "TEXT",
}


# --------------------------------------------------------------------------- #
# Public API — metadata is persisted in app.db (see app_db module)
# --------------------------------------------------------------------------- #


def list_sources() -> list[dict[str, Any]]:
    """Return metadata for all known sources (for the UI selector)."""
    out = []
    for s in app_db.list_datasources():
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "kind": s["kind"],
                "description": s.get("description", ""),
                "row_count": s.get("row_count"),
                "created_at": s.get("created_at"),
            }
        )
    return out


def get_source_meta(source_id: str) -> dict[str, Any] | None:
    return app_db.get_datasource(source_id)


def get_engine_for(source_id: str) -> Engine:
    """Return the SQLAlchemy engine backing ``source_id``."""
    meta = get_source_meta(source_id)
    if meta is None:
        raise KeyError(f"Unknown data source: {source_id!r}")
    if meta["kind"] == "ridego":
        return get_ridego_engine()
    if meta["kind"] == "csv":
        return _csv_engine()
    raise ValueError(f"Unsupported source kind: {meta['kind']!r}")


def get_schema_catalog(source_id: str) -> str:
    """Return the schema text to inject into Oleg's prompt for ``source_id``."""
    meta = get_source_meta(source_id)
    if meta is None:
        raise KeyError(f"Unknown data source: {source_id!r}")
    if meta["kind"] == "ridego":
        return RIDEGO_CATALOG
    if meta["kind"] == "csv":
        return _build_csv_catalog(meta)
    raise ValueError(f"Unsupported source kind: {meta['kind']!r}")


def delete_source(source_id: str) -> None:
    """Delete a user source. The built-in ``ridego`` source cannot be removed."""
    if source_id == RIDEGO_SOURCE_ID:
        raise ValueError("The built-in RideGo source cannot be deleted.")
    meta = get_source_meta(source_id)
    if meta is None:
        raise KeyError(f"Unknown data source: {source_id!r}")
    if meta.get("table_name"):
        # Best-effort table drop; ignore failures (table may already be gone).
        try:
            eng = _csv_engine()
            with eng.begin() as conn:
                conn.execute(text(f'DROP TABLE IF EXISTS "{meta["table_name"]}"'))
        except Exception:  # noqa: BLE001
            pass
    app_db.delete_datasource_row(source_id)


# --------------------------------------------------------------------------- #
# CSV ingestion
# --------------------------------------------------------------------------- #


def _csv_db_path() -> Path:
    return get_settings().data_dir / "csv_sources.db"


def _csv_engine() -> Engine:
    return create_engine(f"sqlite:///{_csv_db_path()}")


def ingest_csv(
    raw_name: str,
    rows_text: str,
    *,
    delimiter: str = ",",
    max_rows: int = 50_000,
) -> dict[str, Any]:
    """Parse CSV text, infer column types, create a SQLite table, and register
    a new data source. Returns the source metadata dict.

    ``rows_text`` is the full CSV file content (decoded). ``max_rows`` caps the
    number of data rows ingested to keep the demo snappy.
    """
    if not raw_name:
        raise ValueError("File name is required.")
    reader = csv.reader(rows_text.splitlines(), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration as e:
        raise ValueError("CSV file is empty (no header row).") from e

    header = [h.strip() for h in header]
    if not header or any(not h for h in header):
        raise ValueError("CSV header contains empty column names.")
    header = [_sanitize_col(h, i) for i, h in enumerate(header)]

    data_rows: list[list[str]] = []
    for r in reader:
        if len(data_rows) >= max_rows:
            break
        if len(r) < len(header):
            r = r + [""] * (len(header) - len(r))
        elif len(r) > len(header):
            r = r[: len(header)]
        data_rows.append(r)

    if not data_rows:
        raise ValueError("CSV file has a header but no data rows.")

    col_types = [_infer_col_kind([row[i] for row in data_rows]) for i in range(len(header))]
    sql_types = [_SQLITE_TYPE[t] for t in col_types]

    source_id = f"csv_{uuid.uuid4().hex[:10]}"
    table_name = f"src_{source_id}"

    eng = _csv_engine()
    with eng.begin() as conn:
        cols_sql = ", ".join(f'"{h}" {sql_types[i]}' for i, h in enumerate(header))
        conn.execute(text(f'CREATE TABLE "{table_name}" ({cols_sql})'))
        placeholders = ", ".join(f":c{i}" for i in range(len(header)))
        insert_sql = f'INSERT INTO "{table_name}" VALUES ({placeholders})'
        batch = [_make_param_dict(header, col_types, row) for row in data_rows]
        conn.execute(text(insert_sql), batch)

    meta_entry = {
        "id": source_id,
        "name": _human_name(raw_name),
        "kind": "csv",
        "description": f"Загружен CSV: {len(header)} колонок, {len(data_rows)} строк.",
        "table_name": table_name,
        "columns": [
            {"name": header[i], "type": col_types[i], "sqlite_type": sql_types[i]}
            for i in range(len(header))
        ],
        "row_count": len(data_rows),
        "created_at": datetime.utcnow().isoformat(timespec="seconds"),
    }

    app_db.save_datasource(meta_entry)
    return meta_entry


# --------------------------------------------------------------------------- #
# CSV helpers
# --------------------------------------------------------------------------- #

_INT_RE = re.compile(r"^-?\d+$")
_FLOAT_RE = re.compile(r"^-?\d+[.,]\d+$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _sanitize_col(name: str, idx: int) -> str:
    cleaned = re.sub(r"[^A-Za-zА-Яа-яЁё0-9_ ]", "_", name).strip().replace(" ", "_")
    if not cleaned:
        cleaned = f"col_{idx}"
    if cleaned[0].isdigit():
        cleaned = f"c_{cleaned}"
    return cleaned.lower()


def _human_name(raw_name: str) -> str:
    base = Path(raw_name).stem
    return base.replace("_", " ").replace("-", " ").title() or raw_name


def _infer_col_kind(values: list[str]) -> str:
    """Infer a column kind from its string sample. Empty strings are ignored;
    if ALL values are empty the column defaults to text."""
    non_empty = [v for v in values if v != ""]
    if not non_empty:
        return "text"
    if all(_ISO_DATE_RE.match(v) for v in non_empty):
        return "text"  # keep dates as text to preserve formatting
    if all(_INT_RE.match(v) for v in non_empty):
        return "integer"
    if all(_INT_RE.match(v) or _FLOAT_RE.match(v) for v in non_empty):
        return "real"
    return "text"


def _coerce_value(kind: str, raw: str) -> Any:
    if raw == "":
        return None
    if kind == "integer":
        try:
            return int(raw)
        except ValueError:
            return raw
    if kind == "real":
        try:
            return float(raw.replace(",", "."))
        except ValueError:
            return raw
    return raw


def _make_param_dict(header: list[str], col_types: list[str], row: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for i, name in enumerate(header):
        out[f"c{i}"] = _coerce_value(col_types[i], row[i])
    return out


def _build_csv_catalog(meta: dict[str, Any]) -> str:
    """Compose a schema-catalog text for a CSV source, describing its single
    table and columns with inferred types. This is what Oleg sees in its prompt."""
    table = meta["table_name"]
    cols = meta.get("columns") or []
    lines = [f"# User-uploaded CSV source: {meta['name']}", ""]
    lines.append(f"## {table}")
    lines.append(f"Rows: {meta.get('row_count', '?')}. Inferred column types below.")
    for c in cols:
        kind = c.get("type", "text")
        lines.append(f"- {c['name']} {kind.upper()}")
    lines.append("")
    lines.append("Notes:")
    lines.append("- Use SQLite syntax. Quote table/column names if unsure (they are case-sensitive).")
    lines.append("- Date columns are stored as TEXT in ISO format (YYYY-MM-DD).")
    lines.append("- Only SELECT / WITH queries are allowed.")
    return "\n".join(lines)
