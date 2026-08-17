"""Data sources API: list, upload (CSV / Excel), PostgreSQL, delete."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.db import datasources

router = APIRouter(prefix="/api/datasources", tags=["datasources"])

_ACCEPTED = {".csv", ".xlsx"}
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB cap on uploaded files


@router.get("")
def list_datasources() -> list[dict]:
    return datasources.list_sources()


@router.get("/{source_id}/suggestions")
async def get_suggestions(source_id: str, model: str = "mock", lang: str = "ru") -> dict:
    """Schema-based example questions; LLM-crafted for real models (cached), heuristic otherwise."""
    meta = datasources.get_source_meta(source_id)
    if meta is None:
        raise HTTPException(404, "Data source not found")
    questions = await datasources.suggest_questions_smart(source_id, model_id=model, lang=lang)
    return {"suggestions": questions}


@router.post("/upload")
async def upload_file(file: UploadFile = File(...)) -> dict:
    """Upload a ``.csv`` or ``.xlsx`` file and register it as one or more data
    sources. CSV yields exactly one source; an Excel workbook yields one source
    per non-empty sheet. Always returns ``{"sources": [...], "count": N}``.
    """
    name = (file.filename or "").lower()
    if not name.endswith(tuple(_ACCEPTED)):
        raise HTTPException(400, "Only .csv and .xlsx files are accepted.")
    raw = await file.read()
    if len(raw) > _MAX_BYTES:
        raise HTTPException(413, "File is too large (max 25 MB).")

    original_name = file.filename or "upload"

    try:
        if name.endswith(".xlsx"):
            metas = datasources.ingest_xlsx(original_name, raw)
        else:
            # CSV: decode then ingest as a single source.
            try:
                text = raw.decode("utf-8-sig")  # utf-8-sig tolerates a BOM
            except UnicodeDecodeError:
                try:
                    text = raw.decode("cp1251")  # common for RU Excel CSV exports
                except UnicodeDecodeError as e:
                    raise HTTPException(400, "CSV must be UTF-8 or CP1251 encoded.") from e
            meta = datasources.ingest_csv(original_name, text)
            metas = [meta]
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    return {
        "sources": [_source_summary(m) for m in metas],
        "count": len(metas),
    }


def _source_summary(meta: dict) -> dict:
    return {
        "id": meta["id"],
        "name": meta["name"],
        "kind": meta["kind"],
        "description": meta["description"],
        "row_count": meta["row_count"],
        "columns": meta["columns"],
        "created_at": meta["created_at"],
    }


@router.delete("/{source_id}")
def delete_datasource(source_id: str) -> dict:
    try:
        datasources.delete_source(source_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True}


# --------------------------------------------------------------------------- #
# PostgreSQL sources
# --------------------------------------------------------------------------- #


class PostgresConnection(BaseModel):
    name: str
    host: str
    port: int = 5432
    database: str
    username: str
    password: str


@router.post("/postgres")
def add_postgres(body: PostgresConnection) -> dict:
    """Register a PostgreSQL source: test connection → introspect → save."""
    try:
        meta = datasources.register_postgres(
            name=body.name,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "id": meta["id"],
        "name": meta["name"],
        "kind": meta["kind"],
        "description": meta["description"],
        "tables": len(meta.get("columns") or []),
    }


class ClickHouseConnection(BaseModel):
    name: str
    host: str
    port: int = 8123
    database: str = "default"
    username: str = "default"
    password: str = ""
    secure: bool = False


@router.post("/clickhouse")
def add_clickhouse(body: ClickHouseConnection) -> dict:
    """Register a ClickHouse source: test connection → introspect → save."""
    try:
        meta = datasources.register_clickhouse(
            name=body.name,
            host=body.host,
            port=body.port,
            database=body.database,
            username=body.username,
            password=body.password,
            secure=body.secure,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "id": meta["id"],
        "name": meta["name"],
        "kind": meta["kind"],
        "description": meta["description"],
        "tables": len(meta.get("columns") or []),
    }


@router.post("/{source_id}/refresh")
def refresh_schema(source_id: str) -> dict:
    """Re-introspect the schema for an existing PostgreSQL source."""
    try:
        meta = datasources.refresh_postgres_schema(source_id)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {
        "id": meta["id"],
        "name": meta["name"],
        "tables": len(meta.get("columns") or []),
    }
