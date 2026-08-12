"""Data sources API: list, upload (CSV / Excel), delete."""
from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.db import datasources

router = APIRouter(prefix="/api/datasources", tags=["datasources"])

_ACCEPTED = {".csv", ".xlsx"}
_MAX_BYTES = 25 * 1024 * 1024  # 25 MB cap on uploaded files


@router.get("")
def list_datasources() -> list[dict]:
    return datasources.list_sources()


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
